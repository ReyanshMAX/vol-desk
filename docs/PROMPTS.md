# PROMPTS.md — system prompts, response schemas, and validation

## Overview

Two LLM call sites: regime labeling and structure construction (D-004). Both use
`complete_json` from docs/INTEGRATIONS.md. The prompt text below is literal — use
it verbatim, not as a description of what to write.

## Non-goals

- No conversation history. Every call is single-shot and stateless.
- No tool use or function calling inside the LLM calls. The model returns JSON; Python acts on it.
- No chain-of-thought scratchpads beyond the `rationale` field.
- No prompt-level risk instructions. Risk is enforced deterministically in code and the prompts must not imply the model is responsible for it.

## Regime agent

Tier `fast`. Called by `regime.classify`, cached `REGIME_TTL_MINUTES` (30).

### System prompt (verbatim)

```
You classify the volatility and trend regime of a single exchange-traded fund
from precomputed quantitative features.

You will be given the feature values, the mechanical rules that a deterministic
classifier applies to those features, and the label that classifier produced.

Your job is to return a final label. In most cases you should agree with the
mechanical label. Deviate only when the features show something the thresholds
miss — for example a feature sitting fractionally on the wrong side of a
boundary while every other feature points the other way, or a combination that
the rules score as trending while the price action is clearly two-sided.

You must choose exactly one label from this set:

RANGE_HIGH_IV      two-sided price action, options premium rich
RANGE_LOW_IV       two-sided price action, options premium cheap
TREND_UP_HIGH_IV   directional up, premium rich
TREND_UP_LOW_IV    directional up, premium cheap
TREND_DOWN_HIGH_IV directional down, premium rich
TREND_DOWN_LOW_IV  directional down, premium cheap
STRESS             disorderly or extreme volatility; no new risk should be taken

Choose STRESS whenever volatility looks disorderly rather than merely elevated,
even if the mechanical rules did not. STRESS is the safe answer under
uncertainty because it opens no new positions.

If iv_rank is null the implied-volatility history is insufficient and you are
working from realized volatility alone. Say so in your rationale and prefer the
mechanical label unless the evidence is strong.

Return only a JSON object. No prose, no markdown fences.
```

### User message template

```python
REGIME_USER_TEMPLATE = """\
symbol: {symbol}
underlying_price: {underlying_price:.2f}

features:
  atm_iv:           {atm_iv}
  iv_rank:          {iv_rank}          # percentile 0-1 over {lookback} days, null if insufficient history
  iv_observations:  {iv_observations}
  realized_vol_20d: {realized_vol_20d:.4f}
  iv_rv_spread:     {iv_rv_spread}
  trend_score:      {trend_score:.3f}  # -1 down to +1 up
  range_score:      {range_score:.3f}  # 0 trending to 1 range-bound
  degraded:         {degraded}

mechanical rules applied:
  iv_rank >= 0.90 or realized_vol_20d >= 0.45  -> STRESS
  range_score >= 0.55                          -> RANGE_*
  else trend_score > 0                         -> TREND_UP_*
  else                                         -> TREND_DOWN_*
  high_iv suffix when iv_rank >= 0.50

mechanical label: {mechanical_label}
"""
```

### Response schema

```python
class RegimeResponse(BaseModel):
    label: RegimeLabel                    # must be one of the seven enum values
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=20, max_length=600)
```

Validation rules beyond the schema:

- `label` outside the enum fails validation and triggers the single retry.
- If `label != mechanical_label`, `rationale` must be at least 60 characters. A deviation without a stated reason is rejected.
- On final failure, `classify` falls back to `mechanical_label` with `confidence=0.0`, `deviated=False`, and `rationale="llm_unavailable_mechanical_fallback"`. This is the one permitted fallback in the system, and it is permitted only because the mechanical label is a real deterministic computation rather than an invented default.

## Strategy agent

Tier `reasoning`. Called by `strategy.construct` only when the entry gate passes
and the regime unlocks at least one structure.

### System prompt (verbatim)

```
You construct one options position for a single exchange-traded fund.

You are given the current regime, quantitative features, the list of structures
that the regime permits, and a filtered option chain with strikes, deltas, bids,
asks, and implied volatilities.

Your task:
1. Choose one structure from the permitted list, or decline.
2. Choose the exact contracts for each leg from the provided chain.
3. State briefly why this structure and these strikes fit the regime.

Selection rules you must follow:
- Short legs of credit structures target 0.16 absolute delta, and must be within
  0.12 to 0.20.
- Long legs of debit structures target 0.45 absolute delta, within 0.35 to 0.55.
- Spread width must be between 1.0 and 5.0 points.
- Credit structures must collect at least 0.20 of the spread width in credit.
- Debit structures must cost no more than 0.45 of the spread width.
- All legs must share one expiration, chosen from the chain provided.
- An iron condor requires all four legs to satisfy the rules. If only one side
  qualifies, do not substitute a single vertical — decline instead.

Declining is a normal and frequent outcome. Decline whenever no combination in
the chain satisfies the rules, when quoted spreads are too wide to trade, or
when the chain is too thin. Never stretch a rule to produce a trade.

The quotes you are given are delayed by approximately fifteen minutes. Treat
deltas and prices as approximate.

Position size is not your decision and is determined elsewhere. Do not include
quantity in your response.

Return only a JSON object. No prose, no markdown fences.
```

### User message template

```python
STRATEGY_USER_TEMPLATE = """\
symbol: {symbol}
underlying_price: {underlying_price:.2f}
regime: {regime_label}
regime_rationale: {regime_rationale}
permitted_structures: {eligible}

features:
  iv_rank:          {iv_rank}
  realized_vol_20d: {realized_vol_20d:.4f}
  iv_rv_spread:     {iv_rv_spread}
  degraded:         {degraded}

expiration: {expiration} ({dte} DTE)

chain:
{chain_table}
"""

# chain_table is a fixed-width text table, one row per contract:
# occ_symbol            right  strike   delta    bid     ask     iv
# SPY260911P00640000    P      640.0    -0.152   1.12    1.19    0.181
```

Only contracts inside `STRIKE_RANGE_PCT` of spot for the selected expiration are
included. A full chain would dominate the context window and degrade selection.

### Response schema

```python
class StrategyLeg(BaseModel):
    occ_symbol: str
    side: Literal["buy", "sell"]

class StrategyResponse(BaseModel):
    decision: Literal["trade", "decline"]
    structure: StructureType | None = None
    legs: list[StrategyLeg] = []
    expiration: date | None = None
    rationale: str = Field(min_length=20, max_length=800)
```

### Post-validation (deterministic, in `strategy.construct`)

Schema validity is not sufficient. Every check below runs on the parsed response,
and any failure discards it and returns `None`. **No retry** — a structurally
invalid selection means the model misread the chain, and asking again wastes
tokens for a low success rate.

```python
def validate_response(r: StrategyResponse, chain, eligible) -> OrderIntent | None:
    if r.decision == "decline":                       return None
    if r.structure not in eligible:                   return None
    if len(r.legs) != EXPECTED_LEGS[r.structure]:     return None   # 2 or 4
    if any(leg.occ_symbol not in chain for leg in r.legs):  return None
    if len({parse_expiry(l.occ_symbol) for l in r.legs}) != 1:  return None
    if not _sides_balanced(r.legs, r.structure):      return None
    if not _strikes_ordered(r.legs, r.structure):     return None
    # recompute from the chain, never from the model
    net = _net_price_from_chain(r.legs, chain)
    width = _width(r.legs)
    if not (WING_WIDTH_MIN <= width <= WING_WIDTH_MAX):   return None
    if _is_credit(r.structure) and net / width < MIN_CREDIT_TO_WIDTH:  return None
    if not _is_credit(r.structure) and net / width > MAX_DEBIT_TO_WIDTH: return None
    if not _short_delta_in_band(r.legs, chain, r.structure):  return None
    if not _legs_liquid(r.legs, chain):               return None
    return OrderIntent(...)
```

Prices, widths, deltas, and max loss are always recomputed from the chain data.
The model selects contracts; it never supplies numbers the system relies on.

## Logging

Every call at both sites writes one `decision_log` row before the function
returns, with `inputs_json` holding the rendered user message fields, `output_json`
holding the raw model text, `rationale` the parsed rationale, `model` the tier's
resolved model ID, `latency_ms` from `LLMResponse`, and `accepted` reflecting
whether the response survived validation.

## Notes

- Temperature 0.2 for both sites. Higher values increase invalid-selection rates; 0.0 has produced degenerate repetition on some open models.
- `max_tokens` 400 for regime, 800 for strategy. The strategy rationale and four legs need the headroom.
- If the strategy agent declines repeatedly across every symbol for many consecutive scans, that is a parameter problem — likely `MIN_CREDIT_TO_WIDTH` against short-DTE chains — not a prompt problem. Log it and raise it; do not loosen thresholds in the prompt.
- The regime prompt names the mechanical rules explicitly so the model can reason about where a feature sits relative to a boundary. Removing them turns the deviation latitude into guessing.
