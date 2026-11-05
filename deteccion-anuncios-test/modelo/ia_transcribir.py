"""Transcribe (faster-whisper, CPU) el audio de las entradas del catalogo y de los huecos para poder
clasificarlos por lo que dicen. Guarda en data/transcripciones.db (clave 'e<id>' / 'h<id>').
  python ia_transcribir.py [--todas]     (por defecto solo las pendientes de revisar; --todas = tambien las ya contestadas)
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--todas", action="store_true")
    ap.add_argument("--hilos", type=int, default=3)
    a = ap.parse_args()
    cat = sqlite3.connect(f"file:{(DATA / 'catalogo.db').as_posix()}?mode=ro", uri=True, timeout=30)
    filtro = "estado IN ('activo','lista_negra')" if a.todas else "estado='activo' AND confirmado=0"
    pend = cat.execute(f"SELECT id FROM catalog WHERE {filtro} AND wav IS NOT NULL ORDER BY n_matches DESC").fetchall()
    cat.close()
    t = sqlite3.connect(str(DATA / "transcripciones.db"), timeout=30)
    t.execute("CREATE TABLE IF NOT EXISTS t (clave TEXT PRIMARY KEY, texto TEXT NOT NULL)")
    hechas = {r[0] for r in t.execute("SELECT clave FROM t")}
    faltan = [i for (i,) in pend if f"e{i}" not in hechas and (DATA / "entradas" / f"{i}.wav").exists()]
    if not faltan:
        return
    from faster_whisper import WhisperModel
    m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=a.hilos)
    for i in faltan:
        segs, _ = m.transcribe(str(DATA / "entradas" / f"{i}.wav"), language="es", beam_size=1)
        t.execute("INSERT OR REPLACE INTO t VALUES (?,?)", (f"e{i}", " ".join(s.text.strip() for s in segs)))
        t.commit()
        print("transcrita", i, flush=True)


if __name__ == "__main__":
    main()
