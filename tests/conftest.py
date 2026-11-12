import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Every module reads STOCK_DASHBOARD_DATA once at import. Point the whole test session at a throwaway
# directory BEFORE any test module imports them, so no test can ever write into the real data/ dir.
_TEST_DATA = tempfile.mkdtemp(prefix="sd-test-data-")
os.environ["STOCK_DASHBOARD_DATA"] = _TEST_DATA
atexit.register(shutil.rmtree, _TEST_DATA, ignore_errors=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_pins(tmp_path, monkeypatch):
    """Each test gets its own empty pins store (pins.json is read by every strategy check)."""
    import pins
    monkeypatch.setattr(pins, "DATA_DIR", tmp_path / "pins_data")
    pins._compile_times.clear()
