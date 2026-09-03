"""Deterministic risk enforcement (D-005). No LLM. Last gate before any
order reaches Alpaca; can only reduce exposure, never widen it.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Literal

from src import config as config_module
from src.agents.strategy import OrderIntent, StructureType, _is_credit
from src.data.alpaca_data import _parse_occ_symbol
from src.execution import mcp_client
from src.store import repo
from src.store.repo import Position

logger = logging.getLogger("vol_desk.risk")


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    qty: int
    veto_reason: str | None
    checks: dict[str, bool]


def _is_defined_risk(intent: OrderIntent) -> bool:
    if len(intent.legs) not in (2, 4):
        return False
    if any(leg.ratio != 1 for leg in intent.legs):
        return False
    buys = sum(1 for leg in intent.legs if leg.side == "buy")
    sells = sum(1 for leg in intent.legs if leg.side == "sell")
    return buys == sells


def _recompute_width(intent: OrderIntent) -> float | None:
    parsed = [(_parse_occ_symbol(leg.occ_symbol)) for leg in intent.legs]  # (expiry, right, strike)

    def side_width(right: str) -> float | None:
        strikes = sorted(strike for _, r, strike in parsed if r == right)
        if len(strikes) != 2:
            return None
        return abs(strikes[1] - strikes[0])

    if intent.structure == StructureType.IRON_CONDOR:
        pw, cw = side_width("P"), side_width("C")
        if pw is None or cw is None:
            return None
        return max(pw, cw)
    right = "P" if intent.structure in (StructureType.PUT_CREDIT_SPREAD, StructureType.PUT_DEBIT_SPREAD) else "C"
    return side_width(right)


def _recompute_max_loss(intent: OrderIntent) -> float:
    width = _recompute_width(intent)
    if width is None:
        return float("inf")  # unparseable legs can never agree; forces a veto
    is_credit = _is_credit(intent.structure)
    if is_credit:
        return (width * 100) - (intent.net_credit * 100)
    return (-intent.net_credit) * 100


def _count(open_positions: list[Position], symbol: str) -> int:
    return sum(1 for p in open_positions if p.underlying == symbol)


def _cluster_for(symbol: str, clusters: dict[str, list[str]]) -> str | None:
    for cluster, symbols in clusters.items():
        if symbol in symbols:
            return cluster
    return None


def _cluster_count(open_positions: list[Position], symbol: str, clusters: dict[str, list[str]]) -> int:
    cluster = _cluster_for(symbol, clusters)
    if cluster is None:
        return 0
    cluster_symbols = set(clusters[cluster])
    return sum(1 for p in open_positions if p.underlying in cluster_symbols)


def _cluster_cap(symbol: str, clusters: dict[str, list[str]], cfg_risk: dict) -> int:
    cluster = _cluster_for(symbol, clusters)
    if cluster == "equity_beta":
        return cfg_risk["max_equity_beta_positions"]
    return cfg_risk["max_concurrent_positions"]


def _size(max_loss_per_contract: float, equity: float, max_risk_per_trade_pct: float) -> int:
    if max_loss_per_contract <= 0 or math.isinf(max_loss_per_contract):
        return 0
    budget = equity * max_risk_per_trade_pct
    return math.floor(budget / max_loss_per_contract)


def _projected_free_cash(account: mcp_client.Account, max_loss_per_contract: float, qty: int) -> float:
    return account.cash - (max_loss_per_contract * qty)


def _dte(expiration: date) -> int:
    return (expiration - date.today()).days


def halt_state() -> Literal["normal", "soft_halt", "hard_halt"]:
    return repo.halt_state()  # type: ignore[return-value]


def evaluate(intent: OrderIntent, account: mcp_client.Account,
             open_positions: list[Position]) -> RiskVerdict:
    """Every check runs and is recorded even after the first failure, so the
    decision_log row shows the complete picture (docs/RISK.md)."""
    cfg = config_module.load()
    r = cfg.risk
    clusters = cfg.correlation_clusters
    checks: dict[str, bool] = {}

    checks["not_halted"] = halt_state() == "normal"
    checks["defined_risk"] = _is_defined_risk(intent)

    recomputed = _recompute_max_loss(intent)
    checks["max_loss_agrees"] = abs(recomputed - intent.max_loss_per_contract) < 0.01

    checks["under_position_cap"] = len(open_positions) < r["max_concurrent_positions"]
    checks["under_symbol_cap"] = _count(open_positions, intent.symbol) < r["max_positions_per_underlying"]
    checks["under_cluster_cap"] = _cluster_count(open_positions, intent.symbol, clusters) < _cluster_cap(intent.symbol, clusters, r)

    checks["under_daily_cap"] = repo.opened_today_count() < r["max_daily_new_positions"]

    qty = _size(recomputed, account.equity, r["max_risk_per_trade_pct"])
    checks["sizeable"] = qty >= 1

    checks["cash_headroom"] = _projected_free_cash(account, recomputed, qty) >= account.equity * r["min_free_cash_pct"]

    checks["dte_in_window"] = cfg.strategy["dte_min"] <= _dte(intent.expiration) <= cfg.strategy["dte_max"]

    approved = all(checks.values())
    veto_reason = None if approved else ",".join(k for k, v in checks.items() if not v)

    repo.log_decision(
        agent="risk", action="evaluate",
        inputs={"intent": {
            "symbol": intent.symbol, "structure": intent.structure.value,
            "legs": [leg.__dict__ for leg in intent.legs],
            "expiration": intent.expiration.isoformat(),
            "net_credit": intent.net_credit,
            "max_loss_per_contract": intent.max_loss_per_contract,
        }},
        output={"checks": checks, "qty": qty if approved else 0},
        underlying=intent.symbol, rationale=None, model=None, latency_ms=None,
        accepted=approved, veto_reason=veto_reason,
    )

    return RiskVerdict(
        approved=approved,
        qty=qty if approved else 0,
        veto_reason=veto_reason,
        checks=checks,
    )


# --- risk_monitor: high-water mark, drawdown tiers, halts ------------------

RECOVERY_FACTOR_DEFAULT = 0.8


def _transition(new_state: Literal["normal", "soft_halt", "hard_halt"]) -> None:
    current = halt_state()
    if new_state == current:
        return
    repo.set_halt_state(new_state)
    logger.warning("halt_state transition: %s -> %s", current, new_state)
    if new_state == "hard_halt":
        _hard_halt_flatten()


def _hard_halt_flatten() -> None:
    """Cancel all open orders, close every position at market. Terminal and
    manual to clear (docs/RISK.md) -- no automatic recovery path."""
    try:
        for order in mcp_client.list_orders(status="open"):
            mcp_client.cancel_order(order.order_id)
    except Exception:
        logger.exception("hard_halt_flatten: failed to cancel open orders")

    closed_symbols: list[str] = []
    try:
        for pos in mcp_client.get_positions():
            mcp_client.close_position(pos.occ_symbol)
            closed_symbols.append(pos.occ_symbol)
    except Exception:
        logger.exception("hard_halt_flatten: failed to close positions")

    repo.log_decision(
        agent="risk", action="hard_halt_flatten",
        inputs={}, output={"closed_symbols": closed_symbols},
        accepted=True, veto_reason=None,
    )


def risk_monitor() -> None:
    """Runs every 60s. Only writer of high_water_mark. No LLM, no calls
    beyond one MCP account query (docs/RISK.md)."""
    cfg = config_module.load()
    r = cfg.risk
    recovery_factor = r.get("recovery_factor", RECOVERY_FACTOR_DEFAULT)

    account = mcp_client.get_account()
    hwm = max(repo.get_hwm(), account.equity)
    repo.set_hwm(hwm)
    dd = (hwm - account.equity) / hwm if hwm > 0 else 0.0

    if dd >= r["hard_drawdown_pct"]:
        _transition("hard_halt")
    elif dd >= r["soft_drawdown_pct"]:
        _transition("soft_halt")
    elif halt_state() == "soft_halt" and dd < r["soft_drawdown_pct"] * recovery_factor:
        _transition("normal")

    open_count = len(repo.open_positions())
    repo.append_equity_curve(account.equity, account.cash, hwm, dd, open_count, halt_state())


def equity_snapshot() -> None:
    """Runs every 15 minutes, market hours or not (docs/ARCHITECTURE.md).
    Read-only with respect to high_water_mark -- risk_monitor is the sole
    writer (docs/RISK.md). This job exists independently of risk_monitor so
    the equity curve keeps accumulating even before Phase 9's drawdown/halt
    logic is exercised."""
    account = mcp_client.get_account()
    hwm = repo.get_hwm()
    dd = (hwm - account.equity) / hwm if hwm > 0 else 0.0
    open_count = len(repo.open_positions())
    repo.append_equity_curve(account.equity, account.cash, hwm, dd, open_count, halt_state())
