"""Tests for weave.core.scaffold — project scaffolding."""
import json
import stat
from pathlib import Path

import pytest

from weave.core.scaffold import scaffold_project
from weave.core.manifest import read_manifest
from weave.schemas.manifest import Phase


def test_scaffold_creates_structure(temp_dir):
    scaffold_project(temp_dir, name="my-project")

    harness = temp_dir / ".harness"
    assert harness.is_dir()

    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        assert (harness / sub).is_dir(), f"Missing subdir: {sub}"

    assert (harness / "manifest.json").is_file()
    assert (harness / "config.json").is_file()
    assert (harness / "context" / "conventions.md").is_file()
    assert (harness / "context" / "brief.md").is_file()
    assert (harness / "context" / "spec.md").is_file()


def test_scaffold_manifest_content(temp_dir):
    scaffold_project(temp_dir, name="test-project", phase="mvp")

    manifest = read_manifest(temp_dir)
    assert manifest.name == "test-project"
    assert manifest.phase == Phase.mvp


def test_scaffold_config_has_providers(temp_dir):
    scaffold_project(temp_dir, name="cfg-test", default_provider="claude-code")

    config_path = temp_dir / ".harness" / "config.json"
    config = json.loads(config_path.read_text())

    assert config["default_provider"] == "claude-code"
    assert "providers" in config
    assert isinstance(config["providers"], dict)


def test_scaffold_preserves_existing_context(temp_dir):
    """scaffold_project must NOT overwrite pre-existing context files."""
    harness = temp_dir / ".harness"
    harness.mkdir()
    context_dir = harness / "context"
    context_dir.mkdir()

    original_content = "# My existing conventions\nDo not overwrite me.\n"
    (context_dir / "conventions.md").write_text(original_content)

    scaffold_project(temp_dir, name="preserve-test")

    result = (context_dir / "conventions.md").read_text()
    assert result == original_content


def test_scaffold_name_defaults_to_dirname(temp_dir):
    """When name is None, it should fall back to the directory name."""
    scaffold_project(temp_dir)
    manifest = read_manifest(temp_dir)
    assert manifest.name == temp_dir.name


def test_scaffold_adapter_scripts_executable(temp_dir):
    """Adapter scripts for installed providers must be executable."""
    scaffold_project(temp_dir, name="exec-test")

    providers_dir = temp_dir / ".harness" / "providers"
    scripts = list(providers_dir.glob("*.sh"))
    for script in scripts:
        mode = script.stat().st_mode
        assert mode & stat.S_IXUSR, f"{script.name} is not user-executable"


"""Tests for quality gate scaffolding."""


def test_scaffold_with_quality_gates_copies_hooks(tmp_path):
    from weave.core.scaffold import scaffold_project

    scaffold_project(tmp_path, name="test-proj", with_quality_gates=True)

    hooks_dir = tmp_path / ".harness" / "hooks"
    assert (hooks_dir / "run-tests.sh").exists()
    assert (hooks_dir / "run-lint.sh").exists()
    assert (hooks_dir / "run-tests.sh").stat().st_mode & stat.S_IXUSR
    assert (hooks_dir / "run-lint.sh").stat().st_mode & stat.S_IXUSR

    config = json.loads((tmp_path / ".harness" / "config.json").read_text())
    assert len(config["hooks"]["post_invoke"]) == 2
    assert any("run-tests" in h for h in config["hooks"]["post_invoke"])
    assert any("run-lint" in h for h in config["hooks"]["post_invoke"])


def test_scaffold_without_quality_gates_no_hooks(tmp_path):
    from weave.core.scaffold import scaffold_project

    scaffold_project(tmp_path, name="test-proj", with_quality_gates=False)

    hooks_dir = tmp_path / ".harness" / "hooks"
    assert not (hooks_dir / "run-tests.sh").exists()
    assert not (hooks_dir / "run-lint.sh").exists()

    config = json.loads((tmp_path / ".harness" / "config.json").read_text())
    assert config["hooks"]["post_invoke"] == []
