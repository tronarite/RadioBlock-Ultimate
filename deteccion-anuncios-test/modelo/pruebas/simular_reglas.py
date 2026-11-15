"""Aplica las reglas de silenciado (reglas.py) a las detecciones guardadas en un catalogo. Solo lectura.

  python pruebas/simular_reglas.py --bd <catalogo.db> [--radio cadena_ser] [--ocultar 0.5]

Informa de: minutos mudos por hora, cuanto del tiempo de los bloques reales queda mudo (bloque real =
racha de detecciones de entradas activas separadas <= 15 s con algun anuncio confirmado, como en
cobertura.py), cuanto programa conocido (P) se silencia y el tramo mudo mas largo.

--ocultar F: prueba de las reglas 3 y 4. Esconde la etiqueta de una fraccion F de las entradas ya
juzgadas (pasan a pendientes) y mide cuantos de esos anuncios escondidos salen mudos de todos modos
(deberia ser alto) y cuanto del programa escondido se silencia por error (deberia ser bajo).
"""
import argparse
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import reglas  # noqa: E402

GAP_BLOQUE_S = 15.0


def total(iv) -> float:
    return sum(b - a for a, b in reglas.unir(iv))


def comun(x, y) -> float:
    """Segundos que comparten dos listas de intervalos."""
    return total(x) - total(reglas.restar(x, y))


def bloques_reales(filas) -> list[tuple[float, float]]:
    """filas: (ini, fin, clase) ordenadas por ini. Rachas de A/N con al menos una A."""
    out, cur = [], []
    for f in filas:
        if f[2] == "P":
            continue
        if cur and f[0] - max(x[1] for x in cur) > GAP_BLOQUE_S:
            if any(x[2] == "A" for x in cur):
                out.append((cur[0][0], max(x[1] for x in cur)))
            cur = []
        cur.append(f)
    if cur and any(x[2] == "A" for x in cur):
        out.append((cur[0][0], max(x[1] for x in cur)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bd", required=True)
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--ocultar", type=float, default=0.0)
    ap.add_argument("--sorteos", type=int, default=5)
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{Path(a.bd).as_posix()}?mode=ro", uri=True, timeout=30)
    filas = []   # (ini, fin, clase, id)
    for ini, fin, eid, estado, conf in c.execute(
            "SELECT d.t_start, d.t_end, k.id, k.estado, k.confirmado FROM detections d JOIN catalog k ON k.id=d.catalog_id "
            "WHERE d.radio=? ORDER BY d.t_start", (a.radio,)):
        cl = reglas.clase(estado, conf)
        if cl:
            filas.append((ini, fin, cl, eid))
    if not filas:
        raise SystemExit("sin detecciones")
    horas = (max(f[1] for f in filas) - filas[0][0]) / 3600
    de = lambda k, fs=filas: [(f[0], f[1]) for f in fs if f[2] == k]
    print(f"{len(filas)} detecciones en {horas:.1f} h: A={len(de('A'))} P={len(de('P'))} N={len(de('N'))}")

    reales = bloques_reales(filas)
    for nombre, kw in (("solo anuncios + relleno", dict(puente=0.0, borde=False)),
                       ("reglas completas", dict())):
        m = reglas.limitar(reglas.planificar(filas, **kw))
        solo_p = reglas.restar(de("P"), de("A"))
        print(f"{nombre:24s}: mudo {total(m) / 60 / horas:5.2f} min/h en {len(m)} tramos (el mas largo {max(b - x for x, b in m) / 60:.1f} min); "
              f"bloques reales mudos {comun(reales, m) / max(total(reales), 1):.0%} de {total(reales) / 60:.0f} min; "
              f"programa conocido mudo {comun(solo_p, m) / 60 / horas:.2f} min/h")

    if a.ocultar > 0:
        juzgadas = sorted({f[3] for f in filas if f[2] in "AP"})
        rec, fal, fal_h = [], [], []
        for s in range(a.sorteos):
            ocultas = set(random.Random(s).sample(juzgadas, int(a.ocultar * len(juzgadas))))
            vis = [(f[0], f[1], "N" if f[3] in ocultas else f[2]) for f in filas]
            esc_a = [(f[0], f[1]) for f in filas if f[3] in ocultas and f[2] == "A"]
            esc_p = reglas.restar([(f[0], f[1]) for f in filas if f[3] in ocultas and f[2] == "P"], esc_a)
            m = reglas.limitar(reglas.planificar(vis))
            rec.append(comun(esc_a, m) / max(total(esc_a), 1))
            fal.append(comun(esc_p, m) / max(total(esc_p), 1))
            fal_h.append(comun(esc_p, m) / 60 / horas)
        md = lambda v: sum(v) / len(v)
        print(f"con el {a.ocultar:.0%} de las etiquetas escondidas ({a.sorteos} sorteos): anuncios escondidos que salen mudos "
              f"{md(rec):.0%}; programa escondido silenciado {md(fal):.0%} ({md(fal_h):.2f} min/h)")


if __name__ == "__main__":
    main()
