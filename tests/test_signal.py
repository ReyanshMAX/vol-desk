import math
from datetime import datetime, timezone

import pytest

from src.agents.signal import SignalSet, _range_score, _realized_vol, _trend_score, passes_entry_gate


def test_realized_vol_zero_for_flat_prices():
    closes = [100.0] * 25
    assert _realized_vol(closes, 20) == pytest.approx(0.0, abs=1e-9)


def test_realized_vol_positive_for_moving_prices():
    closes = [100.0 + (i % 3 - 1) * 2 for i in range(25)]
    vol = _realized_vol(closes, 20)
    assert vol > 0


def test_realized_vol_short_history_returns_zero():
    assert _realized_vol([100.0, 101.0], 20) == 0.0


def test_trend_score_up_trend_is_positive_and_clamped():
    closes = [100.0 + i * 0.5 for i in range(31)]  # steadily rising
    score = _trend_score(closes, fast_days=10, slow_days=30, normalizer=0.03)
    assert 0.0 < score <= 1.0


def test_trend_score_down_trend_is_negative():
    closes = [130.0 - i * 0.5 for i in range(31)]
    score = _trend_score(closes, fast_days=10, slow_days=30, normalizer=0.03)
    assert -1.0 <= score < 0.0


def test_range_score_pure_oscillation_is_high():
    # bounces between 99 and 101 every bar -- big path length, small net displacement
    closes = [100.0 + (1 if i % 2 == 0 else -1) for i in range(31)]
    score = _range_score(closes, slow_days=30)
    assert score > 0.8


def test_range_score_pure_trend_is_low():
    closes = [100.0 + i for i in range(31)]  # monotonic, no reversals
    score = _range_score(closes, slow_days=30)
    assert score == pytest.approx(0.0, abs=1e-9)


def _signal(**overrides) -> SignalSet:
    base = dict(
        symbol="SPY", ts=datetime.now(timezone.utc), underlying_price=450.0,
        atm_iv=0.20, iv_rank=0.5, iv_observations=30, realized_vol_20d=0.15,
        iv_rv_spread=0.05, trend_score=0.1, range_score=0.6, degraded=False,
    )
    base.update(overrides)
    return SignalSet(**base)


def test_passes_entry_gate_true_for_healthy_signal():
    assert passes_entry_gate(_signal()) is True


def test_passes_entry_gate_false_when_atm_iv_missing():
    assert passes_entry_gate(_signal(atm_iv=None)) is False


def test_passes_entry_gate_false_when_no_vol_read_at_all():
    assert passes_entry_gate(_signal(iv_rank=None, iv_rv_spread=None)) is False


def test_passes_entry_gate_false_when_iv_rank_below_threshold():
    assert passes_entry_gate(_signal(iv_rank=0.10)) is False


def test_passes_entry_gate_true_when_iv_rank_none_but_rv_spread_present():
    assert passes_entry_gate(_signal(iv_rank=None, iv_rv_spread=0.02)) is True


def test_passes_entry_gate_false_when_price_not_positive():
    assert passes_entry_gate(_signal(underlying_price=0.0)) is False
