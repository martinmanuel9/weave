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
