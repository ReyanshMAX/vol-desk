from datetime import date, timedelta

import pytest

from src.agents.regime import RegimeLabel
from src.agents.strategy import Leg, OrderIntent, StructureType
from src.data.alpaca_data import OptionSnapshot
from src.execution.orders import _mark_to_market, _position_key, build_position_row
from src.store.repo import Leg as PositionLeg

EXP = date.today() + timedelta(days=10)


def _intent() -> OrderIntent:
    return OrderIntent(
        symbol="SPY", structure=StructureType.PUT_CREDIT_SPREAD,
        legs=[
            Leg(occ_symbol=f"SPY{EXP:%y%m%d}P00440000", side="sell", ratio=1),
            Leg(occ_symbol=f"SPY{EXP:%y%m%d}P00435000", side="buy", ratio=1),
        ],
        expiration=EXP, net_credit=1.10, max_loss_per_contract=390.0,
        short_delta=-0.16, regime_label=RegimeLabel.TREND_UP_HIGH_IV,
        rationale="test",
    )


def test_position_key_is_order_independent():
    a = _position_key(["SPY260101P00100000", "SPY260101P00095000"])
    b = _position_key(["SPY260101P00095000", "SPY260101P00100000"])
    assert a == b


def test_build_position_row_credit_structure_thresholds():
    intent = _intent()
    p = build_position_row(intent, qty=2, fill_price=1.10)
    assert p.entry_credit == 1.10
    assert p.qty == 2
    assert p.state == "open"
    assert p.take_profit_value == pytest.approx(1.10 * 0.5)
    assert p.stop_loss_value == pytest.approx(1.10 * 2.0)


def test_build_position_row_debit_structure_thresholds():
    intent = _intent()
    p = build_position_row(intent, qty=1, fill_price=-1.50)  # debit: negative net_credit
    assert p.entry_credit == -1.50
    entry_debit = 1.50
    assert p.take_profit_value == pytest.approx(entry_debit * 1.5)
    assert p.stop_loss_value == pytest.approx(entry_debit * 0.0)  # 1 - STOP_LOSS_MULT/2 = 1 - 1.0 = 0


def test_mark_to_market_credit_style():
    short_sym = f"SPY{EXP:%y%m%d}P00440000"
    long_sym = f"SPY{EXP:%y%m%d}P00435000"
    legs = [
        PositionLeg(occ_symbol=short_sym, side="sell", ratio=1, strike=440.0, right="P"),
        PositionLeg(occ_symbol=long_sym, side="buy", ratio=1, strike=435.0, right="P"),
    ]
    chain = {
        short_sym: OptionSnapshot(occ_symbol=short_sym, underlying="SPY", expiration=EXP,
                                   strike=440.0, right="P", bid=0.45, ask=0.55, mid=0.50,
                                   delta=-0.10, implied_volatility=0.18),
        long_sym: OptionSnapshot(occ_symbol=long_sym, underlying="SPY", expiration=EXP,
                                  strike=435.0, right="P", bid=0.08, ask=0.12, mid=0.10,
                                  delta=-0.04, implied_volatility=0.19),
    }
    value = _mark_to_market(legs, chain)
    assert value == pytest.approx(0.50 - 0.10)


def test_mark_to_market_missing_leg_returns_none():
    legs = [PositionLeg(occ_symbol="MISSING", side="sell", ratio=1, strike=440.0, right="P")]
    assert _mark_to_market(legs, {}) is None
