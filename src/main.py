"""Entrypoint: boot, reconcile, run scheduler (docs/ARCHITECTURE.md boot
sequence). Any failure in steps 1-5 is fatal -- exit non-zero and let
systemd restart with backoff. Do not start the scheduler in a
partially-initialized state.
"""
from __future__ import annotations

import logging
import sys

from src import config as config_module
from src import scheduler as scheduler_module
from src.agents import regime, risk, signal, strategy
from src.data import iv
from src.execution import mcp_client, orders, reconcile
from src.llm.client import InferenceUnavailable
from src.store import db, repo

logger = logging.getLogger("vol_desk.main")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _log_inference_unavailable_once(action: str) -> None:
    repo.log_decision(
        agent="supervisor", action="inference_unavailable",
        inputs={"job": action}, output={}, accepted=False,
        veto_reason="groq_unavailable",
    )
    logger.warning("%s: inference unavailable, degrading to hold-and-manage (D-010)", action)


def job_iv_snapshot() -> None:
    cfg = config_module.load()
    for sym in cfg.symbol_tickers():
        try:
            iv.snapshot_iv(sym)
        except Exception:
            logger.exception("iv_snapshot(%s) failed", sym)


def job_regime_refresh() -> None:
    """Pre-warm the regime cache for every symbol passing the entry gate,
    logged even when no trade follows (BUILD.md Phase 4 scope: 'the label
    is computed and logged only')."""
    cfg = config_module.load()
    for sym in cfg.symbol_tickers():
        try:
            signals = signal.compute(sym)
            if not signal.passes_entry_gate(signals):
                continue
            regime.classify(sym, signals)
        except InferenceUnavailable:
            _log_inference_unavailable_once("regime_refresh")
            return  # D-010: stop for this cycle, not once per symbol
        except Exception:
            logger.exception("regime_refresh(%s) failed", sym)


def job_entry_scan() -> None:
    """Full entry pipeline (docs/ARCHITECTURE.md). Restricted to the
    trading_window inside market hours -- no entries in the first/last 30
    minutes."""
    cfg = config_module.load()
    if not scheduler_module.in_trading_window(
        cfg.trading_window["open"], cfg.trading_window["close"]
    ):
        return
    if risk.halt_state() != "normal":
        return  # entry_scan short-circuits under any halt (docs/RISK.md)

    account = mcp_client.get_account()
    open_positions = repo.open_positions()

    for sym in cfg.symbol_tickers():
        try:
            signals = signal.compute(sym)
            if not signal.passes_entry_gate(signals):
                continue

            try:
                verdict = regime.classify(sym, signals)
            except InferenceUnavailable:
                _log_inference_unavailable_once("entry_scan")
                return  # D-010: hold and manage, no new entries this scan

            eligible = strategy.STRUCTURE_ELIGIBILITY.get(verdict.label, [])
            if not eligible:
                continue

            chain = _fetch_entry_chain(sym, signals, cfg)
            intent = strategy.construct(sym, signals, verdict, eligible, chain)
            if intent is None:
                continue

            risk_verdict = risk.evaluate(intent, account, open_positions)
            if not risk_verdict.approved:
                continue

            fill = orders.submit_with_ladder(intent, risk_verdict.qty)
            if fill.status in ("FILLED", "PARTIAL") and fill.fill_price is not None:
                position = orders.build_position_row(intent, fill.filled_qty, fill.fill_price)
                repo.upsert_position(position)
                open_positions = repo.open_positions()
        except Exception:
            logger.exception("entry_scan(%s) failed", sym)


def _fetch_entry_chain(symbol: str, signals, cfg):
    from src.data.alpaca_data import fetch_chain
    return fetch_chain(
        symbol, signals.underlying_price,
        dte_min=cfg.strategy["dte_min"], dte_max=cfg.strategy["dte_max"],
        strike_range_pct=cfg.strategy["strike_range_pct"],
    )


def job_manage_positions() -> None:
    orders.manage_positions()


def job_risk_monitor() -> None:
    risk.risk_monitor()


def job_equity_snapshot() -> None:
    risk.equity_snapshot()


def main() -> None:
    # 1. load_config()
    try:
        cfg = config_module.load()
    except config_module.ConfigError as e:
        print(f"FATAL: config error: {e}", file=sys.stderr)
        sys.exit(1)

    _setup_logging(cfg.log_level)
    logger.info("vol-desk booting")

    # 2. db.connect()
    try:
        db.connect(cfg.db_path)
    except Exception:
        logger.exception("FATAL: db connect failed")
        sys.exit(1)

    # 3. mcp_client.connect()
    try:
        mcp_client.connect()
    except Exception:
        logger.exception("FATAL: MCP connect failed")
        sys.exit(1)

    # 4. reconcile.run()
    try:
        report = reconcile.run()
        logger.info("reconcile: matched=%d orphans=%d ghosts=%d in_flight=%d",
                     len(report.matched), len(report.orphans_adopted),
                     len(report.ghosts_closed), len(report.in_flight_resolved))
    except Exception:
        logger.exception("FATAL: reconcile failed")
        sys.exit(1)

    # 5. iv.ensure_seeded()
    try:
        iv.ensure_seeded()
    except Exception:
        logger.exception("FATAL: iv.ensure_seeded failed")
        sys.exit(1)

    # 6. scheduler.start()
    sched = scheduler_module.Scheduler()
    sched.register("risk_monitor", cfg.cadences["risk_monitor_s"], job_risk_monitor, market_hours_only=False)
    sched.register("manage_positions", cfg.cadences["manage_positions_s"], job_manage_positions, market_hours_only=True)
    sched.register("iv_snapshot", cfg.cadences["iv_snapshot_s"], job_iv_snapshot, market_hours_only=True)
    sched.register("entry_scan", cfg.cadences["entry_scan_s"], job_entry_scan, market_hours_only=True)
    sched.register("regime_refresh", cfg.cadences["regime_refresh_s"], job_regime_refresh, market_hours_only=True)
    sched.register("equity_snapshot", cfg.cadences["equity_snapshot_s"], job_equity_snapshot, market_hours_only=False)

    logger.info("vol-desk boot complete, entering scheduler loop")
    sched.start()


if __name__ == "__main__":
    main()
