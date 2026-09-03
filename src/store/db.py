"""SQLite connection management. Single file, WAL mode, single writer.

Boot step 2 (docs/ARCHITECTURE.md): open the DB and apply schema.sql if the
database is empty. Restart must append to the existing file, never recreate
it (BUILD.md Phase 1 acceptance criteria).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def connect(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite file at db_path and apply schema.sql.

    Idempotent: schema.sql uses CREATE TABLE IF NOT EXISTS throughout, so
    calling this against an existing populated DB is a safe no-op beyond the
    PRAGMA statements.
    """
    global _conn
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        conn.executescript(_SCHEMA_PATH.read_text())
    _seed_system_state(conn)
    _conn = conn
    return conn


def _seed_system_state(conn: sqlite3.Connection) -> None:
    now = _now_iso()
    seeds = {
        "high_water_mark": "0.0",
        "halt_state": "normal",
        "schema_version": "1",
    }
    with conn:
        for key, value in seeds.items():
            conn.execute(
                "INSERT OR IGNORE INTO system_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (key, value, now),
            )


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def get() -> sqlite3.Connection:
    """Return the process-wide connection. Must be called after connect()."""
    if _conn is None:
        raise RuntimeError("db.connect() has not been called yet")
    return _conn


def write_lock() -> threading.Lock:
    """Single-writer discipline: the scheduler is single-threaded, but any
    code that might run off that thread (e.g. a future background poller)
    must serialize writes through this lock."""
    return _lock
