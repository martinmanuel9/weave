"""Weave project scaffolding — create .harness/ directory tree."""
from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path

from weave.schemas.config import ProviderConfig, create_default_config
from weave.schemas.manifest import Phase, UnitType, create_manifest
from weave.core.manifest import write_manifest
from weave.core.providers import ProviderInfo, detect_providers


def _builtin_dir() -> Path:
    """Return the path to the built-in provider files directory."""
    return Path(__file__).parent.parent / "providers" / "builtin"


def _builtin_hooks_dir() -> Path:
    """Return the path to the built-in hook scripts directory."""
    import weave
    return Path(weave.__file__).parent / "hooks" / "builtin"


def _copy_builtin_provider_files(provider_name: str, dest_dir: Path) -> None:
    """Copy .sh and .contract.json for provider_name from built-in dir to dest_dir.

    Preserves executable bit on the .sh file. Skips files that already exist
    in dest_dir so that user customisations are not overwritten.
    """
    src_dir = _builtin_dir()
    for suffix in (f"{provider_name}.sh", f"{provider_name}.contract.json"):
        src = src_dir / suffix
        if not src.exists():
            continue
        dest = dest_dir / suffix
        if dest.exists():
            continue
        shutil.copy2(src, dest)
        # Preserve executable bit for adapter scripts
        if suffix.endswith(".sh"):
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def scaffold_project(
    project_dir: Path | str,
    name: str | None = None,
    default_provider: str = "claude-code",
    phase: str = "sandbox",
    with_quality_gates: bool = False,
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
    providers = detect_providers(project_root=project_dir)
    installed = [p for p in providers if p.installed]

    config = create_default_config(default_provider=default_provider)
    config.phase = phase
    for provider in installed:
        config.providers[provider.name] = ProviderConfig(
            command=provider.name,
            enabled=True,
            capability_override=None,
        )
    config.context.translate_to = [p.name for p in installed]

    if with_quality_gates:
        hooks_dir = harness_dir / "hooks"
        builtin_hooks = _builtin_hooks_dir()
        for hook_name in ["run-tests.sh", "run-lint.sh"]:
            src_hook = builtin_hooks / hook_name
            dst_hook = hooks_dir / hook_name
            if src_hook.exists() and not dst_hook.exists():
                shutil.copy2(src_hook, dst_hook)
                dst_hook.chmod(
                    dst_hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                )
        config.hooks.post_invoke = [
            ".harness/hooks/run-tests.sh",
            ".harness/hooks/run-lint.sh",
        ]

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

    # Ensure .env is in .gitignore (append if not already present)
    gitignore_path = project_dir / ".gitignore"
    env_entries = ".env\n.env.local\n.env.*.local\n"
    if gitignore_path.exists():
        existing = gitignore_path.read_text()
        if ".env" not in existing:
            with open(gitignore_path, "a") as f:
                f.write(f"\n# Environment secrets (added by weave init)\n{env_entries}")
    else:
        gitignore_path.write_text(f"# Environment secrets\n{env_entries}")

    # Copy built-in provider files for installed providers
    providers_dir = harness_dir / "providers"
    for provider in installed:
        _copy_builtin_provider_files(provider.name, providers_dir)


def _write_if_not_exists(path: Path, content: str) -> None:
    """Write content to path only if the file does not already exist."""
    if not path.exists():
        path.write_text(content)
