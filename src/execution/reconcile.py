"""Reconciliation (docs/ARCHITECTURE.md). Runs on every boot. This is what
makes redeploy a non-event (D-013): the process holds no authoritative
state and rebuilds its picture from Alpaca + SQLite.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src import config as config_module
from src.data.alpaca_data import (
    _parse_occ_symbol, fetch_chain, fetch_latest_price, parse_occ_root,
)
from src.execution import mcp_client
from src.store import repo
from src.store.repo import Leg as PositionLeg, Position

logger = logging.getLogger("vol_desk.reconcile")


@dataclass
class ReconcileReport:
    matched: list[str] = field(default_factory=list)
    orphans_adopted: list[str] = field(default_factory=list)
    ghosts_closed: list[str] = field(default_factory=list)
    in_flight_resolved: list[str] = field(default_factory=list)


def _group_broker_positions() -> dict[str, list]:
    """Group Alpaca option legs by (underlying, expiration) -> position_key."""
    groups: dict[tuple[str, str], list] = {}
    for bp in mcp_client.get_positions():
        root = parse_occ_root(bp.occ_symbol)
        expiry, right, strike = _parse_occ_symbol(bp.occ_symbol)
        key = (root, expiry.isoformat())
        groups.setdefault(key, []).append((bp, right, strike))

    by_position_key: dict[str, list] = {}
    for (root, expiry_iso), legs in groups.items():
        occ_symbols = [bp.occ_symbol for bp, _, _ in legs]
        pk = repo.position_key(occ_symbols)
        by_position_key[pk] = legs
    return by_position_key


def _structural_width(legs: list) -> float | None:
    by_right: dict[str, list[float]] = {}
    for bp, right, strike in legs:
        by_right.setdefault(right, []).append(strike)
    widths = []
    for strikes in by_right.values():
        if len(strikes) == 2:
            widths.append(abs(strikes[1] - strikes[0]))
    return max(widths) if widths else None


def _adopt_orphan(position_key: str, legs: list) -> Position:
    cfg = config_module.load()
    m = cfg.management
    root = parse_occ_root(legs[0][0].occ_symbol)
    expiry, _, _ = _parse_occ_symbol(legs[0][0].occ_symbol)

    underlying_price = fetch_latest_price(root)
    chain = fetch_chain(root, underlying_price, dte_min=0,
                         dte_max=max(1, (expiry - datetime.now(timezone.utc).date()).days + 1),
                         strike_range_pct=0.30)
    chain_by_symbol = {c.occ_symbol: c for c in chain}

    position_legs: list[PositionLeg] = []
    mark = 0.0
    mark_known = True
    for bp, right, strike in legs:
        side = "sell" if bp.qty < 0 else "buy"
        position_legs.append(PositionLeg(occ_symbol=bp.occ_symbol, side=side, ratio=1,
                                          strike=strike, right=right))
        snap = chain_by_symbol.get(bp.occ_symbol)
        if snap is None or snap.mid is None:
            mark_known = False
        else:
            mark += snap.mid if side == "sell" else -snap.mid

    width = _structural_width(legs)
    is_credit = mark >= 0 if mark_known else True
    entry_credit = mark if mark_known else 0.0

    if width is not None:
        # most conservative: assume the full width is at risk (D-002/D-005
        # -- do not assume favorable credit for an unverified structure)
        structural_max_loss = width * 100
    else:
        structural_max_loss = abs(entry_credit) * 100 or 1.0

    if is_credit:
        take_profit_value = entry_credit * (1 - m["take_profit_pct"])
        stop_loss_value = structural_max_loss / 100  # expressed as close-cost, matching entry_credit's units
    else:
        entry_debit = -entry_credit
        take_profit_value = entry_debit * (1 + m["take_profit_pct"])
        stop_loss_value = structural_max_loss / 100

    qty = abs(legs[0][0].qty) if legs else 1

    p = Position(
        id=0, position_key=position_key, underlying=root, structure="unknown",
        legs=position_legs, qty=qty, opened_at=datetime.now(timezone.utc).isoformat(),
        expiration=expiry.isoformat(), entry_credit=entry_credit,
        max_loss=structural_max_loss, take_profit_value=take_profit_value,
        stop_loss_value=stop_loss_value, state="orphan", regime_at_entry=None,
        decision_id=None, closed_at=None, close_reason=None, realized_pnl=None,
    )
    repo.upsert_position(p)
    repo.log_decision(
        agent="supervisor", action="adopt_orphan",
        inputs={"legs": [bp.occ_symbol for bp, _, _ in legs]},
        output={"position_key": position_key, "entry_credit": entry_credit,
                "mark_known": mark_known},
        underlying=root, accepted=True, veto_reason=None,
    )
    return p


def run() -> ReconcileReport:
    report = ReconcileReport()
    broker_groups = _group_broker_positions()
    db_positions = {p.position_key: p for p in repo.open_positions()}

    # MATCHED / GHOST
    for pk, p in db_positions.items():
        if p.state in ("opening", "closing"):
            _resolve_in_flight(p, broker_groups, report)
            continue
        if pk in broker_groups:
            report.matched.append(pk)
        else:
            # GHOST: DB says open, Alpaca disagrees. Realized P&L would
            # normally come from the Alpaca activities feed, but that
            # capability is not part of REQUIRED_TOOLS (Q-001 covers only
            # account/positions/orders_list/place_mleg_order/cancel_order/
            # close_position) -- record the closure without a fabricated
            # P&L figure rather than guess.
            repo.close_position(pk, "manual", 0.0)
            repo.log_decision(
                agent="supervisor", action="reconcile_ghost",
                inputs={"position_key": pk}, output={},
                underlying=p.underlying, accepted=True, veto_reason=None,
                rationale="closed at Alpaca; realized_pnl unavailable via current MCP tool set (Q-001)",
            )
            report.ghosts_closed.append(pk)

    # ORPHAN
    for pk, legs in broker_groups.items():
        if pk not in db_positions:
            _adopt_orphan(pk, legs)
            report.orphans_adopted.append(pk)

    return report


def _resolve_in_flight(p: Position, broker_groups: dict, report: ReconcileReport) -> None:
    """The schema (docs/DATA.md) has no order_id column, so a state='opening'
    or 'closing' row can only be resolved by whether its legs currently
    exist at the broker -- not by re-querying a specific order. This is a
    coarser resolution than docs/ARCHITECTURE.md's IN_FLIGHT description
    implies; a precise re-query needs an order_id persisted at submission
    time, which is a gap worth raising if IN_FLIGHT states start being
    written (currently build_position_row writes 'open' directly on fill,
    so this path is not yet exercised in normal operation)."""
    if p.position_key in broker_groups:
        repo.upsert_position(Position(**{**p.__dict__, "state": "open"}))
        report.in_flight_resolved.append(p.position_key)
    else:
        repo.upsert_position(Position(**{**p.__dict__, "state": "closed",
                                          "close_reason": "manual", "realized_pnl": 0.0}))
        report.in_flight_resolved.append(p.position_key)
