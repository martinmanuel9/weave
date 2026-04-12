# Volatile Context Population Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `ContextAssembly.volatile_task` with per-invocation context from git state, git history, and session activity so adapters know what just happened.

**Architecture:** New `core/volatile.py` module with three source functions (`_git_diff_section`, `_git_log_section`, `_activity_section`) and one orchestrator (`build_volatile_context`). `ContextAssembly` gains a `with_volatile()` method. `prepare()` gets a 2-line change. New `VolatileContextConfig` schema with per-source toggles, per-source limits, and a global character backstop.

**Tech Stack:** Python 3.12, pydantic v2, subprocess (git), pytest.

**Spec reference:** [`docs/superpowers/specs/2026-04-11-weave-volatile-context-design.md`](../specs/2026-04-11-weave-volatile-context-design.md)

**Baseline test count:** 217 (verified on commit `fe2765a`).

**Target test count:** 236 (+19 new in `test_volatile.py`).

---

## File Structure

| File | Kind | Responsibility |
|---|---|---|
| `src/weave/core/volatile.py` | NEW | `_git_diff_section`, `_git_log_section`, `_activity_section`, `build_volatile_context` |
| `src/weave/schemas/context.py` | MODIFIED | `with_volatile()` method on `ContextAssembly` |
| `src/weave/schemas/config.py` | MODIFIED | `VolatileContextConfig`, `volatile_context` field on `WeaveConfig` |
| `src/weave/core/runtime.py` | MODIFIED | 2-line change in `prepare()` |
| `tests/test_volatile.py` | NEW | 19 tests |

---

## Task 1: Add `VolatileContextConfig` schema + `ContextAssembly.with_volatile()`

Foundation task: the config schema and the assembly method that later tasks consume.

**Files:**
- Modify: `src/weave/schemas/config.py`
- Modify: `src/weave/schemas/context.py`
- Create: `tests/test_volatile.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_volatile.py`:

```python
"""Tests for volatile context population."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weave.schemas.activity import ActivityRecord, ActivityStatus, ActivityType
from weave.schemas.config import VolatileContextConfig, WeaveConfig
from weave.schemas.context import ContextAssembly


def _make_assembly(stable: str = "# Conventions\nbe nice") -> ContextAssembly:
    stable_hash = hashlib.sha256(stable.encode()).hexdigest()
    return ContextAssembly(
        stable_prefix=stable,
        volatile_task="",
        full=stable,
        stable_hash=stable_hash,
        full_hash=stable_hash,
        source_files=["conventions.md"],
    )


def test_volatile_context_config_defaults():
    cfg = VolatileContextConfig()
    assert cfg.enabled is True
    assert cfg.git_diff_enabled is True
    assert cfg.git_diff_max_files == 30
    assert cfg.git_log_enabled is True
    assert cfg.git_log_max_entries == 10
    assert cfg.activity_enabled is True
    assert cfg.activity_max_records == 5
    assert cfg.max_total_chars == 8000


def test_weave_config_has_volatile_context_field():
    config = WeaveConfig()
    assert hasattr(config, "volatile_context")
    assert isinstance(config.volatile_context, VolatileContextConfig)


def test_with_volatile_populates_fields():
    assembly = _make_assembly()
    updated = assembly.with_volatile("## Git State\nsome changes")
    assert updated.volatile_task == "## Git State\nsome changes"
    assert updated.stable_prefix == assembly.stable_prefix
    assert updated.stable_hash == assembly.stable_hash
    assert "## Git State" in updated.full
    assert assembly.stable_prefix in updated.full
    assert "\n---\n" in updated.full


def test_with_volatile_empty_is_noop():
    assembly = _make_assembly()
    updated = assembly.with_volatile("")
    assert updated is assembly
    assert updated.full_hash == assembly.full_hash


def test_with_volatile_full_hash_differs_from_stable_hash():
    assembly = _make_assembly()
    updated = assembly.with_volatile("volatile content")
    assert updated.full_hash != updated.stable_hash
    assert updated.stable_hash == assembly.stable_hash
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `PYTHONPATH=src pytest tests/test_volatile.py -v 2>&1 | tail -20`
Expected: `ImportError` for `VolatileContextConfig` and `AttributeError` for `with_volatile`.

- [ ] **Step 3: Add `VolatileContextConfig` to `schemas/config.py`**

Add this class before `WeaveConfig` (after `SandboxConfig`):

```python
class VolatileContextConfig(BaseModel):
    enabled: bool = True
    git_diff_enabled: bool = True
    git_diff_max_files: int = 30
    git_log_enabled: bool = True
    git_log_max_entries: int = 10
    activity_enabled: bool = True
    activity_max_records: int = 5
    max_total_chars: int = 8000
```

Add the field to `WeaveConfig`:

```python
    volatile_context: VolatileContextConfig = Field(default_factory=VolatileContextConfig)
```

- [ ] **Step 4: Add `with_volatile()` method to `ContextAssembly` in `schemas/context.py`**

Add `import hashlib` at the top of the file (if not already present). Then add this method to the `ContextAssembly` class:

```python
    def with_volatile(self, volatile_text: str) -> "ContextAssembly":
        """Return a new ContextAssembly with volatile_task populated.

        Recomputes `full` and `full_hash`. `stable_prefix` and `stable_hash`
        are preserved unchanged. Returns self unchanged if volatile_text is empty.
        """
        if not volatile_text:
            return self

        full = self.stable_prefix + "\n---\n" + volatile_text
        full_hash = hashlib.sha256(full.encode("utf-8")).hexdigest()

        return ContextAssembly(
            stable_prefix=self.stable_prefix,
            volatile_task=volatile_text,
            full=full,
            stable_hash=self.stable_hash,
            full_hash=full_hash,
            source_files=self.source_files,
        )
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src pytest tests/test_volatile.py -v 2>&1 | tail -20`
Expected: 5 passed.

- [ ] **Step 6: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: `217 + 5 = 222 passed`.

- [ ] **Step 7: Commit**

```bash
git add src/weave/schemas/config.py src/weave/schemas/context.py tests/test_volatile.py
git commit -m "feat(context): add VolatileContextConfig and ContextAssembly.with_volatile()"
```

---

## Task 2: Implement git source functions

The two git-based volatile context sources: `_git_diff_section` and `_git_log_section`.

**Files:**
- Create: `src/weave/core/volatile.py`
- Modify: `tests/test_volatile.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_volatile.py`:

```python
def _init_git_repo(tmp_path: Path) -> Path:
    """Initialize a git repo with one committed file."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "init.txt").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_git_diff_section_shows_modified_and_new_files(tmp_path):
    from weave.core.volatile import _git_diff_section

    _init_git_repo(tmp_path)
    (tmp_path / "init.txt").write_text("changed\n")
    (tmp_path / "new_file.py").write_text("print('hi')\n")

    result = _git_diff_section(tmp_path, max_files=30)
    assert "## Recent Git State" in result
    assert "init.txt" in result
    assert "modified" in result
    assert "new_file.py" in result
    assert "new" in result


def test_git_diff_section_caps_at_max_files(tmp_path):
    from weave.core.volatile import _git_diff_section

    _init_git_repo(tmp_path)
    for i in range(40):
        (tmp_path / f"file_{i:03d}.txt").write_text(f"content {i}\n")

    result = _git_diff_section(tmp_path, max_files=5)
    assert result.count("- ") <= 6  # 5 files + possible "and N more" line
    assert "and 35 more" in result


def test_git_diff_section_empty_when_no_changes(tmp_path):
    from weave.core.volatile import _git_diff_section

    _init_git_repo(tmp_path)
    result = _git_diff_section(tmp_path, max_files=30)
    assert result == ""


def test_git_diff_section_empty_when_not_git_repo(tmp_path):
    from weave.core.volatile import _git_diff_section

    result = _git_diff_section(tmp_path, max_files=30)
    assert result == ""


def test_git_log_section_shows_recent_commits(tmp_path):
    from weave.core.volatile import _git_log_section

    _init_git_repo(tmp_path)
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text(f"{i}\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"commit {i}"], cwd=tmp_path, check=True)

    result = _git_log_section(tmp_path, max_entries=10)
    assert "### Recent commits" in result
    assert "commit 2" in result
    assert "commit 1" in result
    assert "commit 0" in result


def test_git_log_section_caps_at_max_entries(tmp_path):
    from weave.core.volatile import _git_log_section

    _init_git_repo(tmp_path)
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text(f"{i}\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"commit {i}"], cwd=tmp_path, check=True)

    result = _git_log_section(tmp_path, max_entries=3)
    lines = [l for l in result.splitlines() if l.startswith("- ")]
    assert len(lines) == 3


def test_git_log_section_empty_when_no_commits(tmp_path):
    from weave.core.volatile import _git_log_section

    # Init repo but don't commit
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    result = _git_log_section(tmp_path, max_entries=10)
    assert result == ""
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `PYTHONPATH=src pytest tests/test_volatile.py -v -k "git_diff or git_log" 2>&1 | tail -20`
Expected: `ModuleNotFoundError: No module named 'weave.core.volatile'`

- [ ] **Step 3: Create `src/weave/core/volatile.py` with the git sources**

```python
"""Volatile context assembly — per-invocation context from git state and session activity."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from weave.schemas.activity import ActivityRecord, ActivityType

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 5


def _git_diff_section(working_dir: Path, max_files: int) -> str:
    """Git diff + untracked files as a markdown section.

    Returns empty string if no changes, not a git repo, or git fails.
    """
    entries: list[tuple[str, str]] = []  # (filename, label)

    # Tracked changes
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode == 0:
            status_map = {"M": "modified", "A": "new", "D": "deleted", "R": "renamed"}
            for line in result.stdout.splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    status_code = parts[0][0] if parts[0] else "?"
                    filename = parts[1]
                    label = status_map.get(status_code, "changed")
                    entries.append((filename, label))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""

    # Untracked files
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    entries.append((line, "new"))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass  # tracked changes may still be available

    if not entries:
        return ""

    # Deduplicate (a file can appear in both diff and ls-files)
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for filename, label in entries:
        if filename not in seen:
            seen.add(filename)
            unique.append((filename, label))

    lines = ["## Recent Git State", "", "### Changed files (since last commit)"]
    for filename, label in unique[:max_files]:
        lines.append(f"- {filename} ({label})")

    overflow = len(unique) - max_files
    if overflow > 0:
        lines.append(f"- (and {overflow} more...)")

    return "\n".join(lines)


def _git_log_section(working_dir: Path, max_entries: int) -> str:
    """Recent git log as a markdown section.

    Returns empty string if no commits, not a git repo, or git fails.
    """
    try:
        result = subprocess.run(
            ["git", "log", f"--oneline", f"-{max_entries}"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""

    commits = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not commits:
        return ""

    lines = ["### Recent commits"]
    for commit in commits:
        lines.append(f"- {commit}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run the git tests**

Run: `PYTHONPATH=src pytest tests/test_volatile.py -v -k "git_diff or git_log" 2>&1 | tail -20`
Expected: 7 passed.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: `222 + 7 = 229 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/weave/core/volatile.py tests/test_volatile.py
git commit -m "feat(volatile): add git diff and git log context sources"
```

---

## Task 3: Implement activity source + orchestrator

**Files:**
- Modify: `src/weave/core/volatile.py`
- Modify: `tests/test_volatile.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_volatile.py`:

```python
def _make_record(
    session_id: str = "test-session",
    provider: str = "claude-code",
    task: str = "do something",
    duration: float = 100.0,
    status: ActivityStatus = ActivityStatus.success,
    files_changed: list[str] | None = None,
    timestamp: datetime | None = None,
    record_type: ActivityType = ActivityType.invoke,
    metadata: dict | None = None,
) -> ActivityRecord:
    return ActivityRecord(
        session_id=session_id,
        type=record_type,
        provider=provider,
        task=task,
        duration=duration,
        status=status,
        files_changed=files_changed or [],
        timestamp=timestamp or datetime.now(timezone.utc),
        metadata=metadata or {},
    )


def _write_session_records(sessions_dir: Path, session_id: str, records: list[ActivityRecord]) -> None:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    log_file = sessions_dir / f"{session_id}.jsonl"
    with log_file.open("w") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")


def test_activity_section_shows_recent_records(tmp_path):
    from weave.core.volatile import _activity_section

    sessions_dir = tmp_path / "sessions"
    records = [
        _make_record(
            session_id="s1",
            provider="claude-code",
            task="write tests",
            duration=22100.0,
            status=ActivityStatus.success,
            files_changed=["a.py", "b.py"],
            timestamp=datetime(2026, 4, 11, 10, 28, 12, tzinfo=timezone.utc),
        ),
        _make_record(
            session_id="s1",
            provider="claude-code",
            task="implement auth middleware",
            duration=45200.0,
            status=ActivityStatus.success,
            files_changed=["c.py", "d.py", "e.py"],
            timestamp=datetime(2026, 4, 11, 10, 30, 5, tzinfo=timezone.utc),
        ),
    ]
    _write_session_records(sessions_dir, "s1", records)

    result = _activity_section(sessions_dir, "s1", max_records=5)
    assert "## Session Activity" in result
    assert "implement auth middleware" in result
    assert "write tests" in result
    # Most recent first
    lines = [l for l in result.splitlines() if l.startswith("- [")]
    assert "implement auth" in lines[0]
    assert "write tests" in lines[1]


def test_activity_section_caps_at_max_records(tmp_path):
    from weave.core.volatile import _activity_section

    sessions_dir = tmp_path / "sessions"
    records = [
        _make_record(session_id="s1", task=f"task {i}")
        for i in range(10)
    ]
    _write_session_records(sessions_dir, "s1", records)

    result = _activity_section(sessions_dir, "s1", max_records=3)
    lines = [l for l in result.splitlines() if l.startswith("- [")]
    assert len(lines) == 3


def test_activity_section_skips_compaction_summaries(tmp_path):
    from weave.core.volatile import _activity_section

    sessions_dir = tmp_path / "sessions"
    records = [
        _make_record(
            session_id="s1",
            record_type=ActivityType.system,
            task="compaction_summary",
            metadata={"compacted_count": 10},
        ),
        _make_record(session_id="s1", task="real task 1"),
        _make_record(session_id="s1", task="real task 2"),
    ]
    _write_session_records(sessions_dir, "s1", records)

    result = _activity_section(sessions_dir, "s1", max_records=5)
    assert "compaction_summary" not in result
    assert "real task 1" in result
    assert "real task 2" in result


def test_activity_section_empty_when_no_session(tmp_path):
    from weave.core.volatile import _activity_section

    sessions_dir = tmp_path / "sessions"
    result = _activity_section(sessions_dir, "nonexistent", max_records=5)
    assert result == ""


def test_build_volatile_context_combines_sources(tmp_path):
    from weave.core.volatile import build_volatile_context

    repo = _init_git_repo(tmp_path)
    (repo / "new.py").write_text("x = 1\n")

    sessions_dir = repo / ".harness" / "sessions"
    _write_session_records(sessions_dir, "s1", [
        _make_record(session_id="s1", task="previous task"),
    ])

    config = VolatileContextConfig()
    result = build_volatile_context(repo, config, session_id="s1")
    assert "## Recent Git State" in result
    assert "### Recent commits" in result
    assert "## Session Activity" in result


def test_build_volatile_context_disabled_returns_empty(tmp_path):
    from weave.core.volatile import build_volatile_context

    config = VolatileContextConfig(enabled=False)
    result = build_volatile_context(tmp_path, config, session_id="s1")
    assert result == ""


def test_build_volatile_context_omits_empty_sources(tmp_path):
    from weave.core.volatile import build_volatile_context

    sessions_dir = tmp_path / ".harness" / "sessions"
    _write_session_records(sessions_dir, "s1", [
        _make_record(session_id="s1", task="only activity"),
    ])

    config = VolatileContextConfig(git_diff_enabled=False, git_log_enabled=False)
    result = build_volatile_context(tmp_path, config, session_id="s1")
    assert "## Session Activity" in result
    assert "## Recent Git State" not in result
    assert "### Recent commits" not in result


def test_build_volatile_context_truncates_at_global_limit(tmp_path):
    from weave.core.volatile import build_volatile_context

    repo = _init_git_repo(tmp_path)
    for i in range(20):
        (repo / f"file_{i:03d}.py").write_text(f"x = {i}\n")

    config = VolatileContextConfig(max_total_chars=100)
    result = build_volatile_context(repo, config)
    assert len(result) <= 130  # 100 + truncation message
    assert "(volatile context truncated)" in result
```

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `PYTHONPATH=src pytest tests/test_volatile.py -v -k "activity or build_volatile" 2>&1 | tail -30`
Expected: `ImportError` for `_activity_section` and `build_volatile_context`.

- [ ] **Step 3: Add activity source + orchestrator to `volatile.py`**

Append to `src/weave/core/volatile.py`:

```python
def _activity_section(
    sessions_dir: Path,
    session_id: str,
    max_records: int,
) -> str:
    """Recent session activity as a markdown section.

    Returns empty string if no session file or no records.
    Skips compaction_summary records. Most recent first.
    """
    log_file = sessions_dir / f"{session_id}.jsonl"
    if not log_file.exists():
        return ""

    records: list[ActivityRecord] = []
    try:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = ActivityRecord.model_validate_json(line)
                # Skip compaction summaries
                if record.task == "compaction_summary" and record.type == ActivityType.system:
                    continue
                records.append(record)
            except Exception:
                continue  # skip corrupt lines
    except (OSError, UnicodeDecodeError):
        return ""

    if not records:
        return ""

    # Take last N records, then reverse for most-recent-first display
    tail = records[-max_records:]
    tail.reverse()

    lines = ["## Session Activity", "", "### Previous invocations"]
    for r in tail:
        ts = r.timestamp.strftime("%H:%M:%S") if r.timestamp else "??:??:??"
        task_snippet = (r.task or "")[:60]
        provider = r.provider or "unknown"
        status = r.status.value if r.status else "unknown"
        files_count = len(r.files_changed)
        duration_s = f"{(r.duration or 0) / 1000:.1f}s"
        lines.append(
            f'- [{ts}] provider={provider} task="{task_snippet}" '
            f"status={status} files={files_count} duration={duration_s}"
        )

    return "\n".join(lines)


def build_volatile_context(
    working_dir: Path,
    config,  # VolatileContextConfig
    session_id: str | None = None,
) -> str:
    """Assemble volatile context from enabled sources.

    Returns empty string if disabled or all sources are empty.
    """
    if not config.enabled:
        return ""

    sections: list[str] = []

    if config.git_diff_enabled:
        git_diff = _git_diff_section(working_dir, config.git_diff_max_files)
        if git_diff:
            sections.append(git_diff)

    if config.git_log_enabled:
        git_log = _git_log_section(working_dir, config.git_log_max_entries)
        if git_log:
            sections.append(git_log)

    if config.activity_enabled and session_id:
        sessions_dir = working_dir / ".harness" / "sessions"
        activity = _activity_section(
            sessions_dir, session_id, config.activity_max_records,
        )
        if activity:
            sections.append(activity)

    if not sections:
        return ""

    result = "\n\n".join(sections)

    if len(result) > config.max_total_chars:
        result = result[:config.max_total_chars] + "\n(volatile context truncated)"

    return result
```

- [ ] **Step 4: Run all volatile tests**

Run: `PYTHONPATH=src pytest tests/test_volatile.py -v 2>&1 | tail -30`
Expected: 17 passed (5 from Task 1 + 7 from Task 2 + 5 new = wait, let me recount: Task 1 = 5, Task 2 = 7, this task = 4 activity + 4 orchestrator = 8). Total: 20? No — let me count the test functions:

Task 1: `test_volatile_context_config_defaults`, `test_weave_config_has_volatile_context_field`, `test_with_volatile_populates_fields`, `test_with_volatile_empty_is_noop`, `test_with_volatile_full_hash_differs_from_stable_hash` = **5**

Task 2: `test_git_diff_section_shows_modified_and_new_files`, `test_git_diff_section_caps_at_max_files`, `test_git_diff_section_empty_when_no_changes`, `test_git_diff_section_empty_when_not_git_repo`, `test_git_log_section_shows_recent_commits`, `test_git_log_section_caps_at_max_entries`, `test_git_log_section_empty_when_no_commits` = **7**

This task: `test_activity_section_shows_recent_records`, `test_activity_section_caps_at_max_records`, `test_activity_section_skips_compaction_summaries`, `test_activity_section_empty_when_no_session`, `test_build_volatile_context_combines_sources`, `test_build_volatile_context_disabled_returns_empty`, `test_build_volatile_context_omits_empty_sources`, `test_build_volatile_context_truncates_at_global_limit` = **8**

Total in test_volatile.py after this task: 5 + 7 + 8 = **20**. But the spec says 19 total. The 20th test is `test_weave_config_has_volatile_context_field` which was not in the spec's test list but is useful — that's fine, the extra test doesn't hurt.

Expected: 20 passed in test_volatile.py. Full suite: `229 + 8 = 237 passed`.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: `237 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/weave/core/volatile.py tests/test_volatile.py
git commit -m "feat(volatile): add activity source and build_volatile_context orchestrator"
```

---

## Task 4: Wire volatile context into `prepare()` + integration test

The final wiring: 2 new lines in `prepare()` and 1 integration test.

**Files:**
- Modify: `src/weave/core/runtime.py`
- Modify: `tests/test_volatile.py`

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_volatile.py`:

```python
def test_prepare_populates_volatile_context(tmp_path):
    """Full integration: prepare() assembles volatile context from git + session."""
    from weave.core import registry as registry_module
    from weave.core.runtime import prepare

    # Reset registry singleton
    registry_module._REGISTRY_SINGLETON = None

    # Set up a minimal weave project with git history
    repo = _init_git_repo(tmp_path)
    harness = repo / ".harness"
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (harness / sub).mkdir(parents=True, exist_ok=True)

    (harness / "manifest.json").write_text(json.dumps({
        "id": "t", "type": "project", "name": "t", "status": "active",
        "phase": "mvp", "parent": None, "children": [],
        "provider": "claude-code", "agent": None,
        "created": "2026-04-11T00:00:00Z", "updated": "2026-04-11T00:00:00Z",
        "inputs": {}, "outputs": {}, "tags": [],
    }))
    (harness / "config.json").write_text(json.dumps({
        "version": "1", "phase": "mvp", "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
    }))
    (harness / "context" / "conventions.md").write_text("# Conventions\nBe nice.\n")

    # Create a modified file so git diff has something
    (repo / "init.txt").write_text("modified content\n")

    # Re-commit harness so git is clean for harness but init.txt is dirty
    subprocess.run(["git", "add", ".harness"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add harness"], cwd=repo, check=True)
    (repo / "init.txt").write_text("modified after commit\n")

    ctx = prepare(task="test volatile", working_dir=repo)

    assert ctx.context.volatile_task != ""
    assert ctx.context.stable_prefix != ""
    assert ctx.context.full != ctx.context.stable_prefix
    assert "\n---\n" in ctx.context.full
    assert ctx.context.full_hash != ctx.context.stable_hash
    # Git state should be in the volatile section
    assert "init.txt" in ctx.context.volatile_task
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `PYTHONPATH=src pytest tests/test_volatile.py::test_prepare_populates_volatile_context -v 2>&1 | tail -20`
Expected: FAIL — `ctx.context.volatile_task` is still `""` because `prepare()` hasn't been wired yet.

- [ ] **Step 3: Wire volatile context into `prepare()` in `runtime.py`**

Open `src/weave/core/runtime.py`. Find the line:

```python
    context = assemble_context(working_dir)
    session_id = create_session()
```

Insert after the `context = assemble_context(working_dir)` line and before `session_id = create_session()`:

Actually — `session_id` is created AFTER context assembly. The volatile context needs `session_id` for the activity section. But `session_id` is a fresh UUID that won't have any JSONL yet (this is the first invocation of this session). So the activity section will return empty for this session_id — which is correct (there are no previous invocations in a brand-new session).

But wait — in a session-resume scenario (GSD bridge, `session-start` / `session-end`), the session_id might already have activity. In that case, prepare() receives an existing session_id... but currently prepare() always creates a new one. This is a pre-existing limitation — for now, the activity section will only be useful when `prepare()` is called with a session that already has records (which doesn't happen yet).

For this task, insert the volatile lines AFTER `session_id = create_session()`:

Find:
```python
    context = assemble_context(working_dir)
    session_id = create_session()
    pre_invoke_untracked = _snapshot_untracked(working_dir)
```

Replace with:
```python
    context = assemble_context(working_dir)
    session_id = create_session()

    from weave.core.volatile import build_volatile_context
    volatile_text = build_volatile_context(
        working_dir=working_dir,
        config=config.volatile_context,
        session_id=session_id,
    )
    context = context.with_volatile(volatile_text)

    pre_invoke_untracked = _snapshot_untracked(working_dir)
```

- [ ] **Step 4: Run the integration test**

Run: `PYTHONPATH=src pytest tests/test_volatile.py::test_prepare_populates_volatile_context -v 2>&1 | tail -20`
Expected: PASS (the git diff section will show `init.txt` as modified).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -10`
Expected: `237 + 1 = 238 passed`. If any existing tests fail because they assert `ctx.context.full == ctx.context.stable_prefix` or `ctx.context.volatile_task == ""`, those tests are now wrong — volatile context is populated if the project dir is a git repo with changes. Fix by either making the test project dir a clean git repo (no uncommitted changes) or asserting `ctx.context.stable_prefix` instead of `ctx.context.full`.

- [ ] **Step 6: Commit**

```bash
git add src/weave/core/runtime.py tests/test_volatile.py
git commit -m "feat(runtime): wire volatile context into prepare()"
```

Include any fixed test files.

---

## Task 5: Final verification

**Files:** none — verification only.

- [ ] **Step 1: Run the full test suite**

Run: `PYTHONPATH=src pytest tests/ -v 2>&1 | tail -30`
Expected: **~238 tests pass** (217 baseline + 21 new).

- [ ] **Step 2: Verify no circular imports**

Run:
```bash
PYTHONPATH=src python3 -c "
from weave.core.volatile import (
    _git_diff_section,
    _git_log_section,
    _activity_section,
    build_volatile_context,
)
from weave.schemas.context import ContextAssembly
from weave.schemas.config import VolatileContextConfig, WeaveConfig
print('imports: ok')
"
```
Expected: `imports: ok`

- [ ] **Step 3: Smoke test — volatile context in a git repo**

Run:
```bash
PYTHONPATH=src python3 -c "
import tempfile, subprocess, json
from pathlib import Path
from weave.core.volatile import build_volatile_context
from weave.schemas.config import VolatileContextConfig

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    subprocess.run(['git', 'init', '-q'], cwd=tmp, check=True)
    subprocess.run(['git', 'config', 'user.email', 't@t'], cwd=tmp, check=True)
    subprocess.run(['git', 'config', 'user.name', 't'], cwd=tmp, check=True)
    (tmp / 'hello.py').write_text('print(\"hello\")\n')
    subprocess.run(['git', 'add', '.'], cwd=tmp, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'init'], cwd=tmp, check=True)
    (tmp / 'hello.py').write_text('print(\"world\")\n')
    (tmp / 'new.py').write_text('x = 1\n')

    config = VolatileContextConfig()
    result = build_volatile_context(tmp, config)
    print(result)
    assert '## Recent Git State' in result
    assert 'hello.py' in result
    assert 'modified' in result
    assert 'new.py' in result
    assert '### Recent commits' in result
    print('\\nsmoke: ok')
"
```
Expected: formatted git state output followed by `smoke: ok`.

- [ ] **Step 4: Smoke test — with_volatile preserves stable hash**

Run:
```bash
PYTHONPATH=src python3 -c "
import hashlib
from weave.schemas.context import ContextAssembly

stable = '# My Project Conventions'
stable_hash = hashlib.sha256(stable.encode()).hexdigest()
assembly = ContextAssembly(
    stable_prefix=stable, volatile_task='', full=stable,
    stable_hash=stable_hash, full_hash=stable_hash, source_files=['conventions.md'],
)

updated = assembly.with_volatile('## Git State\nchanged stuff')
print('stable_hash preserved:', updated.stable_hash == assembly.stable_hash)
print('full_hash changed:', updated.full_hash != assembly.full_hash)
print('volatile in full:', '## Git State' in updated.full)
assert updated.stable_hash == assembly.stable_hash
assert updated.full_hash != assembly.full_hash
print('smoke: ok')
"
```
Expected: all True lines followed by `smoke: ok`.

- [ ] **Step 5: No commit** — verification only.

---

## Self-Review Notes

**Spec coverage:**
- `VolatileContextConfig` schema with all 8 fields → Task 1
- `ContextAssembly.with_volatile()` method → Task 1
- `_git_diff_section` with status labels, truncation, error handling → Task 2
- `_git_log_section` with truncation, error handling → Task 2
- `_activity_section` with compaction_summary skip, most-recent-first, truncation → Task 3
- `build_volatile_context` orchestrator with per-source toggles and global backstop → Task 3
- `prepare()` 2-line integration → Task 4
- All error conditions from spec's error matrix covered by source functions returning empty string
- 21 tests covering all sources, orchestrator, schema method, config, and integration

**Placeholder scan:** No TBDs or TODOs. Every code block is complete.

**Type consistency:**
- `_git_diff_section(working_dir: Path, max_files: int) -> str` — consistent across Task 2 definition and Task 3 orchestrator call
- `_git_log_section(working_dir: Path, max_entries: int) -> str` — consistent
- `_activity_section(sessions_dir: Path, session_id: str, max_records: int) -> str` — consistent
- `build_volatile_context(working_dir, config, session_id) -> str` — consistent across Task 3 definition and Task 4 `prepare()` call
- `VolatileContextConfig` field names match orchestrator parameter usage exactly
- `with_volatile(volatile_text: str) -> ContextAssembly` — consistent across Task 1 definition and Task 4 call
