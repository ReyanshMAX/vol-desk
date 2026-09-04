# Open Questions

Unresolved. Do not resolve these silently — ask, then move the answer to
DECISIONS.md and delete the entry here.

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
- **Resolve by:** Calling `GET /v2/options/contracts` per symbol for a near-dated expiry and reading the distinct strike spacing off the returned contracts. Write the result into `config/universe.yaml:strike_increment`. `scripts/resolve_open_questions.py` automates this (and Q-004) against the live APIs, printing what it found and asking for confirmation before writing anything.
- **Depends on it:** `occ_symbol()`, `backfill_symbol()`, and the "at least 15 backfill rows per symbol" acceptance criterion in Phase 3.

---

## Q-004: Groq model IDs and free-tier rate limits

- **Blocking:** Phase 4.
- **Options:** Two tiers are needed — a smaller model for regime labeling and a larger one for structure construction. Model availability on Groq's free tier changes, and per-minute and per-day request and token limits are not currently known.
- **Resolve by:** Listing available models on the Groq account (`GET /openai/v1/models` with the real key, or `GET https://api.groq.com/openai/v1/models`) and reading the current free-tier limits from Groq's own docs/console. Record both model IDs in `config/params.yaml:llm.tiers` and confirm the worst-case call volume fits: 7 symbols × 2 sites × 26 scans per session is an upper bound of roughly 360 calls/day, well under typical limits, but this should be checked rather than assumed. `scripts/resolve_open_questions.py` lists the live models and prompts for which to use per tier (defaults are just position-in-list suggestions, not a recommendation) rather than picking automatically.
- **Depends on it:** `llm.tiers` config, `complete()` routing. If the free tier proves too tight, the correct response is lengthening `entry_scan` and `regime_refresh` intervals, not removing an LLM site.
- **Research lead, not a resolution:** third-party pricing aggregators (not Groq's own docs) suggest Groq's free tier currently offers Llama 3.1 8B, Llama 3.3 70B Versatile, GPT-OSS 120B/20B, Llama 4 Scout/Maverick, Mistral Saba, Qwen 3, and Kimi K2, with limits in the neighborhood of 30 RPM / 1,000 RPD / 12,000 TPM for Llama 3.3 70B Versatile specifically. This is not authoritative enough to write into `config/params.yaml` (unlike D-028, which came from the vendor's own source code) — exact model ID strings drift and a wrong one just 400s at runtime. Confirm against the live account before filling in `llm.tiers`.

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
