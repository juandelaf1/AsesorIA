"""Streaming answer contract — pipeline → engine → adapter event flow.

Covers the token-streaming path added for UX fluidity
(``RAGPipeline.answer_query_stream``, ``RAGEngine.query_stream`` and
``RagAdapter.ask_stream``) plus the event-loop offload of sync backends
(``asyncio.to_thread``). The classic non-streaming contract (``query`` /
``ask``) is asserted to stay byte-identical — zero-regression rule; the
existing suites already cover it and are NOT modified.
"""

from __future__ import annotations

import threading

import pytest
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.rag.engine import RAGEngine
from src.rag.pipeline import RAGPipeline
from ui.rag_adapter import RagAdapter

_DOC = Document(
    page_content="Los gastos deducibles reducen la base imponible del IRPF.",
    metadata={"source": "irpf.pdf", "page": 3},
)


class FakeRetriever:
    """Duck-typed retriever: records queries, returns canned documents."""

    def __init__(self, docs=None):
        self.docs = list(docs if docs is not None else [_DOC])
        self.queries: list[str] = []

    def invoke(self, query: str):
        self.queries.append(query)
        return self.docs


def _pipeline() -> RAGPipeline:
    return RAGPipeline(
        retriever=FakeRetriever(),
        # FakeListChatModel pops ONE response per LLM call; .stream()
        # yields it character by character.
        llm=FakeListChatModel(responses=["Los gastos son deducibles."]),
    )


class TestPipelineStream:
    """answer_query_stream: retrieved → token… → done with query() metrics."""

    def test_events_sequence_and_done_payload(self):
        events = list(_pipeline().answer_query_stream("¿Cuáles son?"))

        types = [e["type"] for e in events]
        assert types[0] == "retrieved"
        assert types[-1] == "done"
        assert set(types[1:-1]) == {"token"}

        tokens = [e["text"] for e in events if e["type"] == "token"]
        done = events[-1]
        assert done["answer"] == "".join(tokens) == "Los gastos son deducibles."
        assert set(done["metrics"]) == {
            "retrieval_latency_s",
            "generation_latency_s",
            "total_latency_s",
        }
        assert done["source_documents"] == [_DOC]

    def test_retrieved_event_carries_documents_and_uses_retriever(self):
        pipeline = _pipeline()
        events = list(pipeline.answer_query_stream("¿Qué es deducible?"))

        retrieved = events[0]
        assert retrieved["type"] == "retrieved"
        assert retrieved["documents"] == [_DOC]
        assert pipeline.retriever.queries == ["¿Qué es deducible?"]


class FakeStreamPipeline:
    """Engine-level pipeline double: canned events, never the classic path."""

    def __init__(self) -> None:
        self.history_seen: object = "unset"

    def answer_query_stream(self, question, history=None):
        self.history_seen = history
        yield {
            "type": "retrieved",
            "documents": [_DOC],
            "retrieval_latency_s": 0.01,
        }
        yield {"type": "token", "text": "Según "}
        yield {"type": "token", "text": "el documento."}
        yield {
            "type": "done",
            "answer": "Según el documento.",
            "source_documents": [_DOC],
            "metrics": {
                "retrieval_latency_s": 0.01,
                "generation_latency_s": 0.2,
                "total_latency_s": 0.21,
            },
        }

    def answer_query(self, question, history=None):
        raise AssertionError("the stream path must not call answer_query()")


class TestEngineStream:
    """query_stream: same payload shape as query(), progressive events."""

    def test_done_payload_matches_query_shape(self):
        engine = RAGEngine(pipeline=FakeStreamPipeline())
        events = list(engine.query_stream("¿Qué es?"))

        assert [e["type"] for e in events] == ["retrieved", "token", "token", "done"]
        assert events[0]["n_sources"] == 1

        done = events[-1]
        assert set(done) == {
            "type",
            "question",
            "answer",
            "sources",
            "metrics",
            "latency_ms",
            "raw_documents",
        }
        assert done["question"] == "¿Qué es?"
        assert done["answer"] == "Según el documento."
        assert done["sources"] == [
            {
                "source": "irpf.pdf",
                "page": "Pág. 3",
                "snippet": _DOC.page_content,
            }
        ]
        assert done["latency_ms"] == 210.0
        assert done["raw_documents"] == [_DOC]

    def test_history_forwarded_to_pipeline(self):
        pipeline = FakeStreamPipeline()
        engine = RAGEngine(pipeline=pipeline)
        history = [{"role": "user", "content": "pregunta previa"}]

        list(engine.query_stream("¿Y ahora?", history=history))

        assert pipeline.history_seen == history

    def test_empty_question_single_done_event(self):
        engine = RAGEngine(pipeline=FakeStreamPipeline())
        events = list(engine.query_stream("   "))

        assert len(events) == 1
        assert events[0]["type"] == "done"
        assert events[0]["answer"] == "La consulta no puede estar vacía."


class StreamingBackend:
    """Engine-shaped double exposed through the adapter seam."""

    def __init__(self) -> None:
        self.history_seen: object = "unset"

    def query_stream(self, question, history=None):
        self.history_seen = history
        yield {"type": "retrieved", "n_sources": 2}
        yield {"type": "token", "text": "hola "}
        yield {"type": "token", "text": "mundo"}
        yield {
            "type": "done",
            "question": question,
            "answer": "hola mundo",
            "sources": [{"source": "doc.pdf", "page": "Pág. 1", "snippet": "…"}],
            "metrics": {
                "retrieval_latency_s": 0.0,
                "generation_latency_s": 0.1,
                "total_latency_s": 0.1,
            },
            "latency_ms": 100.0,
            "raw_documents": [],
        }


class LegacyBackend:
    """Classic non-streaming shape: ``query(question, documents)`` only."""

    def query(self, question, documents=None):
        return {"answer": "ok", "sources": []}


class TestAdapterStream:
    @pytest.mark.asyncio
    async def test_tokens_then_normalized_response(self):
        backend = StreamingBackend()
        events = [
            e
            async for e in RagAdapter(backend=backend).ask_stream(
                "q", [], [], history=[{"role": "user", "content": "previa"}]
            )
        ]

        assert [e["type"] for e in events] == ["retrieved", "token", "token", "response"]
        response = events[-1]["response"]
        assert response.answer == "hola mundo"
        assert response.sources[0].document == "doc.pdf"
        assert backend.history_seen == [{"role": "user", "content": "previa"}]

    @pytest.mark.asyncio
    async def test_legacy_backend_single_response_event(self):
        events = [e async for e in RagAdapter(backend=LegacyBackend()).ask_stream("q", [])]

        assert len(events) == 1
        assert events[0]["type"] == "response"
        direct = await RagAdapter(backend=LegacyBackend()).ask("q", [])
        assert events[0]["response"].answer == direct.answer == "ok"

    @pytest.mark.asyncio
    async def test_mock_backend_single_response_event(self):
        adapter = RagAdapter(backend=None)
        events = [e async for e in adapter.ask_stream("q", [])]

        assert adapter.is_mock is True
        assert len(events) == 1
        assert events[0]["type"] == "response"
        assert events[0]["response"].answer  # demo answer, never empty text

    @pytest.mark.asyncio
    async def test_engine_and_adapter_end_to_end(self):
        engine = RAGEngine(pipeline=FakeStreamPipeline())
        events = [e async for e in RagAdapter(backend=engine).ask_stream("¿Qué es?", [])]

        assert [e["type"] for e in events] == ["retrieved", "token", "token", "response"]
        response = events[-1]["response"]
        assert response.answer == "Según el documento."
        assert response.sources[0].document == "irpf.pdf"
        assert response.sources[0].page == 3
        assert response.latency_ms == 210.0

    @pytest.mark.asyncio
    async def test_sync_backend_runs_off_event_loop(self):
        """Blocking sync work must execute in a worker thread (to_thread)."""

        class ThreadBackend:
            def query(self, question, documents=None):
                return {"answer": threading.current_thread().name, "sources": []}

        response = await RagAdapter(backend=ThreadBackend()).ask("q", [])
        assert response.answer  # executed somewhere
        # …in a worker thread, never on the event-loop thread itself:
        assert response.answer != threading.current_thread().name
