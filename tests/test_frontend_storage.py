"""Local element storage (H-32) — `ui/persistence.py`.

Before this, `SQLAlchemyDataLayer.create_element` bailed out without a
storage provider: no `elements` rows were written and the side-panel source
chips vanished after a reload. `LocalStorageClient` persists them with a
self-contained `data:` URI URL (the frontend renders `element.url` via
plain `fetch`; the `/project/file/` endpoint only works inside a live
session).

Deterministic, offline: tmp SQLite DB + tmp storage dir, no Chainlit
server, no LLM.
"""

from __future__ import annotations

import base64
import sqlite3
import sys
from types import SimpleNamespace

import pytest
from chainlit.element import Text

from ui.persistence import (
    EMBED_MAX_BYTES,
    LocalStorageClient,
    SqliteDataLayer,
    build_data_layer,
    ensure_sqlite_schema,
)

_PREFIX = "data:text/plain;base64,"


@pytest.fixture(autouse=True)
def cl_context():
    """Minimal Chainlit context (same pattern as test_native_ux.py):
    upstream's `queue_until_user_message` decorator on `create_element`
    inspects `context.session` and raises without a context."""
    ctx_mod = sys.modules["chainlit.context"]
    token = ctx_mod.context_var.set(
        SimpleNamespace(session=SimpleNamespace(thread_id="thread-1"))
    )
    yield
    ctx_mod.context_var.reset(token)


def _decode(url: str) -> str:
    assert url.startswith(_PREFIX)
    return base64.b64decode(url[len(_PREFIX) :]).decode("utf-8")


def _element(content: str, thread_id: str = "thread-1") -> Text:
    el = Text(
        content=content,
        name="Manual_Renta.pdf",
        display="side",
        thread_id=thread_id,
    )
    el.for_id = "step-1"
    el.mime = "text/plain"
    return el


async def _layer(tmp_path, storage: bool = True) -> SqliteDataLayer:
    db = tmp_path / "chainlit.db"
    ensure_sqlite_schema(db)
    kwargs = {"storage_root": tmp_path / "elements"} if storage else {}
    return SqliteDataLayer(conninfo=f"sqlite+aiosqlite:///{db.as_posix()}", **kwargs)


def _rows(db) -> list:
    with sqlite3.connect(db) as con:
        return con.execute(
            'SELECT "objectKey", url, "forId" FROM elements'
        ).fetchall()


class TestLocalStorageClient:
    @pytest.mark.asyncio
    async def test_upload_embeds_data_uri_and_writes_file(self, tmp_path):
        client = LocalStorageClient(tmp_path / "store")
        out = await client.upload_file(
            "user/elem/doc.txt", "hola áéñ", mime="text/plain"
        )
        assert out["object_key"] == "user/elem/doc.txt"
        assert _decode(out["url"]) == "hola áéñ"
        stored = tmp_path / "store" / "user" / "elem" / "doc.txt"
        assert stored.read_bytes() == "hola áéñ".encode("utf-8")

    @pytest.mark.asyncio
    async def test_upload_bytes_payload(self, tmp_path):
        client = LocalStorageClient(tmp_path / "store")
        out = await client.upload_file("a.bin", b"\x00\x01", mime="image/png")
        assert base64.b64decode(out["url"].split(",", 1)[1]) == b"\x00\x01"

    @pytest.mark.asyncio
    async def test_oversized_payload_has_no_url(self, tmp_path):
        client = LocalStorageClient(tmp_path / "store")
        out = await client.upload_file("big.txt", b"x" * (EMBED_MAX_BYTES + 1))
        assert out["url"] is None
        assert out["object_key"] == "big.txt"

    @pytest.mark.parametrize(
        "key",
        ["../evil.txt", "a/../../evil.txt", "/etc/passwd", ".."],
    )
    @pytest.mark.asyncio
    async def test_unsafe_object_keys_are_rejected(self, tmp_path, key):
        client = LocalStorageClient(tmp_path / "store")
        with pytest.raises(ValueError):
            await client.upload_file(key, "x")
        with pytest.raises(ValueError):
            await client.get_read_url(key)

    @pytest.mark.asyncio
    async def test_delete_file(self, tmp_path):
        client = LocalStorageClient(tmp_path / "store")
        await client.upload_file("user/elem/doc.txt", "contenido")
        assert await client.delete_file("user/elem/doc.txt") is True
        assert not (tmp_path / "store" / "user" / "elem" / "doc.txt").exists()
        assert await client.delete_file("user/elem/doc.txt") is False

    @pytest.mark.asyncio
    async def test_delete_rejects_unsafe_key_without_raising(self, tmp_path):
        client = LocalStorageClient(tmp_path / "store")
        assert await client.delete_file("../evil.txt") is False

    @pytest.mark.asyncio
    async def test_get_read_url_embeds_stored_file(self, tmp_path):
        client = LocalStorageClient(tmp_path / "store")
        await client.upload_file("doc.md", "# Título", mime="text/markdown")
        url = await client.get_read_url("doc.md")
        assert url.startswith("data:text/markdown;base64,")
        assert base64.b64decode(url.split(",", 1)[1]).decode("utf-8") == "# Título"

    @pytest.mark.asyncio
    async def test_get_read_url_keeps_upload_mime_not_filename_guess(self, tmp_path):
        """Side chips are named after their source doc (text inside
        'Manual.pdf'): restore must keep the upload mime, not guess pdf."""
        client = LocalStorageClient(tmp_path / "store")
        await client.upload_file("u/e/Manual.pdf", "fragmento", mime="text/plain")
        url = await client.get_read_url("u/e/Manual.pdf")
        assert url.startswith("data:text/plain;base64,")
        assert base64.b64decode(url.split(",", 1)[1]).decode("utf-8") == "fragmento"

    @pytest.mark.asyncio
    async def test_delete_file_removes_the_mime_sidecar(self, tmp_path):
        client = LocalStorageClient(tmp_path / "store")
        await client.upload_file("doc.txt", "x", mime="text/plain")
        await client.delete_file("doc.txt")
        assert not (tmp_path / "store" / "doc.txt.mime").exists()

    @pytest.mark.asyncio
    async def test_close_is_noop(self, tmp_path):
        await LocalStorageClient(tmp_path / "store").close()


class TestElementPersistence:
    @pytest.mark.asyncio
    async def test_text_element_persists_with_fetchable_url(self, tmp_path):
        layer = await _layer(tmp_path)
        await layer.create_element(_element("fragmento citado"))
        await layer.close()

        (object_key, url, for_id) = _rows(tmp_path / "chainlit.db")[0]
        assert _decode(url) == "fragmento citado"
        assert object_key.endswith("/Manual_Renta.pdf")
        assert for_id == "step-1"
        # The blob also exists on disk for delete/get_read_url semantics.
        assert (tmp_path / "elements" / object_key).read_bytes() == b"fragmento citado"

    @pytest.mark.asyncio
    async def test_oversized_element_is_skipped(self, tmp_path):
        layer = await _layer(tmp_path)
        await layer.create_element(_element("x" * (EMBED_MAX_BYTES + 1)))
        await layer.close()
        assert _rows(tmp_path / "chainlit.db") == []

    @pytest.mark.asyncio
    async def test_without_storage_root_nothing_persists(self, tmp_path):
        layer = await _layer(tmp_path, storage=False)
        await layer.create_element(_element("hola"))
        await layer.close()
        assert _rows(tmp_path / "chainlit.db") == []

    @pytest.mark.asyncio
    async def test_restored_thread_carries_the_element_url(self, tmp_path):
        layer = await _layer(tmp_path)
        await layer.update_thread(thread_id="thread-1", name="Hilo de prueba")
        await layer.create_element(_element("chip del lado", thread_id="thread-1"))

        thread = await layer.get_thread("thread-1")
        await layer.close()

        assert thread is not None
        elements = thread.get("elements") or []
        assert len(elements) == 1
        assert _decode(elements[0]["url"]) == "chip del lado"
        assert elements[0]["display"] == "side"


class TestFactory:
    @pytest.mark.asyncio
    async def test_build_data_layer_wires_local_storage(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CHAINLIT_PERSISTENCE", "1")
        monkeypatch.setenv("CHAINLIT_SQLITE_PATH", str(tmp_path / "t.db"))
        layer = build_data_layer()
        try:
            assert isinstance(layer.storage_provider, LocalStorageClient)
            assert layer.storage_provider.root == tmp_path / "elements"
            assert layer.storage_provider.root.is_dir()
        finally:
            await layer.close()

    @pytest.mark.asyncio
    async def test_persistence_disabled_keeps_no_layer(self, monkeypatch):
        monkeypatch.setenv("CHAINLIT_PERSISTENCE", "0")
        assert build_data_layer() is None
