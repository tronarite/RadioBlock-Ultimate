"""Cuanto de cada bloque de anuncios cubren los anuncios confirmados (solo lectura).

  python cobertura.py [--gap 15]

Bloque = racha de detecciones de entradas activas separadas por <= gap s. Solo cuentan los bloques
con al menos un anuncio confirmado (bloque real). Para cada uno: tiempo cubierto por confirmados,
por activas sin revisar y sin cubrir. Meta para silenciar: >= 85 % cubierto por confirmados.
"""
import argparse
import sqlite3
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def union(iv):
    tot, ini, fin = 0.0, None, None
    for a, b in sorted(iv):
        if fin is None or a > fin:
            if fin is not None:
                tot += fin - ini
            ini, fin = a, b
        else:
            fin = max(fin, b)
    return tot + (fin - ini if fin is not None else 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=15.0)
    ap.add_argument("--bd", default=str(DATA / "catalogo.db"))
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{Path(a.bd).as_posix()}?mode=ro", uri=True, timeout=30)
    print("entradas (estado, confirmada):", dict(Counter(c.execute("SELECT estado, confirmado FROM catalog"))))
    dets = c.execute("SELECT d.t_start, d.t_end, k.confirmado, k.id FROM detections d JOIN catalog k ON k.id=d.catalog_id "
                     "WHERE k.estado='activo' ORDER BY d.t_start").fetchall()
    bloques, cur = [], []
    for d in dets:
        if cur and d[0] - max(x[1] for x in cur) > a.gap:
            bloques.append(cur)
            cur = []
        cur.append(d)
    if cur:
        bloques.append(cur)
    reales = [b for b in bloques if any(x[2] for x in b)]
    filas = []
    for b in reales:
        dur = max(x[1] for x in b) - b[0][0]
        conf = union([(x[0], x[1]) for x in b if x[2]])
        todo = union([(x[0], x[1]) for x in b])
        filas.append((dur, conf, todo - conf, dur - todo))
    t0, t1 = c.execute("SELECT MIN(t_start), MAX(t_end) FROM detections").fetchone()
    print(f"audio escaneado: {((t1 or 0) - (t0 or 0)) / 3600:.1f} h   bloques (gap<={a.gap:.0f}s): {len(bloques)}, "
          f"con algun confirmado: {len(reales)}")
    if not filas:
        return
    D = sum(f[0] for f in filas)
    print(f"tiempo en bloques reales {D / 60:.1f} min: confirmados {sum(f[1] for f in filas) / D:.0%}, "
          f"sin revisar {sum(f[2] for f in filas) / D:.0%}, sin cubrir {sum(f[3] for f in filas) / D:.0%}")
    for u in (0.9, 0.7, 0.5):
        print(f"  bloques con >= {u:.0%} cubierto por confirmados: {sum(1 for f in filas if f[1] >= u * f[0])}/{len(filas)}")
    pend = Counter(x[3] for b in reales for x in b if not x[2])
    print(f"  entradas sin revisar dentro de bloques reales: {len(pend)} (las que mas salen: {pend.most_common(8)})")


if __name__ == "__main__":
    main()
