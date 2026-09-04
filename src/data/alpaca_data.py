"""Bulk market data via alpaca-py REST (D-008 -- MCP is for execution only,
this module is data only; never place an order from here).

Free tier serves the `indicative` options feed, delayed ~15 minutes
(docs/DATA.md). Underlying equity bars are not subject to that delay.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    OptionChainRequest,
    OptionBarsRequest,
    StockBarsRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest

logger = logging.getLogger("vol_desk.alpaca_data")

STRIKE_RANGE_PCT_DEFAULT = 0.15


@dataclass(frozen=True)
class Bar:
    ts: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OptionSnapshot:
    occ_symbol: str
    underlying: str
    expiration: date
    strike: float
    right: Literal["C", "P"]
    bid: float | None
    ask: float | None
    mid: float | None
    delta: float | None
    implied_volatility: float | None  # None if the indicative feed omits it, see Q-002
    open_interest: int | None = None  # filled separately, shortlisted legs only


def _clients() -> tuple[StockHistoricalDataClient, OptionHistoricalDataClient, TradingClient]:
    api_key = os.environ["ALPACA_API_KEY"]
    secret_key = os.environ["ALPACA_SECRET_KEY"]
    stock = StockHistoricalDataClient(api_key, secret_key)
    option = OptionHistoricalDataClient(api_key, secret_key)
    trading = TradingClient(api_key, secret_key, paper=True)
    return stock, option, trading


def fetch_daily_bars(symbol: str, days: int) -> list[Bar]:
    """GET /v2/stocks/bars, timeframe=1Day, adjustment=split, `days` lookback.

    feed=DataFeed.IEX explicitly: alpaca-py's default (SIP) requires a paid
    subscription for recent data and 403s with "subscription does not
    permit querying recent SIP data" on a free/paper account -- confirmed
    live 2026-09-03. IEX is the free-tier feed."""
    stock, _, _ = _clients()
    end = datetime.utcnow()
    start = end - timedelta(days=days * 2)  # pad for weekends/holidays
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        adjustment="split",
        feed=DataFeed.IEX,
    )
    bar_set = stock.get_stock_bars(req)
    bars = [
        Bar(ts=b.timestamp.date(), open=b.open, high=b.high, low=b.low,
            close=b.close, volume=b.volume)
        for b in bar_set[symbol]
    ]
    return bars[-days:]


def fetch_latest_price(symbol: str) -> float:
    """Latest trade price for the underlying. Equity trades are not subject
    to the options feed's 15-minute delay (docs/DATA.md). feed=DataFeed.IEX
    for the same reason as fetch_daily_bars -- SIP needs a paid subscription."""
    stock, _, _ = _clients()
    req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
    trades = stock.get_stock_latest_trade(req)
    return float(trades[symbol].price)


def fetch_chain(symbol: str, underlying_price: float, *,
                 dte_min: int, dte_max: int,
                 strike_range_pct: float = STRIKE_RANGE_PCT_DEFAULT) -> list[OptionSnapshot]:
    """GET /v1beta1/options/snapshots/{underlying}, feed=indicative.

    Filtered server-side by expiration in [dte_min, dte_max] DTE and strike
    within strike_range_pct of underlying_price, per docs/DATA.md -- pulling
    the unfiltered chain for 7 symbols every 15 minutes is wasteful on a
    free-tier VM.
    """
    _, option, _ = _clients()
    today = date.today()
    expiry_gte = today + timedelta(days=dte_min)
    expiry_lte = today + timedelta(days=dte_max)
    strike_low = underlying_price * (1 - strike_range_pct)
    strike_high = underlying_price * (1 + strike_range_pct)

    req = OptionChainRequest(
        underlying_symbol=symbol,
        feed=OptionsFeed.INDICATIVE,
        expiration_date_gte=expiry_gte,
        expiration_date_lte=expiry_lte,
        strike_price_gte=strike_low,
        strike_price_lte=strike_high,
    )
    chain = option.get_option_chain(req)

    snapshots: list[OptionSnapshot] = []
    for occ_symbol, snap in chain.items():
        contract = _parse_occ_symbol(occ_symbol)
        quote = getattr(snap, "latest_quote", None)
        greeks = getattr(snap, "greeks", None)
        iv = getattr(snap, "implied_volatility", None)
        bid = getattr(quote, "bid_price", None) if quote else None
        ask = getattr(quote, "ask_price", None) if quote else None
        mid = (bid + ask) / 2 if (bid is not None and ask is not None) else None
        delta = getattr(greeks, "delta", None) if greeks else None
        snapshots.append(OptionSnapshot(
            occ_symbol=occ_symbol,
            underlying=symbol,
            expiration=contract[0],
            strike=contract[2],
            right=contract[1],
            bid=bid, ask=ask, mid=mid,
            delta=delta,
            implied_volatility=iv,
        ))
    if len(snapshots) >= 200:
        logger.warning("fetch_chain(%s) returned %d contracts; server-side "
                        "filters may not be applying as expected", symbol, len(snapshots))
    return snapshots


def fetch_option_bars(occ_symbols: list[str], on: date) -> dict[str, float]:
    """GET /v1beta1/options/bars for a specific date. Returns close price per
    OCC symbol for symbols that have a bar on that date (data begins Feb 2024,
    per docs/DATA.md). Missing symbols are simply absent from the result --
    callers must treat that as 'skip silently', not an error."""
    _, option, _ = _clients()
    start = datetime(on.year, on.month, on.day)
    end = start + timedelta(days=1)
    req = OptionBarsRequest(symbol_or_symbols=occ_symbols, start=start, end=end,
                             timeframe=TimeFrame.Day)
    bar_set = option.get_option_bars(req)
    out: dict[str, float] = {}
    for sym in occ_symbols:
        bars = bar_set.get(sym) if hasattr(bar_set, "get") else bar_set.data.get(sym)
        if bars:
            out[sym] = bars[-1].close
    return out


def fetch_open_interest(occ_symbol: str) -> int | None:
    """GET /v2/options/contracts for one contract. Open interest lags one
    day (docs/DATA.md). Called only for shortlisted legs, never the full
    chain (docs/STRATEGY.md)."""
    _, _, trading = _clients()
    req = GetOptionContractsRequest(symbol=[occ_symbol])
    resp = trading.get_option_contracts(req)
    contracts = getattr(resp, "option_contracts", resp)
    if not contracts:
        return None
    oi = getattr(contracts[0], "open_interest", None)
    return int(oi) if oi is not None else None


def parse_occ_root(occ_symbol: str) -> str:
    """Underlying ticker portion of an OCC symbol (everything before the
    fixed-width YYMMDD+C/P+strike tail)."""
    return occ_symbol[:-15]


def _parse_occ_symbol(occ_symbol: str) -> tuple[date, Literal["C", "P"], float]:
    """Inverse of backfill.occ_symbol -- extract (expiry, right, strike)."""
    # root is variable-length; walk from the right, the fixed-width tail is
    # YYMMDD (6) + C|P (1) + 8-digit strike*1000
    tail = occ_symbol[-15:]
    yymmdd, right, strike_raw = tail[:6], tail[6], tail[7:]
    expiry = datetime.strptime(yymmdd, "%y%m%d").date()
    strike = int(strike_raw) / 1000.0
    return expiry, right, strike  # type: ignore[return-value]
