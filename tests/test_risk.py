from datetime import date, timedelta

import pytest

from src.agents.regime import RegimeLabel
from src.agents.strategy import Leg, OrderIntent, StructureType
from src.agents import risk
from src.execution.mcp_client import Account
from src.store.repo import Leg as PositionLeg, Position


def _intent(**overrides) -> OrderIntent:
    expiration = date.today() + timedelta(days=10)
    base = dict(
        symbol="SPY",
        structure=StructureType.PUT_CREDIT_SPREAD,
        legs=[
            Leg(occ_symbol=f"SPY{expiration:%y%m%d}P00440000", side="sell", ratio=1),
            Leg(occ_symbol=f"SPY{expiration:%y%m%d}P00435000", side="buy", ratio=1),
        ],
        expiration=expiration,
        net_credit=1.00,
        max_loss_per_contract=400.0,   # width 5.0 * 100 - 1.00*100
        short_delta=-0.16,
        regime_label=RegimeLabel.TREND_UP_HIGH_IV,
        rationale="test fixture",
    )
    base.update(overrides)
    return OrderIntent(**base)


def _account(equity=100_000.0, cash=80_000.0) -> Account:
    from datetime import datetime, timezone
    return Account(equity=equity, cash=cash, buying_power=equity, ts=datetime.now(timezone.utc))


def _open_position(symbol="QQQ") -> Position:
    expiration = (date.today() + timedelta(days=10)).isoformat()
    return Position(
        id=1, position_key=f"key-{symbol}", underlying=symbol,
        structure="put_credit_spread",
        legs=[PositionLeg(occ_symbol="X", side="sell", ratio=1, strike=100.0, right="P")],
        qty=1, opened_at="2026-01-01T00:00:00+00:00", expiration=expiration,
        entry_credit=1.0, max_loss=400.0, take_profit_value=0.5, stop_loss_value=2.0,
        state="open", regime_at_entry=None, decision_id=None, closed_at=None,
        close_reason=None, realized_pnl=None,
    )


def test_size_floors_and_never_rounds_up(db_conn):
    assert risk._size(400.0, 100_000, 0.01) == 2
    assert risk._size(1_500.0, 100_000, 0.01) == 0


def test_size_zero_max_loss_returns_zero(db_conn):
    assert risk._size(0.0, 100_000, 0.01) == 0


def test_recompute_max_loss_matches_construction(db_conn):
    intent = _intent()
    recomputed = risk._recompute_max_loss(intent)
    assert abs(recomputed - intent.max_loss_per_contract) < 0.01


def test_recompute_max_loss_disagrees_when_understated(db_conn):
    intent = _intent(max_loss_per_contract=399.0)  # off by 1.00 from the true 400.0
    recomputed = risk._recompute_max_loss(intent)
    assert abs(recomputed - intent.max_loss_per_contract) >= 0.01


def test_evaluate_approves_clean_intent(db_conn):
    verdict = risk.evaluate(_intent(), _account(), open_positions=[])
    assert verdict.approved is True
    assert verdict.qty >= 1
    assert set(verdict.checks) == {
        "not_halted", "defined_risk", "max_loss_agrees", "under_position_cap",
        "under_symbol_cap", "under_cluster_cap", "under_daily_cap", "sizeable",
        "cash_headroom", "dte_in_window",
    }
    assert all(verdict.checks.values())


def test_evaluate_vetoes_when_halted(db_conn):
    from src.store import repo
    repo.set_halt_state("soft_halt")
    verdict = risk.evaluate(_intent(), _account(), open_positions=[])
    assert verdict.approved is False
    assert verdict.checks["not_halted"] is False


def test_evaluate_vetoes_over_position_cap(db_conn):
    positions = [_open_position(symbol=f"SYM{i}") for i in range(6)]
    verdict = risk.evaluate(_intent(), _account(), open_positions=positions)
    assert verdict.checks["under_position_cap"] is False
    assert verdict.approved is False


def test_evaluate_vetoes_cluster_cap_for_correlated_equity_beta(db_conn):
    # SPY + QQQ already open (equity_beta cluster), cap is 3, so IWM proposal
    # should still pass unless the cap is already at max_equity_beta_positions
    positions = [_open_position(symbol="SPY"), _open_position(symbol="QQQ"),
                 _open_position(symbol="IWM")]
    intent = _intent(symbol="IWM")
    verdict = risk.evaluate(intent, _account(), open_positions=positions)
    assert verdict.checks["under_cluster_cap"] is False
    assert verdict.approved is False


def test_evaluate_defined_risk_false_for_unbalanced_legs(db_conn):
    expiration = date.today() + timedelta(days=10)
    intent = _intent(legs=[
        Leg(occ_symbol=f"SPY{expiration:%y%m%d}P00440000", side="sell", ratio=1),
        Leg(occ_symbol=f"SPY{expiration:%y%m%d}P00435000", side="sell", ratio=1),
    ])
    verdict = risk.evaluate(intent, _account(), open_positions=[])
    assert verdict.checks["defined_risk"] is False
    assert verdict.approved is False
