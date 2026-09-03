import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("VOL_DESK_CONFIG", str(REPO_ROOT / "config"))
os.environ.setdefault("ALPACA_API_KEY", "test-key")
os.environ.setdefault("ALPACA_SECRET_KEY", "test-secret")
os.environ.setdefault("ALPACA_MCP_COMMAND", "true")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")


@pytest.fixture()
def db_conn(tmp_path, monkeypatch):
    """A fresh SQLite DB per test, wired into src.store.db / repo."""
    from src.store import db as db_module
    db_path = tmp_path / "test.db"
    conn = db_module.connect(str(db_path))
    yield conn
    conn.close()
