#!/usr/bin/env python3
"""Hermes Agent adapter -- translates Weave protocol to AIAgent invocation."""

import json
import os
import sys


def main() -> None:
    request = json.load(sys.stdin)

    task = request.get("task", "")
    context = request.get("context", "")
    timeout = request.get("timeout", 300)

    full_prompt = f"{context}\n\n{task}" if context else task

    # Import AIAgent from hermes-agent repo
    hermes_path = os.environ.get(
        "HERMES_AGENT_PATH",
        os.path.expanduser("~/repos/hermes-agent"),
    )
    sys.path.insert(0, hermes_path)

    try:
        from run_agent import AIAgent

        agent = AIAgent(
            base_url=os.environ.get(
                "OPENROUTER_API_URL", "https://openrouter.ai/api/v1"
            ),
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            model=os.environ.get("HERMES_MODEL", "anthropic/claude-sonnet-4"),
            max_iterations=30,
            quiet_mode=True,
            skip_memory=True,
        )

        result = agent.run_conversation(user_message=full_prompt)

        response = {
            "protocol": "weave.response.v1",
            "exitCode": 0,
            "stdout": result.get("response", ""),
            "stderr": "",
            "structured": {
                "usage": result.get("usage", {}),
                "tool_calls": [
                    t.get("name", "") for t in result.get("tool_calls", [])
                ],
            },
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
