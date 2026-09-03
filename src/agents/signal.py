"""Deterministic signal computation (docs/STRATEGY.md). No LLM. Pure with
respect to the DB except for reading iv_history -- never writes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from src import config as config_module
from src.data import alpaca_data, iv as iv_module
from src.store import repo


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class SignalSet:
    symbol: str
    ts: datetime
    underlying_price: float
    atm_iv: float | None
    iv_rank: float | None
    iv_observations: int
    realized_vol_20d: float
    iv_rv_spread: float | None
    trend_score: float
    range_score: float
    degraded: bool


def compute(symbol: str) -> SignalSet:
    cfg = config_module.load()
    s = cfg.signal
    fast_days = s["trend_fast_days"]
    slow_days = s["trend_slow_days"]
    rv_days = s["realized_vol_days"]
    normalizer = s["trend_normalizer"]

    lookback_days = max(slow_days, rv_days) + 5
    bars = alpaca_data.fetch_daily_bars(symbol, days=lookback_days)
    closes = [b.close for b in bars]

    underlying_price = closes[-1] if closes else 0.0

    realized_vol_20d = _realized_vol(closes, rv_days)
    trend_score = _trend_score(closes, fast_days, slow_days, normalizer)
    range_score = _range_score(closes, slow_days)

    rank, observations = iv_module.iv_rank(symbol)
    window = repo.iv_window(symbol, s["iv_rank_lookback_days"])
    atm_iv = window[-1][1] if window else None

    iv_rv_spread = (atm_iv - realized_vol_20d) if atm_iv is not None else None
    degraded = rank is None

    return SignalSet(
        symbol=symbol,
        ts=datetime.now(timezone.utc),
        underlying_price=underlying_price,
        atm_iv=atm_iv,
        iv_rank=rank,
        iv_observations=observations,
        realized_vol_20d=realized_vol_20d,
        iv_rv_spread=iv_rv_spread,
        trend_score=trend_score,
        range_score=range_score,
        degraded=degraded,
    )


def _realized_vol(closes: list[float], days: int) -> float:
    if len(closes) < days + 1:
        return 0.0
    window = closes[-(days + 1):]
    log_returns = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))]
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / len(log_returns)
    return math.sqrt(variance) * math.sqrt(252)


def _sma(closes: list[float], days: int) -> float | None:
    if len(closes) < days:
        return None
    return sum(closes[-days:]) / days


def _trend_score(closes: list[float], fast_days: int, slow_days: int, normalizer: float) -> float:
    fast = _sma(closes, fast_days)
    slow = _sma(closes, slow_days)
    if fast is None or slow is None or slow == 0:
        return 0.0
    raw = (fast - slow) / slow
    return _clamp(raw / normalizer, -1.0, 1.0)


def _range_score(closes: list[float], slow_days: int) -> float:
    if len(closes) < slow_days + 1:
        return 0.0
    window = closes[-(slow_days + 1):]
    displacement = abs(window[-1] - window[0])
    path_length = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if path_length == 0:
        return 1.0
    return 1.0 - _clamp(displacement / path_length, 0.0, 1.0)


def passes_entry_gate(s: SignalSet) -> bool:
    """Cheap pre-filter to avoid LLM calls on quiet scans (docs/STRATEGY.md)."""
    cfg = config_module.load()
    min_iv_rank = cfg.signal["min_iv_rank_for_credit"]

    if s.atm_iv is None:
        return False
    if s.iv_rank is None and s.iv_rv_spread is None:
        return False
    if s.iv_rank is not None and s.iv_rank < min_iv_rank:
        return False
    if not s.underlying_price > 0:
        return False
    return True
