from datetime import datetime, timezone

import pytest

from src.agents.regime import RegimeLabel, mechanical_label, _deviation_reason_ok
from src.agents.signal import SignalSet


def _signal(**overrides) -> SignalSet:
    base = dict(
        symbol="SPY", ts=datetime.now(timezone.utc), underlying_price=450.0,
        atm_iv=0.20, iv_rank=0.5, iv_observations=30, realized_vol_20d=0.15,
        iv_rv_spread=0.05, trend_score=0.1, range_score=0.6, degraded=False,
    )
    base.update(overrides)
    return SignalSet(**base)


def test_stress_from_high_iv_rank():
    s = _signal(iv_rank=0.95)
    assert mechanical_label(s) == RegimeLabel.STRESS


def test_stress_from_high_realized_vol():
    s = _signal(iv_rank=0.10, realized_vol_20d=0.50)
    assert mechanical_label(s) == RegimeLabel.STRESS


def test_range_high_iv():
    s = _signal(iv_rank=0.60, range_score=0.70, trend_score=0.0)
    assert mechanical_label(s) == RegimeLabel.RANGE_HIGH_IV


def test_range_low_iv():
    s = _signal(iv_rank=0.30, range_score=0.70, trend_score=0.0)
    assert mechanical_label(s) == RegimeLabel.RANGE_LOW_IV


def test_trend_up_high_iv():
    s = _signal(iv_rank=0.60, range_score=0.20, trend_score=0.5)
    assert mechanical_label(s) == RegimeLabel.TREND_UP_HIGH_IV


def test_trend_up_low_iv():
    s = _signal(iv_rank=0.30, range_score=0.20, trend_score=0.5)
    assert mechanical_label(s) == RegimeLabel.TREND_UP_LOW_IV


def test_trend_down_high_iv():
    s = _signal(iv_rank=0.60, range_score=0.20, trend_score=-0.5)
    assert mechanical_label(s) == RegimeLabel.TREND_DOWN_HIGH_IV


def test_trend_down_low_iv():
    s = _signal(iv_rank=0.30, range_score=0.20, trend_score=-0.5)
    assert mechanical_label(s) == RegimeLabel.TREND_DOWN_LOW_IV


def test_trend_down_when_iv_rank_none_defaults_to_low_iv_bucket():
    # iv_rank is None -> treated as 0.0 for the high_iv check
    s = _signal(iv_rank=None, range_score=0.20, trend_score=-0.5)
    assert mechanical_label(s) == RegimeLabel.TREND_DOWN_LOW_IV


class _Resp:
    def __init__(self, label, rationale):
        self.label = label
        self.rationale = rationale


def test_deviation_agreeing_with_mechanical_always_ok():
    resp = _Resp(RegimeLabel.RANGE_HIGH_IV, "short")
    assert _deviation_reason_ok(resp, RegimeLabel.RANGE_HIGH_IV) is True


def test_deviation_requires_60_char_rationale():
    short_rationale = "too short"
    resp = _Resp(RegimeLabel.STRESS, short_rationale)
    assert _deviation_reason_ok(resp, RegimeLabel.RANGE_HIGH_IV) is False


def test_deviation_with_long_rationale_ok():
    long_rationale = "x" * 61
    resp = _Resp(RegimeLabel.STRESS, long_rationale)
    assert _deviation_reason_ok(resp, RegimeLabel.RANGE_HIGH_IV) is True
