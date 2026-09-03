"""End-to-end entry_scan pipeline test with every external boundary
(Alpaca MCP, Alpaca market data, Groq) faked. Real code runs for
signal computation, regime mechanical labeling, structure validation,
risk evaluation, and the price ladder -- only network calls are mocked.
This catches wiring bugs between modules that per-function unit tests
can't see (e.g. a signature mismatch, a sign convention flip between
strategy.py and risk.py, a field name typo across the pipeline).
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from src import main
from src import scheduler as scheduler_module
from src.agents import regime as regime_module
from src.agents import strategy as strategy_module
from src.data import alpaca_data
from src.data.alpaca_data import Bar, OptionSnapshot
from src.execution import mcp_client
from src.llm.client import JSONCompletionResult
from src.llm.schemas import RegimeResponse, StrategyLeg, StrategyResponse
from src.store import repo

EXP = date.today() + timedelta(days=10)
SHORT_SYM = f"SPY{EXP:%y%m%d}P00440000"
LONG_SYM = f"SPY{EXP:%y%m%d}P00435000"


def _seed_spy_iv_history():
    """24 older observations ascending 0.10..0.33, then a final (most
    recent) 0.24 -- ranks at 14/25 = 0.56: >= HIGH_IV_RANK (0.50) so the
    regime resolves to *_HIGH_IV, and < STRESS_IV_RANK (0.90) so it isn't
    STRESS. Chosen precisely rather than "high" to land inside that band
    instead of accidentally tripping the stress threshold."""
    now = datetime.now(timezone.utc)
    for i in range(24):
        ts = now - timedelta(days=24 - i)
        repo.insert_iv("SPY", ts, 0.10 + 0.01 * i, 430.0, 10, source="backfill")
    repo.insert_iv("SPY", now, 0.24, 439.0, 10, source="live")


def _fake_daily_bars(symbol: str, days: int) -> list[Bar]:
    # steady uptrend, low realized vol, near-zero range_score -> mechanical
    # label lands on TREND_UP_*, never STRESS
    closes = [410.0 + i * 1.0 for i in range(days)]
    return [Bar(ts=date.today() - timedelta(days=days - i), open=c, high=c, low=c,
                close=c, volume=1_000_000) for i, c in enumerate(closes)]


def _fake_chain(symbol, underlying_price, **kwargs) -> list[OptionSnapshot]:
    short = OptionSnapshot(occ_symbol=SHORT_SYM, underlying="SPY", expiration=EXP,
                            strike=440.0, right="P", bid=1.25, ask=1.35, mid=1.30,
                            delta=-0.16, implied_volatility=0.20, open_interest=200)
    long = OptionSnapshot(occ_symbol=LONG_SYM, underlying="SPY", expiration=EXP,
                           strike=435.0, right="P", bid=0.19, ask=0.21, mid=0.20,
                           delta=-0.06, implied_volatility=0.21, open_interest=200)
    return [short, long]


def _fake_complete_json_meta(system, user, schema, *, tier, **kwargs):
    # regime.py and strategy.py both do `from src.llm import client as
    # llm_client` -- that's the *same* module object, so a single dispatcher
    # is required here rather than patching each call site independently
    # (two separate monkeypatch.setattr calls on the same attribute would
    # just have the second clobber the first for the duration of the test).
    if schema is RegimeResponse:
        # agree with whatever the mechanical rule produced -- extract it
        # from the rendered prompt rather than recomputing, so this stays
        # correct even if the mechanical thresholds change
        mech_line = next(l for l in user.splitlines() if l.startswith("mechanical label:"))
        mech = mech_line.split(":", 1)[1].strip()
        parsed = RegimeResponse(label=mech, confidence=0.75,
                                 rationale="mocked regime agreement for pipeline test")
        return JSONCompletionResult(parsed=parsed, raw_text="{}", model="fake-fast-model",
                                     latency_ms=10, accepted=True)
    if schema is StrategyResponse:
        parsed = StrategyResponse(
            decision="trade", structure=strategy_module.StructureType.PUT_CREDIT_SPREAD,
            legs=[StrategyLeg(occ_symbol=SHORT_SYM, side="sell"),
                  StrategyLeg(occ_symbol=LONG_SYM, side="buy")],
            expiration=EXP, rationale="mocked strategy pick for pipeline test",
        )
        return JSONCompletionResult(parsed=parsed, raw_text="{}", model="fake-reasoning-model",
                                     latency_ms=20, accepted=True)
    raise AssertionError(f"unexpected schema {schema}")


@pytest.fixture()
def wired_pipeline(db_conn, monkeypatch):
    _seed_spy_iv_history()

    monkeypatch.setattr(alpaca_data, "fetch_daily_bars", _fake_daily_bars)
    monkeypatch.setattr(alpaca_data, "fetch_chain", _fake_chain)

    monkeypatch.setattr(scheduler_module, "in_trading_window", lambda *a, **k: True)

    assert regime_module.llm_client is strategy_module.llm_client  # documents the shared-module fact above
    monkeypatch.setattr(regime_module.llm_client, "complete_json_meta", _fake_complete_json_meta)

    account = mcp_client.Account(equity=100_000.0, cash=80_000.0, buying_power=100_000.0,
                                  ts=datetime.now(timezone.utc))
    monkeypatch.setattr(mcp_client, "get_account", lambda: account)

    fill_state = {"order_id": "order-1"}

    def fake_place_mleg_order(legs, qty, limit_price, side, *, is_opening):
        return fill_state["order_id"]

    def fake_list_orders(status="open"):
        return [mcp_client.BrokerOrder(
            order_id=fill_state["order_id"], status="filled",
            symbols=[SHORT_SYM, LONG_SYM], submitted_at=datetime.now(timezone.utc),
            filled_qty=999, filled_avg_price=1.10,
        )]

    monkeypatch.setattr(mcp_client, "place_mleg_order", fake_place_mleg_order)
    monkeypatch.setattr(mcp_client, "list_orders", fake_list_orders)
    monkeypatch.setattr(mcp_client, "cancel_order", lambda order_id: None)

    monkeypatch.setattr("src.execution.orders.time.sleep", lambda s: None)

    return account


def test_entry_scan_opens_a_position_end_to_end(wired_pipeline):
    main.job_entry_scan()

    positions = repo.open_positions()
    spy_positions = [p for p in positions if p.underlying == "SPY"]
    assert len(spy_positions) == 1

    p = spy_positions[0]
    assert p.state == "open"
    assert p.structure == "put_credit_spread"
    assert p.qty >= 1
    assert p.entry_credit == pytest.approx(1.10)
    assert {leg.occ_symbol for leg in p.legs} == {SHORT_SYM, LONG_SYM}

    # risk.evaluate's decision_log row exists and shows the approval
    conn = repo.db.get()
    row = conn.execute(
        "SELECT accepted FROM decision_log WHERE agent='risk' AND underlying='SPY' "
        "ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    assert row["accepted"] == 1

    # regime + strategy each logged exactly one row for SPY
    for agent in ("regime", "strategy"):
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM decision_log WHERE agent=? AND underlying='SPY'",
            (agent,),
        ).fetchone()["n"]
        assert count == 1


def test_entry_scan_skips_symbols_with_no_iv_history(wired_pipeline):
    """Only SPY was seeded with iv_history; the other six symbols must fail
    the entry gate and make zero LLM calls (docs/STRATEGY.md entry gate is
    the token-control mechanism)."""
    main.job_entry_scan()

    conn = repo.db.get()
    for symbol in ("QQQ", "IWM", "GLD", "TLT", "XLE", "HYG"):
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM decision_log WHERE underlying=?", (symbol,)
        ).fetchone()["n"]
        assert count == 0, f"{symbol} should never reach an LLM call with empty iv_history"


def test_entry_scan_vetoes_when_halted(wired_pipeline):
    repo.set_halt_state("soft_halt")
    main.job_entry_scan()
    # entry_scan short-circuits before even computing signals under any halt
    positions = repo.open_positions()
    assert positions == []
