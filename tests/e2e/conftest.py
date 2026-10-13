"""E2E fixtures — Playwright against a mock-mode AsesorIA instance.

Opt-in only, so a plain ``python -m pytest tests/`` (unit run, CI ``test``
job) stays green without a server, a browser or playwright installed:

    E2E=1 python -m pytest tests/e2e -q

By default the suite starts its OWN instance on a free port in mock mode
(no GROQ key → demo mock). OAuth is forced off for that child process:
``chainlit run`` loads ``ui/.env`` on top of the process environment, and
dotenv never overrides keys that are already set, so emptying the
``OAUTH_*`` variables here is what actually prevents the ``/login``
redirect a developer's local credentials would otherwise cause.

Set ``E2E_BASE_URL`` to test an already-running instance instead; it must
be mock mode and must not require login.

Run this package in its OWN pytest invocation. Playwright's sync API drives
the asyncio loop from the main thread through greenlet, and once it has run
the leftover loop state breaks every ``pytest-asyncio`` test collected in
the same session (133 failures in ``tests/test_*``). That is also why the
default run keeps these tests skipped: ``python -m pytest tests/ -q`` must
stay green and fast.

The browser fixture is named ``e2e_browser`` on purpose: a session fixture
called ``browser`` collides with an installed pytest plugin in this
environment and, through it, breaks unrelated asyncio tests as soon as
``tests/e2e`` is collected (133 failures). Keep the ``e2e_`` prefix.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

ARTIFACTS = Path(__file__).parent / "artifacts"
UI_DIR = Path(__file__).resolve().parents[2] / "ui"
READY_TIMEOUT_S = 120


def _enabled() -> bool:
    return bool(os.environ.get("E2E_BASE_URL", "").strip() or os.environ.get("E2E"))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _mock_env(db_path: Path) -> dict:
    env = os.environ.copy()
    # Blank, never delete: `ui/app.py` calls `load_dotenv()` (no override)
    # and dotenv only skips keys that are already SET — an empty string is
    # enough for `if os.environ.get("OAUTH_...")` to stay falsy.
    for key in ("OAUTH_GOOGLE_CLIENT_ID", "OAUTH_GOOGLE_CLIENT_SECRET", "GROQ_API_KEY"):
        env[key] = ""
    for key in list(env):
        if key.startswith("OAUTH_"):
            env[key] = ""
    env["CHAINLIT_SQLITE_PATH"] = str(db_path)
    return env


def _wait_ready(url: str, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"server exited early (code {proc.returncode}):\n{tail}")
        try:
            with urlopen(url, timeout=5) as response:
                if response.status < 500:
                    return
        except HTTPError:
            return  # reachable; the app answered with an error status
        except URLError:
            time.sleep(0.5)
    raise RuntimeError(f"server not ready after {READY_TIMEOUT_S}s ({url})")


@contextmanager
def _mock_server():
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    state_dir = ARTIFACTS.parent / ".server"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "server.log"
    log_path.write_text("", encoding="utf-8")

    command = [
        sys.executable,
        "-m",
        "chainlit",
        "run",
        "app.py",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--headless",
    ]
    with log_path.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            command,
            cwd=str(UI_DIR),
            env=_mock_env(state_dir / "chainlit.db"),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        _wait_ready(url, proc, log_path)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
            proc.kill()
            proc.wait(timeout=15)


@pytest.fixture(scope="session")
def base_url() -> str:
    explicit = os.environ.get("E2E_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    if not _enabled():
        pytest.skip("E2E: set E2E=1 (spawn mock instance) or E2E_BASE_URL (existing one)")
    with _mock_server() as url:
        yield url


@pytest.fixture(scope="session")
def e2e_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - env without playwright
        pytest.skip(f"playwright not installed: {exc}")
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def e2e_browser(e2e_playwright):
    browser = e2e_playwright.chromium.launch(headless=True)
    yield browser
    browser.close()


@pytest.fixture
def page(e2e_browser, base_url):
    context = e2e_browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    yield pg
    context.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Keeps a full-page screenshot of failed E2E tests (CI artifacts)."""
    outcome = yield
    report = outcome.get_result()
    if not report.failed or call.when != "call":
        return
    pg = getattr(item, "funcargs", {}).get("page")
    if pg is None:
        return
    try:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.-]+", "_", item.name)
        pg.screenshot(path=str(ARTIFACTS / f"{safe}.png"), full_page=True)
    except Exception:
        pass  # the original failure is what matters
