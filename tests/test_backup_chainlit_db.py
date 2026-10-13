"""Tests del backup del SQLite de conversaciones (scripts.backup_chainlit_db)."""
import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from scripts.backup_chainlit_db import TABLES, backup, validate


def _make_db(path: Path, rows: int = 3) -> None:
    """Base sintética con el esquema mínimo de Chainlit (3 tablas)."""
    conn = sqlite3.connect(path)
    try:
        with conn:
            for table in TABLES:
                conn.execute(
                    f'CREATE TABLE "{table}" ("id" TEXT PRIMARY KEY, "data" TEXT)'
                )
            for table in TABLES:
                for i in range(rows):
                    conn.execute(
                        f'INSERT INTO "{table}" VALUES (?, ?)',
                        (f"{table}-{i}", "sintetico"),
                    )
    finally:
        conn.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_creates_valid_copy(tmp_path: Path) -> None:
    source = tmp_path / "chainlit.db"
    output = tmp_path / "backups" / "chainlit.bak"
    _make_db(source, rows=4)

    report = backup(source, output)

    assert output.is_file()
    assert report["integrity"] == "ok"
    assert report == {"integrity": "ok", "threads": 4, "steps": 4, "elements": 4}


def test_backup_refuses_to_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "chainlit.db"
    output = tmp_path / "chainlit.bak"
    _make_db(source)
    backup(source, output)

    with pytest.raises(FileExistsError):
        backup(source, output)


def test_backup_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        backup(tmp_path / "no-existe.db", tmp_path / "out.bak")


def test_validate_detects_incomplete_copy(tmp_path: Path) -> None:
    partial = tmp_path / "partial.db"
    conn = sqlite3.connect(partial)
    with conn:
        conn.execute('CREATE TABLE "threads" ("id" TEXT)')
    conn.close()

    with pytest.raises(ValueError, match="Tabla ausente"):
        validate(partial)


def test_restore_preserves_data_and_leaves_original_untouched(tmp_path: Path) -> None:
    """Restauración = copiar la copia válida sobre una ruta aislada.

    Comprueba que los datos sobreviven y que ni la fuente ni el backup
    se modifican durante el proceso.
    """
    source = tmp_path / "chainlit.db"
    output = tmp_path / "chainlit.bak"
    restored = tmp_path / "restored" / "chainlit.db"
    _make_db(source, rows=5)
    source_digest = _digest(source)
    backup(source, output)
    backup_digest = _digest(output)

    restored.parent.mkdir(parents=True)
    shutil.copyfile(output, restored)
    report = validate(restored)

    assert report == {"integrity": "ok", "threads": 5, "steps": 5, "elements": 5}
    assert _digest(source) == source_digest
    assert _digest(output) == backup_digest
