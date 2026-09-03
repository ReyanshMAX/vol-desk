import math

import pytest

from src.data.backfill import _bs_price, implied_vol_from_price, occ_symbol
from datetime import date


def test_occ_symbol_format():
    assert occ_symbol("SPY", date(2026, 9, 18), "C", 450.0) == "SPY260918C00450000"


def test_occ_symbol_half_dollar_strike():
    assert occ_symbol("SPY", date(2026, 9, 18), "P", 452.5) == "SPY260918P00452500"


def test_implied_vol_from_price_recovers_known_sigma():
    spot, strike, dte_years, rate, sigma = 100.0, 100.0, 10 / 365.0, 0.04, 0.20
    price = _bs_price(spot, strike, dte_years, rate, sigma, "C")
    recovered = implied_vol_from_price(price, spot, strike, dte_years, rate, "C")
    assert recovered is not None
    assert abs(recovered - sigma) < 1e-3


def test_implied_vol_from_price_recovers_known_sigma_put():
    spot, strike, dte_years, rate, sigma = 100.0, 105.0, 14 / 365.0, 0.04, 0.35
    price = _bs_price(spot, strike, dte_years, rate, sigma, "P")
    recovered = implied_vol_from_price(price, spot, strike, dte_years, rate, "P")
    assert recovered is not None
    assert abs(recovered - sigma) < 1e-3


def test_implied_vol_from_price_below_intrinsic_returns_none():
    # a call struck at 90 with spot 100 has intrinsic value >= 10;
    # quoting it at 1.00 is below no-arbitrage bounds
    result = implied_vol_from_price(1.00, 100.0, 90.0, 10 / 365.0, 0.04, "C")
    assert result is None


def test_implied_vol_from_price_zero_price_returns_none():
    assert implied_vol_from_price(0.0, 100.0, 100.0, 10 / 365.0, 0.04, "C") is None


def test_implied_vol_from_price_zero_dte_returns_none():
    assert implied_vol_from_price(1.0, 100.0, 100.0, 0.0, 0.04, "C") is None
