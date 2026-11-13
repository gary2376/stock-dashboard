from __future__ import annotations

import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
E2E_ENABLED = os.environ.get("SD_E2E") == "1"
PLAYWRIGHT_AVAILABLE = (
    importlib.util.find_spec("playwright") is not None
    and importlib.util.find_spec("playwright.sync_api") is not None
)
TOUR_PAGES = ("index", "dashboard", "detail", "strategy", "overview")


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: browser end-to-end tests (requires SD_E2E=1 and Playwright)")


def pytest_collection_modifyitems(items):
    e2e_root = Path(__file__).parent
    skip_reason = None
    if not E2E_ENABLED:
        skip_reason = "tests/e2e requires SD_E2E=1"
    elif not PLAYWRIGHT_AVAILABLE:
        skip_reason = "tests/e2e requires the playwright Python package"
    for item in items:
        if e2e_root in Path(str(item.path)).parents:
            item.add_marker(pytest.mark.e2e)
            if skip_reason:
                item.add_marker(pytest.mark.skip(reason=skip_reason))


@pytest.fixture(scope="session", autouse=True)
def _e2e_gate():
    if not E2E_ENABLED:
        pytest.skip("tests/e2e requires SD_E2E=1")
    if not PLAYWRIGHT_AVAILABLE:
        pytest.skip("tests/e2e requires the playwright Python package")


def _python() -> str:
    project_python = ROOT / ".venv" / "bin" / "python3"
    return str(project_python if project_python.exists() else Path(sys.executable))


@pytest.fixture(scope="session")
def demo_data_template(_e2e_gate, tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("sd-e2e-demo")
    subprocess.run(
        [_python(), str(ROOT / "scripts" / "load_demo.py"), str(data_dir)],
        cwd=ROOT,
        check=True,
        timeout=120,
    )
    return data_dir


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(data_dir: Path, log_path: Path):
    port = _free_port()
    env = os.environ.copy()
    env.update({
        "STOCK_DASHBOARD_DATA": str(data_dir),
        "STOCK_DASHBOARD_DEMO": "1",
        "PYTHONUNBUFFERED": "1",
    })
    log = log_path.open("w+", encoding="utf-8")
    process = subprocess.Popen(
        [_python(), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT / "backend",
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            with urllib.request.urlopen(f"{base_url}/api/info", timeout=0.5) as response:
                if response.status == 200:
                    return process, log, base_url
        except OSError:
            time.sleep(0.1)
    log.seek(0)
    output = log.read()
    process.terminate()
    process.wait(timeout=5)
    log.close()
    pytest.fail(f"uvicorn did not start on {base_url}:\n{output}")


def _stop_server(process, log):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    log.close()


@pytest.fixture(scope="module")
def app_url(request, demo_data_template, tmp_path_factory):
    module_name = re.sub(r"[^a-zA-Z0-9_-]", "-", request.module.__name__)
    module_root = tmp_path_factory.mktemp(f"sd-e2e-{module_name}")
    data_dir = module_root / "data"
    shutil.copytree(demo_data_template, data_dir)
    process, log, base_url = _start_server(data_dir, module_root / "uvicorn.log")
    yield base_url
    _stop_server(process, log)


@pytest.fixture(scope="module")
def empty_app_url(_e2e_gate, tmp_path_factory):
    module_root = tmp_path_factory.mktemp("sd-e2e-empty")
    data_dir = module_root / "data"
    data_dir.mkdir()
    process, log, base_url = _start_server(data_dir, module_root / "uvicorn.log")
    yield base_url
    _stop_server(process, log)


@pytest.fixture(scope="session")
def browser(_e2e_gate):
    from playwright.sync_api import Error, sync_playwright

    manager = sync_playwright().start()
    try:
        try:
            instance = manager.chromium.launch(channel="chrome", headless=True)
        except Error:
            instance = manager.chromium.launch(headless=True)
        yield instance
        instance.close()
    finally:
        manager.stop()


def storage_script(*, consent=True, tours=True) -> str:
    statements = []
    if consent:
        statements.append("localStorage.setItem('sd_consent_v1','1')")
    if tours:
        statements.extend(
            f"localStorage.setItem('sd_tour_v1_{page}','1')" for page in TOUR_PAGES
        )
    return ";".join(statements)


@pytest.fixture
def page_factory(browser):
    contexts = []

    def make(*, width=1100, height=900, consent=True, tours=True):
        context = browser.new_context(viewport={"width": width, "height": height})
        script = storage_script(consent=consent, tours=tours)
        if script:
            context.add_init_script(script)
        page = context.new_page()
        contexts.append(context)
        return page

    yield make
    for context in reversed(contexts):
        context.close()


@pytest.fixture
def assert_no_page_errors():
    tracked = []

    def track(page):
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        tracked.append(errors)
        return errors

    yield track
    assert not [error for errors in tracked for error in errors]
