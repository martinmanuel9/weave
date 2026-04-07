"""3-layer config resolution for Weave harness."""
from __future__ import annotations

import json
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


def resolve_config(project_dir: Path, user_home: Path | None = None) -> WeaveConfig:
    """Resolve config from defaults → user → project → local layers."""
    home = user_home or Path.home()
    merged = create_default_config().model_dump()

    for config_path in [
        home / ".harness" / "config.json",             # user
        project_dir / ".harness" / "config.json",       # project
        project_dir / ".harness" / "config.local.json", # local
    ]:
        if config_path.exists():
            merged = _deep_merge(merged, json.loads(config_path.read_text()))

    return WeaveConfig.model_validate(merged)
