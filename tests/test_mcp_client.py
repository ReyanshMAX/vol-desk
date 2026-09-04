"""Unit tests for the wire-format translation between our own types and
the real alpacahq/alpaca-mcp-server tool schema (see mcp_client.py's
module docstring for the source). No live server -- MCPSession.call is
stubbed to capture what would be sent and return a canned response.
"""
import json
from types import SimpleNamespace

import pytest

from src.execution import mcp_client


def _envelope(result) -> SimpleNamespace:
    """Build a fake MCP CallToolResult matching the real
    alpacahq/alpaca-mcp-server's security-envelope wire format, confirmed
    live 2026-09-03 against both get_all_positions and get_account_info:
    an object payload sits directly under "data" ({"data": {"equity": ...}}),
    but an array payload sits one level deeper under "result"
    ({"data": {"result": [...]}}) -- mirrored here exactly since both
    shapes are independently confirmed live, not just inferred from the
    code under test. _unwrap() must peel both; earlier tests in this file
    stubbed already-unwrapped plain values directly, which is how the
    original envelope bug (and this array-vs-object variant of it)
    shipped unnoticed until it hit a live server."""
    data_field = {"result": result} if isinstance(result, list) else result
    text = json.dumps({
        "_alpaca_mcp_security": {"trust": "untrusted_tool_output"},
        "data": data_field,
    })
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


class _StubSession:
    def __init__(self):
        self.calls = []
        self.next_result = None

    def call(self, capability, arguments):
        self.calls.append((capability, arguments))
        return self.next_result


@pytest.fixture()
def stub_session(monkeypatch):
    stub = _StubSession()
    monkeypatch.setattr(mcp_client, "_session", stub)
    return stub


def test_place_mleg_order_credit_open_sends_negative_limit_and_open_intents(stub_session):
    stub_session.next_result = {"id": "order-1"}
    legs = [
        mcp_client.Leg(occ_symbol="SPY260918P00440000", side="sell", ratio=1),
        mcp_client.Leg(occ_symbol="SPY260918P00435000", side="buy", ratio=1),
    ]
    order_id = mcp_client.place_mleg_order(legs, qty=2, limit_price=1.10, side="credit", is_opening=True)

    assert order_id == "order-1"
    capability, args = stub_session.calls[0]
    assert capability == "place_mleg_order"
    assert args["order_class"] == "mleg"
    assert args["type"] == "limit"
    assert args["qty"] == "2"
    # credit -> Alpaca wants a negative number (proceeds), regardless of
    # the sign of the limit_price argument we were called with
    assert args["limit_price"] == "-1.1"

    by_symbol = {leg["symbol"]: leg for leg in args["legs"]}
    assert by_symbol["SPY260918P00440000"]["position_intent"] == "sell_to_open"
    assert by_symbol["SPY260918P00435000"]["position_intent"] == "buy_to_open"


def test_place_mleg_order_debit_open_sends_positive_limit(stub_session):
    stub_session.next_result = {"id": "order-2"}
    legs = [
        mcp_client.Leg(occ_symbol="SPY260918C00450000", side="buy", ratio=1),
        mcp_client.Leg(occ_symbol="SPY260918C00455000", side="sell", ratio=1),
    ]
    mcp_client.place_mleg_order(legs, qty=1, limit_price=1.50, side="debit", is_opening=True)

    _, args = stub_session.calls[0]
    assert args["limit_price"] == "1.5"
    by_symbol = {leg["symbol"]: leg for leg in args["legs"]}
    assert by_symbol["SPY260918C00450000"]["position_intent"] == "buy_to_open"
    assert by_symbol["SPY260918C00455000"]["position_intent"] == "sell_to_open"


def test_place_mleg_order_closing_uses_close_intents(stub_session):
    stub_session.next_result = {"id": "order-3"}
    # reversed legs, as orders.close_structure builds them
    legs = [
        mcp_client.Leg(occ_symbol="SPY260918P00440000", side="buy", ratio=1),
        mcp_client.Leg(occ_symbol="SPY260918P00435000", side="sell", ratio=1),
    ]
    mcp_client.place_mleg_order(legs, qty=2, limit_price=0.90, side="debit", is_opening=False)

    _, args = stub_session.calls[0]
    by_symbol = {leg["symbol"]: leg for leg in args["legs"]}
    assert by_symbol["SPY260918P00440000"]["position_intent"] == "buy_to_close"
    assert by_symbol["SPY260918P00435000"]["position_intent"] == "sell_to_close"


def test_get_positions_derives_signed_qty_from_side(stub_session):
    stub_session.next_result = [
        {"symbol": "SPY260918P00440000", "qty": "2", "side": "short",
         "avg_entry_price": "1.10", "market_value": "-150.0", "unrealized_pl": "50.0"},
        {"symbol": "SPY260918P00435000", "qty": "2", "side": "long",
         "avg_entry_price": "0.20", "market_value": "30.0", "unrealized_pl": "-10.0"},
    ]
    positions = mcp_client.get_positions()
    assert positions[0].qty == -2
    assert positions[0].unrealized_pnl == 50.0
    assert positions[1].qty == 2


def test_list_orders_aggregates_leg_symbols_for_mleg(stub_session):
    stub_session.next_result = [{
        "id": "order-1", "status": "filled", "submitted_at": "2026-09-03T12:00:00+00:00",
        "filled_qty": "2", "filled_avg_price": "-1.10",
        "legs": [{"symbol": "SPY260918P00440000"}, {"symbol": "SPY260918P00435000"}],
    }]
    orders = mcp_client.list_orders(status="all")
    assert orders[0].order_id == "order-1"
    assert set(orders[0].symbols) == {"SPY260918P00440000", "SPY260918P00435000"}
    assert orders[0].filled_qty == 2
    assert orders[0].filled_avg_price == -1.10


def test_list_orders_single_leg_falls_back_to_symbol_field(stub_session):
    stub_session.next_result = [{
        "id": "order-2", "status": "new", "submitted_at": "2026-09-03T12:00:00+00:00",
        "symbol": "SPY", "filled_qty": "0", "filled_avg_price": None,
    }]
    orders = mcp_client.list_orders()
    assert orders[0].symbols == ["SPY"]
    assert orders[0].filled_qty == 0
    assert orders[0].filled_avg_price is None


def test_get_account_parses_string_fields(stub_session):
    stub_session.next_result = {"equity": "100000.00", "cash": "80000.00", "buying_power": "100000.00"}
    account = mcp_client.get_account()
    assert account.equity == 100_000.0
    assert account.cash == 80_000.0


def test_close_position_uses_symbol_or_asset_id_param(stub_session):
    stub_session.next_result = {"id": "close-order-1"}
    order_id = mcp_client.close_position("SPY260918P00440000")
    assert order_id == "close-order-1"
    capability, args = stub_session.calls[0]
    assert args == {"symbol_or_asset_id": "SPY260918P00440000"}


def test_cancel_order_uses_order_id_param(stub_session):
    mcp_client.cancel_order("order-1")
    capability, args = stub_session.calls[0]
    assert capability == "cancel_order"
    assert args == {"order_id": "order-1"}


# --- _unwrap: the real security-envelope wire format --------------------

def test_unwrap_peels_security_envelope_for_list_result():
    assert mcp_client._unwrap(_envelope([])) == []
    positions = [{"symbol": "SPY260918P00440000", "qty": "1"}]
    assert mcp_client._unwrap(_envelope(positions)) == positions


def test_unwrap_peels_security_envelope_for_object_result():
    assert mcp_client._unwrap(_envelope({"id": "order-1"})) == {"id": "order-1"}


def test_unwrap_falls_back_when_no_envelope_present():
    # a bare dict with no data.result wrapper -- must not crash, must not
    # be mistaken for an enveloped response
    raw = SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"equity": "100000.00"}))])
    assert mcp_client._unwrap(raw) == {"equity": "100000.00"}


def test_get_positions_end_to_end_through_the_real_envelope_shape(stub_session):
    """Regression test for the exact bug hit on the first live run: with
    zero positions, the real server returns the envelope wrapping an empty
    list, not a bare []. get_positions() must return [] cleanly rather
    than raising while iterating over an unwrapped envelope dict."""
    stub_session.next_result = _envelope([])
    assert mcp_client.get_positions() == []

    stub_session.next_result = _envelope([
        {"symbol": "SPY260918P00440000", "qty": "2", "side": "short",
         "avg_entry_price": "1.10", "market_value": "-150.0", "unrealized_pl": "50.0"},
    ])
    positions = mcp_client.get_positions()
    assert len(positions) == 1
    assert positions[0].qty == -2


def test_get_account_end_to_end_through_the_real_envelope_shape(stub_session):
    stub_session.next_result = _envelope({"equity": "100000.00", "cash": "80000.00",
                                           "buying_power": "100000.00"})
    account = mcp_client.get_account()
    assert account.equity == 100_000.0
