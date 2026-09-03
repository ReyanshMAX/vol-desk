# STRATEGY.md — regimes, structure menu, strike selection, and position management

## Overview

The strategy layer answers three questions in order: what regime is this symbol
in, which structures are eligible in that regime, and which exact contracts
express it. Regime gating is what keeps structure selection principled rather
than arbitrary — the strategy agent never chooses from the full menu, only from
the subset the regime unlocks.

## Non-goals

- No naked or undefined-risk positions of any kind (D-002).
- No calendar spreads, diagonals, ratio spreads, butterflies, or anything with unequal leg counts. Four structures only.
- No rolling. A position at `FORCE_CLOSE_DTE` is closed, not rolled to a later expiry. Rolling is deferred to v2.
- No earnings or event filtering. The universe is ETF-only so single-name earnings gaps do not apply.
- No intraday scalping, gamma scalping, or delta hedging of open positions.
- No position adjustment. A position's management plan is fixed at entry and only executed, never revised.

## Signal computation (deterministic)

All in `src/agents/signal.py`. No LLM.

```python
IV_RANK_LOOKBACK_DAYS   = 60      # trailing window for the percentile
MIN_IV_OBSERVATIONS     = 20      # below this, iv_rank is None and degraded=True
TREND_FAST_DAYS         = 10
TREND_SLOW_DAYS         = 30
REALIZED_VOL_DAYS       = 20
```

**`realized_vol_20d`** — annualized standard deviation of the last 20 daily
close-to-close log returns, times `sqrt(252)`.

**`iv_rank`** — the percentile of the current `atm_iv` within all `iv_history`
rows for that symbol inside `IV_RANK_LOOKBACK_DAYS`, regardless of `source`
(D-017). Returns `None` and sets `degraded=True` when fewer than
`MIN_IV_OBSERVATIONS` rows exist.

```python
iv_rank = (count of rows with atm_iv < current_atm_iv) / (total rows)
```

**`trend_score`** — normalized fast/slow moving-average separation, clamped:

```python
raw = (sma(close, TREND_FAST_DAYS) - sma(close, TREND_SLOW_DAYS)) / sma(close, TREND_SLOW_DAYS)
trend_score = clamp(raw / TREND_NORMALIZER, -1.0, 1.0)   # TREND_NORMALIZER = 0.03
```

**`range_score`** — how much of the recent range the price has actually traversed;
low realized displacement relative to path length means range-bound:

```python
displacement = abs(close[-1] - close[-TREND_SLOW_DAYS])
path_length  = sum(abs(close[i] - close[i-1]) for i in last TREND_SLOW_DAYS)
range_score  = 1.0 - clamp(displacement / path_length, 0.0, 1.0)
```

### Entry gate

`passes_entry_gate` returns True only if **all** hold. This gate is what keeps
token spend near zero on quiet scans.

```python
s.atm_iv is not None
s.iv_rank is not None or s.iv_rv_spread is not None   # some vol read available
(s.iv_rank is None or s.iv_rank >= MIN_IV_RANK_FOR_CREDIT)  # 0.35
s.underlying_price > 0
```

## Regime taxonomy

`RegimeLabel` is a closed enum (see docs/ARCHITECTURE.md). The mechanical rules
below produce `mechanical_label`; the LLM receives that verdict plus the rules
themselves and may deviate with a stated reason (D-006).

```python
def mechanical_label(s: SignalSet) -> RegimeLabel:
    if s.iv_rank is not None and s.iv_rank >= STRESS_IV_RANK:        # 0.90
        return RegimeLabel.STRESS
    if s.realized_vol_20d >= STRESS_REALIZED_VOL:                    # 0.45
        return RegimeLabel.STRESS

    high_iv = (s.iv_rank if s.iv_rank is not None else 0.0) >= HIGH_IV_RANK  # 0.50
    if s.range_score >= RANGE_THRESHOLD:                             # 0.55
        return RegimeLabel.RANGE_HIGH_IV if high_iv else RegimeLabel.RANGE_LOW_IV
    if s.trend_score > 0:
        return RegimeLabel.TREND_UP_HIGH_IV if high_iv else RegimeLabel.TREND_UP_LOW_IV
    return RegimeLabel.TREND_DOWN_HIGH_IV if high_iv else RegimeLabel.TREND_DOWN_LOW_IV
```

## Structure eligibility map

Binding. The strategy agent receives only the eligible list and cannot select
outside it; a response naming an ineligible structure fails validation.

```python
STRUCTURE_ELIGIBILITY: dict[RegimeLabel, list[StructureType]] = {
    RegimeLabel.RANGE_HIGH_IV:      ["iron_condor", "put_credit_spread", "call_credit_spread"],
    RegimeLabel.RANGE_LOW_IV:       [],                        # stand down
    RegimeLabel.TREND_UP_HIGH_IV:   ["put_credit_spread"],
    RegimeLabel.TREND_DOWN_HIGH_IV: ["call_credit_spread"],
    RegimeLabel.TREND_UP_LOW_IV:    ["call_debit_spread"],
    RegimeLabel.TREND_DOWN_LOW_IV:  ["put_debit_spread"],
    RegimeLabel.STRESS:             [],                        # manage only, no entries
}
```

`RANGE_LOW_IV` is deliberately empty: range-bound with cheap premium offers
nothing worth defined-risk capital. Standing down is a valid and expected outcome.

## Structure definitions

```python
class StructureType(StrEnum):
    PUT_CREDIT_SPREAD  = "put_credit_spread"    # sell higher-strike put,  buy lower-strike put
    CALL_CREDIT_SPREAD = "call_credit_spread"   # sell lower-strike call,  buy higher-strike call
    IRON_CONDOR        = "iron_condor"          # put credit spread + call credit spread, same expiry
    PUT_DEBIT_SPREAD   = "put_debit_spread"     # buy higher-strike put,   sell lower-strike put
    CALL_DEBIT_SPREAD  = "call_debit_spread"    # buy lower-strike call,   sell higher-strike call
```

## Contract selection

```python
DTE_MIN                  = 7
DTE_MAX                  = 14
SHORT_DELTA_TARGET       = 0.16
SHORT_DELTA_BAND         = (0.12, 0.20)
DEBIT_LONG_DELTA_TARGET  = 0.45
DEBIT_LONG_DELTA_BAND    = (0.35, 0.55)
WING_WIDTH_MIN           = 1.0
WING_WIDTH_MAX           = 5.0
MIN_CREDIT_TO_WIDTH      = 0.20     # credit spreads only
MAX_DEBIT_TO_WIDTH       = 0.45     # debit spreads only
MAX_LEG_SPREAD_PCT       = 0.15     # (ask-bid)/mid per leg; reject wider
MIN_LEG_OPEN_INTEREST    = 100
```

**Expiration.** From the chain, select the expiration whose DTE falls in
`[DTE_MIN, DTE_MAX]`. If several qualify, take the one closest to 10 DTE. If none
qualify, no trade for this symbol this scan.

**Credit-spread strikes.** Short leg is the contract whose absolute delta is
nearest `SHORT_DELTA_TARGET` and inside `SHORT_DELTA_BAND`. Long leg is the next
strike further out-of-the-money such that width is inside
`[WING_WIDTH_MIN, WING_WIDTH_MAX]`; prefer the narrowest width that satisfies
`MIN_CREDIT_TO_WIDTH`.

**Iron condor.** Construct the put credit spread and the call credit spread
independently by the rule above, same expiration. Both sides must satisfy every
liquidity check or the whole structure is rejected — do not fall back to a single
vertical.

**Debit-spread strikes.** Long leg nearest `DEBIT_LONG_DELTA_TARGET` inside its
band; short leg one to three strikes further out, choosing the width whose net
debit satisfies `MAX_DEBIT_TO_WIDTH`.

**Liquidity rejection.** Any leg failing `MAX_LEG_SPREAD_PCT` or
`MIN_LEG_OPEN_INTEREST` rejects the entire structure. Do not substitute strikes to
work around an illiquid leg — that is a different trade than the one selected.

Open interest requires a separate contract-endpoint call per contract and lags one
day (see docs/DATA.md). Fetch it only for the shortlisted legs, never the full chain.

## Max loss

`risk.evaluate` recomputes this independently and does not trust the strategy
agent's arithmetic.

```python
# credit structures
max_loss_per_contract = (width * 100) - (net_credit * 100)
# iron condor: width is the wider of the two sides; only one side can lose
# debit structures
max_loss_per_contract = net_debit * 100
```

## Position management (deterministic)

Runs every 5 minutes in `src/execution/orders.py:manage_positions()`. No LLM.
Triggers are evaluated in order and the first match fires.

```python
TAKE_PROFIT_PCT   = 0.50    # close when 50% of entry credit captured
STOP_LOSS_MULT    = 2.00    # close when position value reaches 2x entry credit
FORCE_CLOSE_DTE   = 2       # close regardless of P&L
```

For a credit structure with `entry_credit` per contract and `current_value` = cost
to close per contract:

```python
if current_value <= entry_credit * (1 - TAKE_PROFIT_PCT):  close("take_profit")
elif current_value >= entry_credit * STOP_LOSS_MULT:       close("stop_loss")
elif dte <= FORCE_CLOSE_DTE:                               close("force_close_dte")
```

For a debit structure with `entry_debit`:

```python
if current_value >= entry_debit * (1 + TAKE_PROFIT_PCT):   close("take_profit")
elif current_value <= entry_debit * (1 - STOP_LOSS_MULT/2): close("stop_loss")  # 50% of debit
elif dte <= FORCE_CLOSE_DTE:                               close("force_close_dte")
```

Orphan positions (docs/ARCHITECTURE.md) use `stop_loss_credit = structural max
loss` and rely on `force_close_dte`, since their true entry credit is unknown.

## Notes

- `MIN_IV_RANK_FOR_CREDIT` (0.35) at the entry gate and `HIGH_IV_RANK` (0.50) in regime classification are deliberately different. The gate is a cheap pre-filter to avoid LLM calls; the regime threshold is the actual eligibility boundary.
- All numeric constants in this document live in `config/params.yaml`, not in source. The values here are the seeded defaults.
- Deltas come from the Alpaca chain snapshot and derive from the delayed indicative feed. Treat them as approximate; the `SHORT_DELTA_BAND` exists partly to absorb that staleness.
- A scan producing no trade is the common case and is not an error. Do not add logic that loosens thresholds when no symbol qualifies.
