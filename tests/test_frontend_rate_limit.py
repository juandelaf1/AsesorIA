"""Tests del límite de mensajes por sesión (ui.rate_limit + integración)."""
from types import SimpleNamespace

import pytest

import ui.app as app
import ui.formatters as fmt
from ui.rate_limit import MessageRateLimiter


def _fake_clock():
    """Reloj controlable: devuelve un mutable que el test avanza."""
    state = {"now": 1000.0}
    return state, lambda: state["now"]


def test_allows_up_to_max_then_denies() -> None:
    limiter = MessageRateLimiter(3, 60.0)

    assert [limiter.allow("s1") for _ in range(3)] == [True, True, True]
    assert limiter.allow("s1") is False


def test_window_expiry_frees_slots() -> None:
    state, clock = _fake_clock()
    limiter = MessageRateLimiter(2, 60.0, clock=clock)

    assert limiter.allow("s1")
    state["now"] += 30
    assert limiter.allow("s1")
    assert limiter.allow("s1") is False
    state["now"] += 31  # el primer mensaje sale de la ventana
    assert limiter.allow("s1") is True


def test_sessions_do_not_share_quota() -> None:
    limiter = MessageRateLimiter(1, 60.0)

    assert limiter.allow("s1") is True
    assert limiter.allow("s1") is False
    assert limiter.allow("s2") is True


def test_disabled_when_non_positive() -> None:
    assert MessageRateLimiter(0, 60.0).enabled is False
    assert MessageRateLimiter(10, 0.0).enabled is False
    limiter = MessageRateLimiter(0, 60.0)
    assert all(limiter.allow("s1") for _ in range(100))


def test_retry_after_counts_until_next_slot() -> None:
    state, clock = _fake_clock()
    limiter = MessageRateLimiter(2, 60.0, clock=clock)

    assert limiter.retry_after("s1") == 0.0  # sin uso todavía
    assert limiter.allow("s1")
    assert limiter.retry_after("s1") == 0.0  # queda hueco en la ventana
    state["now"] += 30
    assert limiter.allow("s1")  # ventana llena
    assert limiter.retry_after("s1") == pytest.approx(30.0)
    state["now"] += 20
    assert limiter.retry_after("s1") == pytest.approx(10.0)
    state["now"] += 11  # el primer mensaje sale de la ventana
    assert limiter.allow("s1") is True
    assert limiter.retry_after("s1") == pytest.approx(29.0)


def test_limiter_is_wired_with_env_defaults() -> None:
    assert app.CHAT_RATE_LIMIT_MESSAGES == 10
    assert app.CHAT_RATE_LIMIT_WINDOW_S == 60.0
    assert app.RATE_LIMITER.enabled is True


def test_rate_limit_key_is_stable_per_session(monkeypatch) -> None:
    store: dict = {}
    monkeypatch.setattr(
        app.cl,
        "user_session",
        SimpleNamespace(get=store.get, set=store.__setitem__),
    )

    first = app._rate_limit_key()
    second = app._rate_limit_key()

    assert first == second
    assert len(first) == 32  # uuid4().hex


def test_rate_limited_copy_is_friendly_and_non_technical() -> None:
    text = fmt.format_rate_limited(12.4)

    assert "segundos" in text
    assert "12" in text
    lowered = text.lower()
    for token in ("rate", "limit", "429", "quota", "http"):
        assert token not in lowered


@pytest.mark.asyncio
async def test_handle_message_throttles_bursts_with_session(monkeypatch) -> None:
    store: dict = {}
    monkeypatch.setattr(
        app.cl,
        "user_session",
        SimpleNamespace(get=store.get, set=store.__setitem__),
    )
    monkeypatch.setattr(app, "RATE_LIMITER", MessageRateLimiter(2, 60.0))

    notified: list = []
    answered: list = []

    async def fake_notify(text, actions=None):
        notified.append(text)

    async def fake_answer(question):
        answered.append(question)

    monkeypatch.setattr(app, "_notify", fake_notify)
    monkeypatch.setattr(app, "_answer_question", fake_answer)
    monkeypatch.setattr(app, "_get_documents", lambda: [])
    monkeypatch.setattr(app, "_has_asked", lambda: False)
    monkeypatch.setattr(app, "is_ambiguous", lambda q: False)

    question = "¿Qué gastos son deducibles en el IRPF?"
    for _ in range(2):
        await app._handle_message(SimpleNamespace(content=question))
    await app._handle_message(SimpleNamespace(content=question))

    assert answered == [question, question]
    assert len(notified) == 1
    assert "segundos" in notified[0]
