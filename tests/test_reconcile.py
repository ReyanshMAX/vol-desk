from datetime import date, timedelta

import pytest

from src.data.alpaca_data import OptionSnapshot
from src.execution import mcp_client, reconcile
from src.store import repo
from src.store.repo import Leg as PositionLeg, Position

EXP = date.today() + timedelta(days=10)
SHORT_SYM = f"SPY{EXP:%y%m%d}P00440000"
LONG_SYM = f"SPY{EXP:%y%m%d}P00435000"


def _broker_position(occ_symbol, qty, avg_price=1.0, mv=100.0, upl=0.0) -> mcp_client.BrokerPosition:
    return mcp_client.BrokerPosition(occ_symbol=occ_symbol, qty=qty, avg_entry_price=avg_price,
                                      market_value=mv, unrealized_pnl=upl)


def _fake_snap(occ_symbol, right, strike, mid) -> OptionSnapshot:
    return OptionSnapshot(occ_symbol=occ_symbol, underlying="SPY", expiration=EXP,
                           strike=strike, right=right, bid=mid - 0.02, ask=mid + 0.02,
                           mid=mid, delta=-0.15, implied_volatility=0.2)


def test_group_broker_positions_groups_by_underlying_and_expiry(db_conn, monkeypatch):
    monkeypatch.setattr(mcp_client, "get_positions", lambda: [
        _broker_position(SHORT_SYM, qty=-1),
        _broker_position(LONG_SYM, qty=1),
    ])
    groups = reconcile._group_broker_positions()
    assert len(groups) == 1
    pk = repo.position_key([SHORT_SYM, LONG_SYM])
    assert pk in groups
    assert len(groups[pk]) == 2


def test_adopt_orphan_writes_open_state_with_conservative_plan(db_conn, monkeypatch):
    monkeypatch.setattr("src.execution.reconcile.fetch_latest_price", lambda symbol: 450.0)
    monkeypatch.setattr("src.execution.reconcile.fetch_chain", lambda *a, **k: [
        _fake_snap(SHORT_SYM, "P", 440.0, 1.10),
        _fake_snap(LONG_SYM, "P", 435.0, 0.20),
    ])

    legs = [
        (_broker_position(SHORT_SYM, qty=-1), "P", 440.0),
        (_broker_position(LONG_SYM, qty=1), "P", 435.0),
    ]
    pk = repo.position_key([SHORT_SYM, LONG_SYM])
    p = reconcile._adopt_orphan(pk, legs)

    assert p.state == "orphan"
    assert p.structure == "unknown"
    assert p.underlying == "SPY"
    assert p.position_key == pk
    assert len(p.legs) == 2
    # entry_credit is the current mark (1.10 - 0.20), not a fabricated true entry
    assert p.entry_credit == pytest.approx(0.90, abs=1e-6)
    # conservative stop: full 5-wide structural max loss
    assert p.max_loss == pytest.approx(500.0)

    stored = repo.get_position(pk)
    assert stored is not None
    assert stored.state == "orphan"


def test_run_matched_position_stays_open(db_conn, monkeypatch):
    pk = repo.position_key([SHORT_SYM, LONG_SYM])
    existing = Position(
        id=0, position_key=pk, underlying="SPY", structure="put_credit_spread",
        legs=[PositionLeg(occ_symbol=SHORT_SYM, side="sell", ratio=1, strike=440.0, right="P"),
              PositionLeg(occ_symbol=LONG_SYM, side="buy", ratio=1, strike=435.0, right="P")],
        qty=1, opened_at="2026-01-01T00:00:00+00:00", expiration=EXP.isoformat(),
        entry_credit=1.10, max_loss=390.0, take_profit_value=0.55, stop_loss_value=2.20,
        state="open", regime_at_entry="TREND_UP_HIGH_IV", decision_id=None,
        closed_at=None, close_reason=None, realized_pnl=None,
    )
    repo.upsert_position(existing)

    monkeypatch.setattr(mcp_client, "get_positions", lambda: [
        _broker_position(SHORT_SYM, qty=-1),
        _broker_position(LONG_SYM, qty=1),
    ])

    report = reconcile.run()
    assert pk in report.matched
    assert report.orphans_adopted == []
    assert report.ghosts_closed == []

    stored = repo.get_position(pk)
    assert stored.state == "open"


def test_run_ghost_position_closed_when_absent_at_broker(db_conn, monkeypatch):
    pk = repo.position_key([SHORT_SYM, LONG_SYM])
    existing = Position(
        id=0, position_key=pk, underlying="SPY", structure="put_credit_spread",
        legs=[PositionLeg(occ_symbol=SHORT_SYM, side="sell", ratio=1, strike=440.0, right="P"),
              PositionLeg(occ_symbol=LONG_SYM, side="buy", ratio=1, strike=435.0, right="P")],
        qty=1, opened_at="2026-01-01T00:00:00+00:00", expiration=EXP.isoformat(),
        entry_credit=1.10, max_loss=390.0, take_profit_value=0.55, stop_loss_value=2.20,
        state="open", regime_at_entry="TREND_UP_HIGH_IV", decision_id=None,
        closed_at=None, close_reason=None, realized_pnl=None,
    )
    repo.upsert_position(existing)

    monkeypatch.setattr(mcp_client, "get_positions", lambda: [])  # nothing at the broker

    report = reconcile.run()
    assert pk in report.ghosts_closed

    stored = repo.get_position(pk)
    assert stored.state == "closed"
    assert stored.close_reason == "manual"


def test_run_orphan_adopted_when_present_at_broker_but_not_db(db_conn, monkeypatch):
    monkeypatch.setattr(mcp_client, "get_positions", lambda: [
        _broker_position(SHORT_SYM, qty=-1),
        _broker_position(LONG_SYM, qty=1),
    ])
    monkeypatch.setattr("src.execution.reconcile.fetch_latest_price", lambda symbol: 450.0)
    monkeypatch.setattr("src.execution.reconcile.fetch_chain", lambda *a, **k: [
        _fake_snap(SHORT_SYM, "P", 440.0, 1.10),
        _fake_snap(LONG_SYM, "P", 435.0, 0.20),
    ])

    report = reconcile.run()
    assert len(report.orphans_adopted) == 1
    pk = report.orphans_adopted[0]
    stored = repo.get_position(pk)
    assert stored.state == "orphan"
