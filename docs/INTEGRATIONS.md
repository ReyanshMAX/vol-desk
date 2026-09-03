# INTEGRATIONS.md — Alpaca MCP execution, Groq inference, and failure modes

## Overview

Two external dependencies. The Alpaca MCP server is the sole path for orders,
positions, and account state (D-008). Groq serves both LLM calls behind a
model-agnostic client (D-009). Both have defined degradation behavior; neither is
allowed to fail silently.

## Non-goals

- No direct `alpaca-py` trading client. Market data only. Any order placed outside `execution/orders.py` is a bug.
- No provider failover to a second inference vendor or a local model (D-010).
- No streaming responses. All LLM calls are single-shot with a JSON response.
- No MCP tool discovery at runtime beyond the boot-time assertion below.

## Alpaca MCP server

### Connection

```python
# src/execution/mcp_client.py
ALPACA_MCP_COMMAND = env("ALPACA_MCP_COMMAND")   # e.g. "uvx alpaca-mcp-server"
ALPACA_API_KEY     = env("ALPACA_API_KEY")
ALPACA_SECRET_KEY  = env("ALPACA_SECRET_KEY")
ALPACA_PAPER       = True                        # never configurable, see Notes

def connect() -> MCPSession:
    """
    Start/attach the Alpaca MCP server, list its tools, and assert that every
    name in REQUIRED_TOOLS is present. Raise on a missing tool — do not fall
    back to the REST trading API.
    """
```

**Resolved (D-028)** against `alpacahq/alpaca-mcp-server` v2's published source
(README.md's tool catalog and `src/alpaca_mcp_server/overrides.py`'s
`place_option_order` signature) — see D-028 for the full mapping and its
caveats. `connect()` still calls `assert_required_tools()` against the live
server rather than trusting this table blindly, and one small real order
should still be placed and checked before Phase 7 is considered done.

```python
REQUIRED_TOOLS = {
    "account":          "get_account_info",
    "positions":        "get_all_positions",
    "orders_list":      "get_orders",
    "place_mleg_order": "place_option_order",  # order_class="mleg" (D-027);
                                                # atomic, all legs in one submission
    "cancel_order":     "cancel_order_by_id",
    "close_position":   "close_position",      # used only by hard_halt flatten
}
```

The server's own field names differ from ours in a few places the wrapper
translates so nothing above `mcp_client.py` has to know: `Position.qty` is
unsigned with a separate `side` ("long"/"short") rather than a signed qty;
`Position.unrealized_pl` (not `unrealized_pnl`); `Order.id` (not `order_id`);
a multi-leg order's per-leg symbols live under `Order.legs`, not a top-level
list. Most importantly, `place_option_order`'s multi-leg `limit_price` is a
single signed number where **positive = debit/cost, negative =
credit/proceeds** — the opposite sign of this system's own `net_credit`
convention — so `place_mleg_order` below negates internally rather than
exposing that inversion to callers.

### Wrapper interface

Everything above the client speaks these types, never raw MCP payloads.

```python
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

def get_account() -> Account: ...
def get_positions() -> list[BrokerPosition]: ...
def list_orders(status: str = "open") -> list[BrokerOrder]: ...
def place_mleg_order(legs: list[Leg], qty: int, limit_price: float,
                     side: Literal["credit","debit"], *, is_opening: bool) -> str: ...
    # returns order_id. is_opening selects each leg's position_intent
    # (buy_to_open/sell_to_open vs buy_to_close/sell_to_close) -- the real
    # server wants that stated explicitly, not inferred from account state.
def cancel_order(order_id: str) -> None: ...
def close_position(occ_symbol: str) -> str: ...
```

### The price ladder

Delayed quotes make a single limit at stale mid unreliable (D-022). Entry and exit
both step toward the market and give up rather than crossing arbitrarily far.

```yaml
# config/params.yaml : execution
ladder_steps:        3
ladder_step_pct:     0.10     # of mid, per step, toward less favorable
ladder_wait_seconds: 30       # per rung
```

```python
def submit_with_ladder(intent: OrderIntent, qty: int) -> FillResult:
    """
    Rung 0: limit = mid credit
    Rung 1: limit = mid credit * (1 - ladder_step_pct)
    Rung 2: limit = mid credit * (1 - 2 * ladder_step_pct)

    After each rung, wait ladder_wait_seconds and poll the order.
    Filled  -> cancel nothing, write positions row, return FILLED
    Partial -> cancel remainder, record actual filled qty, return PARTIAL
    Unfilled after final rung -> cancel, return ABANDONED

    ABANDONED is a normal outcome. Do not widen beyond the final rung, do not
    increase qty, do not reselect strikes (CLAUDE.md rule 5).
    """
```

Exits use the same ladder inverted (paying up rather than collecting less). One
exception: `hard_halt` flatten uses `close_position` directly with no ladder,
because at that point exiting matters more than price.

### Failure modes

| Failure | Behavior |
|---|---|
| MCP unreachable at boot | fatal, exit non-zero, systemd restarts with backoff |
| MCP unreachable mid-session | `risk_monitor` logs and retries; `entry_scan` skipped; no state change |
| Order rejected by Alpaca | log to `decision_log` with the broker message, abandon, no retry |
| Partial fill | record actual qty in `positions`, manage the position that exists |
| Order status ambiguous at boot | `reconcile` resolves via `orders_list`, see docs/ARCHITECTURE.md |

## Groq inference

### Client

```python
# src/llm/client.py
GROQ_API_KEY   = env("GROQ_API_KEY")
GROQ_BASE_URL  = "https://api.groq.com/openai/v1"

@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int

def complete(
    system: str,
    user: str,
    *,
    tier: Literal["fast", "reasoning"],
    max_tokens: int = 800,
    temperature: float = 0.2,
    timeout_s: int = 30,
) -> LLMResponse: ...
```

The OpenAI-compatible surface is the model-agnostic seam (D-009). Swapping
providers means changing `GROQ_BASE_URL` and the model map, nothing else. No
provider-specific types cross this boundary.

### Model routing

```yaml
# config/params.yaml : llm
tiers:
  fast:      null    # regime labeling      -- Q-004
  reasoning: null    # structure construction -- Q-004
max_retries: 1
temperature: 0.2
```

Model IDs and free-tier rate limits are unconfirmed — see OPEN_QUESTIONS.md Q-004.
Do not hardcode a model string.

Routing rationale: regime labeling picks from a seven-value enum given
pre-computed features, which a smaller model handles fine. Structure construction
selects contracts and reasons about strikes and credit, and gets the larger model.

### Validation and retry

Full response schemas are in docs/PROMPTS.md.

```python
def complete_json(system: str, user: str, schema: type[T], *, tier: str) -> T | None:
    """
    1. call complete()
    2. strip markdown fences if present
    3. json.loads, then validate against schema
    4. on failure, retry ONCE with the validation error appended to the user
       message
    5. on second failure, log to decision_log with accepted=0 and return None

    Returning None is not an error condition for the caller — it means no trade
    this scan. Never fabricate a default response (CLAUDE.md rule 3).
    """
```

### Degradation (D-010)

When Groq is unreachable or exhausted:

- `entry_scan` short-circuits before `regime.classify`. No new positions.
- `manage_positions`, `risk_monitor`, `iv_snapshot`, `equity_snapshot` continue unaffected — all deterministic.
- Existing positions are managed to take-profit, stop, or force-close normally.
- A `decision_log` row with `agent='supervisor'`, `action='inference_unavailable'` is written once per occurrence, not once per symbol.

The system holds and manages rather than trading blind. It is never acceptable to
substitute a rule-based stand-in for the strategy agent and continue opening
positions — that is a different system trading under the same risk parameters.

## Environment variables

| Variable | Required | Read by | Purpose |
|---|---|---|---|
| `ALPACA_API_KEY` | yes | `mcp_client`, `alpaca_data` | paper account key |
| `ALPACA_SECRET_KEY` | yes | `mcp_client`, `alpaca_data` | paper account secret |
| `ALPACA_MCP_COMMAND` | yes | `mcp_client` | how to launch the MCP server |
| `GROQ_API_KEY` | yes | `llm/client` | inference |
| `VOL_DESK_DB` | no | `store/db` | SQLite path, default `./vol-desk.db` |
| `VOL_DESK_CONFIG` | no | `config` | config dir, default `./config` |
| `LOG_LEVEL` | no | `main` | default `INFO` |

## Notes

- `ALPACA_PAPER` is a constant, not an env var. A typo in an environment file must not be able to point this system at a live account.
- Every LLM call writes a `decision_log` row before returning, including timeouts and validation failures. Latency and token counts go in that row; they are the input to any later cost analysis.
- The MCP session is long-lived across the process lifetime. If the transport dies, reconnect rather than restarting the process, but treat a reconnect failure at boot as fatal.
- Do not log API keys or full MCP payloads containing them. `inputs_json` in `decision_log` holds domain data only.
