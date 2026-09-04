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
    echo "== finding a Python >= 3.11 (enum.StrEnum requires it) =="
    PYBIN=""
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            ver=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
            major=${ver%%.*}
            minor=${ver#*.}
            if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then
                PYBIN="$candidate"
                echo "  using $candidate (Python $ver)"
                break
            fi
        fi
    done
    if [ -z "$PYBIN" ]; then
        echo "FATAL: no Python >= 3.11 found. This codebase uses enum.StrEnum," >&2
        echo "added in 3.11 -- an older interpreter will fail at import time" >&2
        echo "with a confusing error, not a clear one, so this checks first." >&2
        echo "Install one, e.g.:" >&2
        echo "  sudo apt-get install -y python3.11 python3.11-venv" >&2
        echo "(if that package isn't found, your Ubuntu release may need the" >&2
        echo " deadsnakes PPA: sudo add-apt-repository ppa:deadsnakes/ppa)" >&2
        exit 1
    fi
    echo "== creating venv =="
    "$PYBIN" -m venv .venv
fi

echo "== installing dependencies =="
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
    echo "No .env found. Create one (see deploy/vol-desk.env.example) with:"
    echo "  ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_MCP_COMMAND, GROQ_API_KEY"
    exit 1
fi

echo "== starting vol-desk (Ctrl-C to stop) =="
# Belt-and-suspenders: if VOL_DESK_DB/VOL_DESK_CONFIG got exported into
# the calling shell by an earlier `source .env` from before either was
# removed from .env, `source .env` below won't un-export them --
# sourcing only sets what's present in the file, it doesn't clear what's
# absent. Explicitly clear both first so config.py's own (correct)
# relative defaults always win unless this .env sets them on purpose.
unset VOL_DESK_DB VOL_DESK_CONFIG
set -a
source .env
set +a
exec .venv/bin/python -m src.main
