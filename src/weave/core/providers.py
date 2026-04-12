"""Weave provider detection — find installed CLI tools via health checks."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


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


def detect_providers(project_root: Path | None = None) -> list[ProviderInfo]:
    """Check all registry providers and return a ProviderInfo list."""
    from weave.core.registry import get_registry

    root = project_root or Path.cwd()
    registry = get_registry()
    registry.load(root)

    providers: list[ProviderInfo] = []
    for contract in registry.list():
        health_cmd = contract.health_check or f"which {contract.name}"
        installed = check_provider_health(health_cmd)
        providers.append(
            ProviderInfo(
                name=contract.name,
                command=contract.name,
                installed=installed,
                health_check=health_cmd,
                adapter_script=f".harness/providers/{contract.name}.sh",
            )
        )
    return providers
