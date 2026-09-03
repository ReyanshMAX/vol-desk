"""Cold-start IV backfill (D-016): seed iv_history by inverting Black-Scholes
on historical option bars before any live observation exists.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

import pandas_market_calendars as mcal
from scipy.optimize import brentq
from scipy.stats import norm

from src import config as config_module
from src.data import alpaca_data
from src.store import repo

logger = logging.getLogger("vol_desk.backfill")

_NYSE = mcal.get_calendar("NYSE")


def occ_symbol(root: str, expiry: date, right: Literal["C", "P"], strike: float) -> str:
    return f"{root}{expiry:%y%m%d}{right}{int(round(strike * 1000)):08d}"


def _bs_price(spot: float, strike: float, dte_years: float, rate: float,
              sigma: float, right: Literal["C", "P"]) -> float:
    if dte_years <= 0 or sigma <= 0:
        intrinsic = max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
        return intrinsic
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma ** 2) * dte_years) / (sigma * math.sqrt(dte_years))
    d2 = d1 - sigma * math.sqrt(dte_years)
    if right == "C":
        return spot * norm.cdf(d1) - strike * math.exp(-rate * dte_years) * norm.cdf(d2)
    return strike * math.exp(-rate * dte_years) * norm.cdf(-d2) - spot * norm.cdf(-d1)


def implied_vol_from_price(price: float, spot: float, strike: float,
                            dte_years: float, rate: float,
                            right: Literal["C", "P"]) -> float | None:
    """Solve BS(sigma) = price for sigma via Brent's method on [1e-4, 5.0].

    Returns None if price is outside no-arbitrage bounds or the solver does
    not converge (D-025: dividends ignored, bias cancels in within-symbol
    ranking). Callers must handle None -- never substitute a default.
    """
    if price <= 0 or dte_years <= 0:
        return None
    intrinsic = max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
    upper_bound = spot if right == "C" else strike
    if price < intrinsic or price >= upper_bound:
        return None

    def f(sigma: float) -> float:
        return _bs_price(spot, strike, dte_years, rate, sigma, right) - price

    try:
        lo, hi = 1e-4, 5.0
        if f(lo) * f(hi) > 0:
            return None
        return brentq(f, lo, hi, xtol=1e-6, maxiter=200)
    except (ValueError, RuntimeError):
        return None


def _recent_trading_days(n: int, before: date) -> list[date]:
    schedule = _NYSE.schedule(start_date=before - timedelta(days=n * 3), end_date=before)
    days = [d.date() for d in schedule.index]
    return days[-n:]


def _candidate_expiries(from_date: date, target_dte: int) -> list[date]:
    """Weekly listings on these ETFs are Fridays. Build a small window of
    candidate Fridays around from_date + target_dte and let the caller keep
    whichever actually returns bars."""
    target = from_date + timedelta(days=target_dte)
    # nearest Friday on or after target, plus the Friday before, to bracket it
    days_to_friday = (4 - target.weekday()) % 7
    friday = target + timedelta(days=days_to_friday)
    return [friday - timedelta(days=7), friday, friday + timedelta(days=7)]


def backfill_symbol(symbol: str) -> int:
    """Seed iv_history for one symbol. Returns rows written. Idempotent via
    INSERT OR IGNORE on (symbol, observed_at, source)."""
    cfg = config_module.load()
    sym_cfg = cfg.symbol(symbol)
    if sym_cfg.strike_increment is None:
        raise config_module.ConfigError(
            f"universe.yaml: {symbol}.strike_increment is unresolved (Q-003); "
            "cannot construct OCC symbols for backfill"
        )
    increment = sym_cfg.strike_increment
    target_dte = cfg.data["atm_iv_target_dte"]
    trading_days_n = cfg.data["backfill_trading_days"]
    rate = cfg.data["risk_free_rate"]

    today = date.today()
    days = _recent_trading_days(trading_days_n, today)
    bars = {b.ts: b.close for b in alpaca_data.fetch_daily_bars(symbol, days=trading_days_n + 10)}

    rows_written = 0
    for d in days:
        underlying_close = bars.get(d)
        if underlying_close is None:
            continue
        strike = round(underlying_close / increment) * increment

        target_expiry = None
        call_sym = put_sym = None
        for candidate in _candidate_expiries(d, target_dte):
            c_sym = occ_symbol(symbol, candidate, "C", strike)
            p_sym = occ_symbol(symbol, candidate, "P", strike)
            found = alpaca_data.fetch_option_bars([c_sym, p_sym], d)
            if found:
                target_expiry = candidate
                call_sym, put_sym = c_sym, p_sym
                found_prices = found
                break
        if target_expiry is None:
            continue  # skip silently -- thin or unlisted expiry

        dte_years = (target_expiry - d).days / 365.0
        ivs = []
        for occ, right in ((call_sym, "C"), (put_sym, "P")):
            price = found_prices.get(occ)
            if price is None:
                continue
            iv = implied_vol_from_price(price, underlying_close, strike, dte_years, rate, right)  # type: ignore[arg-type]
            if iv is not None:
                ivs.append(iv)
        if not ivs:
            continue

        atm_iv = sum(ivs) / len(ivs)
        observed_at = datetime.combine(d, time(16, 0), tzinfo=timezone.utc)
        repo.insert_iv(symbol, observed_at, atm_iv, underlying_close,
                        (target_expiry - d).days, source="backfill")
        rows_written += 1

    if rows_written < 15:
        logger.warning("backfill_symbol(%s) only produced %d/%d rows; "
                        "iv_rank will be weak until live observations accumulate",
                        symbol, rows_written, trading_days_n)
    return rows_written
