"""Alpaca MCP server client (docs/INTEGRATIONS.md). The sole path for
orders, positions, and account state (D-008) -- no direct alpaca-py trading
client anywhere in this codebase.

Tool names and argument schemas are unconfirmed against the live server --
see OPEN_QUESTIONS.md Q-001. TOOL_NAME_MAP below is intentionally empty
rather than guessed: connect() lists the live server's tools and logs them
(BUILD.md Phase 1 acceptance criterion), and the capability calls below
raise a clear, specific error until a human fills TOOL_NAME_MAP in from
that live listing and moves Q-001 to DECISIONS.md.

The session is long-lived across the process lifetime. It runs on a
dedicated asyncio event loop in a background thread so the (synchronous)
scheduler can call through it without every caller becoming async.
"""
from __future__ import annotations

import asyncio
import logging
import shlex
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("vol_desk.mcp_client")

# Capability -> live MCP tool name. Left unresolved (None) until Q-001 is
# answered against the running server; see docs/INTEGRATIONS.md.
REQUIRED_TOOLS: dict[str, str | None] = {
    "account": None,          # equity, cash, buying power
    "positions": None,        # all open positions incl. option legs
    "orders_list": None,      # open/recent orders, for IN_FLIGHT resolution
    "place_mleg_order": None, # multi-leg options order, limit, DAY, atomic (D-027)
    "cancel_order": None,
    "close_position": None,   # used only by hard_halt flatten
}


class MCPToolsUnresolvedError(RuntimeError):
    """Raised when REQUIRED_TOOLS has not been mapped to real server tool
    names yet. Resolve OPEN_QUESTIONS.md Q-001 by connecting once, reading
    the tool list this raises with, and filling in REQUIRED_TOOLS."""


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
    def __init__(self, command: str) -> None:
        self._command = command
        self._loop = _Loop()
        self._session: ClientSession | None = None
        self._stdio_cm = None
        self._session_cm = None
        self._available_tools: list[str] = []

    def connect(self) -> None:
        self._loop.run(self._connect())

    async def _connect(self) -> None:
        parts = shlex.split(self._command)
        params = StdioServerParameters(command=parts[0], args=parts[1:])
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        tools = await self._session.list_tools()
        self._available_tools = [t.name for t in tools.tools]
        logger.info("MCP connected; available tools: %s", self._available_tools)

    def assert_required_tools(self) -> None:
        unresolved = [cap for cap, name in REQUIRED_TOOLS.items() if name is None]
        if unresolved:
            raise MCPToolsUnresolvedError(
                f"REQUIRED_TOOLS not yet mapped for capabilities {unresolved}. "
                f"Live server tools are: {self._available_tools}. "
                "Resolve OPEN_QUESTIONS.md Q-001 and fill in mcp_client.REQUIRED_TOOLS."
            )
        missing = [name for name in REQUIRED_TOOLS.values() if name not in self._available_tools]
        if missing:
            raise RuntimeError(
                f"MCP server is missing required tools {missing}. "
                f"Available: {self._available_tools}"
            )

    def call(self, capability: str, arguments: dict[str, Any]) -> Any:
        tool_name = REQUIRED_TOOLS.get(capability)
        if tool_name is None:
            raise MCPToolsUnresolvedError(
                f"capability {capability!r} has no resolved MCP tool name; see Q-001"
            )
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
    every capability in REQUIRED_TOOLS is present. Raises on a missing or
    unresolved tool -- never falls back to the REST trading API (D-008)."""
    global _session
    from src import config as config_module
    cfg = config_module.load()
    session = MCPSession(cfg.alpaca_mcp_command)
    session.connect()
    session.assert_required_tools()
    _session = session
    return session


def _require_session() -> MCPSession:
    if _session is None:
        raise RuntimeError("mcp_client.connect() has not been called yet")
    return _session


def get_account() -> Account:
    result = _require_session().call("account", {})
    data = _unwrap(result)
    return Account(
        equity=float(data["equity"]),
        cash=float(data["cash"]),
        buying_power=float(data["buying_power"]),
        ts=datetime.now(timezone.utc),
    )


def get_positions() -> list[BrokerPosition]:
    result = _require_session().call("positions", {})
    data = _unwrap(result)
    return [
        BrokerPosition(
            occ_symbol=p["occ_symbol"],
            qty=int(p["qty"]),
            avg_entry_price=float(p["avg_entry_price"]),
            market_value=float(p["market_value"]),
            unrealized_pnl=float(p["unrealized_pnl"]),
        )
        for p in data
    ]


def list_orders(status: str = "open") -> list[BrokerOrder]:
    result = _require_session().call("orders_list", {"status": status})
    data = _unwrap(result)
    return [
        BrokerOrder(
            order_id=o["order_id"],
            status=o["status"],
            symbols=o["symbols"],
            submitted_at=datetime.fromisoformat(o["submitted_at"]),
            filled_qty=int(o["filled_qty"]) if o.get("filled_qty") is not None else None,
            filled_avg_price=float(o["filled_avg_price"]) if o.get("filled_avg_price") is not None else None,
        )
        for o in data
    ]


def place_mleg_order(legs: list[Leg], qty: int, limit_price: float,
                      side: Literal["credit", "debit"]) -> str:
    result = _require_session().call("place_mleg_order", {
        "legs": [leg.__dict__ for leg in legs],
        "qty": qty,
        "limit_price": limit_price,
        "side": side,
    })
    data = _unwrap(result)
    return data["order_id"]


def cancel_order(order_id: str) -> None:
    _require_session().call("cancel_order", {"order_id": order_id})


def close_position(occ_symbol: str) -> str:
    result = _require_session().call("close_position", {"occ_symbol": occ_symbol})
    data = _unwrap(result)
    return data["order_id"]


def _unwrap(mcp_result: Any) -> Any:
    """MCP tool results carry a content list; the wire shape of each
    capability's payload is part of Q-001 and must be confirmed against the
    live server rather than assumed here."""
    if hasattr(mcp_result, "content"):
        import json
        text = mcp_result.content[0].text
        return json.loads(text)
    return mcp_result
