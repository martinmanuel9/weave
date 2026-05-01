#!/usr/bin/env python3
"""Hermes Agent adapter -- translates Weave protocol to LLM invocation.

Three modes (checked in order):
  1. Ollama (default): calls local Ollama API — no API key needed
  2. CLI mode: shells out to `hermes chat -q` or `claude --print`
  3. API mode: imports AIAgent directly (when OPENROUTER_API_KEY is set)
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request


# ── Ollama (local LLM) ──────────────────────────────────────────────────────

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("HERMES_OLLAMA_MODEL", "gemma4:latest")


def _invoke_ollama(prompt: str, timeout: int) -> dict:
    """Call Ollama's OpenAI-compatible chat completions API."""
    url = f"{OLLAMA_BASE}/v1/chat/completions"
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {
            "exitCode": 0,
            "stdout": content,
            "stderr": "",
            "structured": {
                "model": OLLAMA_MODEL,
                "usage": usage,
            },
        }
    except Exception as exc:
        return {"exitCode": 1, "stdout": "", "stderr": str(exc)}


def _ollama_available() -> bool:
    """Check if Ollama is reachable."""
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE}/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:
        return False


# ── CLI fallback ─────────────────────────────────────────────────────────────

def _invoke_cli(prompt: str, timeout: int) -> dict:
    """Shell out to hermes or claude CLI."""
    hermes_bin = shutil.which("hermes")
    if hermes_bin:
        cmd = [hermes_bin, "chat", "-q", prompt, "-Q"]
    else:
        claude_bin = shutil.which("claude")
        if not claude_bin:
            return {"exitCode": 1, "stdout": "", "stderr": "No hermes or claude CLI found"}
        cmd = [claude_bin, "--print", prompt]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except subprocess.TimeoutExpired:
        return {"exitCode": 124, "stdout": "", "stderr": f"Timeout after {timeout}s"}
    except Exception as exc:
        return {"exitCode": 1, "stdout": "", "stderr": str(exc)}


# ── API mode (AIAgent) ──────────────────────────────────────────────────────

def _invoke_api(prompt: str) -> dict:
    """Import AIAgent from hermes-agent repo (requires OPENROUTER_API_KEY)."""
    hermes_path = os.environ.get(
        "HERMES_AGENT_PATH", os.path.expanduser("~/repos/hermes-agent"),
    )
    sys.path.insert(0, hermes_path)
    from run_agent import AIAgent

    agent = AIAgent(
        base_url=os.environ.get("OPENROUTER_API_URL", "https://openrouter.ai/api/v1"),
        api_key=os.environ["OPENROUTER_API_KEY"],
        model=os.environ.get("HERMES_MODEL", "anthropic/claude-sonnet-4"),
        max_iterations=30,
        quiet_mode=True,
        skip_memory=True,
    )
    result = agent.run_conversation(user_message=prompt)
    return {
        "exitCode": 0,
        "stdout": result.get("final_response", "") or "",
        "stderr": "",
        "structured": {
            "model": os.environ.get("HERMES_MODEL", "anthropic/claude-sonnet-4"),
            "message_count": len(result.get("messages", [])),
        },
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    request = json.load(sys.stdin)
    task = request.get("task", "")
    context = request.get("context", "")
    timeout = request.get("timeout", 300)

    full_prompt = f"{context}\n\n{task}" if context else task

    try:
        # Priority: Ollama (local) > API (OpenRouter) > CLI (hermes/claude)
        if _ollama_available():
            result = _invoke_ollama(full_prompt, timeout)
        elif os.environ.get("OPENROUTER_API_KEY"):
            result = _invoke_api(full_prompt)
        else:
            result = _invoke_cli(full_prompt, timeout)

        response = {
            "protocol": "weave.response.v1",
            "exitCode": result.get("exitCode", 1),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "structured": result.get("structured"),
        }
    except Exception as exc:
        response = {
            "protocol": "weave.response.v1",
            "exitCode": 1,
            "stdout": "",
            "stderr": str(exc),
            "structured": None,
        }

    json.dump(response, sys.stdout)


if __name__ == "__main__":
    main()
