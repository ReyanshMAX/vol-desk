"""Order submission (price ladder, D-022) and deterministic position
management (docs/STRATEGY.md, docs/INTEGRATIONS.md). No LLM.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal

from src import config as config_module
from src.agents.strategy import OrderIntent
from src.data.alpaca_data import OptionSnapshot, _parse_occ_symbol, fetch_chain, fetch_latest_price
from src.execution import mcp_client
from src.store import repo
from src.store.repo import Leg as PositionLeg, Position

logger = logging.getLogger("vol_desk.orders")


@dataclass(frozen=True)
class FillResult:
    status: Literal["FILLED", "PARTIAL", "ABANDONED"]
    filled_qty: int
    fill_price: float | None   # per contract, same sign convention as net_credit
    order_id: str | None


def _mcp_legs(legs: list, reverse_sides: bool = False) -> list[mcp_client.Leg]:
    def flip(side: str) -> str:
        return "buy" if side == "sell" else "sell"
    return [
        mcp_client.Leg(occ_symbol=l.occ_symbol,
                        side=(flip(l.side) if reverse_sides else l.side),
                        ratio=1)
        for l in legs
    ]


def _run_ladder(mcp_legs: list[mcp_client.Leg], qty: int, base_price: float,
                 side: Literal["credit", "debit"], *, is_opening: bool) -> FillResult:
    cfg = config_module.load()
    ex = cfg.execution
    steps, step_pct, wait_s = ex["ladder_steps"], ex["ladder_step_pct"], ex["ladder_wait_seconds"]

    order_id: str | None = None
    for rung in range(steps):
        if side == "credit":
            limit = base_price * (1 - rung * step_pct)
        else:
            limit = base_price * (1 + rung * step_pct)

        order_id = mcp_client.place_mleg_order(mcp_legs, qty, limit, side, is_opening=is_opening)
        time.sleep(wait_s)

        orders = mcp_client.list_orders(status="all")
        match = next((o for o in orders if o.order_id == order_id), None)
        status = (match.status if match else "unknown").lower()
        filled_qty = getattr(match, "filled_qty", None) if match else None
        avg_price = getattr(match, "filled_avg_price", None) if match else None

        if status == "filled":
            return FillResult("FILLED", qty, avg_price if avg_price is not None else limit, order_id)
        if status == "partially_filled":
            # Terminal at any rung, not just the last one: the next rung
            # would otherwise resubmit the ORIGINAL qty against a position
            # that already has some fill, risking an over-fill. Docs/
            # INTEGRATIONS.md's ladder outcomes (Filled/Partial/Unfilled)
            # are evaluated per rung; only "unfilled" is scoped to "after
            # the final rung" specifically -- a partial stops the ladder
            # immediately, matching CLAUDE.md rule 5 (never widen to chase
            # a fill, which includes never re-submitting on top of one).
            mcp_client.cancel_order(order_id)
            return FillResult("PARTIAL", int(filled_qty or 0), avg_price, order_id)

        mcp_client.cancel_order(order_id)

    return FillResult("ABANDONED", 0, None, order_id)


def submit_with_ladder(intent: OrderIntent, qty: int) -> FillResult:
    """CLAUDE.md rule 5: if this fails to fill inside the configured
    ladder, abandon it. Never widen, never increase size, never reselect
    strikes."""
    side: Literal["credit", "debit"] = "credit" if intent.net_credit >= 0 else "debit"
    base_price = abs(intent.net_credit)
    mcp_legs = _mcp_legs(intent.legs)

    result = _run_ladder(mcp_legs, qty, base_price, side, is_opening=True)

    if result.status == "ABANDONED":
        repo.log_decision(
            agent="manager", action="submit_with_ladder",
            inputs={"symbol": intent.symbol, "structure": intent.structure.value, "qty": qty},
            output={"status": "ABANDONED"},
            underlying=intent.symbol, accepted=False, veto_reason="ladder_exhausted",
        )
    return result


def build_position_row(intent: OrderIntent, qty: int, fill_price: float) -> Position:
    cfg = config_module.load()
    m = cfg.management
    is_credit = fill_price >= 0

    legs: list[PositionLeg] = []
    for leg in intent.legs:
        _, right, strike = _parse_occ_symbol(leg.occ_symbol)
        legs.append(PositionLeg(occ_symbol=leg.occ_symbol, side=leg.side, ratio=leg.ratio,
                                 strike=strike, right=right))

    if is_credit:
        take_profit_value = fill_price * (1 - m["take_profit_pct"])
        stop_loss_value = fill_price * m["stop_loss_mult"]
    else:
        entry_debit = -fill_price
        take_profit_value = entry_debit * (1 + m["take_profit_pct"])
        stop_loss_value = entry_debit * (1 - m["stop_loss_mult"] / 2)

    return Position(
        id=0,
        position_key=repo.position_key([l.occ_symbol for l in intent.legs]),
        underlying=intent.symbol,
        structure=intent.structure.value,
        legs=legs,
        qty=qty,
        opened_at=datetime.now(timezone.utc).isoformat(),
        expiration=intent.expiration.isoformat(),
        entry_credit=fill_price,
        max_loss=intent.max_loss_per_contract,
        take_profit_value=take_profit_value,
        stop_loss_value=stop_loss_value,
        state="open",
        regime_at_entry=intent.regime_label.value,
        decision_id=None,
        closed_at=None,
        close_reason=None,
        realized_pnl=None,
    )


def _mark_to_market(legs: list[PositionLeg], chain_by_symbol: dict[str, OptionSnapshot]) -> float | None:
    total = 0.0
    for leg in legs:
        c = chain_by_symbol.get(leg.occ_symbol)
        if c is None or c.mid is None:
            return None
        total += c.mid if leg.side == "sell" else -c.mid
    return total


def _dte(expiration_iso: str) -> int:
    return (date.fromisoformat(expiration_iso) - date.today()).days


def close_structure(p: Position, reason: str) -> FillResult:
    is_credit = p.entry_credit >= 0
    closing_side: Literal["credit", "debit"] = "debit" if is_credit else "credit"
    mcp_legs = _mcp_legs(p.legs, reverse_sides=True)

    underlying_price = fetch_latest_price(p.underlying)
    chain = fetch_chain(p.underlying, underlying_price, dte_min=0,
                         dte_max=max(1, _dte(p.expiration) + 1), strike_range_pct=0.30)
    chain_by_symbol = {c.occ_symbol: c for c in chain}
    mark = _mark_to_market(p.legs, chain_by_symbol)
    base_price = abs(mark) if mark is not None else abs(p.entry_credit)

    result = _run_ladder(mcp_legs, p.qty, base_price, closing_side, is_opening=False)

    if result.status in ("FILLED", "PARTIAL") and result.fill_price is not None:
        close_price = result.fill_price
        if is_credit:
            realized_pnl = (p.entry_credit - close_price) * 100 * result.filled_qty
        else:
            entry_debit = -p.entry_credit
            realized_pnl = (close_price - entry_debit) * 100 * result.filled_qty

        remaining_qty = p.qty - result.filled_qty
        if result.status == "FILLED" or remaining_qty <= 0:
            repo.close_position(p.position_key, reason, realized_pnl)
        else:
            # Partial close: the schema (docs/DATA.md) has one realized_pnl
            # field per row, sized for a single terminal close -- it cannot
            # represent "N of qty contracts closed, M still open." Keep the
            # row open with the remaining qty (still managed normally next
            # cycle) and record the partial fill's P&L in decision_log only,
            # which is the documented fallback for state the current-state
            # tables don't capture (D-014).
            repo.upsert_position(Position(**{**p.__dict__, "qty": remaining_qty}))
            repo.log_decision(
                agent="manager", action="partial_close",
                inputs={"position_key": p.position_key, "reason": reason,
                        "original_qty": p.qty},
                output={"filled_qty": result.filled_qty, "remaining_qty": remaining_qty,
                        "realized_pnl_on_filled": realized_pnl, "close_price": close_price},
                underlying=p.underlying, accepted=True, veto_reason=None,
            )
    else:
        repo.log_decision(
            agent="manager", action="close_structure",
            inputs={"position_key": p.position_key, "reason": reason},
            output={"status": result.status},
            underlying=p.underlying, accepted=False, veto_reason="close_ladder_exhausted",
        )
    return result


def manage_positions() -> None:
    """Runs every 5 minutes. No LLM -- continues when GROQ_API_KEY is unset
    (D-010). Triggers evaluated in order; first match fires."""
    cfg = config_module.load()

    for p in repo.open_positions():
        if p.state not in ("open", "orphan"):
            continue

        underlying_price = fetch_latest_price(p.underlying)
        chain = fetch_chain(p.underlying, underlying_price, dte_min=0,
                             dte_max=max(1, _dte(p.expiration) + 1), strike_range_pct=0.30)
        chain_by_symbol = {c.occ_symbol: c for c in chain}
        mark = _mark_to_market(p.legs, chain_by_symbol)
        if mark is None:
            logger.info("manage_positions: no mark for %s this cycle, skipping", p.position_key)
            continue

        is_credit = p.entry_credit >= 0
        current_value = mark if is_credit else -mark
        dte = _dte(p.expiration)

        reason: str | None = None
        if is_credit:
            if current_value <= p.take_profit_value:
                reason = "take_profit"
            elif current_value >= p.stop_loss_value:
                reason = "stop_loss"
            elif dte <= cfg.management["force_close_dte"]:
                reason = "force_close_dte"
        else:
            if current_value >= p.take_profit_value:
                reason = "take_profit"
            elif current_value <= p.stop_loss_value:
                reason = "stop_loss"
            elif dte <= cfg.management["force_close_dte"]:
                reason = "force_close_dte"

        if reason:
            close_structure(p, reason)
