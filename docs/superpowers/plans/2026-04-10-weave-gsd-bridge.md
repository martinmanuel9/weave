# MAR-143 Implementation Plan — GSD → Weave Bridge via Session Wrapping

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap GSD plan execution as a single weave session via two new CLI commands (`weave session-start` and `weave session-end`). Captures the plan as a unit — cumulative file diff, security scan, policy result — without intercepting individual subagent task calls.

**Architecture:** New `core/session_marker.py` module persists start-time git state (HEAD SHA + untracked snapshot) so that session-end can compute the cumulative `files_changed` even after an arbitrary subagent execution between the boundaries. Two new CLI commands wrap `prepare()` (start) and `_security_scan` + `_revert` + `_record` (end) using a synthetic `InvokeResult`. The GSD `execute-plan` workflow markdown gets two surgical bash insertions to call the new commands.

**Tech Stack:** Python 3.10+, Pydantic 2.x, stdlib `subprocess` for git diff, Click for CLI, pytest with `CliRunner` for integration tests. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-04-10-weave-gsd-bridge-design.md`

**Linear:** [MAR-143](https://linear.app/martymanny/issue/MAR-143)

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `src/weave/schemas/session_marker.py` | `SessionMarker` Pydantic model with 7 fields |
| `src/weave/core/session_marker.py` | `write_marker`, `read_marker`, `compute_files_changed` |
| `tests/test_session_marker.py` | 9 unit tests |

### Modified files

| File | Change |
|------|--------|
| `src/weave/cli.py` | Add `session_start_cmd` and `session_end_cmd` CLI commands |
| `tests/test_runtime.py` | Add 5 integration tests for the new CLI commands |

### Out-of-repo file (documented but not modified by this plan)

| File | Note |
|------|------|
| `~/.claude/get-shit-done/workflows/execute-plan.md` | Two new bash steps wrapping the subagent dispatch — applied separately by the operator after merge |

---

## Task 1: Create `SessionMarker` schema

**Files:**
- Create: `src/weave/schemas/session_marker.py`
- Test: `tests/test_schemas.py` (add one test at the end)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schemas.py`:

```python
def test_session_marker_fields():
    from datetime import datetime, timezone
    from weave.schemas.session_marker import SessionMarker

    sm = SessionMarker(
        session_id="test-id",
        start_time=datetime.now(timezone.utc),
        git_available=True,
        start_head_sha="a" * 40,
        pre_invoke_untracked=["existing.txt"],
        task="execute plan 03-01",
        working_dir="/tmp/test",
    )
    assert sm.session_id == "test-id"
    assert sm.git_available is True
    assert sm.start_head_sha == "a" * 40
    assert sm.pre_invoke_untracked == ["existing.txt"]
    assert sm.task == "execute plan 03-01"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_schemas.py::test_session_marker_fields -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.schemas.session_marker'`

- [ ] **Step 3: Create `src/weave/schemas/session_marker.py`**

```python
"""Session marker schema — start-time state for wrapped session-end calls."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SessionMarker(BaseModel):
    """Persisted start-time state for a wrapped session.

    Written by `weave session-start` and read by `weave session-end` to
    compute the cumulative files_changed for security scanning. Lives at
    `.harness/sessions/<session_id>.start_marker.json` next to the binding
    sidecar.

    The marker captures everything `session-end` needs to compute the diff
    without requiring the start and end commands to run in the same process.
    """
    session_id: str
    start_time: datetime
    git_available: bool
    start_head_sha: str | None
    pre_invoke_untracked: list[str] = Field(default_factory=list)
    task: str
    working_dir: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_schemas.py::test_session_marker_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143
git add src/weave/schemas/session_marker.py tests/test_schemas.py
git commit -m "$(cat <<'EOF'
feat(schemas): add SessionMarker model

Introduces SessionMarker as the start-time state persisted by
weave session-start and read by weave session-end. Seven fields:
session_id, start_time, git_available, start_head_sha,
pre_invoke_untracked, task, working_dir. Lives at
.harness/sessions/<session_id>.start_marker.json next to the
binding sidecar.

Linear: MAR-143

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Implement `write_marker` and `read_marker`

**Files:**
- Create: `src/weave/core/session_marker.py`
- Create: `tests/test_session_marker.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_marker.py`:

```python
"""Tests for session marker I/O and files_changed computation."""
import subprocess
from pathlib import Path


def _git_init(working_dir: Path) -> None:
    """Initialize a git repo in working_dir with a seed commit."""
    subprocess.run(["git", "init", "-q"], cwd=working_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=working_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=working_dir, check=True)
    (working_dir / "seed.txt").write_text("seed")
    subprocess.run(["git", "add", "seed.txt"], cwd=working_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=working_dir, check=True)


def test_write_marker_captures_git_state(temp_dir):
    """write_marker captures HEAD SHA and untracked files in a git repo."""
    from weave.core.session_marker import write_marker

    _git_init(temp_dir)
    (temp_dir / "user_work.txt").write_text("untracked")

    sessions_dir = temp_dir / ".harness" / "sessions"
    marker = write_marker(
        session_id="test-session",
        task="test task",
        working_dir=temp_dir,
        sessions_dir=sessions_dir,
    )

    assert marker.session_id == "test-session"
    assert marker.task == "test task"
    assert marker.git_available is True
    assert marker.start_head_sha is not None
    assert len(marker.start_head_sha) == 40
    assert "user_work.txt" in marker.pre_invoke_untracked

    # Marker file persisted to disk
    sidecar = sessions_dir / "test-session.start_marker.json"
    assert sidecar.exists()


def test_write_marker_handles_non_git_directory(temp_dir):
    """write_marker falls back gracefully when working_dir is not a git repo."""
    from weave.core.session_marker import write_marker

    sessions_dir = temp_dir / ".harness" / "sessions"
    marker = write_marker(
        session_id="non-git-session",
        task="test",
        working_dir=temp_dir,
        sessions_dir=sessions_dir,
    )

    assert marker.git_available is False
    assert marker.start_head_sha is None
    assert marker.pre_invoke_untracked == []

    # Marker file still written
    sidecar = sessions_dir / "non-git-session.start_marker.json"
    assert sidecar.exists()


def test_read_marker_returns_none_for_missing_file(temp_dir):
    """read_marker returns None when the marker file does not exist."""
    from weave.core.session_marker import read_marker

    sessions_dir = temp_dir / ".harness" / "sessions"
    sessions_dir.mkdir(parents=True)

    result = read_marker("nonexistent", sessions_dir)
    assert result is None


def test_read_marker_round_trips_all_fields(temp_dir):
    """write_marker + read_marker is lossless for all fields."""
    from weave.core.session_marker import read_marker, write_marker

    _git_init(temp_dir)
    (temp_dir / "extra.txt").write_text("extra")

    sessions_dir = temp_dir / ".harness" / "sessions"
    original = write_marker(
        session_id="round-trip",
        task="round trip test",
        working_dir=temp_dir,
        sessions_dir=sessions_dir,
    )

    loaded = read_marker("round-trip", sessions_dir)
    assert loaded is not None
    assert loaded.session_id == original.session_id
    assert loaded.start_time == original.start_time
    assert loaded.git_available == original.git_available
    assert loaded.start_head_sha == original.start_head_sha
    assert loaded.pre_invoke_untracked == original.pre_invoke_untracked
    assert loaded.task == original.task
    assert loaded.working_dir == original.working_dir
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_session_marker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.core.session_marker'`

- [ ] **Step 3: Create `src/weave/core/session_marker.py`**

```python
"""Session marker — capture and read start-time state for wrapped sessions."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from weave.schemas.session_marker import SessionMarker


# Empty tree object SHA — a baseline that git diff can work against when
# HEAD does not exist (no commits yet in the repo).
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _detect_git_state(working_dir: Path) -> tuple[bool, str | None, list[str]]:
    """Detect git availability, capture HEAD SHA, and snapshot untracked files.

    Returns (git_available, start_head_sha, pre_invoke_untracked).
    Falls back to (False, None, []) if any git command fails.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            return False, None, []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False, None, []

    # Capture HEAD SHA, falling back to the empty tree SHA if HEAD does not exist
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            start_head_sha = result.stdout.strip()
        else:
            start_head_sha = _EMPTY_TREE_SHA
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        start_head_sha = _EMPTY_TREE_SHA

    # Capture untracked file snapshot
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            pre_invoke_untracked = sorted(
                line for line in result.stdout.splitlines() if line
            )
        else:
            pre_invoke_untracked = []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pre_invoke_untracked = []

    return True, start_head_sha, pre_invoke_untracked


def write_marker(
    session_id: str,
    task: str,
    working_dir: Path,
    sessions_dir: Path,
) -> SessionMarker:
    """Capture start-time state and persist a SessionMarker.

    Returns the marker (also written to disk). Detects git availability,
    captures HEAD SHA, captures untracked file list. Falls back to
    git_available=False when git rev-parse fails.
    """
    git_available, start_head_sha, pre_invoke_untracked = _detect_git_state(working_dir)

    marker = SessionMarker(
        session_id=session_id,
        start_time=datetime.now(timezone.utc),
        git_available=git_available,
        start_head_sha=start_head_sha,
        pre_invoke_untracked=pre_invoke_untracked,
        task=task,
        working_dir=str(working_dir.resolve()),
    )

    sessions_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = sessions_dir / f"{session_id}.start_marker.json"
    sidecar_path.write_text(marker.model_dump_json(indent=2))
    return marker


def read_marker(session_id: str, sessions_dir: Path) -> SessionMarker | None:
    """Load a SessionMarker from its .start_marker.json sidecar.

    Returns None if the file does not exist. Raises on malformed JSON
    or Pydantic validation errors — a broken marker is an operator-facing
    error, not silently ignorable.
    """
    sidecar_path = sessions_dir / f"{session_id}.start_marker.json"
    if not sidecar_path.exists():
        return None
    return SessionMarker.model_validate_json(sidecar_path.read_text())
```

- [ ] **Step 4: Run tests**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_session_marker.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143
git add src/weave/core/session_marker.py tests/test_session_marker.py
git commit -m "$(cat <<'EOF'
feat(session-marker): add write_marker and read_marker

Captures start-time git state (HEAD SHA + untracked snapshot) and
persists a SessionMarker sidecar at
.harness/sessions/<session_id>.start_marker.json. Falls back gracefully
when working_dir is not a git repo (git_available=False, empty
untracked list, null SHA). Uses the empty tree object SHA when git
is available but HEAD does not exist (no commits yet).

read_marker returns None for missing files; raises for malformed JSON.

Linear: MAR-143

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Implement `compute_files_changed`

**Files:**
- Modify: `src/weave/core/session_marker.py`
- Modify: `tests/test_session_marker.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_marker.py`:

```python
def test_compute_files_changed_includes_committed_work(temp_dir):
    """compute_files_changed picks up files modified or added in commits since the marker."""
    from weave.core.session_marker import compute_files_changed, write_marker

    _git_init(temp_dir)
    sessions_dir = temp_dir / ".harness" / "sessions"
    write_marker(
        session_id="committed-work",
        task="test",
        working_dir=temp_dir,
        sessions_dir=sessions_dir,
    )

    # Modify the seed file and add a new tracked file, then commit
    (temp_dir / "seed.txt").write_text("modified seed")
    (temp_dir / "new_tracked.txt").write_text("new tracked")
    subprocess.run(["git", "add", "seed.txt", "new_tracked.txt"], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "subagent commit"], cwd=temp_dir, check=True)

    marker = write_marker(
        session_id="committed-work-2",
        task="test",
        working_dir=temp_dir,
        sessions_dir=sessions_dir,
    )
    # Use the FIRST marker for the diff baseline
    from weave.core.session_marker import read_marker
    first_marker = read_marker("committed-work", sessions_dir)
    assert first_marker is not None

    files = compute_files_changed(first_marker, temp_dir)
    assert "seed.txt" in files
    assert "new_tracked.txt" in files


def test_compute_files_changed_includes_uncommitted_modifications(temp_dir):
    """compute_files_changed picks up uncommitted modifications to tracked files."""
    from weave.core.session_marker import compute_files_changed, write_marker

    _git_init(temp_dir)
    sessions_dir = temp_dir / ".harness" / "sessions"
    marker = write_marker(
        session_id="uncommitted",
        task="test",
        working_dir=temp_dir,
        sessions_dir=sessions_dir,
    )

    # Modify a tracked file without committing
    (temp_dir / "seed.txt").write_text("modified but not committed")

    files = compute_files_changed(marker, temp_dir)
    assert "seed.txt" in files


def test_compute_files_changed_includes_new_untracked_files(temp_dir):
    """compute_files_changed picks up new untracked files created after the marker."""
    from weave.core.session_marker import compute_files_changed, write_marker

    _git_init(temp_dir)
    sessions_dir = temp_dir / ".harness" / "sessions"
    marker = write_marker(
        session_id="new-untracked",
        task="test",
        working_dir=temp_dir,
        sessions_dir=sessions_dir,
    )

    # Create a new untracked file AFTER the marker
    (temp_dir / "new_file.txt").write_text("new")

    files = compute_files_changed(marker, temp_dir)
    assert "new_file.txt" in files


def test_compute_files_changed_excludes_pre_existing_untracked(temp_dir):
    """Files that were untracked at marker time are NOT in files_changed if nothing else changed."""
    from weave.core.session_marker import compute_files_changed, write_marker

    _git_init(temp_dir)

    # Pre-existing untracked file
    (temp_dir / "pre_existing.txt").write_text("pre")

    sessions_dir = temp_dir / ".harness" / "sessions"
    marker = write_marker(
        session_id="pre-existing",
        task="test",
        working_dir=temp_dir,
        sessions_dir=sessions_dir,
    )
    assert "pre_existing.txt" in marker.pre_invoke_untracked

    # Do nothing else
    files = compute_files_changed(marker, temp_dir)
    assert "pre_existing.txt" not in files
    assert files == []


def test_compute_files_changed_returns_empty_for_non_git(temp_dir):
    """Non-git directories produce an empty files_changed list."""
    from weave.core.session_marker import compute_files_changed, write_marker

    sessions_dir = temp_dir / ".harness" / "sessions"
    marker = write_marker(
        session_id="non-git",
        task="test",
        working_dir=temp_dir,
        sessions_dir=sessions_dir,
    )
    assert marker.git_available is False

    # Modify some files (won't matter)
    (temp_dir / "anything.txt").write_text("anything")

    files = compute_files_changed(marker, temp_dir)
    assert files == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_session_marker.py -v -k "compute_files_changed"`
Expected: FAIL with `ImportError: cannot import name 'compute_files_changed'`

- [ ] **Step 3: Append `compute_files_changed` to `src/weave/core/session_marker.py`**

Append after `read_marker`:

```python
def compute_files_changed(
    marker: SessionMarker,
    working_dir: Path,
) -> list[str]:
    """Compute the cumulative files_changed list since the marker was written.

    For git-available sessions: combines `git diff <start_sha>...HEAD`
    (committed work since start), `git diff HEAD` (uncommitted modifications),
    and current untracked files minus the pre_invoke_untracked snapshot
    (new untracked).

    For non-git sessions: returns []. Logged as a degraded-enforcement signal.

    Best-effort: individual subprocess failures contribute nothing to the
    result; the function continues with what it has.
    """
    if not marker.git_available or marker.start_head_sha is None:
        return []

    files: set[str] = set()

    # 1. Committed work between start and HEAD
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", marker.start_head_sha, "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            files.update(line for line in result.stdout.splitlines() if line)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # 2. Uncommitted modifications
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            files.update(line for line in result.stdout.splitlines() if line)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # 3. New untracked (current untracked - pre_invoke_untracked)
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            current_untracked = {
                line for line in result.stdout.splitlines() if line
            }
            pre_set = set(marker.pre_invoke_untracked)
            files.update(current_untracked - pre_set)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return sorted(files)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_session_marker.py -v`
Expected: PASS (9 tests total — 4 from Task 2 + 5 from this task)

- [ ] **Step 5: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143
git add src/weave/core/session_marker.py tests/test_session_marker.py
git commit -m "$(cat <<'EOF'
feat(session-marker): add compute_files_changed

Combines three git queries to compute the cumulative files_changed
since the marker was written: committed work via git diff
<start_sha>...HEAD, uncommitted modifications via git diff HEAD,
and new untracked files via ls-files --others minus the
pre_invoke_untracked snapshot.

Returns sorted list for stable test assertions. Best-effort with
30s timeouts; subprocess failures contribute nothing rather than
raising. Non-git sessions return [] immediately.

Linear: MAR-143

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Implement `weave session-start` CLI command

**Files:**
- Modify: `src/weave/cli.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime.py`:

```python
def test_session_start_writes_marker_and_binding(temp_dir, monkeypatch):
    """weave session-start writes both binding sidecar and start marker, prints session_id."""
    import subprocess
    from click.testing import CliRunner
    from weave.cli import main

    _init_harness(temp_dir)

    # Initialize git so the marker captures git state
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    result = runner.invoke(main, ["session-start", "--task", "test plan"])
    assert result.exit_code == 0

    session_id = result.stdout.strip().splitlines()[0]
    assert len(session_id) >= 20  # UUID-ish

    # Both sidecars exist
    binding = temp_dir / ".harness" / "sessions" / f"{session_id}.binding.json"
    marker = temp_dir / ".harness" / "sessions" / f"{session_id}.start_marker.json"
    assert binding.exists()
    assert marker.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_runtime.py::test_session_start_writes_marker_and_binding -v`
Expected: FAIL — `weave session-start` is not yet a registered command. Click will report "No such command 'session-start'".

- [ ] **Step 3: Add `session_start_cmd` to `src/weave/cli.py`**

Add this new command function in `src/weave/cli.py`. Place it AFTER the existing `invoke_cmd` function and BEFORE the `# weave translate` section header. Locate the line `# weave translate` (around line 229) and insert the new code BEFORE it:

```python
# ---------------------------------------------------------------------------
# weave session-start
# ---------------------------------------------------------------------------

@main.command("session-start")
@click.option("--task", "-t", required=True, help="Task description for the wrapped session")
@click.option("--provider", "-p", default=None, help="Override default provider")
@click.option("--risk-class", default=None,
              type=click.Choice(["read-only", "workspace-write", "external-network", "destructive"]),
              help="Request a specific risk class (must be <= provider ceiling)")
def session_start_cmd(task, provider, risk_class):
    """Start a wrapped session for external execution (e.g., GSD plan).

    Captures pre-state, writes binding sidecar and start marker, prints
    the session ID to stdout. The caller is responsible for running
    `weave session-end --session-id <id>` after the wrapped work completes.
    """
    try:
        from weave.core.runtime import prepare
        from weave.core.session_marker import write_marker
        from weave.schemas.policy import RiskClass

        cwd = Path.cwd()
        requested = RiskClass(risk_class) if risk_class else None

        prepared = prepare(
            task=task,
            working_dir=cwd,
            provider=provider,
            caller="external",
            requested_risk_class=requested,
        )

        sessions_dir = cwd / ".harness" / "sessions"
        write_marker(
            session_id=prepared.session_id,
            task=task,
            working_dir=cwd,
            sessions_dir=sessions_dir,
        )

        # Print session_id to stdout for shell capture
        click.echo(prepared.session_id)

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
```

- [ ] **Step 4: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_runtime.py::test_session_start_writes_marker_and_binding -v`
Expected: PASS

- [ ] **Step 5: Run the full runtime suite to catch regressions**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_runtime.py -v`
Expected: all runtime tests pass

- [ ] **Step 6: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143
git add src/weave/cli.py tests/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(cli): add weave session-start command

New CLI command for wrapping external execution (e.g., GSD plans)
in a weave session. Calls prepare() to write the binding sidecar
and assemble context, then write_marker() to persist start-time
git state. Prints session_id to stdout for shell capture.

caller="external" — distinct from cli/itzel/gsd because the
actual external system is opaque to weave at this layer.

Linear: MAR-143

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Implement `weave session-end` CLI command

**Files:**
- Modify: `src/weave/cli.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime.py`:

```python
def test_session_end_completes_clean_session(temp_dir, monkeypatch):
    """session-start + session-end on a clean working tree → SUCCESS, empty findings."""
    import subprocess
    from click.testing import CliRunner
    from weave.cli import main
    from weave.core.session import read_session_activities

    _init_harness(temp_dir)
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    start_result = runner.invoke(main, ["session-start", "--task", "clean test"])
    assert start_result.exit_code == 0
    session_id = start_result.stdout.strip().splitlines()[0]

    # Do nothing — clean working tree
    end_result = runner.invoke(main, ["session-end", "--session-id", session_id])
    assert end_result.exit_code == 0

    # JSONL contains a final ActivityRecord with success status
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, session_id)
    assert len(records) >= 1
    final = records[-1]
    assert final.runtime_status == "success"
    assert final.security_findings == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_runtime.py::test_session_end_completes_clean_session -v`
Expected: FAIL — `weave session-end` is not yet registered.

- [ ] **Step 3: Add `session_end_cmd` to `src/weave/cli.py`**

Add this new command function in `src/weave/cli.py` directly AFTER `session_start_cmd` (added in Task 4) and BEFORE the `# weave translate` section header:

```python
# ---------------------------------------------------------------------------
# weave session-end
# ---------------------------------------------------------------------------

@main.command("session-end")
@click.option("--session-id", required=True, help="Session ID returned by session-start")
def session_end_cmd(session_id):
    """Finalize a wrapped session: scan changed files, run security policy, record outcome."""
    try:
        from weave.core.config import resolve_config
        from weave.core.context import assemble_context
        from weave.core.invoker import InvokeResult
        from weave.core.policy import evaluate_policy
        from weave.core.runtime import (
            PreparedContext,
            _record,
            _revert,
            _security_scan,
        )
        from weave.core.session_marker import compute_files_changed, read_marker
        from weave.schemas.policy import RuntimeStatus

        cwd = Path.cwd()
        sessions_dir = cwd / ".harness" / "sessions"

        # Load the marker
        marker = read_marker(session_id, sessions_dir)
        if marker is None:
            click.echo(
                f"Error: No start marker for session {session_id}. "
                f"Did you run 'weave session-start' first?",
                err=True,
            )
            sys.exit(1)

        # Reconstruct a PreparedContext directly (no second prepare() call)
        config = resolve_config(cwd)
        provider_name = config.default_provider
        provider_config = config.providers.get(provider_name)
        if provider_config is None:
            click.echo(f"Error: Provider '{provider_name}' not configured", err=True)
            sys.exit(1)

        adapter_script = cwd / ".harness" / "providers" / f"{provider_name}.sh"
        context = assemble_context(cwd)

        ctx = PreparedContext(
            config=config,
            active_provider=provider_name,
            provider_config=provider_config,
            adapter_script=adapter_script,
            context=context,
            session_id=session_id,
            working_dir=cwd,
            phase=config.phase,
            task=marker.task,
            caller="external",
            requested_risk_class=None,
            pre_invoke_untracked=set(marker.pre_invoke_untracked),
        )

        # Compute the cumulative files_changed
        files_changed = compute_files_changed(marker, cwd)

        # Construct synthetic InvokeResult
        fake_invoke_result = InvokeResult(
            exit_code=0,
            stdout="",
            stderr="",
            structured=None,
            duration=0.0,
            files_changed=files_changed,
        )

        # Run security scan
        security_result = _security_scan(ctx, fake_invoke_result)

        # Determine status
        if security_result.action_taken == "denied":
            status = RuntimeStatus.DENIED
        elif security_result.action_taken == "flagged":
            status = RuntimeStatus.FLAGGED
        else:
            status = RuntimeStatus.SUCCESS

        # Run revert (no-op unless action_taken == "denied" and phase is mvp/enterprise)
        _revert(ctx, fake_invoke_result, security_result)

        # Re-evaluate policy at end-time using current config
        policy_result = evaluate_policy(
            provider=provider_config,
            requested_class=None,
            phase=ctx.phase,
        )

        # Record the final activity
        _record(
            ctx=ctx,
            invoke_result=fake_invoke_result,
            policy_result=policy_result,
            security_result=security_result,
            pre_hook_results=[],
            post_hook_results=[],
            status=status,
        )

        # Print outcome to stdout
        click.echo(
            f"session {session_id} | status {status.value} | "
            f"{len(files_changed)} file(s) changed"
        )

        # Exit code mapping matches weave invoke
        if status == RuntimeStatus.DENIED:
            sys.exit(2)
        # SUCCESS and FLAGGED both exit 0

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
```

- [ ] **Step 4: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_runtime.py::test_session_end_completes_clean_session -v`
Expected: PASS

- [ ] **Step 5: Run the full runtime suite to catch regressions**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_runtime.py -v`
Expected: all runtime tests pass

- [ ] **Step 6: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143
git add src/weave/cli.py tests/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(cli): add weave session-end command

Finalizes a wrapped session by reading the start marker, computing
the cumulative files_changed via git diff, running the security
scan over the synthetic InvokeResult, executing _revert if denied
in mvp/enterprise phase, and writing the final ActivityRecord.

Reconstructs PreparedContext directly from marker + current config
+ freshly-assembled context to avoid creating an orphan binding
sidecar via a second prepare() call. Re-evaluates policy at end
time; the start-time binding sidecar (MAR-141) captures the
start-time hashes for drift detection.

Hooks are passed as empty lists since no hooks ran in the wrapped
flow (no _policy_check or _cleanup invocations).

Exit codes match weave invoke: DENIED=2, SUCCESS/FLAGGED=0,
errors=1.

Linear: MAR-143

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Integration test — `session-end` detects denied file in mvp phase

**Files:**
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_runtime.py`:

```python
def test_session_end_detects_committed_denied_file_in_mvp(temp_dir, monkeypatch):
    """In mvp phase, a committed .env between session-start and session-end is denied + reverted."""
    import json as _json
    import subprocess
    from click.testing import CliRunner
    from weave.cli import main
    from weave.core.session import read_session_activities

    _init_harness(temp_dir)

    # Switch to mvp phase
    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["phase"] = "mvp"
    config_path.write_text(_json.dumps(config))

    # Init git
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    start_result = runner.invoke(main, ["session-start", "--task", "denied test"])
    assert start_result.exit_code == 0
    session_id = start_result.stdout.strip().splitlines()[0]

    # Simulate the wrapped subagent: commit a .env file (matches default deny list)
    (temp_dir / ".env").write_text("SECRET=leaked")
    subprocess.run(["git", "add", ".env"], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "leak secret"], cwd=temp_dir, check=True)

    end_result = runner.invoke(main, ["session-end", "--session-id", session_id])
    assert end_result.exit_code == 2  # DENIED

    # MAR-139 revert removed the committed file from the working tree
    assert not (temp_dir / ".env").exists()

    # JSONL records the denial
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, session_id)
    final = records[-1]
    assert final.runtime_status == "denied"
    assert any(
        f.get("rule_id") == "write-deny-list"
        for f in final.security_findings
    )
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_runtime.py::test_session_end_detects_committed_denied_file_in_mvp -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143
git add tests/test_runtime.py
git commit -m "$(cat <<'EOF'
test(runtime): verify session-end denies + reverts in mvp phase

End-to-end test: session-start, then commit a .env file (default
deny list), then session-end. Expected: exit 2 (DENIED), the
committed file is reverted by MAR-139's _revert, JSONL has
runtime_status: denied with a write-deny-list finding.

Proves the bridge captures committed work between markers and
hands it to the security scan correctly.

Linear: MAR-143

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Integration test — `session-end` raises for missing marker

**Files:**
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_runtime.py`:

```python
def test_session_end_raises_for_missing_marker(temp_dir, monkeypatch):
    """session-end errors clearly when no marker exists for the given session_id."""
    from click.testing import CliRunner
    from weave.cli import main

    _init_harness(temp_dir)

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    result = runner.invoke(main, ["session-end", "--session-id", "nonexistent-uuid"])
    assert result.exit_code != 0
    assert "No start marker" in result.stderr or "No start marker" in result.output
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_runtime.py::test_session_end_raises_for_missing_marker -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143
git add tests/test_runtime.py
git commit -m "$(cat <<'EOF'
test(runtime): verify session-end errors clearly for missing marker

Operators must call session-start before session-end. A bare
session-end on a nonexistent session_id should produce a clear
error message and non-zero exit, not a cryptic stack trace.

Linear: MAR-143

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Integration test — `session-end` graceful in non-git directory

**Files:**
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_runtime.py`:

```python
def test_session_end_handles_non_git_directory_gracefully(temp_dir, monkeypatch):
    """session-end in a non-git directory: no enforcement, session still recorded."""
    from click.testing import CliRunner
    from weave.cli import main
    from weave.core.session import read_session_activities

    _init_harness(temp_dir)
    # NO git init — non-git directory

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    start_result = runner.invoke(main, ["session-start", "--task", "non-git test"])
    assert start_result.exit_code == 0
    session_id = start_result.stdout.strip().splitlines()[0]

    # Modify some files (won't be tracked because non-git)
    (temp_dir / "anything.txt").write_text("anything")

    end_result = runner.invoke(main, ["session-end", "--session-id", session_id])
    assert end_result.exit_code == 0  # SUCCESS, no enforcement

    # JSONL recorded the session as success with empty files_changed
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, session_id)
    final = records[-1]
    assert final.runtime_status == "success"
    assert final.files_changed == []
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/test_runtime.py::test_session_end_handles_non_git_directory_gracefully -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143
git add tests/test_runtime.py
git commit -m "$(cat <<'EOF'
test(runtime): verify session-end is graceful in non-git directories

Matches MAR-139's _revert posture: non-git working_dir is a
degraded enforcement environment. session-start succeeds with
git_available=False, session-end records the session with
empty files_changed and runtime_status=success. Operators
running mvp/enterprise without git accept the risk.

Linear: MAR-143

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Final verification

**Files:** none — verification only

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && pytest tests/ -v 2>&1 | tail -30`
Expected: **135 tests pass**

Running tally across tasks:
- Task 1: +1 (test_session_marker_fields in test_schemas.py)
- Task 2: +4 (write/read_marker tests in test_session_marker.py)
- Task 3: +5 (compute_files_changed tests in test_session_marker.py)
- Task 4: +1 (test_session_start_writes_marker_and_binding)
- Task 5: +1 (test_session_end_completes_clean_session)
- Task 6: +1 (test_session_end_detects_committed_denied_file_in_mvp)
- Task 7: +1 (test_session_end_raises_for_missing_marker)
- Task 8: +1 (test_session_end_handles_non_git_directory_gracefully)
- **Total new: 15. Final: 121 + 15 = 136.**

Wait — the spec said 135, but the running tally gives 136. The discrepancy: I planned 9 unit tests in `test_session_marker.py` (4 from Task 2 + 5 from Task 3 = 9), plus 1 schema test in Task 1, plus 5 integration tests in Tasks 4-8 = 15 new tests. 121 + 15 = 136. The spec under-counted by one (it said "9 unit + 5 integration" = 14, but the schema test in `test_schemas.py` is a 15th test).

Final expected: **136 tests passing**.

- [ ] **Step 2: Verify no circular imports**

Run:
```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && PYTHONPATH=src python3 -c "
from weave.cli import main
from weave.core.session_marker import write_marker, read_marker, compute_files_changed
from weave.schemas.session_marker import SessionMarker
print('imports: ok')
"
```
Expected: prints `imports: ok`

- [ ] **Step 3: Manual smoke test of session-start/session-end round trip**

Run:
```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-143 && PYTHONPATH=src python3 -c "
import subprocess, tempfile, json
from pathlib import Path
from click.testing import CliRunner
from weave.cli import main

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    harness = tmp / '.harness'
    harness.mkdir()
    for sub in ['context', 'hooks', 'providers', 'sessions', 'integrations']:
        (harness / sub).mkdir()
    (harness / 'manifest.json').write_text(json.dumps({
        'id': 'test', 'type': 'project', 'name': 'test',
        'status': 'active', 'phase': 'sandbox'
    }))
    (harness / 'config.json').write_text(json.dumps({
        'version': '1', 'phase': 'sandbox', 'default_provider': 'claude-code',
        'providers': {'claude-code': {
            'command': '.harness/providers/claude-code.sh',
            'enabled': True, 'capability': 'workspace-write'
        }}
    }))
    adapter = harness / 'providers' / 'claude-code.sh'
    adapter.write_text('#!/bin/bash\nread INPUT\necho ok\n')
    adapter.chmod(0o755)

    subprocess.run(['git', 'init', '-q'], cwd=tmp, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test'], cwd=tmp, check=True)
    subprocess.run(['git', 'config', 'user.name', 'test'], cwd=tmp, check=True)
    subprocess.run(['git', 'add', '.'], cwd=tmp, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'init'], cwd=tmp, check=True)

    import os
    os.chdir(tmp)
    runner = CliRunner()
    start = runner.invoke(main, ['session-start', '--task', 'manual smoke test'])
    print('start exit:', start.exit_code)
    sid = start.stdout.strip().splitlines()[0]
    print('session_id:', sid)

    marker = harness / 'sessions' / f'{sid}.start_marker.json'
    binding = harness / 'sessions' / f'{sid}.binding.json'
    print('marker exists:', marker.exists())
    print('binding exists:', binding.exists())

    end = runner.invoke(main, ['session-end', '--session-id', sid])
    print('end exit:', end.exit_code)
    print('end output:', end.stdout.strip())
    print('manual smoke: ok')
"
```
Expected: prints `manual smoke: ok` and various status lines

- [ ] **Step 4: No commit** — Task 9 is verification only.

---

## Self-Review Notes

**Spec coverage:**
- `SessionMarker` schema with 7 fields → Task 1
- `core/session_marker.py` with `write_marker`, `read_marker`, `compute_files_changed` → Tasks 2 + 3
- Empty tree SHA fallback when HEAD doesn't exist → Task 2 implementation (`_detect_git_state`)
- Best-effort error handling (subprocess failures contribute nothing) → Task 3 implementation
- `weave session-start` CLI command → Task 4
- `weave session-end` CLI command → Task 5
- Reconstructed `PreparedContext` directly from marker + config + context_assembly → Task 5 implementation
- Re-evaluated policy at end-time → Task 5 implementation
- Empty hook lists at session-end → Task 5 implementation
- Exit code mapping (DENIED=2, SUCCESS/FLAGGED=0, errors=1) → Task 5 implementation
- Synthetic `InvokeResult` for security scan → Task 5 implementation
- 9 unit tests in `test_session_marker.py` → Tasks 2 (4 tests) + 3 (5 tests)
- Integration tests covering: session-start writes both sidecars, clean session, denied + reverted in mvp, missing marker error, non-git graceful → Tasks 4, 5, 6, 7, 8
- All 121 pre-existing tests continue to pass → Task 4 Step 5, Task 5 Step 5, Task 9 Step 1

**Placeholder scan:** No TBDs, TODOs, or placeholder steps. Every code block is complete and copy-pastable.

**Type consistency:**
- `SessionMarker` fields are referenced consistently across Tasks 1-8 (`session_id`, `start_time`, `git_available`, `start_head_sha`, `pre_invoke_untracked`, `task`, `working_dir`)
- `write_marker(session_id, task, working_dir, sessions_dir) -> SessionMarker` — defined Task 2, used in Task 4 (CLI) and tests
- `read_marker(session_id, sessions_dir) -> SessionMarker | None` — defined Task 2, used in Task 5 (CLI) and tests
- `compute_files_changed(marker, working_dir) -> list[str]` — defined Task 3, used in Task 5 (CLI) and tests
- `session_start_cmd` and `session_end_cmd` use `caller="external"` consistently
- The synthetic `InvokeResult` constructor in Task 5 uses the same field names as `invoker.py`'s real `InvokeResult` (verified against `src/weave/core/invoker.py`)

**Expected final test count:** 136 tests (121 baseline + 15 new: 1 schema + 9 unit + 5 integration).

**Out-of-repo file:** the GSD `execute-plan.md` modifications are documented in the spec but applied separately by the operator after merge. The spec's "GSD Skill Markdown Changes" section has the exact bash blocks to insert. This is intentional — the weave repo doesn't own that file, and applying it during the implementation plan would mix repo and out-of-repo changes in a way that's hard to track.
