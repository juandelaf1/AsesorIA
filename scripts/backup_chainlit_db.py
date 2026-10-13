"""Backup coherente del SQLite de conversaciones (Chainlit data layer).

Usa la *online backup API* de SQLite (``Connection.backup``), que produce una
copia consistente aunque haya actividad concurrente de escritura — a
diferencia de copiar el fichero en frío, que puede capturar un estado a
medias entre transacciones.

El destino nunca se sobrescribe: si la ruta existe, el script falla. La
copia se valida al terminar (``PRAGMA integrity_check`` + recuento de las
tablas de conversación) y, en plataformas posix, se crean con permisos 0600.

Uso:
    python -m scripts.backup_chainlit_db [--source PATH] --output PATH

Restauración (manual, en una instancia parada):
    cp <backup> ui/.data/chainlit.db      # o $CHAINLIT_SQLITE_PATH
La restauración NO modifica el backup ni la base original: solo hay que
reescribir el fichero activo con la copia validada.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "ui" / ".data" / "chainlit.db"
TABLES = ("threads", "steps", "elements")


def default_source() -> Path:
    """Ruta real de la base: $CHAINLIT_SQLITE_PATH o el default local."""
    env = os.environ.get("CHAINLIT_SQLITE_PATH", "").strip()
    return Path(env) if env else DEFAULT_SOURCE


def backup(source: Path, output: Path) -> dict:
    """Copia ``source`` en ``output`` con la backup API de SQLite.

    Lanza ``FileNotFoundError`` si falta la fuente y ``FileExistsError``
    si el destino ya existe (nunca sobrescribe).
    """
    if not source.is_file():
        raise FileNotFoundError(f"Base de origen no encontrada: {source}")
    if output.exists():
        raise FileExistsError(f"El destino ya existe, no se sobrescribe: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(output.as_posix())
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    if os.name == "posix":  # Windows no soporta os.chmod útil
        os.chmod(output, 0o600)
    return validate(output)


def validate(path: Path) -> dict:
    """Abre la copia y comprueba integridad y presencia de las tablas.

    Devuelve los recuentos por tabla; lanza ``ValueError`` si el check
    falla o falta alguna tabla (indica una copia incompleta).
    """
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        check = conn.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise ValueError(f"integrity_check fallido: {check!r}")
        counts = {}
        for table in TABLES:
            try:
                row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            except sqlite3.OperationalError as exc:  # tabla inexistente
                raise ValueError(f"Tabla ausente en la copia: {table}") from exc
            if row is None:
                raise ValueError(f"Tabla ausente en la copia: {table}")
            counts[table] = int(row[0])
        return {"integrity": "ok", **counts}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=None,
                        help="Base a copiar (default: $CHAINLIT_SQLITE_PATH o ui/.data/chainlit.db)")
    parser.add_argument("--output", type=Path, required=True,
                        help="Fichero de backup; no se sobrescribe")
    args = parser.parse_args()
    source = args.source or default_source()
    try:
        report = backup(source, args.output)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Backup OK: {args.output}")
    for table in TABLES:
        print(f"  {table}: {report[table]} filas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
