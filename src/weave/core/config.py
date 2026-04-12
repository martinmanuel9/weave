"""3-layer config resolution for Weave harness."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

from ..schemas.config import WeaveConfig, create_default_config


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _migrate_provider_legacy_keys(merged: dict) -> None:
    """Rename legacy `capability` → `capability_override` on every provider entry.

    Drops the legacy `health_check` key (it now lives on the contract).
    Emits a DeprecationWarning once per migrated key. Mutates `merged` in place.
    """
    providers = merged.get("providers")
    if not isinstance(providers, dict):
        return
    for provider_name, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        if "capability" in entry:
            legacy = entry.pop("capability")
            existing = entry.get("capability_override")
            if existing is None:
                entry["capability_override"] = legacy
                warnings.warn(
                    f"config: provider {provider_name!r} uses legacy 'capability' key; "
                    f"renaming to 'capability_override'",
                    DeprecationWarning,
                    stacklevel=2,
                )
            else:
                warnings.warn(
                    f"config: provider {provider_name!r} has both 'capability' and "
                    f"'capability_override'; legacy 'capability' ignored",
                    DeprecationWarning,
                    stacklevel=2,
                )
        if "health_check" in entry:
            entry.pop("health_check")


def resolve_config(project_dir: Path, user_home: Path | None = None) -> WeaveConfig:
    """Resolve config from defaults → user → project → local layers."""
    home = user_home or Path.home()
    merged = create_default_config().model_dump()

    for config_path in [
        home / ".harness" / "config.json",
        project_dir / ".harness" / "config.json",
        project_dir / ".harness" / "config.local.json",
    ]:
        if config_path.exists():
            merged = _deep_merge(merged, json.loads(config_path.read_text()))

    _migrate_provider_legacy_keys(merged)

    return WeaveConfig.model_validate(merged)
