import pytest
from pathlib import Path
import tempfile
import shutil


@pytest.fixture
def temp_dir():
    d = Path(tempfile.mkdtemp(prefix="weave-test-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def harness_dir(temp_dir):
    h = temp_dir / ".harness"
    h.mkdir()
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (h / sub).mkdir()
    return h
