"""E2E (Playwright + axe) — smoke, reload and accessibility.

See ``tests/e2e/conftest.py`` for how to run it (``E2E=1`` gate). Mock mode
is the target: deterministic answers, no login, no LLM calls.
"""

from __future__ import annotations

from pathlib import Path

AXE = Path(__file__).parent / "vendor" / "axe.min.js"
COMPOSER = 'textarea:not([name="g-recaptcha-response"]):not(.g-recaptcha-response)'

# One of the nine starter questions: concrete, never guidance-blocked and
# never a decision-flow question — the mock always answers it.
QUESTION = "¿Qué gastos son realmente deducibles en Hacienda?"
DEMO_FOOTER = "respuesta simulada por el modo demostración"
WELCOME = "Asesor Fiscal IA"
DEMO_NOTICE = "Modo demostración"


def _open(pg, base_url: str):
    pg.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=60000)
    pg.locator(COMPOSER).first.wait_for(state="visible", timeout=30000)
    # The composer is server-rendered and appears before the websocket
    # session is live, so asking that early silently drops the message.
    # The welcome is pushed by `on_chat_start`: it is the real ready signal.
    pg.get_by_text(WELCOME).first.wait_for(state="visible", timeout=30000)
    return pg


def _ask(pg, question: str = QUESTION):
    ta = pg.locator(COMPOSER).first
    ta.wait_for(state="visible", timeout=30000)
    ta.fill(question)
    pg.keyboard.press("Enter")


def _wait_answer(pg, timeout: int = 30000):
    """Waits until the assistant finished (mock footer present)."""
    pg.get_by_text(DEMO_FOOTER, exact=False).first.wait_for(timeout=timeout)


def _run_axe(pg) -> list:
    pg.add_script_tag(path=str(AXE))
    result = pg.evaluate(
        "async () => await window.axe.run(document, { resultTypes: ['violations'] })"
    )
    return result.get("violations") or []


class TestSmoke:
    def test_welcome_loads_with_composer(self, page, base_url):
        _open(page, base_url)
        assert "/login" not in page.url, "mock mode must not redirect to login"
        notice = page.get_by_text(DEMO_NOTICE, exact=False).first
        notice.wait_for(state="visible", timeout=30000)
        ta = page.locator(COMPOSER).first
        assert ta.is_editable()

    def test_ask_returns_mock_answer(self, page, base_url):
        _open(page, base_url)
        _ask(page)
        # The user turn is rendered…
        assert page.get_by_text(QUESTION, exact=False).first.is_visible(timeout=30000)
        # …and the mock answers end-to-end (adapter → step → message).
        _wait_answer(page)
        assert page.get_by_text(DEMO_FOOTER, exact=False).first.is_visible()

    def test_reload_leaves_app_usable(self, page, base_url):
        """Reload after an exchange: the app comes back usable, not frozen.

        Restoring the CONVERSATION HISTORY on reload needs a persisted
        thread, and Chainlit only resumes a thread for an authenticated
        user (socket.resume_thread bails out when `session.user` is None).
        Mock mode is anonymous by design, so history restoration is covered
        by the unit suite (TestChatResume) and stays blocked on the backend
        auth contract; what this test pins is the symptom that regression
        produced here — a reload that never recovers the composer.
        """
        _open(page, base_url)
        _ask(page)
        _wait_answer(page)

        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.get_by_text(WELCOME).first.wait_for(state="visible", timeout=30000)
        ta = page.locator(COMPOSER).first
        ta.wait_for(state="visible", timeout=30000)
        assert ta.is_editable()


class TestAccessibility:
    def test_axe_no_critical_or_serious_violations(self, page, base_url):
        """axe-core (vendored, offline) on the initial and answered states.

        Only critical/serious impacts fail the run: minor issues are
        reported in the assertion message only when they co-occur, per the
        project rule of chasing real UX problems, not scores.
        """
        _open(page, base_url)
        violations = _run_axe(page)

        _ask(page)
        _wait_answer(page)
        page.wait_for_timeout(600)  # let the streamed DOM settle
        violations += _run_axe(page)

        serious = [
            v for v in violations if v.get("impact") in ("critical", "serious")
        ]
        detail = "\n".join(
            f"[{v.get('impact')}] {v.get('id')}: {v.get('help')} "
            f"({len(v.get('nodes') or [])} nodos)"
            for v in serious
        )
        assert not serious, f"Violaciones axe critical/serious:\n{detail}"


class TestResponsive:
    def test_composer_usable_at_420px(self, page, base_url):
        # Same session browser as `page`; only the viewport changes. A
        # fixture named `browser` collides with an installed pytest plugin
        # (see conftest), so the browser is reached through the page.
        context = page.context.browser.new_context(viewport={"width": 420, "height": 860})
        pg = context.new_page()
        try:
            _open(pg, base_url)
            ta = pg.locator(COMPOSER).first
            assert ta.is_visible()
            assert ta.is_editable()
        finally:
            context.close()
