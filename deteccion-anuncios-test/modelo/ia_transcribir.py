"""Transcribe (faster-whisper, CPU) el audio de las entradas del catalogo y de los huecos para poder
clasificarlos por lo que dicen. Guarda en data/transcripciones.db (clave 'e<id>' / 'h<id>').
  python ia_transcribir.py [--todas]     (por defecto solo las pendientes de revisar; --todas = tambien las ya contestadas)
Una entrada se transcribe si no tiene texto o si su wav ha cambiado desde que se transcribio (el afinado lo recorta).
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
    cols = {x[1] for x in t.execute("PRAGMA table_info(t)")}
    for col, tipo in (("mtime", "REAL"), ("ts", "TEXT")):   # fecha del wav transcrito y cuando se transcribio (UTC)
        if col not in cols:
            t.execute(f"ALTER TABLE t ADD COLUMN {col} {tipo}")
    hechas = dict(t.execute("SELECT clave, mtime FROM t"))

    def falta(i: int) -> bool:
        f = DATA / "entradas" / f"{i}.wav"
        if not f.exists():
            return False
        if f"e{i}" not in hechas:
            return True
        m = hechas[f"e{i}"]   # None en las transcripciones antiguas: no se rehacen
        return m is not None and abs(f.stat().st_mtime - m) > 1

    faltan = [i for (i,) in pend if falta(i)]
    if not faltan:
        return
    from faster_whisper import WhisperModel
    m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=a.hilos)
    for i in faltan:
        f = DATA / "entradas" / f"{i}.wav"
        mtime = f.stat().st_mtime
        segs, _ = m.transcribe(str(f), language="es", beam_size=1)
        t.execute("INSERT OR REPLACE INTO t(clave, texto, mtime, ts) VALUES (?,?,?,datetime('now'))",
                  (f"e{i}", " ".join(s.text.strip() for s in segs), mtime))
        t.commit()
        print("transcrita", i, flush=True)


if __name__ == "__main__":
    main()
