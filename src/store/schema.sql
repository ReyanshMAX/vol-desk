-- Verbatim from docs/DATA.md. Applied on first boot (empty DB) only.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS iv_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol            TEXT    NOT NULL,
    observed_at       TEXT    NOT NULL,           -- ISO8601 UTC
    atm_iv            REAL    NOT NULL CHECK (atm_iv > 0),
    underlying_price  REAL    NOT NULL,
    dte               INTEGER NOT NULL,
    source            TEXT    NOT NULL CHECK (source IN ('live','backfill')),
    UNIQUE (symbol, observed_at, source)
);
CREATE INDEX IF NOT EXISTS idx_iv_symbol_time ON iv_history (symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS decision_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    agent         TEXT    NOT NULL CHECK (agent IN ('regime','signal','strategy','risk','supervisor','manager')),
    action        TEXT    NOT NULL,
    underlying    TEXT,
    inputs_json   TEXT    NOT NULL,
    output_json   TEXT    NOT NULL,
    rationale     TEXT,
    model         TEXT,
    latency_ms    INTEGER,
    accepted      INTEGER NOT NULL CHECK (accepted IN (0,1)),
    veto_reason   TEXT
);
CREATE INDEX IF NOT EXISTS idx_decision_ts    ON decision_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_decision_agent ON decision_log (agent, ts DESC);

CREATE TABLE IF NOT EXISTS positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_key        TEXT    NOT NULL UNIQUE,  -- sha1 of sorted occ_symbols
    underlying          TEXT    NOT NULL,
    structure           TEXT    NOT NULL CHECK (structure IN
                          ('put_credit_spread','call_credit_spread','iron_condor',
                           'put_debit_spread','call_debit_spread','unknown')),
    legs_json           TEXT    NOT NULL,         -- [{occ_symbol, side, ratio, strike, right}]
    qty                 INTEGER NOT NULL CHECK (qty > 0),
    opened_at           TEXT    NOT NULL,
    expiration          TEXT    NOT NULL,         -- ISO date
    entry_credit        REAL    NOT NULL,         -- per contract; negative for debit structures
    max_loss            REAL    NOT NULL,         -- per contract, dollars
    take_profit_value   REAL    NOT NULL,         -- close-cost trigger
    stop_loss_value     REAL    NOT NULL,
    state               TEXT    NOT NULL CHECK (state IN
                          ('opening','open','closing','closed','orphan')),
    regime_at_entry     TEXT,
    decision_id         INTEGER REFERENCES decision_log(id),
    closed_at           TEXT,
    close_reason        TEXT CHECK (close_reason IN
                          ('take_profit','stop_loss','force_close_dte','hard_halt','manual',NULL)),
    realized_pnl        REAL
);
CREATE INDEX IF NOT EXISTS idx_positions_state ON positions (state);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions (underlying, state);

CREATE TABLE IF NOT EXISTS equity_curve (
    ts                TEXT    PRIMARY KEY,
    equity            REAL    NOT NULL,
    cash              REAL    NOT NULL,
    high_water_mark   REAL    NOT NULL,
    drawdown_pct      REAL    NOT NULL,
    open_positions    INTEGER NOT NULL,
    halt_state        TEXT    NOT NULL CHECK (halt_state IN ('normal','soft_halt','hard_halt'))
);

CREATE TABLE IF NOT EXISTS system_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
-- seeded keys: high_water_mark, halt_state, schema_version
