"""Repository interface per docs/DATA.md. All SQLite access goes through
this module -- no other module opens a cursor directly.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.store import db


@dataclass(frozen=True)
class Leg:
    occ_symbol: str
    side: str        # "buy" | "sell"
    ratio: int
    strike: float
    right: str        # "C" | "P"


@dataclass(frozen=True)
class Position:
    id: int
    position_key: str
    underlying: str
    structure: str
    legs: list[Leg]
    qty: int
    opened_at: str
    expiration: str
    entry_credit: float
    max_loss: float
    take_profit_value: float
    stop_loss_value: float
    state: str
    regime_at_entry: str | None
    decision_id: int | None
    closed_at: str | None
    close_reason: str | None
    realized_pnl: float | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_position(row: sqlite3.Row) -> Position:
    legs = [Leg(**leg) for leg in json.loads(row["legs_json"])]
    return Position(
        id=row["id"],
        position_key=row["position_key"],
        underlying=row["underlying"],
        structure=row["structure"],
        legs=legs,
        qty=row["qty"],
        opened_at=row["opened_at"],
        expiration=row["expiration"],
        entry_credit=row["entry_credit"],
        max_loss=row["max_loss"],
        take_profit_value=row["take_profit_value"],
        stop_loss_value=row["stop_loss_value"],
        state=row["state"],
        regime_at_entry=row["regime_at_entry"],
        decision_id=row["decision_id"],
        closed_at=row["closed_at"],
        close_reason=row["close_reason"],
        realized_pnl=row["realized_pnl"],
    )


# --- iv_history ---------------------------------------------------------

def insert_iv(symbol: str, observed_at: datetime, atm_iv: float,
              underlying_price: float, dte: int, source: str) -> None:
    conn = db.get()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO iv_history "
            "(symbol, observed_at, atm_iv, underlying_price, dte, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, observed_at.isoformat(), atm_iv, underlying_price, dte, source),
        )


def iv_window(symbol: str, days: int) -> list[tuple[datetime, float]]:
    conn = db.get()
    cutoff = _cutoff_iso(days)
    rows = conn.execute(
        "SELECT observed_at, atm_iv FROM iv_history "
        "WHERE symbol = ? AND observed_at >= ? ORDER BY observed_at ASC",
        (symbol, cutoff),
    ).fetchall()
    return [(datetime.fromisoformat(r["observed_at"]), r["atm_iv"]) for r in rows]


def iv_count(symbol: str, days: int) -> int:
    conn = db.get()
    cutoff = _cutoff_iso(days)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM iv_history WHERE symbol = ? AND observed_at >= ?",
        (symbol, cutoff),
    ).fetchone()
    return row["n"]


def _cutoff_iso(days: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# --- decision_log --------------------------------------------------------

def log_decision(agent: str, action: str, inputs: dict, output: dict, *,
                  underlying: str | None = None, rationale: str | None = None,
                  model: str | None = None, latency_ms: int | None = None,
                  accepted: bool, veto_reason: str | None = None) -> int:
    conn = db.get()
    with conn:
        cur = conn.execute(
            "INSERT INTO decision_log "
            "(ts, agent, action, underlying, inputs_json, output_json, "
            " rationale, model, latency_ms, accepted, veto_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(), agent, action, underlying,
                json.dumps(inputs, default=str), json.dumps(output, default=str),
                rationale, model, latency_ms, int(accepted), veto_reason,
            ),
        )
        return cur.lastrowid


# --- positions -------------------------------------------------------------

def open_positions() -> list[Position]:
    conn = db.get()
    rows = conn.execute(
        "SELECT * FROM positions WHERE state IN ('opening','open','closing','orphan')"
    ).fetchall()
    return [_row_to_position(r) for r in rows]


def upsert_position(p: Position) -> None:
    conn = db.get()
    legs_json = json.dumps([leg.__dict__ for leg in p.legs])
    with conn:
        conn.execute(
            "INSERT INTO positions "
            "(position_key, underlying, structure, legs_json, qty, opened_at, "
            " expiration, entry_credit, max_loss, take_profit_value, "
            " stop_loss_value, state, regime_at_entry, decision_id, "
            " closed_at, close_reason, realized_pnl) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(position_key) DO UPDATE SET "
            " qty=excluded.qty, state=excluded.state, "
            " take_profit_value=excluded.take_profit_value, "
            " stop_loss_value=excluded.stop_loss_value, "
            " closed_at=excluded.closed_at, close_reason=excluded.close_reason, "
            " realized_pnl=excluded.realized_pnl",
            (
                p.position_key, p.underlying, p.structure, legs_json, p.qty,
                p.opened_at, p.expiration, p.entry_credit, p.max_loss,
                p.take_profit_value, p.stop_loss_value, p.state,
                p.regime_at_entry, p.decision_id, p.closed_at, p.close_reason,
                p.realized_pnl,
            ),
        )


def close_position(position_key: str, reason: str, realized_pnl: float) -> None:
    conn = db.get()
    with conn:
        conn.execute(
            "UPDATE positions SET state='closed', close_reason=?, "
            "realized_pnl=?, closed_at=? WHERE position_key=?",
            (reason, realized_pnl, _now_iso(), position_key),
        )


def get_position(position_key: str) -> Position | None:
    conn = db.get()
    row = conn.execute(
        "SELECT * FROM positions WHERE position_key = ?", (position_key,)
    ).fetchone()
    return _row_to_position(row) if row else None


def opened_today_count() -> int:
    conn = db.get()
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM positions WHERE opened_at >= ? AND state != 'orphan'",
        (today,),
    ).fetchone()
    return row["n"]


# --- system_state / halt / hwm -------------------------------------------

def _get_state(key: str) -> str | None:
    conn = db.get()
    row = conn.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(key: str, value: str) -> None:
    conn = db.get()
    with conn:
        conn.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, _now_iso()),
        )


def get_hwm() -> float:
    value = _get_state("high_water_mark")
    return float(value) if value is not None else 0.0


def set_hwm(value: float) -> None:
    _set_state("high_water_mark", str(value))


def halt_state() -> str:
    return _get_state("halt_state") or "normal"


def set_halt_state(state: str) -> None:
    _set_state("halt_state", state)


# --- equity_curve ----------------------------------------------------------

def append_equity_curve(equity: float, cash: float, hwm: float,
                         drawdown_pct: float, open_positions: int,
                         halt_state: str) -> None:
    conn = db.get()
    with conn:
        conn.execute(
            "INSERT INTO equity_curve "
            "(ts, equity, cash, high_water_mark, drawdown_pct, open_positions, halt_state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now_iso(), equity, cash, hwm, drawdown_pct, open_positions, halt_state),
        )
