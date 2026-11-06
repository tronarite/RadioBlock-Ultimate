"""Textos de las entradas sin revisar, para clasificarlas por lo que dicen (solo lectura).

  python ia_pendientes.py [--n 150] > pendientes.json

Primero las que suenan dentro de bloques reales (junto a un anuncio confirmado), luego el resto por
veces oidas. Solo las de >= 6 s con transcripcion y sin respuesta. Las de texto identico salen juntas
(una linea, varios ids). El veredicto se guarda con ia_aplicar.py ({"id": "si"|"no"}, dudosos fuera).
"""
import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
GAP_S = 15.0


def ro(nombre: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{(DATA / nombre).as_posix()}?mode=ro", uri=True, timeout=30)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150, help="entradas como maximo")
    a = ap.parse_args()
    cat = ro("catalogo.db")
    textos = dict(ro("transcripciones.db").execute("SELECT clave, texto FROM t"))
    ya = {r[0] for r in ro("respuestas.db").execute("SELECT catalog_id FROM respuestas")} if (DATA / "respuestas.db").exists() else set()
    dets = cat.execute("SELECT d.t_start, d.t_end, k.confirmado, k.id FROM detections d JOIN catalog k ON k.id=d.catalog_id "
                       "WHERE k.estado='activo' ORDER BY d.t_start").fetchall()
    bloques, cur = [], []
    for d in dets:
        if cur and d[0] - max(x[1] for x in cur) > GAP_S:
            bloques.append(cur)
            cur = []
        cur.append(d)
    if cur:
        bloques.append(cur)
    en_bloque = Counter(x[3] for b in bloques if any(y[2] for y in b) for x in b if not x[2])
    pend = {r[0]: (r[1], r[2]) for r in cat.execute(
        "SELECT id, dur, n_matches FROM catalog WHERE estado='activo' AND confirmado=0 AND dur >= 6")}
    orden = sorted((i for i in pend if f"e{i}" in textos and i not in ya),
                   key=lambda i: (-en_bloque[i], -pend[i][1]))[:a.n]
    grupos: dict[str, dict] = {}
    for i in orden:
        texto = textos[f"e{i}"].strip()
        clave = re.sub(r"[^a-z0-9ñáéíóúü ]", "", texto.lower()).strip() or f"(vacio {i})"
        g = grupos.setdefault(clave, {"ids": [], "dur": [], "en_bloque": 0, "texto": texto})
        g["ids"].append(i)
        g["dur"].append(round(pend[i][0]))
        g["en_bloque"] += en_bloque[i]
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(list(grupos.values()), ensure_ascii=False, indent=0))
    print(f"{len(orden)} entradas en {len(grupos)} textos distintos (pendientes con texto y >= 6 s: "
          f"{sum(1 for i in pend if f'e{i}' in textos and i not in ya)})", file=sys.stderr)


if __name__ == "__main__":
    main()
