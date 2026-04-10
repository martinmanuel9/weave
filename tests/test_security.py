"""Tests for the weave security module."""
import os
from pathlib import Path


def test_check_write_deny_blocks_dotenv(temp_dir):
    from weave.core.security import check_write_deny
    patterns = [".env", "*.pem"]
    denied = check_write_deny([".env", "src/main.py"], temp_dir, patterns)
    assert ".env" in denied
    assert "src/main.py" not in denied


def test_check_write_deny_glob_patterns(temp_dir):
    from weave.core.security import check_write_deny
    patterns = ["*.pem", "*.key"]
    denied = check_write_deny(
        ["cert.pem", "id_rsa.key", "safe.txt"], temp_dir, patterns
    )
    assert "cert.pem" in denied
    assert "id_rsa.key" in denied
    assert "safe.txt" not in denied


def test_check_write_deny_symlink_aware(temp_dir):
    """Writing through a symlink to a denied path should be detected."""
    from weave.core.security import check_write_deny
    real_env = temp_dir / ".env"
    real_env.write_text("SECRET=x")
    link = temp_dir / "innocuous.txt"
    os.symlink(real_env, link)

    patterns = [".env"]
    denied = check_write_deny(["innocuous.txt"], temp_dir, patterns)
    assert "innocuous.txt" in denied


def test_check_write_deny_nested_path(temp_dir):
    from weave.core.security import check_write_deny
    patterns = [".harness/config.json"]
    denied = check_write_deny(
        [".harness/config.json", ".harness/context/spec.md"], temp_dir, patterns
    )
    assert ".harness/config.json" in denied
    assert ".harness/context/spec.md" not in denied
