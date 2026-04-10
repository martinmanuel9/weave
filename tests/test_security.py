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


def test_check_write_deny_honors_allow_override(temp_dir):
    """Allow pattern exempts a file that matches a deny pattern."""
    from weave.core.security import check_write_deny
    denied = check_write_deny(
        files_changed=[".env"],
        working_dir=temp_dir,
        patterns=[".env"],
        allow_patterns=[".env"],
    )
    assert denied == []


def test_check_write_deny_allow_does_not_leak_to_other_files(temp_dir):
    """Allow is surgical — it exempts only matching files, not others."""
    from weave.core.security import check_write_deny
    denied = check_write_deny(
        files_changed=[".env", "cert.pem"],
        working_dir=temp_dir,
        patterns=[".env", "*.pem"],
        allow_patterns=[".env"],
    )
    assert denied == ["cert.pem"]


def test_scanner_detects_pth_injection(temp_dir):
    from weave.core.security import scan_files, DEFAULT_RULES
    f = temp_dir / "evil.pth"
    f.write_text("import os; os.system('ls')")
    findings = scan_files(["evil.pth"], temp_dir, DEFAULT_RULES)
    assert any(x.rule_id == "pth-injection" for x in findings)


def test_scanner_detects_base64_exec(temp_dir):
    from weave.core.security import scan_files, DEFAULT_RULES
    f = temp_dir / "bad.py"
    # Build string via fragment concatenation so this test file does not
    # contain the literal pattern the scanner looks for.
    bad = "import base64\n" + "e" + "xec(base64.b64decode('cHJpbnQoMSk='))"
    f.write_text(bad)
    findings = scan_files(["bad.py"], temp_dir, DEFAULT_RULES)
    assert any(x.rule_id == "base64-exec" for x in findings)


def test_scanner_detects_credential_harvest(temp_dir):
    from weave.core.security import scan_files, DEFAULT_RULES
    f = temp_dir / "snoop.py"
    f.write_text("open('/home/user/.ssh/id_rsa').read()")
    findings = scan_files(["snoop.py"], temp_dir, DEFAULT_RULES)
    assert any(x.rule_id == "credential-harvest" for x in findings)


def test_scanner_clean_file_returns_no_findings(temp_dir):
    from weave.core.security import scan_files, DEFAULT_RULES
    f = temp_dir / "good.py"
    f.write_text("def add(a, b):\n    return a + b\n")
    findings = scan_files(["good.py"], temp_dir, DEFAULT_RULES)
    assert findings == []


def test_resolve_action_sandbox_downgrades_deny_to_warn():
    from weave.core.security import resolve_action
    assert resolve_action("deny", phase="sandbox") == "warn"
    assert resolve_action("warn", phase="sandbox") == "warn"
    assert resolve_action("log", phase="sandbox") == "log"


def test_resolve_action_mvp_preserves_deny():
    from weave.core.security import resolve_action
    assert resolve_action("deny", phase="mvp") == "deny"
    assert resolve_action("deny", phase="enterprise") == "deny"
    assert resolve_action("warn", phase="mvp") == "warn"
