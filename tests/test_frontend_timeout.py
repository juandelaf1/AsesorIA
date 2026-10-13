"""Backend response deadline — `ui/rag_adapter.py` + timeout UI state.

Covers the P0 latency fix: every backend round-trip (`ask`) and every
streaming event (`ask_stream`) is bounded by `RAG_BACKEND_TIMEOUT_S`
(default 60 s), so a hung provider call surfaces as the friendly
«La consulta ha tardado demasiado» state instead of leaving the composer
waiting forever. Deterministic: fake backends sleep longer than a tiny
configured deadline — no LLM, no network.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import ui.rag_adapter as adapter_module
from ui.formatters import format_error, format_error_message
from ui.rag_adapter import RagAdapter, backend_timeout

RAW = {"answer": "Respuesta de prueba.", "sources": []}


class SlowSyncBackend:
    """Sync backend (the real engine's shape) blocked longer than the deadline."""

    def __init__(self, delay: float):
        self.delay = delay

    def query(self, question, documents):
        time.sleep(self.delay)
        return RAW


class SlowAsyncBackend:
    """Async backend whose coroutine never resolves in time."""

    def __init__(self, delay: float):
        self.delay = delay

    async def query(self, question, documents):
        await asyncio.sleep(self.delay)
        return RAW


class BlockingStreamBackend:
    """Streaming backend: the first event arrives after the deadline."""

    def __init__(self, delay: float):
        self.delay = delay

    def query_stream(self, question, history=None):
        time.sleep(self.delay)
        yield {"type": "retrieved", "n_sources": 1}
        yield {"type": "done", "answer": "tarde", "sources": []}


class FastStreamBackend:
    """Streaming backend answering immediately."""

    def query_stream(self, question, history=None):
        yield {"type": "retrieved", "n_sources": 1}
        yield {"type": "done", "answer": "ok", "sources": []}


class TestBackendTimeoutConfig:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("RAG_BACKEND_TIMEOUT_S", raising=False)
        assert backend_timeout() == adapter_module.BACKEND_TIMEOUT_S

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("RAG_BACKEND_TIMEOUT_S", "5")
        assert backend_timeout() == 5.0

    @pytest.mark.parametrize("raw", ["", "0", "-3", "abc", "1.5.2"])
    def test_invalid_or_non_positive_falls_back_to_default(
        self, monkeypatch, raw
    ):
        monkeypatch.setenv("RAG_BACKEND_TIMEOUT_S", raw)
        assert backend_timeout() == adapter_module.BACKEND_TIMEOUT_S


class TestAskTimeout:
    @pytest.mark.asyncio
    async def test_slow_sync_backend_raises_timeout(self, monkeypatch):
        monkeypatch.setenv("RAG_BACKEND_TIMEOUT_S", "0.05")
        adapter = RagAdapter(backend=SlowSyncBackend(delay=0.6))
        with pytest.raises(TimeoutError):
            await adapter.ask("¿Cuándo me doy de alta?", [])

    @pytest.mark.asyncio
    async def test_slow_async_backend_raises_timeout(self, monkeypatch):
        monkeypatch.setenv("RAG_BACKEND_TIMEOUT_S", "0.05")
        adapter = RagAdapter(backend=SlowAsyncBackend(delay=0.6))
        with pytest.raises(TimeoutError):
            await adapter.ask("¿Cuándo me doy de alta?", [])

    @pytest.mark.asyncio
    async def test_fast_sync_backend_still_answers(self, monkeypatch):
        monkeypatch.setenv("RAG_BACKEND_TIMEOUT_S", "10")
        adapter = RagAdapter(backend=SlowSyncBackend(delay=0))
        resp = await adapter.ask("¿Cuándo me doy de alta?", [])
        assert resp.answer == "Respuesta de prueba."

    @pytest.mark.asyncio
    async def test_backend_failure_still_propagates_unchanged(self, monkeypatch):
        """A real backend error must NOT be disguised as a timeout."""
        monkeypatch.setenv("RAG_BACKEND_TIMEOUT_S", "10")

        class BrokenBackend:
            def query(self, question, documents):
                raise RuntimeError("chroma caído")

        adapter = RagAdapter(backend=BrokenBackend())
        with pytest.raises(RuntimeError):
            await adapter.ask("¿Pregunta?", [])


class TestStreamTimeout:
    @pytest.mark.asyncio
    async def test_first_event_is_bounded(self, monkeypatch):
        monkeypatch.setenv("RAG_BACKEND_TIMEOUT_S", "0.05")
        adapter = RagAdapter(backend=BlockingStreamBackend(delay=0.6))
        with pytest.raises(TimeoutError):
            async for _event in adapter.ask_stream("¿Pregunta?", []):
                pytest.fail("no event may arrive before the deadline")

    @pytest.mark.asyncio
    async def test_fast_stream_delivers_events(self, monkeypatch):
        monkeypatch.setenv("RAG_BACKEND_TIMEOUT_S", "10")
        adapter = RagAdapter(backend=FastStreamBackend())
        events = [event async for event in adapter.ask_stream("¿Pregunta?", [])]
        assert [event["type"] for event in events] == ["retrieved", "response"]
        assert events[-1]["response"].answer == "ok"


class TestTimeoutCopy:
    def test_timeout_state_is_friendly(self):
        msg = format_error("timeout")
        assert "tardado demasiado" in msg
        assert "error técnico" not in msg
        assert "TimeoutError" not in msg

    def test_timeout_exception_maps_to_timeout_state(self):
        msg = format_error_message(TimeoutError("connection reset by peer"))
        assert "tardado demasiado" in msg
        assert "connection reset by peer" not in msg

    def test_connection_state_still_maps_connection_errors(self):
        msg = format_error_message(ConnectionError("refused"))
        assert "conectar" in msg
