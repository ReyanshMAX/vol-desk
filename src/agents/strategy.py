"""Structure construction (D-006/D-007). LLM call site 2 of 2. The model
selects contracts; every number it relies on is recomputed from the chain,
never trusted from the model's own arithmetic (docs/PROMPTS.md).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal

from src import config as config_module
from src.agents.regime import RegimeLabel, RegimeVerdict
from src.agents.signal import SignalSet
from src.data.alpaca_data import OptionSnapshot, fetch_open_interest
from src.llm import client as llm_client
from src.llm import prompts
from src.store import repo

logger = logging.getLogger("vol_desk.strategy")


class StructureType(StrEnum):
    PUT_CREDIT_SPREAD = "put_credit_spread"
    CALL_CREDIT_SPREAD = "call_credit_spread"
    IRON_CONDOR = "iron_condor"
    PUT_DEBIT_SPREAD = "put_debit_spread"
    CALL_DEBIT_SPREAD = "call_debit_spread"


STRUCTURE_ELIGIBILITY: dict[RegimeLabel, list[StructureType]] = {
    RegimeLabel.RANGE_HIGH_IV: [StructureType.IRON_CONDOR, StructureType.PUT_CREDIT_SPREAD, StructureType.CALL_CREDIT_SPREAD],
    RegimeLabel.RANGE_LOW_IV: [],
    RegimeLabel.TREND_UP_HIGH_IV: [StructureType.PUT_CREDIT_SPREAD],
    RegimeLabel.TREND_DOWN_HIGH_IV: [StructureType.CALL_CREDIT_SPREAD],
    RegimeLabel.TREND_UP_LOW_IV: [StructureType.CALL_DEBIT_SPREAD],
    RegimeLabel.TREND_DOWN_LOW_IV: [StructureType.PUT_DEBIT_SPREAD],
    RegimeLabel.STRESS: [],
}

EXPECTED_LEGS: dict[StructureType, int] = {
    StructureType.PUT_CREDIT_SPREAD: 2,
    StructureType.CALL_CREDIT_SPREAD: 2,
    StructureType.IRON_CONDOR: 4,
    StructureType.PUT_DEBIT_SPREAD: 2,
    StructureType.CALL_DEBIT_SPREAD: 2,
}

_CREDIT_STRUCTURES = {StructureType.PUT_CREDIT_SPREAD, StructureType.CALL_CREDIT_SPREAD, StructureType.IRON_CONDOR}


@dataclass(frozen=True)
class Leg:
    occ_symbol: str
    side: Literal["buy", "sell"]
    ratio: int


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    structure: StructureType
    legs: list[Leg]
    expiration: date
    net_credit: float          # per contract, positive = credit received (negative for debit)
    max_loss_per_contract: float
    short_delta: float
    regime_label: RegimeLabel
    rationale: str


def _is_credit(structure: StructureType) -> bool:
    return structure in _CREDIT_STRUCTURES


def _select_expiration(chain: list[OptionSnapshot], dte_min: int, dte_max: int, target_dte: int) -> date | None:
    today = date.today()
    candidates = sorted({c.expiration for c in chain
                          if dte_min <= (c.expiration - today).days <= dte_max})
    if not candidates:
        return None
    return min(candidates, key=lambda e: abs((e - today).days - target_dte))


def construct(symbol: str, signals: SignalSet, regime: RegimeVerdict,
              eligible: list[StructureType], chain: list[OptionSnapshot]) -> OrderIntent | None:
    if not eligible:
        return None

    cfg = config_module.load()
    st = cfg.strategy
    dte_min, dte_max = st["dte_min"], st["dte_max"]
    target_dte = cfg.data["atm_iv_target_dte"]

    expiration = _select_expiration(chain, dte_min, dte_max, target_dte)
    if expiration is None:
        return None

    expiry_chain = [c for c in chain if c.expiration == expiration]
    if not expiry_chain:
        return None
    dte = (expiration - date.today()).days

    rows = [
        {"occ_symbol": c.occ_symbol, "right": c.right, "strike": c.strike,
         "delta": c.delta, "bid": c.bid or 0.0, "ask": c.ask or 0.0, "iv": c.implied_volatility}
        for c in sorted(expiry_chain, key=lambda c: (c.right, c.strike))
    ]
    chain_table = prompts.render_chain_table(rows)

    system = prompts.STRATEGY_SYSTEM_PROMPT
    user = prompts.STRATEGY_USER_TEMPLATE.format(
        symbol=symbol, underlying_price=signals.underlying_price,
        regime_label=regime.label.value, regime_rationale=regime.rationale,
        eligible=[e.value for e in eligible],
        iv_rank=signals.iv_rank, realized_vol_20d=signals.realized_vol_20d,
        iv_rv_spread=signals.iv_rv_spread, degraded=signals.degraded,
        expiration=expiration.isoformat(), dte=dte, chain_table=chain_table,
    )

    from src.llm.schemas import StrategyResponse  # local import, avoids a cycle

    # complete_json_meta's single-retry contract (docs/INTEGRATIONS.md)
    # applies here same as regime -- it covers malformed/schema-invalid
    # JSON. PROMPTS.md's "No retry" rule is specifically about the
    # deterministic post-validation below (validate_response): once the
    # response parses, a structurally invalid *selection* is discarded
    # outright, never retried.
    result = llm_client.complete_json_meta(
        system, user, StrategyResponse, tier="reasoning",
        max_tokens=cfg.llm_strategy_max_tokens, temperature=cfg.llm_temperature,
        timeout_s=cfg.llm_timeout_s, max_retries=cfg.llm_max_retries,
    )

    intent: OrderIntent | None = None
    if result.parsed is not None and result.parsed.decision == "trade":
        chain_by_symbol = {c.occ_symbol: c for c in expiry_chain}
        intent = validate_response(result.parsed, chain_by_symbol, eligible, symbol, signals, regime)

    repo.log_decision(
        agent="strategy", action="construct",
        inputs={"symbol": symbol, "eligible": [e.value for e in eligible],
                "expiration": expiration.isoformat(), "regime": regime.label.value},
        output={"raw": result.raw_text, "accepted_intent": intent is not None},
        underlying=symbol,
        rationale=(result.parsed.rationale if result.parsed else None),
        model=result.model, latency_ms=result.latency_ms,
        accepted=intent is not None,
        veto_reason=None if intent is not None else "decline_or_validation_failed",
    )

    return intent


def validate_response(r, chain_by_symbol: dict[str, OptionSnapshot],
                       eligible: list[StructureType], symbol: str,
                       signals: SignalSet, regime: RegimeVerdict) -> OrderIntent | None:
    """Deterministic post-validation (docs/PROMPTS.md). No retry -- a
    structurally invalid selection means the model misread the chain."""
    cfg = config_module.load()
    st = cfg.strategy

    if r.decision == "decline":
        return None
    if r.structure is None or r.structure not in eligible:
        return None
    expected = EXPECTED_LEGS[r.structure]
    if len(r.legs) != expected:
        return None
    if any(leg.occ_symbol not in chain_by_symbol for leg in r.legs):
        return None

    contracts = [chain_by_symbol[leg.occ_symbol] for leg in r.legs]
    if len({c.expiration for c in contracts}) != 1:
        return None

    if not _sides_balanced(r.legs, contracts, r.structure):
        return None
    if not _strikes_ordered(contracts, r.structure):
        return None
    if not _sides_match_strikes(r.legs, contracts, r.structure):
        return None

    net = _net_price_from_chain(r.legs, contracts)
    width = _width(contracts, r.structure)
    if width is None or not (st["wing_width_min"] <= width <= st["wing_width_max"]):
        return None

    is_credit = _is_credit(r.structure)
    if is_credit:
        if width == 0 or net / width < st["min_credit_to_width"]:
            return None
    else:
        debit = -net
        if width == 0 or debit / width > st["max_debit_to_width"]:
            return None

    if not _short_delta_in_band(r.legs, contracts, r.structure, st):
        return None
    if not _legs_liquid(contracts, st):
        return None

    max_loss = _max_loss(net, width, is_credit)
    short_delta = _extract_short_delta(r.legs, contracts, r.structure)

    return OrderIntent(
        symbol=symbol,
        structure=r.structure,
        legs=[Leg(occ_symbol=l.occ_symbol, side=l.side, ratio=1) for l in r.legs],
        expiration=contracts[0].expiration,
        net_credit=net,
        max_loss_per_contract=max_loss,
        short_delta=short_delta,
        regime_label=regime.label,
        rationale=r.rationale,
    )


def _sides_balanced(legs, contracts: list[OptionSnapshot], structure: StructureType) -> bool:
    by_symbol = {leg.occ_symbol: leg for leg in legs}
    if structure == StructureType.IRON_CONDOR:
        puts = [c for c in contracts if c.right == "P"]
        calls = [c for c in contracts if c.right == "C"]
        if len(puts) != 2 or len(calls) != 2:
            return False
        return (_pair_is_credit_vertical(puts, by_symbol, "P")
                and _pair_is_credit_vertical(calls, by_symbol, "C"))
    if structure in (StructureType.PUT_CREDIT_SPREAD, StructureType.PUT_DEBIT_SPREAD):
        if any(c.right != "P" for c in contracts):
            return False
    if structure in (StructureType.CALL_CREDIT_SPREAD, StructureType.CALL_DEBIT_SPREAD):
        if any(c.right != "C" for c in contracts):
            return False
    sides = {by_symbol[c.occ_symbol].side for c in contracts}
    return sides == {"buy", "sell"}


def _pair_is_credit_vertical(pair: list[OptionSnapshot], by_symbol, right: str) -> bool:
    sides = {by_symbol[c.occ_symbol].side for c in pair}
    return sides == {"buy", "sell"}


def _strikes_ordered(contracts: list[OptionSnapshot], structure: StructureType) -> bool:
    def strikes_for(right: str) -> list[OptionSnapshot]:
        return sorted((c for c in contracts if c.right == right), key=lambda c: c.strike)

    if structure in (StructureType.PUT_CREDIT_SPREAD, StructureType.PUT_DEBIT_SPREAD):
        puts = strikes_for("P")
        return len(puts) == 2 and puts[0].strike < puts[1].strike
    if structure in (StructureType.CALL_CREDIT_SPREAD, StructureType.CALL_DEBIT_SPREAD):
        calls = strikes_for("C")
        return len(calls) == 2 and calls[0].strike < calls[1].strike
    if structure == StructureType.IRON_CONDOR:
        puts, calls = strikes_for("P"), strikes_for("C")
        if len(puts) != 2 or len(calls) != 2:
            return False
        return puts[0].strike < puts[1].strike <= calls[0].strike < calls[1].strike
    return False


def _sides_match_strikes(legs, contracts: list[OptionSnapshot], structure: StructureType) -> bool:
    """_sides_balanced only checks that a buy and a sell are present;
    _strikes_ordered only checks two distinct strikes exist. Neither catches
    a model naming the correct structure but swapping which strike is
    bought vs. sold (e.g. selling the LOWER put and buying the HIGHER put
    under 'put_credit_spread', which docs/STRATEGY.md's own definition
    rules out). Reversed sides on a nominal credit structure force a
    negative net that MIN_CREDIT_TO_WIDTH already rejects, and on a nominal
    debit structure force a negative max_loss that risk.evaluate's
    'sizeable' check already vetoes -- but both are safety nets, not a
    correctness check of the structure definition itself. Enforce it
    directly here instead of relying on those side effects."""
    by_symbol = {leg.occ_symbol: leg for leg in legs}

    def strikes_for(right: str) -> list[OptionSnapshot]:
        return sorted((c for c in contracts if c.right == right), key=lambda c: c.strike)

    def vertical_ok(pair: list[OptionSnapshot], low_side: str, high_side: str) -> bool:
        if len(pair) != 2:
            return False
        low, high = pair
        return (by_symbol[low.occ_symbol].side == low_side
                and by_symbol[high.occ_symbol].side == high_side)

    if structure == StructureType.PUT_CREDIT_SPREAD:
        # sell higher-strike put, buy lower-strike put
        return vertical_ok(strikes_for("P"), low_side="buy", high_side="sell")
    if structure == StructureType.CALL_CREDIT_SPREAD:
        # sell lower-strike call, buy higher-strike call
        return vertical_ok(strikes_for("C"), low_side="sell", high_side="buy")
    if structure == StructureType.PUT_DEBIT_SPREAD:
        # buy higher-strike put, sell lower-strike put
        return vertical_ok(strikes_for("P"), low_side="sell", high_side="buy")
    if structure == StructureType.CALL_DEBIT_SPREAD:
        # buy lower-strike call, sell higher-strike call
        return vertical_ok(strikes_for("C"), low_side="buy", high_side="sell")
    if structure == StructureType.IRON_CONDOR:
        return (vertical_ok(strikes_for("P"), low_side="buy", high_side="sell")
                and vertical_ok(strikes_for("C"), low_side="sell", high_side="buy"))
    return False


def _mid(c: OptionSnapshot) -> float:
    if c.mid is not None:
        return c.mid
    if c.bid is not None and c.ask is not None:
        return (c.bid + c.ask) / 2
    return 0.0


def _net_price_from_chain(legs, contracts: list[OptionSnapshot]) -> float:
    by_symbol = {c.occ_symbol: c for c in contracts}
    total = 0.0
    for leg in legs:
        c = by_symbol[leg.occ_symbol]
        mid = _mid(c)
        total += mid if leg.side == "sell" else -mid
    return total


def _width(contracts: list[OptionSnapshot], structure: StructureType) -> float | None:
    def side_width(right: str) -> float | None:
        strikes = sorted(c.strike for c in contracts if c.right == right)
        if len(strikes) != 2:
            return None
        return abs(strikes[1] - strikes[0])

    if structure == StructureType.IRON_CONDOR:
        pw, cw = side_width("P"), side_width("C")
        if pw is None or cw is None:
            return None
        return max(pw, cw)  # only one side can lose (docs/STRATEGY.md)
    right = "P" if structure in (StructureType.PUT_CREDIT_SPREAD, StructureType.PUT_DEBIT_SPREAD) else "C"
    return side_width(right)


def _max_loss(net: float, width: float, is_credit: bool) -> float:
    if is_credit:
        return (width * 100) - (net * 100)
    return (-net) * 100


def _short_delta_in_band(legs, contracts: list[OptionSnapshot], structure: StructureType, st: dict) -> bool:
    by_symbol = {c.occ_symbol: c for c in contracts}
    lo_s, hi_s = st["short_delta_band"]
    lo_l, hi_l = st["debit_long_delta_band"]
    is_credit = _is_credit(structure)
    ok = True
    for leg in legs:
        c = by_symbol[leg.occ_symbol]
        if c.delta is None:
            return False
        adelta = abs(c.delta)
        if is_credit and leg.side == "sell":
            ok &= lo_s <= adelta <= hi_s
        if not is_credit and leg.side == "buy":
            ok &= lo_l <= adelta <= hi_l
    return ok


def _extract_short_delta(legs, contracts: list[OptionSnapshot], structure: StructureType) -> float:
    by_symbol = {c.occ_symbol: c for c in contracts}
    is_credit = _is_credit(structure)
    target_side = "sell" if is_credit else "buy"
    for leg in legs:
        if leg.side == target_side:
            c = by_symbol[leg.occ_symbol]
            if c.delta is not None:
                return c.delta
    return 0.0


def _legs_liquid(contracts: list[OptionSnapshot], st: dict) -> bool:
    max_spread_pct = st["max_leg_spread_pct"]
    min_oi = st["min_leg_open_interest"]
    for c in contracts:
        if c.bid is None or c.ask is None or c.mid is None or c.mid <= 0:
            return False
        spread_pct = (c.ask - c.bid) / c.mid
        if spread_pct > max_spread_pct:
            return False
        oi = c.open_interest if c.open_interest is not None else fetch_open_interest(c.occ_symbol)
        if oi is None or oi < min_oi:
            return False
    return True
