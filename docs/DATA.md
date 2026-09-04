# DATA.md — market data ingestion, the IV pipeline, and persistence schema

## Overview

Market data is read from Alpaca's REST API with `alpaca-py` (D-008 — the MCP
server is for execution, not bulk data). Everything the system knows that Alpaca
does not store lives in one SQLite file. The centerpiece is `iv_history`, which
exists because Alpaca's option chain has no historical lookup and IV rank is
therefore not obtainable from the API at all (D-015).

## Non-goals

- No tick or quote streaming. Polling only, at the cadences in docs/ARCHITECTURE.md.
- No caching layer beyond the in-process regime cache. Chain snapshots are fetched fresh per scan.
- No data-quality backfill of underlying bars. Alpaca's equity bar history is used as-is.
- No storage of full option chains. Only one ATM IV observation per symbol per snapshot.
- No index options. Alpaca does not currently provide index market data.

## The delayed-feed constraint

**The account is on Alpaca's free tier, which serves the `indicative` options
feed: quotes delayed roughly 15 minutes.** Greeks and IV returned by the chain
endpoint derive from that same delayed feed.

Binding consequences, which must be honored everywhere and not quietly worked
around:

1. Every option quote, mid, delta, and IV is stale by construction. Never treat a chain mid as an executable price.
2. Orders use the price ladder in docs/INTEGRATIONS.md, not a single limit at stale mid (D-022).
3. No entry threshold may be tighter than plausible 15-minute drift. `SHORT_DELTA_BAND` is wide for this reason.
4. Underlying **equity** bars are not subject to the options delay and are the more reliable input. Prefer underlying-derived signals (`realized_vol_20d`, `trend_score`, `range_score`) where a choice exists.

## Alpaca endpoints used

| Purpose | Endpoint | Notes |
|---|---|---|
| Underlying daily bars | `GET /v2/stocks/bars` | 1 year lookback, `timeframe=1Day`, `adjustment=split`, `feed=iex` (D-030 — a free/paper account can't query recent SIP data) |
| Underlying latest trade | `GET /v2/stocks/{symbol}/trades/latest` | `feed=iex`, same reason (D-030) |
| Option chain snapshot | `GET /v1beta1/options/snapshots/{underlying}` | `feed=indicative`; returns latest quote, greeks, IV per contract |
| Historical option bars | `GET /v1beta1/options/bars` | takes a list of OCC symbols + date range; **data begins Feb 2024** |
| Contract metadata / OI | `GET /v2/options/contracts` | one call per contract, open interest lags one day |

Rate limit is generous (roughly 1,000 requests/minute) relative to this system's
cadence. No rate limiter is needed; a simple retry with exponential backoff on
429/5xx is sufficient.

Chain snapshot calls are filtered server-side by expiration and strike range where
the API supports it, to avoid pulling thousands of contracts per symbol:

```python
def fetch_chain(symbol: str, underlying_price: float) -> list[OptionSnapshot]:
    """
    Filter to expirations in [DTE_MIN, DTE_MAX] and strikes within
    STRIKE_RANGE_PCT (0.15) of underlying_price. Pulling the unfiltered
    chain for 7 symbols every 15 minutes is wasteful and slow on a
    free-tier VM.
    """
```

## OCC symbol construction

Required for the backfill, which must name contracts without a historical chain to
enumerate them from.

```
Format:  {ROOT}{YYMMDD}{C|P}{STRIKE * 1000, zero-padded to 8}
Example: SPY   260918     C          00450000   ->  SPY260918C00450000
```

```python
def occ_symbol(root: str, expiry: date, right: Literal["C","P"], strike: float) -> str:
    return f"{root}{expiry:%y%m%d}{right}{int(round(strike * 1000)):08d}"
```

Strike increments differ per symbol and are required to generate valid symbols.
See OPEN_QUESTIONS.md Q-003 — do not guess these.

```yaml
# config/universe.yaml
symbols:
  - {ticker: SPY, cluster: equity_beta, strike_increment: null}  # Q-003
  - {ticker: QQQ, cluster: equity_beta, strike_increment: null}
  - {ticker: IWM, cluster: equity_beta, strike_increment: null}
  - {ticker: GLD, cluster: metals,      strike_increment: null}
  - {ticker: TLT, cluster: duration,    strike_increment: null}
  - {ticker: XLE, cluster: energy,      strike_increment: null}
  - {ticker: HYG, cluster: credit,      strike_increment: null}
```

## IV pipeline

### Live snapshot (`iv_snapshot`, every 15m)

```python
def snapshot_iv(symbol: str) -> None:
    """
    Take one ATM IV observation and append it to iv_history.

    1. Fetch underlying price.
    2. Fetch chain filtered to the expiration nearest ATM_IV_TARGET_DTE (10).
    3. Select the call and put whose strike is nearest underlying_price.
    4. atm_iv = mean of the two implied_volatility values, skipping any that
       are None or <= 0.
    5. Insert one row with source='live'.

    If the chain returns no usable IV (see Q-002), compute it via
    implied_vol_from_price() on the quote mid instead, and still write
    source='live'. Do not skip the observation — gaps in iv_history
    degrade iv_rank for 60 days.
    """
```

One row per symbol per snapshot. Storing whole-chain IV would be a firehose for no
gain — `iv_rank` needs a single consistent ATM series per symbol, and consistency
of the measurement point matters more than its breadth.

### Cold-start backfill (`src/data/backfill.py`)

Runs once at boot when a symbol has fewer than `MIN_IV_OBSERVATIONS` rows (D-016).

```python
BACKFILL_TRADING_DAYS  = 30
ATM_IV_TARGET_DTE      = 10
RISK_FREE_RATE         = 0.04     # D-024

def backfill_symbol(symbol: str) -> int:
    """
    Seed iv_history by inverting Black-Scholes on historical option bars.
    Returns the number of rows written.

    For each of the last BACKFILL_TRADING_DAYS sessions D:
      1. underlying_close = daily bar close for symbol at D
      2. target_expiry    = the listed expiry nearest D + ATM_IV_TARGET_DTE days
                            (weeklies for these ETFs; construct candidate Fridays
                            and keep those that return bars)
      3. strike           = underlying_close rounded to strike_increment
      4. call_sym, put_sym = occ_symbol(...) for that strike and expiry
      5. fetch /v1beta1/options/bars for both symbols on date D
      6. price            = bar close for each leg
      7. iv_call, iv_put  = implied_vol_from_price(...) for each
      8. atm_iv           = mean of successful inversions
      9. INSERT row with source='backfill', observed_at = D at 16:00 ET

    Skip a date silently if bars are missing for both legs — early or illiquid
    expiries will have gaps. Log a warning if fewer than 15 of 30 dates
    produced rows, since iv_rank will be weak.
    """
```

### Black-Scholes inversion

```python
def implied_vol_from_price(
    price: float,            # observed option price
    spot: float,
    strike: float,
    dte_years: float,
    rate: float,             # RISK_FREE_RATE
    right: Literal["C", "P"],
) -> float | None:
    """
    Solve BS(sigma) = price for sigma via Brent's method on [1e-4, 5.0].
    Returns None if the price is outside no-arbitrage bounds or the solver
    does not converge — callers must handle None, never substitute a default.

    Dividends are ignored (D-025). Output is consumed only for within-symbol
    percentile ranking, where a near-constant bias cancels.
    """
```

Use `scipy.optimize.brentq` over a hand-rolled Newton iteration: Newton is
unstable near expiry where vega approaches zero, which is exactly the short-DTE
regime this system operates in.

## SQLite schema

`src/store/schema.sql`, applied on first boot. WAL mode, single writer.

```sql
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
```

## Repository interface

```python
# src/store/repo.py
def insert_iv(symbol: str, observed_at: datetime, atm_iv: float,
              underlying_price: float, dte: int, source: str) -> None: ...
def iv_window(symbol: str, days: int) -> list[tuple[datetime, float]]: ...
def iv_count(symbol: str, days: int) -> int: ...

def log_decision(agent: str, action: str, inputs: dict, output: dict, *,
                 underlying: str | None = None, rationale: str | None = None,
                 model: str | None = None, latency_ms: int | None = None,
                 accepted: bool, veto_reason: str | None = None) -> int: ...

def open_positions() -> list[Position]: ...
def upsert_position(p: Position) -> None: ...
def close_position(position_key: str, reason: str, realized_pnl: float) -> None: ...

def get_hwm() -> float: ...
def set_hwm(value: float) -> None: ...
def halt_state() -> str: ...
def set_halt_state(state: str) -> None: ...
def append_equity_curve(equity: float, cash: float, hwm: float,
                        drawdown_pct: float, open_positions: int,
                        halt_state: str) -> None: ...
```

## Notes

- `position_key` is `sha1` of the sorted OCC symbols joined by `|`. This makes reconciliation matching deterministic and independent of leg ordering returned by Alpaca.
- `entry_credit` is signed: positive for credit structures, negative for debit structures. `max_loss` is always positive dollars per contract.
- `iv_history` has a `UNIQUE (symbol, observed_at, source)` constraint so re-running the backfill is idempotent. Use `INSERT OR IGNORE`.
- The `unknown` structure value and nullable `regime_at_entry` exist solely for adopted orphans, which have no known entry context.
- Never delete from `decision_log`. It is the evidence trail and the fallback state-reconstruction path (D-014).
