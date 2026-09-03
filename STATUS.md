# Status

**Last updated:** 2026-09-03
**Current phase:** all 9 phases implemented in code and unit-tested against
fixtures; none verified against the live Alpaca MCP server or Groq (no
credentials/host available in this session). Treat every phase as
**code-complete, integration-unverified** until run on the VM per
docs/DEPLOY.md.
**Next action:** Resolve OPEN_QUESTIONS.md Q-001 (connect to the live Alpaca
MCP server, list its tools, fill in `src/execution/mcp_client.REQUIRED_TOOLS`)
and Q-003 (strike increments per symbol, fill in `config/universe.yaml`).
Both block ever running Phase 1's boot sequence for real. Q-004 (Groq model
IDs) blocks any real LLM call. See "Blocked" below.

---

## Done

_Written this session (2026-09-03), all against fixtures/unit tests, no live
Alpaca or Groq calls made:_

- `config/params.yaml`, `config/universe.yaml` — every literal from
  docs/STRATEGY.md and docs/RISK.md; `strike_increment` and `llm.tiers` left
  `null` rather than guessed (Q-003, Q-004)
- `src/config.py` — typed load and validation, fails loudly on any missing
  key or env var
- `src/store/schema.sql`, `db.py`, `repo.py` — full schema and repository
  interface from docs/DATA.md
- `src/scheduler.py` — job registration, NYSE market-hours gating via
  `pandas-market-calendars`, trading-window helper for entry_scan
- `src/execution/mcp_client.py` — connect/list-tools/assert-required-tools
  path; capability calls (`get_account`, `place_mleg_order`, etc.) raise a
  clear `MCPToolsUnresolvedError` until Q-001 is resolved and
  `REQUIRED_TOOLS` is filled in from a live tool listing
- `src/data/alpaca_data.py`, `iv.py`, `backfill.py` — bar/chain fetch,
  OCC symbol construction, Black-Scholes inversion via `scipy.optimize.brentq`,
  live IV snapshotting, cold-start backfill
- `src/llm/client.py`, `prompts.py`, `schemas.py` — Groq client behind the
  OpenAI-compatible surface, verbatim prompt text, pydantic response schemas,
  single-retry-then-fallback contract
- `src/agents/signal.py`, `regime.py`, `strategy.py`, `risk.py` — full
  deterministic signal computation, mechanical + LLM regime labeling,
  structure construction with deterministic post-validation, all eight risk
  checks, sizing, drawdown tiers/halts/hard-halt flatten
- `src/execution/orders.py`, `reconcile.py` — price ladder submission,
  position management triggers (take-profit/stop-loss/force-close-dte),
  boot-time reconciliation (MATCHED/ORPHAN/GHOST/IN_FLIGHT)
- `src/main.py` — full boot sequence (steps 1-6) and all six scheduled jobs
  wired into the entry pipeline from docs/ARCHITECTURE.md
- `tests/` — 69 passing tests: unit coverage for the deterministic layer
  (BS inversion round-trip, OCC symbol construction, signal math, mechanical
  regime labeling, risk sizing/veto checks, strategy post-validation,
  position-management threshold math, config loading, reconciliation
  grouping/orphan-adoption/ghost-closing), plus a mocked end-to-end
  `entry_scan` pipeline test (`tests/test_pipeline_e2e.py`) that runs real
  signal→regime→strategy→risk→orders code with only the Alpaca/Groq network
  boundaries faked -- this caught two real bugs before they'd have hit a
  live account (see Deviations)
- `deploy/` — `provision.sh`, `vol-desk.service`, `vol-desk.env.example`:
  the docs/DEPLOY.md commands as ready-to-run files, not yet run against a
  real VM

## In progress

_Nothing actively in progress. The deterministic/pure-Python layer is
complete for all 9 phases; what remains is entirely integration work that
needs a live Alpaca paper account + MCP server + Groq key + host, none of
which are available in this session._

## Next up

1. Resolve Q-001 against a live Alpaca MCP server; fill in
   `mcp_client.REQUIRED_TOOLS`
2. Resolve Q-003 (strike increments) and Q-004 (Groq model IDs); fill in
   `config/universe.yaml` and `config/params.yaml:llm.tiers`
3. Provision a host (Q-005) and run Phase 1's acceptance criteria for real
   on the VM per docs/DEPLOY.md
4. Work through Phases 2-9's acceptance criteria against the live services,
   in order — the code exists for all of them but none has touched a real
   Alpaca or Groq endpoint yet

## Blocked

- **Q-001** MCP tool names/argument schemas unverified — blocks ever
  connecting for real; `mcp_client.py` is written to fail loudly and name
  what's missing rather than guess
- **Q-002** does the free chain return IV — code handles both branches
  (`iv.snapshot_iv` falls back to inverting the quote mid), unverified live
- **Q-003** strike increments per symbol — blocks backfill; `universe.yaml`
  intentionally left null rather than guessed
- **Q-004** Groq model IDs and rate limits — blocks any real LLM call;
  `params.yaml:llm.tiers` intentionally left null
- **Q-005** host not yet provisioned — nothing in this session had VM access

## Deviations from spec

- **Bugs found and fixed during a self-review pass, before any of this ran
  against a live account:**
  - `strategy.py` validated that a response's legs balanced (one buy, one
    sell) and that two distinct strikes existed, but not that the *correct*
    strike was on the *correct* side (e.g. a `put_credit_spread` response
    could have sold the lower strike and bought the higher one, which is
    the wrong construction per docs/STRATEGY.md's own definition). This
    happened to be caught downstream by the credit/debit-to-width and
    sizing checks as a side effect, but not as an explicit correctness
    check. Added `_sides_match_strikes` to reject it directly.
  - `orders.py`'s `submit_with_ladder` only treated a partially-filled rung
    as terminal on the *last* ladder rung; on an earlier rung it would
    cancel and resubmit the *original* full quantity on the next rung,
    risking an over-fill on top of the partial. Now any partial fill is
    terminal at whichever rung it occurs.
  - `orders.py`'s `close_structure` always marked a position fully
    `state='closed'` even on a partial close, silently losing track of the
    contracts still open at the broker. It now keeps the row `state='open'`
    with the reduced remaining qty and records the partial fill's P&L in
    `decision_log` (the schema has one `realized_pnl` field per row, sized
    for a single terminal close, not partial accounting).
  - `strategy.py`'s LLM call used `max_retries=0`, conflating PROMPTS.md's
    "no retry" rule (which is about the deterministic *post-validation*
    step) with the JSON-schema-level retry contract from INTEGRATIONS.md,
    which should apply uniformly to both LLM call sites. Fixed to use the
    configured retry count.
  - `_position_key` was duplicated identically in `orders.py` and
    `reconcile.py`; consolidated into `store/repo.py` so the two can't
    silently drift apart.
  - `mcp_client.py` had no reconnect path for a transport dying mid-session
    (INTEGRATIONS.md: "reconnect rather than restarting the process"); a
    failed call now attempts one reconnect-and-retry before propagating.

- `mcp_client.py`'s IN_FLIGHT resolution in `reconcile.py` is coarser than
  docs/ARCHITECTURE.md implies: the schema (docs/DATA.md) has no `order_id`
  column on `positions`, so a row can only be resolved by whether its legs
  currently exist at the broker, not by re-querying a specific order.
  `build_position_row` also writes `state='open'` directly on fill rather
  than an intermediate `'opening'`, so this path is not exercised in normal
  operation yet. Flagged in a code comment at the call site; not yet added
  to OPEN_QUESTIONS.md because it doesn't block any current phase.
- Regime/structure enums (`RegimeLabel`, `StructureType`) live in
  `agents/regime.py` and `agents/strategy.py` as docs/ARCHITECTURE.md
  specifies, but the pydantic response schemas that validate against them
  (`llm/schemas.py`) are imported lazily inside `classify()`/`construct()`
  rather than at module load, to avoid a circular import. No behavioral
  change.

---

## Update protocol

Write to this file when any of these happen:

- A phase completes — move it to **Done** with the date and note any deviations
- A phase criterion completes — check the box
- Work stops mid-phase — set **Next action** to the specific next step, not a vague area. "Implement `snapshot_iv()` per Phase 2 criterion 2" is resumable; "continue on data" is not
- Something becomes blocked — add it to **Blocked** with the question ID
- Implementation diverges from a spec doc — log it under **Deviations** *and* update the spec doc itself (CLAUDE.md standing rule 2)

**Next action** is the highest-value field here. It is what makes a cold
session productive in one read instead of five minutes of code archaeology.
Keep it specific enough to start typing from.
