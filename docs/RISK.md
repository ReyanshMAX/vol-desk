# RISK.md — sizing, drawdown tiers, correlation caps, veto protocol, halts

## Overview

`src/agents/risk.py` is the last gate before any order reaches Alpaca. It contains
no LLM (D-005), recomputes every number it is given rather than trusting the
strategy agent, and can only ever reduce exposure — it sizes down, vetoes, or
halts, never the reverse.

## Non-goals

- No portfolio optimization, Kelly sizing, or volatility-targeted position sizing. Fixed fractional risk only.
- No portfolio-level greek management (net delta, net vega targets). Per-position and per-cluster limits only.
- No margin or buying-power modeling beyond the check that Alpaca accepts the order. Defined-risk structures make max loss the binding constraint, not margin.
- No dynamic parameter tuning. Limits change by editing `config/params.yaml` and restarting.

## Parameters

```yaml
# config/params.yaml : risk
max_risk_per_trade_pct:      0.01    # 1% of equity as max loss on the position
soft_drawdown_pct:           0.05    # 5% from high-water mark -> no new entries
hard_drawdown_pct:           0.10    # 10% from high-water mark -> flatten and halt
max_concurrent_positions:    6
max_positions_per_underlying: 1
max_equity_beta_positions:   3       # SPY + QQQ + IWM combined
min_free_cash_pct:           0.20    # keep 20% of equity uncommitted
max_daily_new_positions:     4
```

## Correlation clusters

```python
CORRELATION_CLUSTERS: dict[str, list[str]] = {
    "equity_beta": ["SPY", "QQQ", "IWM"],
    "metals":      ["GLD"],
    "duration":    ["TLT"],
    "energy":      ["XLE"],
    "credit":      ["HYG"],
}
```

Only `equity_beta` has a cap below `max_concurrent_positions`. Three short-premium
positions across SPY, QQQ, and IWM is one equity bet held three times, not three
independent bets, and the drawdown tiers assume some independence across the book.

## `risk.evaluate()` — check order

Every check runs and every result is recorded in `RiskVerdict.checks`, even after
the first failure, so `decision_log` shows the complete picture rather than the
first tripwire. `approved` is `all(checks.values())`.

```python
def evaluate(intent: OrderIntent, account: Account,
             open_positions: list[Position]) -> RiskVerdict:
    checks = {}

    # 1. halt state
    checks["not_halted"] = halt_state() == "normal"

    # 2. structure is defined-risk and legs are balanced
    checks["defined_risk"] = _is_defined_risk(intent)   # 2 or 4 legs, ratios all 1,
                                                        # long/short counts equal per side

    # 3. independent max-loss recomputation from legs and strikes,
    #    NOT from intent.max_loss_per_contract
    recomputed = _recompute_max_loss(intent)
    checks["max_loss_agrees"] = abs(recomputed - intent.max_loss_per_contract) < 0.01

    # 4. position count limits
    checks["under_position_cap"] = len(open_positions) < max_concurrent_positions
    checks["under_symbol_cap"]   = _count(open_positions, intent.symbol) < max_positions_per_underlying
    checks["under_cluster_cap"]  = _cluster_count(open_positions, intent.symbol) < _cluster_cap(intent.symbol)

    # 5. daily churn
    checks["under_daily_cap"] = _opened_today() < max_daily_new_positions

    # 6. sizing produces at least one contract
    qty = _size(recomputed, account.equity)
    checks["sizeable"] = qty >= 1

    # 7. cash headroom after this position
    checks["cash_headroom"] = _projected_free_cash(account, recomputed, qty) >= \
                              account.equity * min_free_cash_pct

    # 8. expiration sanity
    checks["dte_in_window"] = DTE_MIN <= _dte(intent.expiration) <= DTE_MAX

    approved = all(checks.values())
    return RiskVerdict(
        approved=approved,
        qty=qty if approved else 0,
        veto_reason=None if approved else ",".join(k for k, v in checks.items() if not v),
        checks=checks,
    )
```

## Sizing

```python
def _size(max_loss_per_contract: float, equity: float) -> int:
    budget = equity * max_risk_per_trade_pct
    return math.floor(budget / max_loss_per_contract)
```

Floor, never round. A position that sizes to zero contracts is a veto
(`sizeable`), not a one-contract trade. On a 100,000 paper account at 1%, budget
is 1,000 per position; a 5-wide spread collecting 1.00 credit has 400 max loss per
contract and sizes to 2 contracts.

## Drawdown tiers

High-water mark is stored in `system_state` under key `high_water_mark` and only
ever increases. `risk_monitor` runs every 60 seconds and is the only writer.

```python
def risk_monitor() -> None:
    account = mcp_client.get_account()
    hwm = max(store.get_hwm(), account.equity)
    store.set_hwm(hwm)
    dd = (hwm - account.equity) / hwm

    if dd >= hard_drawdown_pct:
        transition("hard_halt")
    elif dd >= soft_drawdown_pct:
        transition("soft_halt")
    elif store.halt_state() == "soft_halt" and dd < soft_drawdown_pct * RECOVERY_FACTOR:
        transition("normal")     # RECOVERY_FACTOR = 0.8, hysteresis

    store.append_equity_curve(account, hwm, dd, store.halt_state())
```

| State | New entries | Existing positions | Exit condition |
|---|---|---|---|
| `normal` | allowed | managed normally | — |
| `soft_halt` | blocked | managed normally | drawdown recovers below `soft_drawdown_pct * 0.8` |
| `hard_halt` | blocked | **flattened immediately**, then none | manual only |

**Hysteresis matters.** Without `RECOVERY_FACTOR`, equity oscillating around the
5% line flips the system between states every minute and floods the log.

**`hard_halt` is terminal and manual to clear.** On transition: cancel all open
orders, close every position at market through the MCP close-position tool, write
`halt_state='hard_halt'` to `system_state`, log to `decision_log` with
`agent='risk'`, `action='hard_halt_flatten'`. The scheduler keeps running so
`equity_snapshot` and `risk_monitor` continue, but `entry_scan` short-circuits.
Clearing requires editing `system_state` directly. Do not add an automatic
recovery path — the point of a kill switch is that it stays pulled until a human
looks at why.

## Veto protocol

A veto is a normal outcome, not an error. On veto:

1. Write a `decision_log` row with `agent='risk'`, `accepted=0`, `veto_reason` set to the comma-joined failing check names, `inputs_json` containing the full `OrderIntent`, `output_json` containing `checks`.
2. Return. Do not retry, do not ask the strategy agent for an alternative, do not relax a threshold.

Repeated vetoes for the same reason are a signal that strategy parameters and risk
parameters disagree. That is a config problem for a human to resolve, not
something the system resolves at runtime.

## Notes

- `risk.evaluate` must have no network calls other than the account state passed into it. It is called inside the entry pipeline and must stay fast and pure enough to unit test with fixtures.
- `_recompute_max_loss` deliberately duplicates arithmetic already done in `src/agents/strategy.py`. This is intentional redundancy across a deterministic/LLM boundary — the strategy agent's output passed through a language model and is not trusted.
- Orphan positions count toward every position and cluster limit. A book full of orphans correctly blocks new entries.
- The equity used for sizing is Alpaca's reported account equity, not a locally tracked figure. Local equity tracking would drift from the broker.
