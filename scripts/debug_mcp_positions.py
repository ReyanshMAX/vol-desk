"""One-off diagnostic: print the raw MCP response for get_all_positions so
we can see its actual shape rather than guess. Delete after use."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution import mcp_client

session = mcp_client.connect()
result = session.call("positions", {})
print("type(result):", type(result))
print("has .content:", hasattr(result, "content"))
if hasattr(result, "content"):
    print("len(content):", len(result.content))
    for i, block in enumerate(result.content):
        print(f"--- content[{i}] ---")
        print("type:", type(block))
        text = getattr(block, "text", None)
        print("text repr:", repr(text)[:2000])
