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
