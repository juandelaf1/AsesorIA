"""Conversation persistence — Chainlit native data layer on SQLite.

Scope: frontend integration only. The schema and the layer itself are
Chainlit's (`chainlit.data.sql_alchemy.SQLAlchemyDataLayer`); this module
adds the SQLite adaptations that upstream keeps PostgreSQL-only:

- DDL bootstrap (Chainlit ships queries but no schema creation),
- `tags` as JSON text (upstream binds Python lists, which SQLite rejects),
- no foreign keys (upstream's own issue #1788 workaround),
- a local blob storage for elements (H-32): upstream skips
  `create_element` entirely without a storage provider, so the side-panel
  source chips disappeared after a reload. `LocalStorageClient` persists
  them (see below).

Ownership of the database (backups, migrations, Postgres) belongs to the
backend role — see `.agent-local/audit/product-ux/PERSISTENCE_HANDOFF.md`.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional, Union

from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data.storage_clients.base import BaseStorageClient

logger = logging.getLogger(__name__)

# One file per installation; separate deployments never share records.
DEFAULT_DB_PATH = Path(__file__).resolve().parent / ".data" / "chainlit.db"

# Embedded-content ceiling. Element payloads at or below this size are
# persisted as a `data:` URI in `elements.url` (see LocalStorageClient);
# bigger payloads are NOT persisted at all — same behaviour as before the
# storage client existed, and it keeps multi-MB attachments out of SQLite.
EMBED_MAX_BYTES = 1_000_000

# Official schema (docs.chainlit.io/data-layers/sqlalchemy) adapted to
# SQLite: UUID→TEXT, JSONB→TEXT, TEXT[]→TEXT, FK constraints dropped.
DDL = """
CREATE TABLE IF NOT EXISTS users (
  "id" TEXT PRIMARY KEY,
  "identifier" TEXT NOT NULL UNIQUE,
  "metadata" TEXT NOT NULL,
  "createdAt" TEXT
);
CREATE TABLE IF NOT EXISTS threads (
  "id" TEXT PRIMARY KEY,
  "createdAt" TEXT,
  "name" TEXT,
  "userId" TEXT,
  "userIdentifier" TEXT,
  "tags" TEXT,
  "metadata" TEXT
);
CREATE INDEX IF NOT EXISTS idx_threads_user ON threads ("userId");
CREATE TABLE IF NOT EXISTS steps (
  "id" TEXT PRIMARY KEY,
  "name" TEXT NOT NULL,
  "type" TEXT NOT NULL,
  "threadId" TEXT NOT NULL,
  "parentId" TEXT,
  "streaming" BOOLEAN NOT NULL,
  "waitForAnswer" BOOLEAN,
  "isError" BOOLEAN,
  "metadata" TEXT,
  "tags" TEXT,
  "input" TEXT,
  "output" TEXT,
  "createdAt" TEXT,
  "command" TEXT,
  "start" TEXT,
  "end" TEXT,
  "generation" TEXT,
  "showInput" TEXT,
  "language" TEXT,
  "indent" INTEGER,
  "defaultOpen" BOOLEAN,
  "autoCollapse" BOOLEAN,
  "modes" TEXT
);
CREATE INDEX IF NOT EXISTS idx_steps_thread ON steps ("threadId");
CREATE TABLE IF NOT EXISTS elements (
  "id" TEXT PRIMARY KEY,
  "threadId" TEXT,
  "type" TEXT,
  "url" TEXT,
  "chainlitKey" TEXT,
  "name" TEXT NOT NULL,
  "display" TEXT,
  "objectKey" TEXT,
  "size" TEXT,
  "page" INTEGER,
  "language" TEXT,
  "forId" TEXT,
  "mime" TEXT,
  "props" TEXT
);
CREATE TABLE IF NOT EXISTS feedbacks (
  "id" TEXT PRIMARY KEY,
  "forId" TEXT NOT NULL,
  "threadId" TEXT NOT NULL,
  "value" INTEGER NOT NULL,
  "comment" TEXT
);
"""


SCHEMA_MIGRATIONS: dict[str, dict[str, str]] = {
    "steps": {"autoCollapse": "BOOLEAN DEFAULT FALSE"},
}


def ensure_sqlite_schema(path: Path | str) -> None:
    """Create the Chainlit schema and add columns newer Chainlit releases
    expect (idempotent, no side effects)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.executescript(DDL)
        for table, columns in SCHEMA_MIGRATIONS.items():
            existing = {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}
            for column, decl in columns.items():
                if column not in existing:
                    con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {decl}')


def _encode_tags(tags: Any) -> Any:
    if isinstance(tags, (list, tuple)):
        return json.dumps(list(tags), ensure_ascii=False)
    return tags


def _decode_tags(tags: Any) -> Any:
    if isinstance(tags, str) and tags:
        try:
            parsed = json.loads(tags)
        except ValueError:
            return tags
        return parsed if isinstance(parsed, list) else tags
    return tags


def _decode_thread(thread: Optional[dict]) -> Optional[dict]:
    if not thread:
        return thread
    if "tags" in thread:
        thread["tags"] = _decode_tags(thread.get("tags"))
    for step in thread.get("steps") or []:
        if isinstance(step, dict) and "tags" in step:
            step["tags"] = _decode_tags(step.get("tags"))
    return thread


def _payload_size(element: Any) -> Optional[int]:
    """Byte size of what `create_element` would persist, or None if unknown."""
    path = getattr(element, "path", None)
    if path:
        try:
            return Path(path).stat().st_size
        except OSError:
            return None
    content = getattr(element, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return len(content)
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    return None


class LocalStorageClient(BaseStorageClient):
    """Local blob storage for Chainlit elements (filesystem next to the DB).

    The frontend fetches `element.url` to render a restored element
    (`fetch(url)` in the compiled client), and the `/project/file/` endpoint
    only serves files of a LIVE session — after a reload it 404s. So the
    URL returned here is a `data:` URI: self-contained, fetchable without
    any server route, and stored inside the `elements` row.

    Files are ALSO written under `root` so `delete_file`/`get_read_url`
    keep real storage semantics (object keys are sanitised: absolute paths
    and `..` never escape the root).
    """

    def __init__(self, root: Union[Path, str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, object_key: str) -> Path:
        if not object_key or Path(object_key).is_absolute() or ".." in Path(object_key).parts:
            raise ValueError(f"Unsafe object key: {object_key!r}")
        path = (self.root / object_key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"Object key escapes the storage root: {object_key!r}")
        return path

    @staticmethod
    def _data_uri(data: Union[bytes, str], mime: str) -> Optional[str]:
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        if len(payload) > EMBED_MAX_BYTES:
            return None  # callers above the ceiling never persist (see create_element)
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{mime or 'application/octet-stream'};base64,{encoded}"

    @staticmethod
    def _mime_sidecar(path: Path) -> Path:
        # `get_read_url` runs on restore and must reproduce the upload-time
        # mime; guessing from the file name is wrong (side chips are named
        # after their source document, e.g. text inside "Manual.pdf").
        return Path(str(path) + ".mime")

    async def upload_file(
        self,
        object_key: str,
        data: Union[bytes, str],
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: Optional[str] = None,
    ) -> dict:
        path = self._resolve(object_key)
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        if overwrite or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            self._mime_sidecar(path).write_text(mime or "", encoding="utf-8")
        else:
            payload = path.read_bytes()
        return {"object_key": object_key, "url": self._data_uri(payload, mime)}

    async def delete_file(self, object_key: str) -> bool:
        try:
            path = self._resolve(object_key)
        except ValueError:
            return False
        sidecar = self._mime_sidecar(path)
        if sidecar.is_file():
            sidecar.unlink()
        if path.is_file():
            path.unlink()
            return True
        return False

    async def get_read_url(self, object_key: str) -> str:
        # On-demand read (nothing is written to the DB here): always embed.
        path = self._resolve(object_key)
        sidecar = self._mime_sidecar(path)
        if sidecar.is_file():
            mime = sidecar.read_text(encoding="utf-8") or "application/octet-stream"
        else:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        payload = path.read_bytes()
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    async def close(self) -> None:
        return None


class SqliteDataLayer(SQLAlchemyDataLayer):
    """Chainlit's SQLAlchemy layer with SQLite-safe tag handling."""

    def __init__(self, *args: Any, storage_root: Union[Path, str, None] = None, **kwargs: Any):
        """Like upstream, plus a local storage root.

        `storage_root` wires a `LocalStorageClient` unless an explicit
        `storage_provider` is passed (test seam). Omitted → no provider →
        elements are not persisted (upstream default).
        """
        if storage_root is not None and "storage_provider" not in kwargs:
            kwargs["storage_provider"] = LocalStorageClient(storage_root)
        super().__init__(*args, **kwargs)

    async def create_element(self, element: Any):
        """Persists an element (H-32), guarding size and failures.

        - payload above `EMBED_MAX_BYTES` → skipped (pre-storage behaviour:
          no row, no broken chip after a reload; keeps multi-MB attachments
          out of SQLite);
        - any failure is logged server-side and swallowed: element
          persistence must never break the answer flow.
        """
        size = _payload_size(element)
        if size is not None and size > EMBED_MAX_BYTES:
            return
        try:
            return await super().create_element(element)
        except Exception as exc:
            logger.warning(
                "Element not persisted (%s): %s", type(exc).__name__, exc
            )

    async def update_thread(
        self,
        thread_id: str,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        tags: Optional[list] = None,
    ):
        return await super().update_thread(
            thread_id,
            name=name,
            user_id=user_id,
            metadata=metadata,
            tags=_encode_tags(tags),
        )

    async def create_step(self, step: dict):
        step = dict(step)
        if "tags" in step:
            step["tags"] = _encode_tags(step.get("tags"))
        return await super().create_step(step)

    async def update_step(self, step: dict):
        step = dict(step)
        if "tags" in step:
            step["tags"] = _encode_tags(step.get("tags"))
        return await super().update_step(step)

    async def get_thread(self, thread_id: str):
        return _decode_thread(await super().get_thread(thread_id))

    async def list_threads(self, pagination, filters):
        res = await super().list_threads(pagination, filters)
        for thread in res.data:
            _decode_thread(thread)
        return res


def build_data_layer() -> Optional[SqliteDataLayer]:
    """Factory registered with `@cl.data_layer`; Chainlit calls it lazily.

    Returns None (persistence off) when `CHAINLIT_PERSISTENCE=0`.
    """
    if os.environ.get("CHAINLIT_PERSISTENCE", "1") == "0":
        return None
    db_path = Path(os.environ.get("CHAINLIT_SQLITE_PATH") or DEFAULT_DB_PATH)
    ensure_sqlite_schema(db_path)
    return SqliteDataLayer(
        conninfo=f"sqlite+aiosqlite:///{db_path.absolute().as_posix()}",
        # Element blobs live next to the DB (same .data/ tree, gitignored).
        storage_root=db_path.absolute().parent / "elements",
    )
