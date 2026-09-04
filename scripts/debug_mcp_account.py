"""One-off diagnostic: print the raw MCP response for get_account_info so
we can see its actual shape rather than guess. Delete after use."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution import mcp_client

session = mcp_client.connect()
result = session.call("account", {})
print("type(result):", type(result))
if hasattr(result, "content"):
    text = result.content[0].text
    print("raw text:", text)
