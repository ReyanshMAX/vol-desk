from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from src.agents.regime import RegimeLabel, RegimeVerdict
from src.agents.signal import SignalSet
from src.agents.strategy import StructureType, validate_response
from src.data.alpaca_data import OptionSnapshot


EXP = date.today() + timedelta(days=10)


@dataclass
class FakeLeg:
    occ_symbol: str
    side: str


@dataclass
class FakeResponse:
    decision: str
    structure: StructureType | None
    legs: list
    rationale: str = "x" * 30


def _snap(symbol, right, strike, bid, ask, delta, oi=200) -> OptionSnapshot:
    mid = (bid + ask) / 2
    return OptionSnapshot(
        occ_symbol=symbol, underlying="SPY", expiration=EXP, strike=strike,
        right=right, bid=bid, ask=ask, mid=mid, delta=delta,
        implied_volatility=0.20, open_interest=oi,
    )


def _put_credit_chain():
    # net credit 1.10 on a 5-wide spread = 0.22 of width (>= MIN_CREDIT_TO_WIDTH 0.20);
    # both legs quoted tight enough to pass MAX_LEG_SPREAD_PCT (0.15)
    short = _snap(f"SPY{EXP:%y%m%d}P00440000", "P", 440.0, 1.25, 1.35, -0.16)
    long = _snap(f"SPY{EXP:%y%m%d}P00435000", "P", 435.0, 0.19, 0.21, -0.06)
    return {short.occ_symbol: short, long.occ_symbol: long}, short, long


def _signal_and_regime():
    s = SignalSet(symbol="SPY", ts=None, underlying_price=450.0, atm_iv=0.20,
                  iv_rank=0.5, iv_observations=30, realized_vol_20d=0.15,
                  iv_rv_spread=0.05, trend_score=0.5, range_score=0.2, degraded=False)
    r = RegimeVerdict(symbol="SPY", label=RegimeLabel.TREND_UP_HIGH_IV,
                       mechanical_label=RegimeLabel.TREND_UP_HIGH_IV, deviated=False,
                       rationale="x" * 30, confidence=0.8, model="test", ts=None)
    return s, r


def test_valid_put_credit_spread_accepted(db_conn):
    chain, short, long = _put_credit_chain()
    resp = FakeResponse("trade", StructureType.PUT_CREDIT_SPREAD,
                         [FakeLeg(short.occ_symbol, "sell"), FakeLeg(long.occ_symbol, "buy")])
    s, r = _signal_and_regime()
    intent = validate_response(resp, chain, [StructureType.PUT_CREDIT_SPREAD], "SPY", s, r)
    assert intent is not None
    assert intent.structure == StructureType.PUT_CREDIT_SPREAD
    assert len(intent.legs) == 2
    assert intent.net_credit == pytest.approx(1.30 - 0.20, abs=1e-6)
    assert intent.max_loss_per_contract == pytest.approx((5.0 * 100) - (1.10 * 100), abs=0.5)


def test_decline_returns_none(db_conn):
    chain, short, long = _put_credit_chain()
    resp = FakeResponse("decline", None, [])
    s, r = _signal_and_regime()
    assert validate_response(resp, chain, [StructureType.PUT_CREDIT_SPREAD], "SPY", s, r) is None


def test_ineligible_structure_rejected(db_conn):
    chain, short, long = _put_credit_chain()
    resp = FakeResponse("trade", StructureType.CALL_CREDIT_SPREAD,
                         [FakeLeg(short.occ_symbol, "sell"), FakeLeg(long.occ_symbol, "buy")])
    s, r = _signal_and_regime()
    # only put_credit_spread is eligible in this regime
    assert validate_response(resp, chain, [StructureType.PUT_CREDIT_SPREAD], "SPY", s, r) is None


def test_leg_absent_from_chain_rejected(db_conn):
    chain, short, long = _put_credit_chain()
    resp = FakeResponse("trade", StructureType.PUT_CREDIT_SPREAD,
                         [FakeLeg("SPY_NOT_IN_CHAIN", "sell"), FakeLeg(long.occ_symbol, "buy")])
    s, r = _signal_and_regime()
    assert validate_response(resp, chain, [StructureType.PUT_CREDIT_SPREAD], "SPY", s, r) is None


def test_short_delta_outside_band_rejected(db_conn):
    # short leg delta -0.05 is well outside the 0.12-0.20 band
    short = _snap(f"SPY{EXP:%y%m%d}P00440000", "P", 440.0, 1.10, 1.20, -0.05)
    long = _snap(f"SPY{EXP:%y%m%d}P00435000", "P", 435.0, 0.20, 0.30, -0.02)
    chain = {short.occ_symbol: short, long.occ_symbol: long}
    resp = FakeResponse("trade", StructureType.PUT_CREDIT_SPREAD,
                         [FakeLeg(short.occ_symbol, "sell"), FakeLeg(long.occ_symbol, "buy")])
    s, r = _signal_and_regime()
    assert validate_response(resp, chain, [StructureType.PUT_CREDIT_SPREAD], "SPY", s, r) is None


def test_illiquid_leg_rejected_on_open_interest(db_conn):
    short = _snap(f"SPY{EXP:%y%m%d}P00440000", "P", 440.0, 1.10, 1.20, -0.16, oi=10)
    long = _snap(f"SPY{EXP:%y%m%d}P00435000", "P", 435.0, 0.20, 0.30, -0.06, oi=200)
    chain = {short.occ_symbol: short, long.occ_symbol: long}
    resp = FakeResponse("trade", StructureType.PUT_CREDIT_SPREAD,
                         [FakeLeg(short.occ_symbol, "sell"), FakeLeg(long.occ_symbol, "buy")])
    s, r = _signal_and_regime()
    assert validate_response(resp, chain, [StructureType.PUT_CREDIT_SPREAD], "SPY", s, r) is None


def test_credit_below_min_credit_to_width_rejected(db_conn):
    # 0.20 credit on a 5-wide spread is 0.04 of width, below MIN_CREDIT_TO_WIDTH 0.20
    short = _snap(f"SPY{EXP:%y%m%d}P00440000", "P", 440.0, 0.15, 0.25, -0.16)
    long = _snap(f"SPY{EXP:%y%m%d}P00435000", "P", 435.0, 0.10, 0.20, -0.06)
    chain = {short.occ_symbol: short, long.occ_symbol: long}
    resp = FakeResponse("trade", StructureType.PUT_CREDIT_SPREAD,
                         [FakeLeg(short.occ_symbol, "sell"), FakeLeg(long.occ_symbol, "buy")])
    s, r = _signal_and_regime()
    assert validate_response(resp, chain, [StructureType.PUT_CREDIT_SPREAD], "SPY", s, r) is None


def test_iron_condor_requires_all_four_legs(db_conn):
    put_short = _snap(f"SPY{EXP:%y%m%d}P00440000", "P", 440.0, 1.25, 1.35, -0.16)
    put_long = _snap(f"SPY{EXP:%y%m%d}P00435000", "P", 435.0, 0.19, 0.21, -0.06)
    call_short = _snap(f"SPY{EXP:%y%m%d}C00460000", "C", 460.0, 1.25, 1.35, 0.16)
    call_long = _snap(f"SPY{EXP:%y%m%d}C00465000", "C", 465.0, 0.19, 0.21, 0.06)
    chain = {s.occ_symbol: s for s in (put_short, put_long, call_short, call_long)}
    resp = FakeResponse("trade", StructureType.IRON_CONDOR, [
        FakeLeg(put_short.occ_symbol, "sell"), FakeLeg(put_long.occ_symbol, "buy"),
        FakeLeg(call_short.occ_symbol, "sell"), FakeLeg(call_long.occ_symbol, "buy"),
    ])
    s, r = _signal_and_regime()
    intent = validate_response(resp, chain, [StructureType.IRON_CONDOR], "SPY", s, r)
    assert intent is not None
    assert len(intent.legs) == 4


def test_iron_condor_missing_a_leg_rejected(db_conn):
    put_short = _snap(f"SPY{EXP:%y%m%d}P00440000", "P", 440.0, 1.10, 1.20, -0.16)
    put_long = _snap(f"SPY{EXP:%y%m%d}P00435000", "P", 435.0, 0.20, 0.30, -0.06)
    call_short = _snap(f"SPY{EXP:%y%m%d}C00460000", "C", 460.0, 1.05, 1.15, 0.16)
    chain = {s.occ_symbol: s for s in (put_short, put_long, call_short)}
    resp = FakeResponse("trade", StructureType.IRON_CONDOR, [
        FakeLeg(put_short.occ_symbol, "sell"), FakeLeg(put_long.occ_symbol, "buy"),
        FakeLeg(call_short.occ_symbol, "sell"),
    ])
    s, r = _signal_and_regime()
    assert validate_response(resp, chain, [StructureType.IRON_CONDOR], "SPY", s, r) is None
