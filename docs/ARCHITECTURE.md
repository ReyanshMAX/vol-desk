# ARCHITECTURE.md — agent topology, control loop, and the deterministic/LLM boundary

## Overview

A single Python process runs an in-process scheduler that dispatches five jobs on
independent cadences. Four agent modules sit behind the scheduler: `regime`,
`signal`, `strategy`, and `risk`. The supervisor is not an agent — it is the
scheduler plus the entry pipeline that wires the four together. Two of the four
agents call an LLM; two do not.

## Non-goals

- No web server, HTTP API, or dashboard.
- No message queue, worker pool, or multi-process concurrency. One process, one thread of control, one SQLite writer.
- No dynamic universe selection, screening, or symbol discovery. The universe is a static config list.
- No hot reload. Config changes take effect on process restart.
- No retry-until-success on LLM calls beyond the caps in docs/PROMPTS.md.

## Agent responsibilities and LLM usage

| Agent | LLM? | Reads | Emits |
|---|---|---|---|
| `signal` | no | underlying bars, `iv_history`, chain snapshot | `SignalSet` per symbol |
| `regime` | **yes** | `SignalSet` + mechanical rule verdict | `RegimeLabel` from fixed enum + rationale |
| `strategy` | **yes** | `RegimeLabel`, `SignalSet`, chain snapshot, eligible structures | `OrderIntent` (unvalidated) |
| `risk` | no | `OrderIntent`, account state, open positions, `equity_curve` | `RiskVerdict` (approve+size / veto+reason) |

Position management (take-profit, stop, force-close) is **not** an agent. It is a
deterministic job in `src/execution/orders.py` driven by rows in `positions`. It
requires no LLM and continues to run when inference is unavailable (D-010).

## Cadences

Registered in `src/scheduler.py`. All times US/Eastern; all jobs are no-ops when
the market is closed except `reconcile` and `equity_snapshot`.

| Job | Interval | Market hours only | LLM |
|---|---|---|---|
| `risk_monitor` | 60s | no | no |
| `manage_positions` | 5m | yes | no |
| `iv_snapshot` | 15m | yes | no |
| `entry_scan` | 15m | yes | yes, conditionally |
| `regime_refresh` | 30m | yes | yes |
| `equity_snapshot` | 15m | no | no |
| `reconcile` | on boot only | no | no |

`entry_scan` only invokes the `strategy` LLM when `signal` produces at least one
symbol passing the entry gate in docs/STRATEGY.md. A scan with no qualifying
symbol costs zero tokens. This is the primary token-control mechanism.

Trading window is restricted to 10:00-15:30 ET (see `params.yaml:
trading_window`). No entries in the first 30 minutes or last 30 minutes.
`manage_positions` runs across the full session.

## Boot sequence

`src/main.py` on every start, in order. Any failure in steps 1-5 is fatal — exit
non-zero and let systemd restart with backoff. Do not start the scheduler in a
partially-initialized state.

```
1. load_config()            config/params.yaml + config/universe.yaml + env
2. db.connect()             open SQLite, PRAGMA journal_mode=WAL, apply schema.sql if empty
3. mcp_client.connect()     Alpaca MCP server; fail if unreachable
4. reconcile.run()          see below
5. iv.ensure_seeded()       if any symbol has < MIN_IV_OBSERVATIONS rows, run backfill
6. scheduler.start()        register jobs, enter loop
```

## Reconciliation (`src/execution/reconcile.py`)

Runs on every boot. This is what makes redeploy a non-event (D-013).

```python
def run() -> ReconcileReport:
    """
    Compare Alpaca positions against the `positions` table and converge.

    For each Alpaca option position, group legs into logical positions by
    (underlying, expiration) and match against positions.position_key.

    Four cases:
      MATCHED    in both, state='open'          -> no action
      ORPHAN     in Alpaca, not in DB           -> adopt (below)
      GHOST      in DB state='open', not Alpaca -> mark state='closed',
                                                   realized_pnl from Alpaca activities
      IN_FLIGHT  in DB state='opening'/'closing' -> re-query orders; resolve to
                                                   'open'/'closed', or 'orphan' if
                                                   the order is gone and legs exist
    """
```

**Orphan adoption.** Never ignore an unrecognized position and never flatten it
reflexively. Insert a `positions` row with `state='orphan'` and the most
conservative management plan available: `take_profit_credit` computed from the
current mark rather than an unknown entry credit, `stop_loss_credit` set to the
structural max loss, and force-close at `FORCE_CLOSE_DTE`. Log to
`decision_log` with `agent='supervisor'`, `action='adopt_orphan'`. Orphans count
against position limits in docs/RISK.md.

## Entry pipeline

Executed by `entry_scan`. Any stage returning empty ends the scan for that symbol.

```
for symbol in universe:
    signals   = signal.compute(symbol)              # deterministic
    if not signal.passes_entry_gate(signals): continue
    regime    = regime.classify(symbol, signals)    # LLM, cached 30m
    eligible  = STRUCTURE_ELIGIBILITY[regime.label]
    if not eligible: continue
    intent    = strategy.construct(symbol, signals, regime, eligible, chain)  # LLM
    if intent is None: continue
    verdict   = risk.evaluate(intent, account, open_positions)  # deterministic
    if not verdict.approved: log_veto(); continue
    orders.submit(intent, verdict.qty)              # MCP, price ladder
```

`regime.classify` is cached per symbol for `REGIME_TTL_MINUTES` (30). `entry_scan`
runs every 15 minutes, so it reuses a cached label roughly every other scan.

## Interface contracts

Defined before implementation so phases do not collide. These signatures are
binding — changing one requires updating this doc in the same change (rule 2).

```python
# src/agents/signal.py
@dataclass(frozen=True)
class SignalSet:
    symbol: str
    ts: datetime
    underlying_price: float
    atm_iv: float | None          # None when chain lacks IV, see Q-002
    iv_rank: float | None         # 0.0-1.0 percentile within IV_RANK_LOOKBACK_DAYS
    iv_observations: int          # rows backing iv_rank
    realized_vol_20d: float       # annualized close-to-close
    iv_rv_spread: float | None    # atm_iv - realized_vol_20d
    trend_score: float            # -1.0..1.0, see docs/STRATEGY.md
    range_score: float            # 0.0..1.0, higher = more range-bound
    degraded: bool                # True when iv_rank unavailable, RV fallback used

def compute(symbol: str) -> SignalSet: ...
def passes_entry_gate(s: SignalSet) -> bool: ...

# src/agents/regime.py
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
    mechanical_label: RegimeLabel   # what the rules alone produced
    deviated: bool                  # label != mechanical_label
    rationale: str                  # required, especially when deviated
    confidence: float               # 0.0-1.0
    model: str
    ts: datetime

def classify(symbol: str, signals: SignalSet) -> RegimeVerdict: ...
def mechanical_label(signals: SignalSet) -> RegimeLabel: ...

# src/agents/strategy.py
@dataclass(frozen=True)
class Leg:
    occ_symbol: str
    side: Literal["buy", "sell"]
    ratio: int                      # always 1 for v1 structures

@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    structure: StructureType
    legs: list[Leg]                 # 2 for verticals, 4 for condors
    expiration: date
    net_credit: float               # per contract, positive = credit received
    max_loss_per_contract: float
    short_delta: float
    regime_label: RegimeLabel
    rationale: str

def construct(symbol, signals, regime, eligible, chain) -> OrderIntent | None: ...

# src/agents/risk.py
@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    qty: int                        # 0 when not approved
    veto_reason: str | None
    checks: dict[str, bool]         # every check name -> pass/fail, all logged

def evaluate(intent: OrderIntent, account: Account,
             open_positions: list[Position]) -> RiskVerdict: ...
def halt_state() -> Literal["normal", "soft_halt", "hard_halt"]: ...
```

## Notes

- The scheduler is single-threaded and jobs run to completion. A slow LLM call delays subsequent jobs; this is acceptable and preferable to concurrent SQLite writers. `risk_monitor` at 60s is the tightest cadence and must stay fast — it performs no network calls beyond one MCP account query.
- `signal.compute` is pure with respect to the DB except for reading `iv_history`. It never writes. `iv_snapshot` is the only writer to `iv_history` during live operation.
- Every LLM invocation writes a `decision_log` row regardless of outcome, including validation failures and timeouts. The log is the evidence trail; gaps in it are bugs.
- `degraded=True` on a `SignalSet` must propagate into the strategy prompt so the model knows IV rank is unavailable. Do not silently substitute a default.
