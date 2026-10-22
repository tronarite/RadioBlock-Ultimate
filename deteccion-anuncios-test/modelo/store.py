"""Almacén SQLite del modelo de anuncios (clips, características, etiquetas).

Contrato compartido por capture.py, features.py, train.py, review.py y loop.py.
Una fila de `clips` = un trozo de audio de CLIP_SECONDS segundos (wav mono 22050 Hz).
`label`: 1 = anuncio, 0 = no anuncio, NULL = sin revisar. Las etiquetas SOLO las
pone el usuario (source='user'); las propuestas automáticas van en `score`.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
CLIPS_DIR = DATA_DIR / "clips"
DB_PATH = DATA_DIR / "modelo.db"
MODEL_PATH = DATA_DIR / "modelo.joblib"

CLIP_SECONDS = 10
SAMPLE_RATE = 22050

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    radio TEXT NOT NULL,
    timestamp TEXT NOT NULL,          -- ISO, inicio del clip
    path TEXT NOT NULL UNIQUE,        -- relativo a CLIPS_DIR
    features BLOB,                    -- np.save de float32 (features.py)
    repeat_count INTEGER,             -- veces que su huella aparece en otros momentos (loop.py)
    score REAL,                       -- P(anuncio) del último modelo
    label INTEGER,                    -- 1 / 0 / NULL (solo el usuario)
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


def pack(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, np.asarray(arr, dtype=np.float32))
    return buf.getvalue()


def unpack(blob: bytes) -> np.ndarray:
    return np.load(io.BytesIO(blob))


def clip_path(row) -> Path:
    return CLIPS_DIR / row["path"]


# --- v2: ventanas y tramos (diseño de docs/DISENO_MODELO.md) ---------------
# Audio continuo = clips de 10 s contiguos de una radio. Una ventana es
# WINDOW_S s de audio que empieza en t_start (epoch segundos), avanza HOP_S.
WINDOW_S = 3.0
HOP_S = 1.0

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    radio TEXT NOT NULL,
    t_start REAL NOT NULL,            -- epoch s del inicio de la ventana
    backend TEXT NOT NULL,            -- 'panns' | 'handcrafted' | ...
    emb BLOB NOT NULL,                -- pack() del embedding
    score REAL,                       -- P(anuncio) del último modelo
    UNIQUE (radio, t_start, backend)
);
CREATE INDEX IF NOT EXISTS ix_windows_radio_t ON windows(radio, t_start);

-- Tramo candidato [t_start, t_end] a revisar. La etiqueta se propaga a las
-- ventanas que caen dentro (label 1/0 con peso por solape).
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    radio TEXT NOT NULL,
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    source TEXT NOT NULL,             -- 'repeat' | 'model'
    group_key TEXT,                   -- tramos repetidos del mismo audio comparten clave
    score REAL,
    n_repeats INTEGER,
    label INTEGER,                    -- 1 anuncio / 0 no / NULL sin revisar (solo el usuario)
    labeled_at TEXT,
    t_start_user REAL,                -- bordes corregidos por el usuario (si los ajusta)
    t_end_user REAL,
    UNIQUE (radio, source, t_start, t_end)
);
CREATE INDEX IF NOT EXISTS ix_segments_label ON segments(label);

-- Cortes internos de un bloque marcados por el usuario ("cortar aquí"): separan
-- cuñas dentro del bloque solo para que el modelo aprenda; el muteo usa el bloque entero.
CREATE TABLE IF NOT EXISTS cuts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id INTEGER NOT NULL REFERENCES segments(id),
    t REAL NOT NULL,                  -- epoch s del corte
    UNIQUE (segment_id, t)
);
"""


def conn2() -> sqlite3.Connection:
    c = conn()
    c.executescript(SCHEMA_V2)
    return c


def clips_for_range(c, radio: str, t0: float, t1: float):
    """Clips (filas) de la radio que solapan [t0, t1] epoch s, ordenados."""
    import datetime as _dt

    rows = c.execute("SELECT * FROM clips WHERE radio=? ORDER BY timestamp", (radio,)).fetchall()
    out = []
    for r in rows:
        s = _dt.datetime.fromisoformat(r["timestamp"]).timestamp()
        if s + CLIP_SECONDS > t0 and s < t1:
            out.append((s, r))
    return out
