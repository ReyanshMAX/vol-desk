#!/usr/bin/env bash
# Provisioning script for docs/DEPLOY.md. Run as root (or with sudo) on a
# fresh Ubuntu 22.04/24.04 LTS VM. Idempotent-ish: safe to re-run, but it
# does not undo anything -- read it before running on a host you care about.
#
# This only gets you to "service installed and enabled." It does NOT start
# the service for you: Q-001/Q-003/Q-004 (see OPEN_QUESTIONS.md) must be
# resolved and /etc/vol-desk.env filled in first, or vol-desk will fail
# loudly on boot per its config validation (by design -- see CLAUDE.md
# rule 3, config.py never invents a value).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ReyanshMAX/vol-desk}"
REPO_BRANCH="${REPO_BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/home/voldesk/vol-desk}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== apt packages =="
apt-get update
apt-get install -y python3.11 python3.11-venv python3-pip git sqlite3

echo "== voldesk system user =="
if ! id -u voldesk >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin voldesk
fi

echo "== uv (needed to run the Alpaca MCP server via 'uvx alpaca-mcp-server', D-028) =="
if ! sudo -u voldesk bash -c 'command -v uv' >/dev/null 2>&1; then
    sudo -u voldesk bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

echo "== clone/update repo =="
if [ -d "$INSTALL_DIR/.git" ]; then
    sudo -u voldesk git -C "$INSTALL_DIR" fetch origin
    sudo -u voldesk git -C "$INSTALL_DIR" checkout "$REPO_BRANCH"
    sudo -u voldesk git -C "$INSTALL_DIR" pull origin "$REPO_BRANCH"
else
    sudo -u voldesk git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

echo "== virtualenv + dependencies =="
sudo -u voldesk python3.11 -m venv "$INSTALL_DIR/.venv"
sudo -u voldesk "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo "== host timezone (must be UTC -- docs/DEPLOY.md notes) =="
timedatectl set-timezone UTC

echo "== environment file =="
if [ ! -f /etc/vol-desk.env ]; then
    install -m 600 -o root -g root "$SCRIPT_DIR/vol-desk.env.example" /etc/vol-desk.env
    echo "  created /etc/vol-desk.env from the example -- fill in the blanks before starting the service"
else
    echo "  /etc/vol-desk.env already exists, leaving it alone"
fi

echo "== systemd unit =="
install -m 644 "$SCRIPT_DIR/vol-desk.service" /etc/systemd/system/vol-desk.service
systemctl daemon-reload
systemctl enable vol-desk

cat <<'EOF'

Provisioning done. Before starting:
  1. Fill in /etc/vol-desk.env (ALPACA_API_KEY, ALPACA_SECRET_KEY,
     ALPACA_MCP_COMMAND, GROQ_API_KEY)
  2. Resolve OPEN_QUESTIONS.md Q-001, Q-003, Q-004 and fill in
     config/universe.yaml + config/params.yaml accordingly
  3. sudo systemctl start vol-desk
  4. sudo journalctl -u vol-desk -f
EOF
