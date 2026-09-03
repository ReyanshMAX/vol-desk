# Build Plan

Phases are vertical slices. Each one runs end to end when complete. Phases are
dependency-ordered, not time-boxed — a phase is done when every acceptance
criterion is verified, not when a period elapses (D-026).

On finishing a phase: verify every acceptance criterion, then update STATUS.md
before starting the next phase.

---

## Phase 1 — Deployed skeleton that reads the account

The thinnest path that exercises every layer: config, SQLite, MCP, scheduler,
systemd. Deploy is inside this phase so integration pain surfaces first rather
than last.

**Scope**
- `config/params.yaml` and `config/universe.yaml` with every literal from docs/STRATEGY.md and docs/RISK.md
- `src/config.py` — typed load and validation, fail loudly on a missing key
- `src/store/db.py`, `schema.sql`, `repo.py` — full schema from docs/DATA.md applied on first boot
- `src/execution/mcp_client.py` — connect, assert `REQUIRED_TOOLS`, `get_account()`
- `src/scheduler.py` with one registered job: `equity_snapshot` every 15 minutes
- `src/main.py` boot sequence steps 1, 2, 3, 6 (reconcile and IV seeding are later phases)
- systemd unit deployed and running on the VM per docs/DEPLOY.md

**Non-goals for this phase**
- No market data, no signals, no orders, no LLM calls
- No reconciliation — boot step 4 is a no-op stub
- No market-hours gating; `equity_snapshot` runs on a fixed interval

**Interface contracts established**
```
src/config.py
  def load() -> Config
src/store/repo.py
  def append_equity_curve(equity, cash, hwm, drawdown_pct, open_positions, halt_state) -> None
  def get_hwm() -> float
  def set_hwm(value: float) -> None
src/execution/mcp_client.py
  def connect() -> MCPSession
  def get_account() -> Account
src/scheduler.py
  def register(name: str, interval_s: int, fn: Callable[[], None], market_hours_only: bool) -> None
  def start() -> None
```

**Acceptance criteria**
- [ ] `systemctl status vol-desk` shows `active (running)` on the VM
- [ ] `journalctl -u vol-desk` shows a successful MCP connection and the resolved tool names at boot
- [ ] Boot fails with a non-zero exit and a clear message when `ALPACA_API_KEY` is unset
- [ ] `sqlite3 $VOL_DESK_DB ".tables"` lists all five tables
- [ ] After 30 minutes, `SELECT COUNT(*) FROM equity_curve` returns at least 2, with `equity` matching the Alpaca dashboard
- [ ] `systemctl restart vol-desk` leaves the DB intact and appends new rows rather than recreating the file

**Depends on:** none

---

## Phase 2 — Live IV observations accumulating

**Scope**
- `src/data/alpaca_data.py` — `fetch_daily_bars()`, `fetch_chain()` with expiration and strike filtering per docs/DATA.md
- `src/data/iv.py` — `snapshot_iv()`, `iv_rank()`, `ensure_seeded()` (stub returning immediately)
- Register `iv_snapshot` every 15 minutes, market hours only
- Market-hours gating in the scheduler using `pandas-market-calendars`

**Non-goals for this phase**
- No backfill — `iv_history` starts empty and fills forward only
- No signal computation, no regime, no trading
- No handling of a chain that omits IV; if IV is missing, log and skip (Q-002 resolves this in Phase 3)

**Interface contracts established**
```
src/data/alpaca_data.py
  def fetch_daily_bars(symbol: str, days: int) -> list[Bar]
  def fetch_chain(symbol: str, underlying_price: float) -> list[OptionSnapshot]
src/data/iv.py
  def snapshot_iv(symbol: str) -> None
  def iv_rank(symbol: str) -> tuple[float | None, int]   # (rank, observation_count)
```

**Acceptance criteria**
- [ ] `SELECT symbol, COUNT(*) FROM iv_history GROUP BY symbol` returns 7 rows after one market session
- [ ] Every `iv_history` row has `source='live'`, `atm_iv > 0`, and a `dte` within 5 of `ATM_IV_TARGET_DTE`
- [ ] `fetch_chain('SPY', price)` returns fewer than 200 contracts, confirming filters are applied server-side
- [ ] No `iv_snapshot` rows are written outside 09:30-16:00 ET on a trading day
- [ ] Killing the process mid-session and restarting resumes snapshots with no duplicate rows

**Depends on:** Phase 1

---

## Phase 3 — Backfilled IV history and a computable IV rank

**Scope**
- `src/data/backfill.py` — OCC symbol construction, historical option bar fetch, Black-Scholes inversion via `scipy.optimize.brentq`
- `ensure_seeded()` wired into boot step 5
- Resolve Q-002 (does the free chain return IV?) and Q-003 (strike increments); move both to DECISIONS.md
- If Q-002 resolves as "IV absent", `snapshot_iv` computes IV from the quote mid using the same inversion path

**Non-goals for this phase**
- No dividend adjustment (D-025), no live risk-free rate (D-024)
- No backfill of anything other than the ATM IV series
- No re-backfill on later boots once `MIN_IV_OBSERVATIONS` is satisfied

**Interface contracts established**
```
src/data/backfill.py
  def occ_symbol(root: str, expiry: date, right: Literal["C","P"], strike: float) -> str
  def implied_vol_from_price(price, spot, strike, dte_years, rate, right) -> float | None
  def backfill_symbol(symbol: str) -> int
```

**Acceptance criteria**
- [ ] `implied_vol_from_price` recovers a known sigma to within 1e-3 when fed a price generated by the forward BS formula (unit test)
- [ ] `implied_vol_from_price` returns `None` for a price below intrinsic value rather than raising
- [ ] After a fresh boot on an empty DB, every symbol has at least 15 `source='backfill'` rows
- [ ] `iv_rank('SPY')` returns a float in [0,1] with `iv_observations >= MIN_IV_OBSERVATIONS`
- [ ] Re-running `backfill_symbol('SPY')` writes 0 new rows (idempotent via `INSERT OR IGNORE`)
- [ ] Backfilling all 7 symbols on the VM completes without the process being OOM-killed

**Depends on:** Phase 2

---

## Phase 4 — Signals and regime labels

**Scope**
- `src/agents/signal.py` — full `SignalSet` computation and `passes_entry_gate`
- `src/llm/client.py` — `complete()` and `complete_json()` with the single-retry contract
- `src/agents/regime.py` — `mechanical_label()` and LLM `classify()` with the 30-minute cache
- `src/llm/prompts.py` and `schemas.py` with the verbatim text from docs/PROMPTS.md
- Register `regime_refresh` every 30 minutes, market hours only
- Resolve Q-004 (Groq model IDs and rate limits); move to DECISIONS.md

**Non-goals for this phase**
- No structure construction, no orders
- No eligibility map consumption yet — the label is computed and logged only

**Interface contracts established**
```
src/agents/signal.py
  def compute(symbol: str) -> SignalSet
  def passes_entry_gate(s: SignalSet) -> bool
src/agents/regime.py
  def mechanical_label(s: SignalSet) -> RegimeLabel
  def classify(symbol: str, signals: SignalSet) -> RegimeVerdict
src/llm/client.py
  def complete(system, user, *, tier, max_tokens, temperature, timeout_s) -> LLMResponse
  def complete_json(system, user, schema, *, tier) -> T | None
```

**Acceptance criteria**
- [ ] `SELECT * FROM decision_log WHERE agent='regime'` shows rows with a populated `rationale`, `model`, and `latency_ms`
- [ ] Every logged `label` is one of the seven enum values
- [ ] A forced malformed LLM response (test double) produces one retry, then a row with `accepted=0`, and `classify` returns the mechanical fallback
- [ ] With `GROQ_API_KEY` unset, `regime_refresh` logs `inference_unavailable` once and the process stays running
- [ ] `passes_entry_gate` returns False for every symbol when `iv_history` is empty, and no LLM call is made — verified by zero `agent='regime'` rows

**Depends on:** Phase 3

---

## Phase 5 — Structure construction, dry run

**Scope**
- `src/agents/strategy.py` — `construct()`, chain table rendering, full deterministic post-validation from docs/PROMPTS.md
- `STRUCTURE_ELIGIBILITY` map and DTE/strike selection helpers from docs/STRATEGY.md
- Register `entry_scan` every 15 minutes, market hours only, terminating at `OrderIntent` — logged, never submitted
- Open-interest fetch for shortlisted legs only

**Non-goals for this phase**
- No risk evaluation, no sizing, no order submission
- No `positions` rows written

**Interface contracts established**
```
src/agents/strategy.py
  def construct(symbol, signals, regime, eligible, chain) -> OrderIntent | None
  def validate_response(r: StrategyResponse, chain, eligible) -> OrderIntent | None
```

**Acceptance criteria**
- [ ] `SELECT * FROM decision_log WHERE agent='strategy'` shows both `trade` and `decline` outcomes over a session
- [ ] Every logged `OrderIntent` has 2 legs for a vertical or 4 for a condor, one shared expiration, and DTE within [7,14]
- [ ] A test double returning an ineligible structure for the regime is rejected with no retry
- [ ] A test double returning an `occ_symbol` absent from the chain is rejected
- [ ] `net_credit` and `max_loss_per_contract` on every intent match values recomputed from the chain fixture, not values the model emitted
- [ ] A `RANGE_LOW_IV` or `STRESS` regime produces zero LLM strategy calls

**Depends on:** Phase 4

---

## Phase 6 — Risk evaluation and sizing, dry run

**Scope**
- `src/agents/risk.py` — `evaluate()` with all eight checks, `_size()`, `_recompute_max_loss()`
- `halt_state()` reading `system_state`
- Entry pipeline extended through `risk.evaluate`, still stopping before submission
- Veto logging per docs/RISK.md

**Non-goals for this phase**
- No `risk_monitor` job yet, no drawdown transitions, no flatten
- No order submission

**Interface contracts established**
```
src/agents/risk.py
  def evaluate(intent: OrderIntent, account: Account, open_positions: list[Position]) -> RiskVerdict
  def halt_state() -> Literal["normal","soft_halt","hard_halt"]
```

**Acceptance criteria**
- [ ] Every `agent='risk'` row's `output_json` contains all eight check names with boolean values, including on approval
- [ ] A fixture with 6 open positions produces `under_position_cap=false` and `approved=false`
- [ ] A fixture with open SPY and QQQ positions vetoes a proposed IWM position via `under_cluster_cap`
- [ ] `_size(400.0, 100_000)` returns 2; `_size(1_500.0, 100_000)` returns 0 and vetoes on `sizeable`
- [ ] An intent whose `max_loss_per_contract` is understated by 1.00 relative to its legs fails `max_loss_agrees`
- [ ] Setting `system_state.halt_state='soft_halt'` causes every evaluation to fail `not_halted`

**Depends on:** Phase 5

---

## Phase 7 — Live order submission

The first phase that places real paper orders.

**Scope**
- `src/execution/orders.py` — `submit_with_ladder()` and the MCP `place_mleg_order` path
- `positions` row written on fill with the full management plan (`take_profit_value`, `stop_loss_value`)
- Partial-fill and abandonment handling
- Resolve Q-001 (MCP multi-leg tool names and argument schemas); move to DECISIONS.md

**Non-goals for this phase**
- No exit logic — positions opened in this phase are managed manually until Phase 8
- No halt-driven flattening

**Interface contracts established**
```
src/execution/orders.py
  def submit_with_ladder(intent: OrderIntent, qty: int) -> FillResult
  def build_position_row(intent, qty, fill_price) -> Position
```

**Acceptance criteria**
- [ ] A filled order produces exactly one `positions` row with `state='open'` and a `position_key` matching the sha1 of its sorted OCC symbols
- [ ] The Alpaca dashboard shows the position with leg count and strikes matching the `positions` row
- [ ] An unfillable limit (ladder exhausted) leaves no `positions` row, no resting order at Alpaca, and one `decision_log` row recording `ABANDONED`
- [ ] `take_profit_value` equals `entry_credit * 0.5` and `stop_loss_value` equals `entry_credit * 2.0` for a credit structure
- [ ] A rejected order logs the broker's message and does not retry

**Depends on:** Phase 6

---

## Phase 8 — Position management

**Scope**
- `manage_positions()` job every 5 minutes, market hours only
- Take-profit, stop-loss, and force-close-at-DTE triggers from docs/STRATEGY.md
- Exit ladder (inverted) and `positions` transition to `state='closed'` with `close_reason` and `realized_pnl`

**Non-goals for this phase**
- No rolling, no adjustment, no re-entry after a close
- No orphan handling

**Interface contracts established**
```
src/execution/orders.py
  def manage_positions() -> None
  def close_structure(p: Position, reason: str) -> FillResult
```

**Acceptance criteria**
- [ ] A position whose close cost falls to 50% of entry credit closes with `close_reason='take_profit'`
- [ ] A position whose close cost reaches 2x entry credit closes with `close_reason='stop_loss'`
- [ ] A position at 2 DTE closes with `close_reason='force_close_dte'` regardless of P&L
- [ ] `realized_pnl` on a closed row reconciles with the Alpaca activity feed to within one cent per contract
- [ ] Only one trigger fires per position per cycle; a position meeting both take-profit and DTE conditions records `take_profit`
- [ ] `manage_positions` completes normally with `GROQ_API_KEY` unset

**Depends on:** Phase 7

---

## Phase 9 — Halts, flatten, and reconciliation

The phase that makes the system genuinely safe to leave unattended.

**Scope**
- `risk_monitor()` job every 60 seconds: high-water mark, drawdown, state transitions with `RECOVERY_FACTOR` hysteresis
- `hard_halt` flatten path via MCP `close_position`
- `src/execution/reconcile.py` — MATCHED / ORPHAN / GHOST / IN_FLIGHT resolution and orphan adoption
- Boot step 4 wired to `reconcile.run()`

**Non-goals for this phase**
- No automatic recovery from `hard_halt` — clearing stays manual
- No alerting or notification channel

**Interface contracts established**
```
src/agents/risk.py
  def risk_monitor() -> None
src/execution/reconcile.py
  def run() -> ReconcileReport
```

**Acceptance criteria**
- [ ] Manually setting `high_water_mark` to force a 6% drawdown transitions to `soft_halt`, blocks new entries, and leaves `manage_positions` running
- [ ] Recovery to a 3.9% drawdown returns state to `normal`; a 4.5% drawdown does not (hysteresis at `0.05 * 0.8`)
- [ ] Forcing an 11% drawdown cancels open orders, closes every position, writes `halt_state='hard_halt'`, and logs `hard_halt_flatten`
- [ ] After a `hard_halt`, `entry_scan` makes zero LLM calls
- [ ] Deleting a `positions` row for a live Alpaca position and restarting produces an adopted row with `state='orphan'` and `structure='unknown'`
- [ ] Closing a position manually in the Alpaca dashboard and restarting marks the DB row `state='closed'` with a populated `realized_pnl`
- [ ] Adopted orphans count toward `max_concurrent_positions` in a subsequent `risk.evaluate`

**Depends on:** Phase 8
