# DEPLOY.md — host setup, service management, and redeploy behavior

## Overview

The system runs as a single systemd-managed Python process on a generic always-on
Linux VM. Nothing in the architecture depends on the host (D-029, formerly D-011)
— this document is the only place a host is named, and swapping providers changes
provisioning commands and nothing else.

**Current host (D-029):** a personal Windows machine via WSL2, run in the
foreground around market hours rather than as an always-on systemd daemon — see
"WSL2 (personal machine)" below. D-011's always-on-VM plan required a card for
identity verification on every free-tier provider checked (GCP, Oracle, Fly.io);
none was available, and the remaining "no card" VPS options weren't trustworthy
enough to hold live trading credentials. The systemd/VM path below is kept as the
target architecture and remains fully valid if a card becomes available later —
nothing about the application changes, only how it's launched and supervised.

The commands and files below are also captured as ready-to-run artifacts under
`deploy/`: `provision.sh` (the provisioning steps, idempotent-ish), `vol-desk.service`
(the systemd unit verbatim), and `vol-desk.env.example` (the environment file
template). Running `provision.sh` gets a fresh VM to "service installed and
enabled" — it does not start the service, since that still needs Q-001/Q-003/Q-004
resolved and the env file filled in first.

## Non-goals

- No containers. A single Python process with a virtualenv on a micro instance does not benefit from Docker, and the image pull is a real cost on a constrained free-tier disk.
- No CI/CD pipeline. Deployment is `git pull` and `systemctl restart`.
- No reverse proxy, TLS, or inbound networking. The process makes only outbound calls.
- No log shipping or external monitoring. Logs go to journald; the decision trail is in SQLite.
- No secrets manager. Environment variables in a root-owned file.

## Host

Any always-on Linux VM with roughly 1 vCPU, 1 GB RAM, and 10 GB disk. The workload
is network-bound: a handful of REST calls per minute, occasional LLM requests, and
small SQLite writes.

| Candidate | Notes |
|---|---|
| **GCP e2-micro** (primary) | free tier, us-west1/us-central1/us-east1 only; signup generally completes without manual review |
| **Oracle Always Free** (fallback) | more generous specs; account approval is inconsistent and can be rejected or reclaimed |

Pick whichever provisions successfully and record it in STATUS.md. Do not
re-architect for either.

## WSL2 (personal machine)

D-029's actual current host: no dedicated VM, no systemd. The process runs in
the foreground inside WSL2 (Ubuntu) on a Windows machine, started before market
open and stopped after close. This trades unattended operation for zero cost and
zero card requirement — see D-029's stated tradeoff: an open position goes
unmanaged (no take-profit/stop/halt) for however long the machine is off.
Restarting is still safe (D-013: `reconcile.run()` rebuilds state from Alpaca +
SQLite on every boot), it's just not continuous.

```powershell
# in an elevated (Administrator) PowerShell, one-time setup
wsl --install -d Ubuntu
# reboot if prompted, then open the new "Ubuntu" app from the Start menu
```

Inside the Ubuntu WSL shell, one-time setup:

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip git sqlite3
curl -LsSf https://astral.sh/uv/install.sh | sh   # for `uvx alpaca-mcp-server`

git clone https://github.com/ReyanshMAX/vol-desk ~/vol-desk
cd ~/vol-desk
cp deploy/vol-desk.env.example .env
nano .env   # fill in ALPACA_API_KEY, ALPACA_SECRET_KEY, GROQ_API_KEY
```

Each trading day, before market open:

```bash
cd ~/vol-desk && bash deploy/run_local.sh
```

`run_local.sh` creates the venv on first run, installs dependencies, sources
`.env`, and runs `python -m src.main` in the foreground — `Ctrl-C` to stop after
close. `.env` is gitignored; it is never committed (see CLAUDE.md's git safety
notes — a `.env` was accidentally committed and pushed once during this build
and had to be rotated and purged from history; don't repeat that).

## Provisioning

```bash
# Ubuntu 22.04 LTS or 24.04 LTS
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip git sqlite3

sudo useradd --system --create-home --shell /usr/sbin/nologin voldesk
sudo -u voldesk git clone <repo-url> /home/voldesk/vol-desk
cd /home/voldesk/vol-desk

sudo -u voldesk python3.11 -m venv .venv
sudo -u voldesk .venv/bin/pip install -r requirements.txt

# timezone matters: the scheduler computes market hours in US/Eastern
sudo timedatectl set-timezone UTC
```

### Dependencies

```
# requirements.txt
alpaca-py>=0.21
mcp>=1.0
openai>=1.40          # OpenAI-compatible client pointed at Groq
scipy>=1.11           # brentq for BS inversion
numpy>=1.26
pydantic>=2.7
pyyaml>=6.0
pandas-market-calendars>=4.4   # NYSE session calendar
```

No database server, no web framework, no async runtime beyond what the MCP client
requires.

## Environment file

```bash
sudo install -m 600 -o root -g root /dev/null /etc/vol-desk.env
sudo tee /etc/vol-desk.env >/dev/null <<'EOF'
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_MCP_COMMAND=
GROQ_API_KEY=
VOL_DESK_DB=/home/voldesk/vol-desk/vol-desk.db
VOL_DESK_CONFIG=/home/voldesk/vol-desk/config
LOG_LEVEL=INFO
EOF
```

Variable meanings are in docs/INTEGRATIONS.md. Mode 600, root-owned; systemd reads
it before dropping privileges.

## systemd unit

```ini
# /etc/systemd/system/vol-desk.service
[Unit]
Description=vol-desk autonomous options trading agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=voldesk
WorkingDirectory=/home/voldesk/vol-desk
EnvironmentFile=/etc/vol-desk.env
ExecStart=/home/voldesk/vol-desk/.venv/bin/python -m src.main
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal
SyslogIdentifier=vol-desk

# a crash loop must not hammer Alpaca or Groq
StartLimitIntervalSec=300
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vol-desk
sudo journalctl -u vol-desk -f
```

## Redeploy

```bash
sudo -u voldesk git -C /home/voldesk/vol-desk pull
sudo -u voldesk /home/voldesk/vol-desk/.venv/bin/pip install -r requirements.txt
sudo systemctl restart vol-desk
```

Downtime is a few seconds. This is safe at any time because the process holds no
authoritative state (D-013): on restart, `reconcile.run()` rebuilds the picture
from Alpaca positions plus the SQLite tables, and management resumes.

**Restarting does not reset the paper account.** Positions, cash, and order
history live on Alpaca's servers and are untouched by a redeploy. Resetting the
paper account is a deliberate action in the Alpaca dashboard, and doing so orphans
every row in `positions` — after a reset, wipe the `positions` table and the
`high_water_mark` key or reconciliation will spend a boot marking everything as
GHOST.

Redeploying while an order is mid-ladder leaves an unfilled limit order at
Alpaca. `reconcile` classifies it as `IN_FLIGHT` and resolves it from
`orders_list`. Preferably restart outside the `entry_scan` window.

## Operational commands

```bash
# state of the world
sudo systemctl status vol-desk
sudo journalctl -u vol-desk --since "1 hour ago"

# what has the system been deciding
sqlite3 $VOL_DESK_DB \
  "SELECT ts, agent, action, underlying, accepted, veto_reason
   FROM decision_log ORDER BY ts DESC LIMIT 40;"

# equity and halt state
sqlite3 $VOL_DESK_DB \
  "SELECT ts, equity, drawdown_pct, halt_state FROM equity_curve
   ORDER BY ts DESC LIMIT 20;"

# open book
sqlite3 $VOL_DESK_DB \
  "SELECT underlying, structure, qty, entry_credit, expiration, state
   FROM positions WHERE state IN ('open','orphan');"

# clear a hard halt (deliberate, manual, after investigating)
sqlite3 $VOL_DESK_DB \
  "UPDATE system_state SET value='normal', updated_at=datetime('now')
   WHERE key='halt_state';"
sudo systemctl restart vol-desk
```

## Backup

```bash
# SQLite is the only durable local state; back it up before risky changes
sudo -u voldesk sqlite3 $VOL_DESK_DB ".backup '/home/voldesk/vol-desk-$(date +%F).db'"
```

`iv_history` is the expensive table to lose — rebuilding it means re-running the
backfill and waiting for live observations to re-accumulate.

## Notes

- Host clock set to UTC; the scheduler converts to US/Eastern for market hours. Do not set the host timezone to Eastern — DST transitions in the host clock are a worse failure than a conversion in code.
- `Restart=always` with `StartLimitBurst=5` over 300s means a persistent failure (bad credentials, unreachable MCP) stops retrying instead of hammering the APIs.
- The service runs as an unprivileged user with no inbound ports. There is no reason to expose anything.
- On a 1 GB instance, watch memory during the backfill: it fetches option bars for 30 dates across 7 symbols. Process one symbol at a time and do not hold all responses in memory simultaneously.
