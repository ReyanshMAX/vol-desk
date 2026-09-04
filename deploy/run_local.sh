#!/usr/bin/env bash
# Local run helper for a personal machine (e.g. WSL2 on Windows), as an
# alternative to deploy/provision.sh + systemd on a dedicated VM. No
# separate system user, no systemd -- just a venv and a foreground process
# you start before market open and stop after close.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

if [ ! -d .venv ]; then
    echo "== creating venv =="
    python3.11 -m venv .venv || python3 -m venv .venv
fi

echo "== installing dependencies =="
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
    echo "No .env found. Create one (see deploy/vol-desk.env.example) with:"
    echo "  ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_MCP_COMMAND, GROQ_API_KEY"
    exit 1
fi

echo "== starting vol-desk (Ctrl-C to stop) =="
set -a
source .env
set +a
exec .venv/bin/python -m src.main
