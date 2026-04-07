"""Weave project scaffolding — create .harness/ directory tree."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from weave.schemas.config import ProviderConfig, create_default_config
from weave.schemas.manifest import Phase, UnitType, create_manifest
from weave.core.manifest import write_manifest
from weave.core.providers import ProviderInfo, detect_providers


# CLI flags per provider name
_CLI_FLAGS: dict[str, str] = {
    "claude-code": "--print",
    "codex": "--print",
    "gemini": "",
    "ollama": "run",
}

_ADAPTER_TEMPLATE = """\
#!/usr/bin/env bash
# Weave provider adapter for {name}
set -euo pipefail
INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"
STDOUT=""
STDERR=""
EXIT_CODE=0
TMPFILE="${{TMPDIR:-/tmp}}/weave-stderr-$$"
STDOUT=$({command} {cli_flag} "$TASK" 2>"$TMPFILE") || EXIT_CODE=$?
STDERR=$(cat "$TMPFILE" 2>/dev/null || echo "")
rm -f "$TMPFILE"
jq -n --arg stdout "$STDOUT" --arg stderr "$STDERR" --argjson exitCode "$EXIT_CODE" \\
  '{{ exitCode: $exitCode, stdout: $stdout, stderr: $stderr, structured: {{}} }}'
"""


def _build_adapter_script(provider: ProviderInfo) -> str:
    cli_flag = _CLI_FLAGS.get(provider.name, "")
    return _ADAPTER_TEMPLATE.format(
        name=provider.name,
        command=provider.command,
        cli_flag=cli_flag,
    )


def scaffold_project(
    project_dir: Path | str,
    name: str | None = None,
    default_provider: str = "claude-code",
    phase: str = "sandbox",
) -> None:
    """Scaffold a Weave project at project_dir.

    Creates the .harness/ directory tree, manifest.json, config.json,
    context template files, and bash adapter scripts for installed providers.
    """
    project_dir = Path(project_dir)
    harness_dir = project_dir / ".harness"

    # Derive project name from directory if not provided
    if name is None:
        name = project_dir.name

    # Create subdirectories
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (harness_dir / sub).mkdir(parents=True, exist_ok=True)

    # Write manifest
    phase_enum = Phase(phase)
    manifest = create_manifest(
        name=name,
        unit_type=UnitType.project,
        phase=phase_enum,
        provider=default_provider,
    )
    write_manifest(project_dir, manifest)

    # Detect providers and build config
    providers = detect_providers()
    installed = [p for p in providers if p.installed]

    config = create_default_config(default_provider=default_provider)
    config.phase = phase
    for provider in installed:
        config.providers[provider.name] = ProviderConfig(
            command=provider.adapter_script,
            enabled=True,
            health_check=provider.health_check,
        )
    config.context.translate_to = [p.name for p in installed]

    config_path = harness_dir / "config.json"
    config_path.write_text(config.model_dump_json(indent=2))

    # Write context template files (only if they don't already exist)
    context_dir = harness_dir / "context"
    _write_if_not_exists(
        context_dir / "conventions.md",
        f"# {name} \u2014 Conventions\n\nAdd your project coding standards and rules here.\n",
    )
    _write_if_not_exists(
        context_dir / "brief.md",
        f"# {name} \u2014 Brief\n\nDescribe what this project is building.\n",
    )
    _write_if_not_exists(
        context_dir / "spec.md",
        f"# {name} \u2014 Specification\n\nAdd requirements and acceptance criteria here.\n",
    )

    # Generate bash adapter scripts for installed providers
    providers_dir = harness_dir / "providers"
    for provider in installed:
        script_path = providers_dir / f"{provider.name}.sh"
        script_path.write_text(_build_adapter_script(provider))
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_if_not_exists(path: Path, content: str) -> None:
    """Write content to path only if the file does not already exist."""
    if not path.exists():
        path.write_text(content)
