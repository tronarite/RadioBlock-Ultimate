"""Almacén SQLite del audio grabado (`clips`) y de los tramos repetidos (`segments`).

Una fila de `clips` = un trozo de audio de CLIP_SECONDS segundos (wav mono 22050 Hz).
La base es data/modelo.db (la que escribe la captura) o la de RB_DB (el ciclo usa data/catalogo.db).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
CLIPS_DIR = DATA_DIR / "clips"
DB_PATH = Path(os.environ["RB_DB"]) if os.environ.get("RB_DB") else DATA_DIR / "modelo.db"

CLIP_SECONDS = 10
SAMPLE_RATE = 22050

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    radio TEXT NOT NULL,
    timestamp TEXT NOT NULL,          -- ISO, inicio del clip
    path TEXT NOT NULL UNIQUE,        -- relativo a CLIPS_DIR
    features BLOB,                    -- features..labeled_at: de la version antigua, sin uso
    repeat_count INTEGER,
    score REAL,
    label INTEGER,
    labeled_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_clips_label ON clips(label);
CREATE INDEX IF NOT EXISTS ix_clips_radio_ts ON clips(radio, timestamp);
"""


def conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def clip_path(row) -> Path:
    return CLIPS_DIR / row["path"]


# Tramo [t_start, t_end] (epoch s) que se repite en otro momento (segments_repeat.py); construir()
# lo pasa al catalogo. Las bases antiguas pueden tener ademas las tablas windows, cuts y marks: sin uso.
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    radio TEXT NOT NULL,
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    source TEXT NOT NULL,             -- 'repeat' ('hueco' en bases antiguas: lo borra catalogo.podar)
    group_key TEXT,                   -- tramos repetidos del mismo audio comparten clave
    score REAL,
    n_repeats INTEGER,
    label INTEGER,                    -- 1 anuncio / 0 no / NULL sin etiqueta
    labeled_at TEXT,
    t_start_user REAL,                -- bordes corregidos a mano (version antigua)
    t_end_user REAL,
    UNIQUE (radio, source, t_start, t_end)
);
CREATE INDEX IF NOT EXISTS ix_segments_label ON segments(label);
"""


def conn2() -> sqlite3.Connection:
    c = conn()
    c.executescript(SCHEMA_V2)
    return c


def clips_for_range(c, radio: str, t0: float, t1: float):
    """Clips (filas) de la radio que solapan [t0, t1] epoch s, ordenados."""
    import datetime as _dt

    # solo los clips del rango: las fechas ISO se ordenan bien como texto y hay indice (radio, timestamp)
    iso = lambda t: _dt.datetime.fromtimestamp(t).isoformat(timespec="seconds")
    rows = c.execute("SELECT * FROM clips WHERE radio=? AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
                     (radio, iso(t0 - CLIP_SECONDS - 1), iso(t1 + 1))).fetchall()
    out = []
    for r in rows:
        s = _dt.datetime.fromisoformat(r["timestamp"]).timestamp()
        if s + CLIP_SECONDS > t0 and s < t1:
            out.append((s, r))
    return out
