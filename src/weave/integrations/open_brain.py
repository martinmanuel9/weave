"""
MCP HTTP client for Open Brain.
Uses only stdlib urllib — no requests dependency.
"""
import json
import urllib.request
from urllib.error import URLError

# Supabase anon key for localhost demo instances
_SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9"
    ".CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0"
)


def _is_localhost(url: str) -> bool:
    return "localhost" in url or "127.0.0.1" in url


def _build_headers(key: str, url: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "x-brain-key": key,
    }
    if _is_localhost(url):
        headers["Authorization"] = f"Bearer {_SUPABASE_ANON_KEY}"
    return headers


def _mcp_post(url: str, key: str, payload: dict) -> dict:
    """Send a JSON-RPC 2.0 request and return the parsed response dict."""
    body = json.dumps(payload).encode()
    headers = _build_headers(key, url)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def capture_thought(url: str, key: str, content: str) -> bool:
    """
    POST an MCP tools/call for capture_thought.

    Returns True on success, False on error.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "capture_thought",
            "arguments": {"content": content},
        },
        "id": 1,
    }
    try:
        result = _mcp_post(url, key, payload)
        # A well-formed JSON-RPC response with no "error" key is a success
        return "error" not in result
    except (URLError, OSError, json.JSONDecodeError, KeyError):
        return False


def search_thoughts(url: str, key: str, query: str, limit: int = 5) -> str:
    """
    POST an MCP tools/call for search_thoughts.

    Returns the result content as a string, or an empty string on error.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "search_thoughts",
            "arguments": {"query": query, "limit": limit},
        },
        "id": 1,
    }
    try:
        result = _mcp_post(url, key, payload)
        if "error" in result:
            return ""
        # Extract text content from the MCP response
        content = result.get("result", {})
        if isinstance(content, dict):
            # MCP tools/call response: {"content": [{"type": "text", "text": "..."}]}
            items = content.get("content", [])
            texts = [item.get("text", "") for item in items if isinstance(item, dict)]
            return "\n".join(texts)
        return str(content)
    except (URLError, OSError, json.JSONDecodeError, KeyError):
        return ""
