# vol-desk

vol-desk sells options premium on seven ETFs — SPY, QQQ, IWM, GLD, TLT, XLE,
and HYG — on the idea that implied volatility usually prices above what
actually plays out. A deterministic layer computes IV rank, realized
volatility, and trend scores to figure out what's even worth trading. From
there, an LLM labels the volatility regime for each symbol and picks the
actual contracts once a structure is unlocked: credit spreads, iron
condors, or occasionally a debit spread when trend is strong and IV is
cheap. Every trade is capped risk, no naked legs.

Before anything reaches Alpaca, a risk engine runs eight checks — drawdown
against the account's high-water mark, position and correlation caps,
sizing, cash headroom, DTE window — and can size down, veto, or halt a
trade. A hard drawdown halt has to be cleared manually rather than
resetting itself.

Alpaca is the source of truth for positions and cash; a local SQLite
database tracks everything else — entry credit, the management plan, IV
history, the high-water mark. The whole thing runs as one long-lived
process that reconciles state on every boot, so it can restart safely at
any time. All trades are placed via the [Alpaca MCP server](https://github.com/alpacahq/alpaca-mcp-server).

Runs against Alpaca **paper trading** only — this is a research and
engineering project, not investment advice.

## How it decides

| Regime | Unlocks |
|---|---|
| Range · high IV | Iron condor, put credit spread, call credit spread |
| Range · low IV | Nothing — standing down is a normal outcome |
| Trend up · high IV | Put credit spread |
| Trend down · high IV | Call credit spread |
| Trend up · low IV | Call debit spread |
| Trend down · low IV | Put debit spread |
| Stress (IV rank ≥ 0.90 or 20d realized vol ≥ 0.45) | Nothing — existing positions still managed |

Regime is classified by a mechanical rule first; the LLM receives that
verdict plus the rule itself and may deviate only with a stated reason.
Every contract the strategy LLM proposes is independently recomputed in
code afterward.

## Risk limits

| | |
|---|---|
| Max risk per trade | 1% of equity |
| Soft drawdown halt (blocks new entries) | 5% from high-water mark |
| Hard drawdown halt (flattens everything, manual clear) | 10% from high-water mark |
| Max concurrent positions | 6 |
| Max positions per underlying | 1 |
| Equity-beta cluster cap (SPY+QQQ+IWM) | 3 |
| Take-profit | 50% of credit captured |
| Stop-loss | 2× entry credit |
| Force-close | 2 DTE, regardless of P/L |

## Repo layout

```
CLAUDE.md STATUS.md BUILD.md DECISIONS.md OPEN_QUESTIONS.md
docs/            architecture, strategy, risk, data, integrations, deploy specs
config/          universe.yaml (the 7 symbols), params.yaml (every tunable literal)
src/
  main.py        entrypoint: boot, reconcile, run scheduler
  agents/        regime.py signal.py strategy.py risk.py
  data/          alpaca_data.py iv.py backfill.py
  execution/     mcp_client.py orders.py reconcile.py
  store/         db.py schema.sql repo.py
  llm/           client.py prompts.py schemas.py
tests/
dashboard/       Next.js dashboard + slide deck, deployed separately (see below)
deploy/          systemd unit, provisioning script, local run helper
```

`CLAUDE.md` is the entry point for the spec docs and explains where to look
for what. `DECISIONS.md` records settled architectural calls with reasons;
`OPEN_QUESTIONS.md` tracks anything still ambiguous.

## Running it

Requires Python 3.11+, an Alpaca paper account, and a free Groq API key.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

cp deploy/vol-desk.env.example .env
# fill in ALPACA_API_KEY, ALPACA_SECRET_KEY, GROQ_API_KEY

set -a; source .env; set +a
python -m src.main
```

`deploy/run_local.sh` wraps this for local/foreground runs.
`deploy/provision.sh` and `deploy/vol-desk.service` set it up as a
systemd-managed always-on process — see `docs/DEPLOY.md`.

On first boot, `strike_increment` in `config/universe.yaml` and the Groq
model IDs in `config/params.yaml` are resolved live against the real APIs
(see `scripts/resolve_open_questions.py`) rather than guessed.

## Dashboard

`dashboard/` is a separate Next.js app that reads live account, position,
and order data directly from Alpaca's REST API and renders it — equity,
P/L, open positions, activity, an equity curve, a "how it works" page, and
a slide deck at `/deck`. It's deployed independently on Vercel and needs
its own `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` set in that project's
environment variables.

```bash
cd dashboard
npm install
npm run dev
```
