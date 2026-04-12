# `weave providers list` + opencode Built-in Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `opencode` as 6th built-in provider and add a `weave providers list` CLI command to inspect the provider registry.

**Architecture:** New contract manifest + adapter script for opencode. New click command group `providers` with `list` subcommand. Reads from the registry, probes health via `check_provider_health`, formats output.

**Tech Stack:** Python 3.12, click, pytest.

**Baseline test count:** 241.

**Target test count:** 245 (+4).

---

## Task 1: Add opencode as 6th built-in provider

**Files:**
- Create: `src/weave/providers/builtin/opencode.contract.json`
- Create: `src/weave/providers/builtin/opencode.sh`
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Update BUILTIN_NAMES in `tests/test_registry.py`**

Find:
```python
BUILTIN_NAMES = {"claude-code", "codex", "gemini", "ollama", "vllm"}
```
Replace with:
```python
BUILTIN_NAMES = {"claude-code", "codex", "gemini", "ollama", "opencode", "vllm"}
```

- [ ] **Step 2: Run registry tests to confirm failure**

Run: `PYTHONPATH=src pytest tests/test_registry.py -v 2>&1 | tail -20`
Expected: `test_registry_loads_all_five_builtins` and `test_registry_builtin_files_exist_on_disk` fail (opencode missing).

- [ ] **Step 3: Create the opencode contract manifest**

Create `src/weave/providers/builtin/opencode.contract.json`:

```json
{
  "contract_version": "1",
  "name": "opencode",
  "display_name": "OpenCode",
  "adapter": "opencode.sh",
  "adapter_runtime": "bash",
  "capability_ceiling": "workspace-write",
  "protocol": {
    "request_schema": "weave.request.v1",
    "response_schema": "weave.response.v1"
  },
  "declared_features": ["tool-use", "file-edit", "shell-exec"],
  "health_check": "opencode --version"
}
```

- [ ] **Step 4: Create the opencode adapter script**

Create `src/weave/providers/builtin/opencode.sh`:

```bash
#!/usr/bin/env bash
# Weave provider adapter for opencode (sst/opencode)
set -euo pipefail
INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"

if ! command -v opencode >/dev/null 2>&1; then
  jq -n --arg stderr "opencode not found on PATH" \
    '{ protocol: "weave.response.v1", exitCode: 127, stdout: "", stderr: $stderr, structured: {} }'
  exit 0
fi

STDOUT=""
STDERR=""
EXIT_CODE=0
TMPFILE="${TMPDIR:-/tmp}/weave-opencode-stderr-$$"
STDOUT=$(opencode run "$TASK" 2>"$TMPFILE") || EXIT_CODE=$?
STDERR=$(cat "$TMPFILE" 2>/dev/null || echo "")
rm -f "$TMPFILE"

jq -n \
  --arg stdout "$STDOUT" \
  --arg stderr "$STDERR" \
  --argjson exitCode "$EXIT_CODE" \
  '{ protocol: "weave.response.v1", exitCode: $exitCode, stdout: $stdout, stderr: $stderr, structured: {} }'
```

Make it executable:
```bash
chmod +x src/weave/providers/builtin/opencode.sh
```

- [ ] **Step 5: Run registry tests**

Run: `PYTHONPATH=src pytest tests/test_registry.py -v 2>&1 | tail -20`
Expected: all pass (including opencode in BUILTIN_NAMES).

- [ ] **Step 6: Validate contract via pydantic**

Run:
```bash
PYTHONPATH=src python3 -c "
import json
from weave.schemas.provider_contract import ProviderContract
c = ProviderContract.model_validate(json.load(open('src/weave/providers/builtin/opencode.contract.json')))
print(f'{c.name}: {c.capability_ceiling.value} — {[f.value for f in c.declared_features]}')
"
```
Expected: `opencode: workspace-write — ['tool-use', 'file-edit', 'shell-exec']`

- [ ] **Step 7: Run full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: 241 passed (no new tests added, just BUILTIN_NAMES updated so existing test now checks 6).

- [ ] **Step 8: Commit**

```bash
git add src/weave/providers/builtin/opencode.contract.json src/weave/providers/builtin/opencode.sh tests/test_registry.py
git commit -m "feat(providers): add opencode as 6th built-in provider"
```

---

## Task 2: Add `weave providers list` CLI command

**Files:**
- Modify: `src/weave/cli.py`
- Modify: `tests/test_providers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_providers.py`:

```python
def test_providers_list_cli_exists():
    from click.testing import CliRunner
    from weave.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["providers", "list", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output.lower()


def test_providers_list_cli_shows_builtins(tmp_path):
    from click.testing import CliRunner
    from weave.cli import main
    import os

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude"}}}'
    )

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.chdir(tmp_path)
        result = runner.invoke(main, ["providers", "list"])

    assert result.exit_code == 0
    assert "claude-code" in result.output
    assert "opencode" in result.output
    assert "vllm" in result.output
    assert "workspace-write" in result.output
    assert "read-only" in result.output


def test_providers_list_json_flag(tmp_path):
    from click.testing import CliRunner
    from weave.cli import main
    import os

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude"}}}'
    )

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.chdir(tmp_path)
        result = runner.invoke(main, ["providers", "list", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 6
    names = {p["name"] for p in data}
    assert "claude-code" in names
    assert "opencode" in names
```

Note: `import json` should already be at the top of `test_providers.py`. If not, add it.

- [ ] **Step 2: Run tests to confirm failure**

Run: `PYTHONPATH=src pytest tests/test_providers.py -v -k "providers_list" 2>&1 | tail -20`
Expected: FAIL — `providers` subgroup doesn't exist yet.

- [ ] **Step 3: Add the `providers` command group and `list` subcommand to `cli.py`**

Read `src/weave/cli.py` to find the right insertion point (after the last `@main.command`). Add:

```python
@main.group("providers")
def providers_group():
    """Manage provider contracts and registry."""
    pass


@providers_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def providers_list_cmd(as_json):
    """List all registered providers with health status and capabilities."""
    from weave.core.registry import get_registry, ProviderRegistry
    from weave.core.providers import check_provider_health

    registry = get_registry()
    registry.load(Path.cwd())
    contracts = registry.list()

    if as_json:
        import json as json_mod
        output = []
        for c in contracts:
            installed = check_provider_health(c.health_check) if c.health_check else False
            output.append({
                "name": c.name,
                "display_name": c.display_name,
                "capability_ceiling": c.capability_ceiling.value,
                "declared_features": [f.value for f in c.declared_features],
                "health_check": c.health_check,
                "installed": installed,
                "source": c.source,
                "adapter_runtime": c.adapter_runtime.value,
            })
        click.echo(json_mod.dumps(output, indent=2))
        return

    click.echo(f"\nProviders ({len(contracts)} registered):\n")
    for c in contracts:
        installed = check_provider_health(c.health_check) if c.health_check else False
        features = ", ".join(f.value for f in c.declared_features)
        health = "installed" if installed else "not found"
        click.echo(
            f"  {c.name:<15} {c.capability_ceiling.value:<18} "
            f"[{features}]"
        )
        click.echo(
            f"  {'':<15} {health:<18} ({c.source})"
        )
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src pytest tests/test_providers.py -v 2>&1 | tail -20`
Expected: all pass including the 3 new ones.

- [ ] **Step 5: Run full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: 244 passed (241 + 3 new).

Note: the test count may be 245 if the BUILTIN_NAMES change from Task 1 caused an existing assertion to be counted differently. Accept anything in 244-246 range.

- [ ] **Step 6: Commit**

```bash
git add src/weave/cli.py tests/test_providers.py
git commit -m "feat(cli): add weave providers list command with --json flag"
```

---

## Task 3: Final verification

- [ ] **Step 1: Full test suite**

Run: `PYTHONPATH=src pytest tests/ -v 2>&1 | tail -30`
Expected: ~245 passed.

- [ ] **Step 2: Smoke test**

```bash
PYTHONPATH=src python3 -c "
from pathlib import Path
from weave.core.registry import ProviderRegistry
import tempfile
with tempfile.TemporaryDirectory() as d:
    r = ProviderRegistry()
    r.load(Path(d))
    names = sorted(c.name for c in r.list())
    print('providers:', names)
    assert 'opencode' in names
    assert len(names) == 6
    print('ok')
"
```

- [ ] **Step 3: No commit** — verification only.

---

## Self-Review Notes

**Spec coverage:** opencode contract + adapter (Task 1), CLI command with text + JSON output (Task 2), BUILTIN_NAMES updated (Task 1).

**Type consistency:** `ProviderContract` fields accessed in Task 2 CLI match the schema from Phase 3. `check_provider_health` signature matches `providers.py`.

**No placeholders.** All code blocks complete.
