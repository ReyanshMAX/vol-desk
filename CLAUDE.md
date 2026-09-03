# vol-desk — Claude Code Entry Point

## What this is

`vol-desk` is an autonomous options-trading system that runs unattended against an
Alpaca **paper** account. Its edge thesis is volatility selling: implied volatility
tends to price richer than subsequently realized volatility, so the system sells
defined-risk premium structures (credit spreads, iron condors) on a fixed universe
of seven liquid ETFs, and occasionally buys defined-risk debit spreads on
high-conviction directional signals. It is single-user, single-account,
single-process. There is no multi-tenancy, no web service, and no user interface.

The single most important architectural constraint: **the control loop is
deterministic Python; LLM calls are confined to two specific judgment points**
(regime labeling and structure construction). Risk enforcement contains no LLM and
holds veto authority over every order. The second most important constraint: the
Alpaca account is the source of truth for positions and cash, and the local SQLite
file is the source of truth for everything Alpaca does not store (entry credit,
management plan, IV history, high-water mark). The process itself holds no
authoritative state and must be safely restartable at any moment.

## Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | stdlib + a small dependency set, see docs/DEPLOY.md |
| Runtime | single long-lived process, systemd-managed | scheduler in-process, no cron |
| Market data | Alpaca REST via `alpaca-py` | free **indicative** feed, 15-min delayed |
| Execution | Alpaca **MCP server** only | orders, positions, account state |
| Inference | Groq free tier via OpenAI-compatible client | model-agnostic interface |
| Data | SQLite (single file, WAL mode) | no network DB |
| Deploy | generic always-on Linux VM | GCP e2-micro or Oracle Always Free |

## Repo structure

```
vol-desk/
  CLAUDE.md STATUS.md BUILD.md DECISIONS.md OPEN_QUESTIONS.md
  docs/            spec documents, see routing table below
  config/
    universe.yaml  the seven tradable underlyings
    params.yaml    risk and structure parameters, all tunable literals
  src/
    main.py        entrypoint: boot, reconcile, run scheduler
    config.py      typed config loading and validation
    scheduler.py   cadence registry and dispatch
    agents/        regime.py signal.py strategy.py risk.py
    data/          alpaca_data.py iv.py backfill.py
    execution/     mcp_client.py orders.py reconcile.py
    store/         db.py schema.sql repo.py
    llm/           client.py prompts.py schemas.py
  tests/
```

## Where to look

| Working on | Read |
|---|---|
| current state, what to do next | STATUS.md |
| build order, phase acceptance criteria | BUILD.md |
| why something is the way it is | DECISIONS.md |
| a missing or ambiguous requirement | OPEN_QUESTIONS.md |
| agent topology, control loop, cadences, LLM boundary | docs/ARCHITECTURE.md |
| regimes, structure menu, strike/DTE selection, management | docs/STRATEGY.md |
| sizing, drawdown tiers, correlation caps, veto, halts | docs/RISK.md |
| Alpaca endpoints, feed limits, IV pipeline, SQLite DDL | docs/DATA.md |
| MCP execution contract, Groq client, model routing | docs/INTEGRATIONS.md |
| system prompt text, JSON response schemas, validation | docs/PROMPTS.md |
| VM setup, systemd unit, env vars, restart behavior | docs/DEPLOY.md |

## Standing rules

1. **Read STATUS.md first.** It is the current state of the build. Do not infer
   progress from the codebase.
2. **Specs are source of truth.** If the implementation must deviate from a spec
   doc, update that doc in the same change. A stale spec is worse than no spec.
3. **Never invent unspecified behavior.** If a requirement is missing, ambiguous,
   or contradicts another doc, stop and ask. Add it to OPEN_QUESTIONS.md. Do not
   pick a reasonable-sounding default and proceed.
4. **Do not relitigate DECISIONS.md.** Those choices are settled with reasons
   recorded. Reopen one only if new information directly invalidates the stated
   reason, and say which reason and why.

## Two rules specific to this project

5. **Never widen risk to chase fills.** If an order will not fill inside the
   configured price ladder, abandon it. Do not increase size, widen wings, or
   move strikes to make a trade happen.
6. **Every order passes `risk.evaluate()` immediately before submission.** No
   code path may call the MCP order tool directly. See docs/RISK.md.
