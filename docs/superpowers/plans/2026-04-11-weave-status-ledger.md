# `weave status` Ledger Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `weave status` show compacted session history from the ledger and fix the session count to exclude the ledger file.

**Architecture:** Add `read_session_history()` to `compaction.py`, update `status_cmd` to read the ledger and display a "Session history" section. Three small tasks.

**Tech Stack:** Python 3.12, click, pytest.

**Spec reference:** [`docs/superpowers/specs/2026-04-11-weave-status-ledger-design.md`](../specs/2026-04-11-weave-status-ledger-design.md)

**Baseline test count:** 238.

**Target test count:** 241 (+3).

---

## Task 1: Add `read_session_history` to compaction.py

**Files:**
- Modify: `src/weave/core/compaction.py`
- Modify: `tests/test_compaction.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_compaction.py`:

```python
def test_read_session_history(tmp_path):
    from weave.core.compaction import read_session_history

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    ledger = sessions_dir / "session_history.jsonl"
    ledger.write_text(
        '{"session_id": "sess-old", "provider": "ollama", "started": "2026-04-09T08:00:00+00:00", "ended": "2026-04-09T09:00:00+00:00", "invocation_count": 3, "total_duration_ms": 12100.0, "final_status": "success", "files_changed_count": 5, "task_snippet": "analyze code"}\n'
        '{"session_id": "sess-new", "provider": "claude-code", "started": "2026-04-10T10:00:00+00:00", "ended": "2026-04-10T11:00:00+00:00", "invocation_count": 12, "total_duration_ms": 45000.0, "final_status": "success", "files_changed_count": 8, "task_snippet": "build the thing"}\n'
    )
    entries = read_session_history(sessions_dir)
    assert len(entries) == 2
    # Most recent first (by position in file — last line is newest)
    assert entries[0]["session_id"] == "sess-new"
    assert entries[1]["session_id"] == "sess-old"


def test_read_session_history_missing_ledger(tmp_path):
    from weave.core.compaction import read_session_history

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    entries = read_session_history(sessions_dir)
    assert entries == []
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `PYTHONPATH=src pytest tests/test_compaction.py -v -k "read_session_history" 2>&1 | tail -20`
Expected: `ImportError` — `read_session_history` doesn't exist yet.

- [ ] **Step 3: Add `read_session_history` to `compaction.py`**

Append to `src/weave/core/compaction.py`:

```python
def read_session_history(sessions_dir: Path, max_entries: int = 10) -> list[dict]:
    """Read the session history ledger and return entries, most recent first.

    Returns an empty list if the ledger doesn't exist or is empty.
    Corrupt lines are silently skipped.
    """
    ledger_path = sessions_dir / "session_history.jsonl"
    if not ledger_path.exists():
        return []

    entries: list[dict] = []
    try:
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeDecodeError):
        return []

    # Most recent last in file (append-only), so reverse for most-recent-first
    entries.reverse()
    return entries[:max_entries]
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src pytest tests/test_compaction.py -v -k "read_session_history" 2>&1 | tail -20`
Expected: 2 passed.

- [ ] **Step 5: Run full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: 240 passed.

- [ ] **Step 6: Commit**

```bash
git add src/weave/core/compaction.py tests/test_compaction.py
git commit -m "feat(compaction): add read_session_history for ledger reading"
```

---

## Task 2: Update `status_cmd` — fix count + ledger section

**Files:**
- Modify: `src/weave/cli.py`
- Modify: `tests/test_compaction.py` (or create a status-specific test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_compaction.py`:

```python
def test_status_cmd_shows_session_history(tmp_path):
    """weave status includes compacted session history from the ledger."""
    from click.testing import CliRunner
    from weave.cli import main

    harness = tmp_path / ".harness"
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (harness / sub).mkdir(parents=True)

    (harness / "manifest.json").write_text(json.dumps({
        "id": "t", "type": "project", "name": "statustest", "status": "active",
        "phase": "sandbox", "parent": None, "children": [],
        "provider": "claude-code", "agent": None,
        "created": "2026-04-11T00:00:00Z", "updated": "2026-04-11T00:00:00Z",
        "inputs": {}, "outputs": {}, "tags": [],
    }))
    (harness / "config.json").write_text(json.dumps({
        "version": "1", "phase": "sandbox", "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
    }))

    # Write a ledger entry
    sessions_dir = harness / "sessions"
    (sessions_dir / "session_history.jsonl").write_text(
        '{"session_id": "sess-compacted", "provider": "claude-code", '
        '"started": "2026-04-10T10:00:00+00:00", "ended": "2026-04-10T11:00:00+00:00", '
        '"invocation_count": 5, "total_duration_ms": 30000.0, '
        '"final_status": "success", "files_changed_count": 3, '
        '"task_snippet": "build feature"}\n'
    )

    # Write one active session
    (sessions_dir / "active-sess.jsonl").write_text('{"dummy": true}\n')

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.chdir(tmp_path)
        result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert "1 active" in result.output
    assert "1 compacted" in result.output
    assert "Session history" in result.output
    assert "sess-compacted" in result.output
    assert "5 invocations" in result.output
```

- [ ] **Step 2: Run the test to confirm failure**

Run: `PYTHONPATH=src pytest tests/test_compaction.py::test_status_cmd_shows_session_history -v 2>&1 | tail -20`
Expected: FAIL — current status_cmd doesn't show ledger or split counts.

- [ ] **Step 3: Update `status_cmd` in `cli.py`**

Open `src/weave/cli.py`. Find `status_cmd` (around line 528). Replace the session counting and display logic. The full updated function:

Find the block that counts sessions (around line 551-565):

```python
        # Count sessions and gather recent activities
        sessions_dir = cwd / ".harness" / "sessions"
        session_count = 0
        recent_activities = []

        if sessions_dir.exists():
            session_files = sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            session_count = len(session_files)
            for sf in session_files[:5]:
                session_id = sf.stem
                acts = read_session_activities(sessions_dir, session_id)
                recent_activities.extend(acts)
            # Sort by timestamp descending and take 10
            recent_activities.sort(key=lambda a: a.timestamp, reverse=True)
            recent_activities = recent_activities[:10]
```

Replace with:

```python
        # Count sessions and gather recent activities
        sessions_dir = cwd / ".harness" / "sessions"
        active_count = 0
        recent_activities = []
        history_entries = []

        if sessions_dir.exists():
            session_files = sorted(
                (p for p in sessions_dir.glob("*.jsonl") if p.name != "session_history.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            active_count = len(session_files)
            for sf in session_files[:5]:
                session_id = sf.stem
                acts = read_session_activities(sessions_dir, session_id)
                recent_activities.extend(acts)
            recent_activities.sort(key=lambda a: a.timestamp, reverse=True)
            recent_activities = recent_activities[:10]

            from weave.core.compaction import read_session_history
            history_entries = read_session_history(sessions_dir, max_entries=10)
```

Then find the `Sessions:` output line (around line 573):

```python
        click.echo(f"Sessions: {session_count}")
```

Replace with:

```python
        compacted_count = len(history_entries)
        click.echo(f"Sessions: {active_count} active, {compacted_count} compacted")
```

Then find the end of the activity output section (around line 582-583) and after it, add:

```python
        if history_entries:
            click.echo("\nSession history (compacted):")
            for entry in history_entries:
                started = entry.get("started", "")
                date_str = started[:10] if started else "unknown"
                sid = entry.get("session_id", "unknown")[:12]
                provider = entry.get("provider") or "unknown"
                count = entry.get("invocation_count", 0)
                dur_s = (entry.get("total_duration_ms", 0) or 0) / 1000
                status = entry.get("final_status", "unknown")
                click.echo(f"  [{date_str}] {sid} — {provider} — {count} invocations — {dur_s:.1f}s — {status}")
```

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH=src pytest tests/test_compaction.py::test_status_cmd_shows_session_history -v 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: 241 passed.

- [ ] **Step 6: Commit**

```bash
git add src/weave/cli.py tests/test_compaction.py
git commit -m "feat(status): show compacted session history and fix session count"
```

---

## Task 3: Final verification

- [ ] **Step 1: Full test suite**

Run: `PYTHONPATH=src pytest tests/ -v 2>&1 | tail -30`
Expected: **241 passed**.

- [ ] **Step 2: Import check**

```bash
PYTHONPATH=src python3 -c "
from weave.core.compaction import read_session_history
from weave.cli import main
print('imports: ok')
"
```

- [ ] **Step 3: No commit** — verification only.

---

## Self-Review Notes

**Spec coverage:** All 4 changes from the spec are covered: session count fix (Task 2), read_session_history (Task 1), appended section (Task 2), split count display (Task 2).

**Placeholder scan:** No TBDs. All code blocks complete.

**Type consistency:** `read_session_history(sessions_dir, max_entries=10) -> list[dict]` — consistent across Task 1 definition and Task 2 caller.
