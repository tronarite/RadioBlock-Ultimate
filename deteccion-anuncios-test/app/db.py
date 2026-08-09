"""Acceso a SQLite en crudo (sin ORM) — este proyecto es solo para
verificar la detección, así que se mantiene lo más simple posible.

Una sola base de datos para todas las emisoras, distinguidas por la
columna `radio` (su clave corta, ver app/radios.py) — los patrones nunca
se comparan entre emisoras distintas, cada una tiene los suyos.
"""

from __future__ import annotations

import datetime
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "detector.db"
SEGMENTS_DIR = DATA_DIR / "segments"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db() -> None:
    with _lock:
        conn = get_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS segmentos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    radio TEXT NOT NULL DEFAULT 'cadena_ser',
                    timestamp TEXT NOT NULL,
                    duracion REAL NOT NULL,
                    fingerprint BLOB,
                    archivo_audio TEXT,
                    grupo_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS grupos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    radio TEXT NOT NULL DEFAULT 'cadena_ser',
                    fingerprint BLOB NOT NULL,
                    n_segmentos INTEGER NOT NULL,
                    n_apariciones INTEGER NOT NULL,
                    primera_vez TEXT NOT NULL,
                    ultima_vez TEXT NOT NULL,
                    representative_segment_id INTEGER,
                    inicio_estimado REAL,
                    fin_estimado REAL,
                    n_estimaciones INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            # Migraciones incrementales para bases de datos creadas antes de
            # cada columna nueva.
            if "radio" not in _column_names(conn, "segmentos"):
                conn.execute("ALTER TABLE segmentos ADD COLUMN radio TEXT NOT NULL DEFAULT 'cadena_ser'")
            if "radio" not in _column_names(conn, "grupos"):
                conn.execute("ALTER TABLE grupos ADD COLUMN radio TEXT NOT NULL DEFAULT 'cadena_ser'")
            grupos_cols = _column_names(conn, "grupos")
            if "inicio_estimado" not in grupos_cols:
                conn.execute("ALTER TABLE grupos ADD COLUMN inicio_estimado REAL")
            if "fin_estimado" not in grupos_cols:
                conn.execute("ALTER TABLE grupos ADD COLUMN fin_estimado REAL")
            if "n_estimaciones" not in grupos_cols:
                conn.execute("ALTER TABLE grupos ADD COLUMN n_estimaciones INTEGER NOT NULL DEFAULT 0")

            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_segmentos_radio_timestamp ON segmentos(radio, timestamp);
                CREATE INDEX IF NOT EXISTS idx_segmentos_grupo ON segmentos(grupo_id);
                CREATE INDEX IF NOT EXISTS idx_grupos_radio ON grupos(radio);
                """
            )
            conn.commit()
        finally:
            conn.close()


def insert_segmento(
    radio: str, timestamp: datetime.datetime, duracion: float, fingerprint: bytes | None, archivo_audio: str | None
) -> int:
    with _lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO segmentos (radio, timestamp, duracion, fingerprint, archivo_audio) VALUES (?, ?, ?, ?, ?)",
                (radio, timestamp.isoformat(), duracion, fingerprint, archivo_audio),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
