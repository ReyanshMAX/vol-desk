"""Alpaca MCP server client (docs/INTEGRATIONS.md). The sole path for
orders, positions, and account state (D-008) -- no direct alpaca-py trading
client anywhere in this codebase.

Q-001 (tool names/argument schemas) is resolved against the *source* of the
official server, alpacahq/alpaca-mcp-server (v2, FastMCP + OpenAPI-generated
tools, https://github.com/alpacahq/alpaca-mcp-server) -- specifically
README.md's tool catalog and src/alpaca_mcp_server/overrides.py's
place_option_order signature, fetched 2026-09-03. This is strong evidence
(it's the vendor's own published source, not inference from REST docs that
Q-001 explicitly warned might not match the MCP surface) but it is still
not a live call: `assert_required_tools()` below still fails loudly and
names exactly what's missing if a real connection disagrees with any of
this, per Q-001's original directive to never silently guess. Launch
command: `uvx alpaca-mcp-server` (env: ALPACA_API_KEY, ALPACA_SECRET_KEY,
optional ALPACA_PAPER_TRADE -- defaults true).

The session is long-lived across the process lifetime. It runs on a
dedicated asyncio event loop in a background thread so the (synchronous)
scheduler can call through it without every caller becoming async.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("vol_desk.mcp_client")

# Capability -> live MCP tool name. Real names per alpacahq/alpaca-mcp-server
# (see module docstring); the two order-mutating tools were confirmed from
# the auto-generated OpenAPI wrapper functions, which mirror Alpaca's public
# Trading API operation names and parameter shapes 1:1.
REQUIRED_TOOLS: dict[str, str] = {
    "account": "get_account_info",
    "positions": "get_all_positions",
    "orders_list": "get_orders",
    "place_mleg_order": "place_option_order",  # multi-leg via order_class="mleg" (D-027)
    "cancel_order": "cancel_order_by_id",
    "close_position": "close_position",        # used only by hard_halt flatten
}


class MCPToolsUnresolvedError(RuntimeError):
    """Raised when the live server disagrees with REQUIRED_TOOLS -- a
    missing tool, in practice, since the names themselves are no longer
    placeholders (see module docstring re: Q-001)."""


@dataclass(frozen=True)
class Account:
    equity: float
    cash: float
    buying_power: float
    ts: datetime


@dataclass(frozen=True)
class BrokerPosition:
    occ_symbol: str
    qty: int                  # signed: negative = short
    avg_entry_price: float
    market_value: float
    unrealized_pnl: float


@dataclass(frozen=True)
class BrokerOrder:
    order_id: str
    status: str
    symbols: list[str]
    submitted_at: datetime
    filled_qty: int | None = None
    filled_avg_price: float | None = None


@dataclass(frozen=True)
class Leg:
    occ_symbol: str
    side: Literal["buy", "sell"]
    ratio: int


class _Loop:
    """Owns the background asyncio loop the MCP session lives on."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mcp-client-loop")
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


class MCPSession:
    def __init__(self, command: str, api_key: str, secret_key: str) -> None:
        self._command = command
        self._api_key = api_key
        self._secret_key = secret_key
        self._loop = _Loop()
        self._session: ClientSession | None = None
        self._stdio_cm = None
        self._session_cm = None
        self._available_tools: list[str] = []

    def connect(self) -> None:
        self._loop.run(self._connect())

    async def _connect(self) -> None:
        parts = shlex.split(self._command)
        # StdioServerParameters does NOT inherit the parent process's
        # environment unless told to -- the alpaca-mcp-server subprocess
        # needs its own copy of the credentials to authenticate to Alpaca.
        # ALPACA_PAPER_TRADE is forced to "true" here regardless of
        # anything in the parent env: ALPACA_PAPER is documented as a
        # constant, never configurable (docs/INTEGRATIONS.md notes,
        # D-011-adjacent) -- a typo in an environment file must not be
        # able to point this at a live account. PATH is passed through so
        # `uvx`/`python` etc. can actually be found on disk.
        env = {
            "ALPACA_API_KEY": self._api_key,
            "ALPACA_SECRET_KEY": self._secret_key,
            "ALPACA_PAPER_TRADE": "true",
        }
        path = os.environ.get("PATH")
        if path:
            env["PATH"] = path
        params = StdioServerParameters(command=parts[0], args=parts[1:], env=env)
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        tools = await self._session.list_tools()
        self._available_tools = [t.name for t in tools.tools]
        logger.info("MCP connected; available tools: %s", self._available_tools)

    def assert_required_tools(self) -> None:
        missing = [name for name in REQUIRED_TOOLS.values() if name not in self._available_tools]
        if missing:
            raise MCPToolsUnresolvedError(
                f"MCP server is missing required tools {missing} (expected from "
                f"alpacahq/alpaca-mcp-server -- see this module's docstring). "
                f"Available: {self._available_tools}. If this server is a "
                "different version or fork, update REQUIRED_TOOLS to match."
            )

    def call(self, capability: str, arguments: dict[str, Any]) -> Any:
        tool_name = REQUIRED_TOOLS[capability]
        try:
            return self._loop.run(self._call(tool_name, arguments))
        except Exception:
            # docs/INTEGRATIONS.md: "If the transport dies, reconnect rather
            # than restarting the process." One reconnect-and-retry; if that
            # also fails, propagate -- the caller (a scheduled job) already
            # catches and logs per-job, so a still-dead transport degrades
            # that job for one cycle rather than crashing the process.
            logger.warning("MCP call %s failed, attempting one reconnect", capability)
            # best-effort: does not explicitly tear down the old stdio
            # transport first (it is presumed already dead), so this may
            # leak a defunct subprocess handle rather than doing a clean
            # __aexit__ -- acceptable for a rare failure path, not reused
            # across repeated failures in a tight loop
            self.connect()
            self.assert_required_tools()
            return self._loop.run(self._call(tool_name, arguments))

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        assert self._session is not None
        result = await self._session.call_tool(tool_name, arguments)
        return result


_session: MCPSession | None = None


def connect() -> MCPSession:
    """Start/attach the Alpaca MCP server, list its tools, and assert that
    every capability in REQUIRED_TOOLS is present. Raises on a missing
    tool -- never falls back to the REST trading API (D-008)."""
    global _session
    from src import config as config_module
    cfg = config_module.load()
    session = MCPSession(cfg.alpaca_mcp_command, cfg.alpaca_api_key, cfg.alpaca_secret_key)
    session.connect()
    session.assert_required_tools()
    _session = session
    return session


def _require_session() -> MCPSession:
    if _session is None:
        raise RuntimeError("mcp_client.connect() has not been called yet")
    return _session


def get_account() -> Account:
    """GET /v2/account via get_account_info. Alpaca returns equity/cash/
    buying_power as JSON strings; float() handles that transparently."""
    result = _require_session().call("account", {})
    data = _unwrap(result)
    return Account(
        equity=float(data["equity"]),
        cash=float(data["cash"]),
        buying_power=float(data["buying_power"]),
        ts=datetime.now(timezone.utc),
    )


def get_positions() -> list[BrokerPosition]:
    """GET /v2/positions via get_all_positions. Alpaca's Position object
    carries an unsigned qty plus a side ('long'/'short') rather than a
    signed qty, and the P&L field is named unrealized_pl -- both handled
    here so the rest of the codebase only ever sees our own signed
    convention (docs/INTEGRATIONS.md's BrokerPosition contract)."""
    result = _require_session().call("positions", {})
    data = _unwrap(result)
    positions = []
    for p in data:
        qty = float(p["qty"])
        if p.get("side") == "short":
            qty = -qty
        positions.append(BrokerPosition(
            occ_symbol=p["symbol"],
            qty=int(qty),
            avg_entry_price=float(p["avg_entry_price"]),
            market_value=float(p["market_value"]),
            unrealized_pnl=float(p["unrealized_pl"]),
        ))
    return positions


def list_orders(status: str = "open") -> list[BrokerOrder]:
    """GET /v2/orders via get_orders. A multi-leg (mleg) order carries its
    aggregate status/filled_qty/filled_avg_price at the top level and each
    leg's own symbol under 'legs'; a single-leg order has 'symbol' directly.
    Both are normalized into BrokerOrder.symbols here."""
    result = _require_session().call("orders_list", {"status": status})
    data = _unwrap(result)
    orders = []
    for o in data:
        legs = o.get("legs")
        symbols = [leg["symbol"] for leg in legs] if legs else [o["symbol"]]
        orders.append(BrokerOrder(
            order_id=o["id"],
            status=o["status"],
            symbols=symbols,
            submitted_at=datetime.fromisoformat(o["submitted_at"]),
            filled_qty=int(float(o["filled_qty"])) if o.get("filled_qty") is not None else None,
            filled_avg_price=float(o["filled_avg_price"]) if o.get("filled_avg_price") is not None else None,
        ))
    return orders


def place_mleg_order(legs: list[Leg], qty: int, limit_price: float,
                      side: Literal["credit", "debit"], *, is_opening: bool) -> str:
    """place_option_order with order_class='mleg'. Alpaca's own sign
    convention for a multi-leg limit_price (confirmed from
    alpaca_mcp_server/overrides.py): positive = debit/cost, negative =
    credit/proceeds -- the exact opposite of nothing, i.e. it IS this
    system's own net_credit convention negated, so the translation here is
    a single sign flip: our 'credit' side (side=='credit') means Alpaca
    should see a negative number.

    is_opening selects each leg's position_intent (buy_to_open/sell_to_open
    vs buy_to_close/sell_to_close) -- required so Alpaca doesn't have to
    infer open-vs-close from existing position state, which would be a
    silent way to submit the wrong thing.
    """
    magnitude = abs(limit_price)
    alpaca_limit_price = -magnitude if side == "credit" else magnitude

    def position_intent(leg_side: str) -> str:
        if is_opening:
            return "buy_to_open" if leg_side == "buy" else "sell_to_open"
        return "buy_to_close" if leg_side == "buy" else "sell_to_close"

    result = _require_session().call("place_mleg_order", {
        "qty": str(qty),
        "type": "limit",
        "time_in_force": "day",
        "order_class": "mleg",
        "limit_price": str(alpaca_limit_price),
        "legs": [
            {
                "symbol": leg.occ_symbol,
                "ratio_qty": str(leg.ratio),
                "side": leg.side,
                "position_intent": position_intent(leg.side),
            }
            for leg in legs
        ],
    })
    data = _unwrap(result)
    return data["id"]


def cancel_order(order_id: str) -> None:
    """DELETE /v2/orders/{order_id} via cancel_order_by_id."""
    _require_session().call("cancel_order", {"order_id": order_id})


def close_position(occ_symbol: str) -> str:
    """DELETE /v2/positions/{symbol_or_asset_id} via close_position. No
    qty/percentage passed -- a full close, which is all this system ever
    does (hard_halt flatten and close_structure both close the whole
    position; a partial exit isn't something this codebase requests)."""
    result = _require_session().call("close_position", {"symbol_or_asset_id": occ_symbol})
    data = _unwrap(result)
    return data["id"]


def _unwrap(mcp_result: Any) -> Any:
    """MCP tool results carry a content list whose text is the JSON body
    the underlying Alpaca REST call returned."""
    if hasattr(mcp_result, "content"):
        import json
        text = mcp_result.content[0].text
        return json.loads(text)
    return mcp_result
