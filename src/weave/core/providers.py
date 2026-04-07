"""Weave provider detection — find installed CLI tools via health checks."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field


KNOWN_PROVIDERS: list[dict] = [
    {"name": "claude-code", "command": "claude", "health_check": "which claude"},
    {"name": "codex", "command": "codex", "health_check": "which codex"},
    {"name": "gemini", "command": "gemini", "health_check": "which gemini"},
    {"name": "ollama", "command": "ollama", "health_check": "which ollama"},
]


@dataclass
class ProviderInfo:
    name: str
    command: str
    installed: bool
    health_check: str
    adapter_script: str = field(default="")


def check_provider_health(cmd: str) -> bool:
    """Run a health-check command and return True if it exits with code 0."""
    try:
        parts = cmd.split()
        result = subprocess.run(
            parts,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def detect_providers() -> list[ProviderInfo]:
    """Check all known providers and return a ProviderInfo list."""
    providers: list[ProviderInfo] = []
    for p in KNOWN_PROVIDERS:
        installed = check_provider_health(p["health_check"])
        providers.append(
            ProviderInfo(
                name=p["name"],
                command=p["command"],
                installed=installed,
                health_check=p["health_check"],
                adapter_script=f".harness/providers/{p['name']}.sh",
            )
        )
    return providers
