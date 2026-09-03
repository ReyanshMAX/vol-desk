"""Live IV snapshotting and rank computation (docs/DATA.md)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src import config as config_module
from src.data import alpaca_data, backfill
from src.store import repo

logger = logging.getLogger("vol_desk.iv")


def snapshot_iv(symbol: str) -> None:
    """Take one ATM IV observation and append it to iv_history. One row per
    symbol per snapshot -- storing the whole chain would be a firehose for
    no gain (docs/DATA.md)."""
    cfg = config_module.load()
    target_dte = cfg.data["atm_iv_target_dte"]
    rate = cfg.data["risk_free_rate"]

    underlying_price = alpaca_data.fetch_latest_price(symbol)
    chain = alpaca_data.fetch_chain(
        symbol, underlying_price,
        dte_min=max(0, target_dte - 5), dte_max=target_dte + 5,
    )
    if not chain:
        logger.info("snapshot_iv(%s): empty chain, skipping this snapshot", symbol)
        return

    # nearest expiration to target_dte
    today = datetime.now(timezone.utc).date()
    by_expiry: dict = {}
    for c in chain:
        by_expiry.setdefault(c.expiration, []).append(c)
    target_expiry = min(by_expiry, key=lambda e: abs((e - today).days - target_dte))
    contracts = by_expiry[target_expiry]

    call = min((c for c in contracts if c.right == "C"),
               key=lambda c: abs(c.strike - underlying_price), default=None)
    put = min((c for c in contracts if c.right == "P"),
              key=lambda c: abs(c.strike - underlying_price), default=None)

    ivs: list[float] = []
    for c in (call, put):
        if c is None:
            continue
        iv = c.implied_volatility
        if iv is None or iv <= 0:
            # Q-002: indicative feed may omit IV -- fall back to inverting
            # the quote mid rather than skipping the observation.
            if c.mid is not None and c.mid > 0:
                dte_years = (c.expiration - today).days / 365.0
                iv = backfill.implied_vol_from_price(
                    c.mid, underlying_price, c.strike, dte_years, rate, c.right)
        if iv is not None and iv > 0:
            ivs.append(iv)

    if not ivs:
        logger.warning("snapshot_iv(%s): no usable IV on either side of the "
                        "chain this snapshot, skipping", symbol)
        return

    atm_iv = sum(ivs) / len(ivs)
    dte = (target_expiry - today).days
    repo.insert_iv(symbol, datetime.now(timezone.utc), atm_iv, underlying_price,
                    dte, source="live")


def iv_rank(symbol: str) -> tuple[float | None, int]:
    """Percentile of the most recent atm_iv within IV_RANK_LOOKBACK_DAYS,
    across both source='live' and source='backfill' rows (D-017).

    Returns (rank, observation_count). rank is None when observation_count
    is below MIN_IV_OBSERVATIONS.
    """
    cfg = config_module.load()
    lookback = cfg.signal["iv_rank_lookback_days"]
    min_obs = cfg.signal["min_iv_observations"]

    window = repo.iv_window(symbol, lookback)
    count = len(window)
    if count < min_obs or count == 0:
        return None, count

    current = window[-1][1]
    below = sum(1 for _, iv in window if iv < current)
    rank = below / count
    return rank, count


def ensure_seeded() -> None:
    """Boot step 5: backfill any symbol with fewer than MIN_IV_OBSERVATIONS
    rows. Runs once; a later boot with a satisfied count is a no-op (no
    re-backfill, per BUILD.md Phase 3 non-goals)."""
    cfg = config_module.load()
    min_obs = cfg.signal["min_iv_observations"]
    lookback = cfg.signal["iv_rank_lookback_days"]
    for sym_cfg in cfg.symbols:
        symbol = sym_cfg.ticker
        count = repo.iv_count(symbol, lookback)
        if count >= min_obs:
            continue
        if sym_cfg.strike_increment is None:
            logger.warning(
                "ensure_seeded(%s): strike_increment unresolved (Q-003), "
                "cannot backfill; iv_rank stays degraded for this symbol", symbol)
            continue
        written = backfill.backfill_symbol(symbol)
        logger.info("ensure_seeded(%s): backfilled %d rows", symbol, written)
