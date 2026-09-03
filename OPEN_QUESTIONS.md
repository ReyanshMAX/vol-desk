# Open Questions

Unresolved. Do not resolve these silently — ask, then move the answer to
DECISIONS.md and delete the entry here.

---

## Q-001: Alpaca MCP server tool names and argument schemas

Multi-leg capability itself is confirmed (D-027). What remains is the exact wire
surface.

- **Blocking:** Phase 7 for the order tools; Phase 1 for the `REQUIRED_TOOLS` assertion.
- **Options:** The capability set in docs/INTEGRATIONS.md (`account`, `positions`, `orders_list`, `place_mleg_order`, `cancel_order`, `close_position`) is what the system needs, but the server's actual tool names and argument shapes are unverified — in particular how legs are represented (list of OCC symbols with sides, versus a structured spread type), and whether limit price is expressed as a net credit, net debit, or signed value.
- **Resolve by:** Connecting to the MCP server and listing its tools. Read the schemas off the live server; do not infer them from the REST API docs, which may not match the MCP surface.
- **Depends on it:** `mcp_client.REQUIRED_TOOLS`, the `place_mleg_order` signature, and the sign convention in `submit_with_ladder`'s price ladder. Getting the credit/debit sign wrong will submit orders at inverted prices that either fill instantly at a bad price or never fill — verify the convention against one small test order before Phase 7's criteria are considered met.

---

## Q-002: Does the free indicative feed populate greeks and implied volatility?

- **Blocking:** Phase 3. Phase 2 works either way by logging and skipping.
- **Options:** (a) The chain snapshot returns `greeks` and `implied_volatility` on the indicative feed, and they are used directly. (b) Those fields are OPRA-only and arrive null, in which case IV is computed from the delayed quote mid using `implied_vol_from_price`, and delta must also be computed locally from that IV.
- **Resolve by:** One call to `GET /v1beta1/options/snapshots/SPY` with `feed=indicative` on the live paper key. Inspect for populated `greeks.delta` and `implied_volatility`.
- **Depends on it:** `snapshot_iv` implementation, `SignalSet.atm_iv`, and all delta-based strike selection in docs/STRATEGY.md. If (b), the chain table in the strategy prompt is populated with locally computed deltas, and Phase 5's acceptance criteria still hold unchanged.

---

## Q-003: Strike increments per symbol

- **Blocking:** Phase 3. OCC symbols cannot be constructed for the backfill without them.
- **Options:** Increments differ by symbol and by price region, and some of these ETFs list half-dollar strikes near the money. Hardcoding 1.0 for all seven will silently generate invalid OCC symbols that return empty bars, which looks identical to a thin expiry and will be misdiagnosed as missing data.
- **Resolve by:** Calling `GET /v2/options/contracts` per symbol for a near-dated expiry and reading the distinct strike spacing off the returned contracts. Write the result into `config/universe.yaml:strike_increment`.
- **Depends on it:** `occ_symbol()`, `backfill_symbol()`, and the "at least 15 backfill rows per symbol" acceptance criterion in Phase 3.

---

## Q-004: Groq model IDs and free-tier rate limits

- **Blocking:** Phase 4.
- **Options:** Two tiers are needed — a smaller model for regime labeling and a larger one for structure construction. Model availability on Groq's free tier changes, and per-minute and per-day request and token limits are not currently known.
- **Resolve by:** Listing available models on the Groq account and reading the current free-tier limits. Record both model IDs in `config/params.yaml:llm.tiers` and confirm the worst-case call volume fits: 7 symbols × 2 sites × 26 scans per session is an upper bound of roughly 360 calls/day, well under typical limits, but this should be checked rather than assumed.
- **Depends on it:** `llm.tiers` config, `complete()` routing. If the free tier proves too tight, the correct response is lengthening `entry_scan` and `regime_refresh` intervals, not removing an LLM site.

---

## Q-005: Which host was actually provisioned?

- **Blocking:** no — docs/DEPLOY.md covers both candidates and the architecture is host-agnostic (D-011).
- **Options:** GCP e2-micro free tier (primary) or Oracle Always Free (fallback).
- **Resolve by:** Whichever provisions successfully. Record it in STATUS.md and, if it materially changed any provisioning step, update docs/DEPLOY.md.
- **Depends on it:** Nothing in the source tree.

---

## Q-006: Behavior when a position's expiration is a holiday-shortened or half session

- **Blocking:** no — will not arise until a position is held into such a session.
- **Options:** `FORCE_CLOSE_DTE=2` assumes normal sessions. On a half day, the 5-minute `manage_positions` cadence and the exit ladder may not complete before the early close.
- **Resolve by:** Deciding whether to force-close a day earlier when the calendar shows a shortened session inside the position's remaining life.
- **Depends on it:** `manage_positions` DTE trigger. Until resolved, the system behaves as specified — do not add holiday special-casing silently.
