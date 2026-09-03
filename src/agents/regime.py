"""Regime labeling (D-006). LLM call site 1 of 2. Deterministic feature
computation already happened in signal.compute; this module runs the
mechanical rule and, when inference is available, lets the LLM confirm or
deviate with a stated reason.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from src import config as config_module
from src.agents.signal import SignalSet
from src.llm import client as llm_client
from src.llm import prompts
from src.store import repo

logger = logging.getLogger("vol_desk.regime")


class RegimeLabel(StrEnum):
    RANGE_HIGH_IV = "RANGE_HIGH_IV"
    RANGE_LOW_IV = "RANGE_LOW_IV"
    TREND_UP_HIGH_IV = "TREND_UP_HIGH_IV"
    TREND_UP_LOW_IV = "TREND_UP_LOW_IV"
    TREND_DOWN_HIGH_IV = "TREND_DOWN_HIGH_IV"
    TREND_DOWN_LOW_IV = "TREND_DOWN_LOW_IV"
    STRESS = "STRESS"


@dataclass(frozen=True)
class RegimeVerdict:
    symbol: str
    label: RegimeLabel
    mechanical_label: RegimeLabel
    deviated: bool
    rationale: str
    confidence: float
    model: str
    ts: datetime


_cache: dict[str, tuple[RegimeVerdict, float]] = {}


def mechanical_label(s: SignalSet) -> RegimeLabel:
    cfg = config_module.load()
    r = cfg.regime

    if s.iv_rank is not None and s.iv_rank >= r["stress_iv_rank"]:
        return RegimeLabel.STRESS
    if s.realized_vol_20d >= r["stress_realized_vol"]:
        return RegimeLabel.STRESS

    high_iv = (s.iv_rank if s.iv_rank is not None else 0.0) >= r["high_iv_rank"]
    if s.range_score >= r["range_threshold"]:
        return RegimeLabel.RANGE_HIGH_IV if high_iv else RegimeLabel.RANGE_LOW_IV
    if s.trend_score > 0:
        return RegimeLabel.TREND_UP_HIGH_IV if high_iv else RegimeLabel.TREND_UP_LOW_IV
    return RegimeLabel.TREND_DOWN_HIGH_IV if high_iv else RegimeLabel.TREND_DOWN_LOW_IV


def _fallback_verdict(symbol: str, mech: RegimeLabel) -> RegimeVerdict:
    return RegimeVerdict(
        symbol=symbol, label=mech, mechanical_label=mech, deviated=False,
        rationale="llm_unavailable_mechanical_fallback", confidence=0.0,
        model="mechanical", ts=datetime.now(timezone.utc),
    )


def classify(symbol: str, signals: SignalSet) -> RegimeVerdict:
    """Cached per symbol for regime.regime_ttl_minutes. Raises
    llm_client.InferenceUnavailable when Groq is unreachable or a tier is
    unresolved -- callers (the entry pipeline / regime_refresh job) decide
    whether to log the single supervisor-level occurrence and fall back to
    mechanical_label for the remaining symbols in that scan (D-010)."""
    cfg = config_module.load()
    ttl_s = cfg.regime["regime_ttl_minutes"] * 60

    cached = _cache.get(symbol)
    if cached is not None:
        verdict, expires_at = cached
        if time.monotonic() < expires_at:
            return verdict

    mech = mechanical_label(signals)

    from src.llm.schemas import RegimeResponse  # local import, avoids a cycle

    system = prompts.REGIME_SYSTEM_PROMPT
    user = prompts.REGIME_USER_TEMPLATE.format(
        symbol=signals.symbol,
        underlying_price=signals.underlying_price,
        atm_iv=signals.atm_iv,
        iv_rank=signals.iv_rank,
        lookback=cfg.signal["iv_rank_lookback_days"],
        iv_observations=signals.iv_observations,
        realized_vol_20d=signals.realized_vol_20d,
        iv_rv_spread=signals.iv_rv_spread,
        trend_score=signals.trend_score,
        range_score=signals.range_score,
        degraded=signals.degraded,
        mechanical_label=mech.value,
    )

    result = llm_client.complete_json_meta(
        system, user, RegimeResponse, tier="fast",
        max_tokens=cfg.llm_regime_max_tokens, temperature=cfg.llm_temperature,
        timeout_s=cfg.llm_timeout_s, max_retries=cfg.llm_max_retries,
    )

    verdict: RegimeVerdict
    if result.parsed is not None and _deviation_reason_ok(result.parsed, mech):
        parsed = result.parsed
        deviated = parsed.label != mech
        verdict = RegimeVerdict(
            symbol=symbol, label=parsed.label, mechanical_label=mech,
            deviated=deviated, rationale=parsed.rationale,
            confidence=parsed.confidence, model=result.model or "unknown",
            ts=datetime.now(timezone.utc),
        )
        accepted = True
    else:
        verdict = _fallback_verdict(symbol, mech)
        accepted = False

    repo.log_decision(
        agent="regime", action="classify",
        inputs={"symbol": symbol, "signals": signals.__dict__, "mechanical_label": mech.value},
        output={"raw": result.raw_text, "label": verdict.label.value if accepted else None},
        underlying=symbol, rationale=verdict.rationale, model=result.model,
        latency_ms=result.latency_ms, accepted=accepted,
        veto_reason=None if accepted else "validation_failed",
    )

    _cache[symbol] = (verdict, time.monotonic() + ttl_s)
    return verdict


def _deviation_reason_ok(parsed, mech: RegimeLabel) -> bool:
    """label != mechanical_label requires rationale >= 60 chars (docs/PROMPTS.md).
    A deviation without a stated reason is rejected."""
    if parsed.label == mech:
        return True
    return len(parsed.rationale) >= 60
