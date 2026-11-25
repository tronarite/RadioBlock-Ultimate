"""Prueba a ciegas del juez: le pasa los textos de entradas ya contestadas (por el usuario o por la IA
a mano en 2026) sin ensenarle la respuesta y compara. Solo lectura: no guarda nada.

  python pruebas/juez_ciegas.py [--data <carpeta>] [--verdad <respuestas.db>] [--radio cadena_ser]

La verdad sale de las respuestas del 6-nov (--verdad, por defecto data/base-t0/respuestas.db), no de
data/respuestas.db: esa ya guarda los veredictos del propio juez y la prueba se compararia consigo misma.

Usa el mismo modelo y el mismo prompt que juez.py (RB_JUEZ_URL / RB_JUEZ_KEY / RB_JUEZ_MODELO o juez.env).
Criterio (docs/GUIA_IA.md, T4): encuentra >= 85 % de los Si, y los "dice anuncio y estaba como No"
son 3 o menos (leerlos: el 13-nov eran anuncios de verdad mal etiquetados).
"""
import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import juez  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(juez.DATA))
    ap.add_argument("--verdad", default=str(juez.DATA / "base-t0" / "respuestas.db"))
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--lote", type=int, default=40)
    a = ap.parse_args()
    data = Path(a.data)
    juez.cargar_env(data)
    ro = lambda nombre: sqlite3.connect(f"file:{(data / nombre).as_posix()}?mode=ro", uri=True, timeout=30)
    cat = ro("catalogo.db")
    textos = dict(ro("transcripciones.db").execute("SELECT clave, texto FROM t"))
    verdad: dict[int, str] = {}
    verdad_db = sqlite3.connect(f"file:{Path(a.verdad).as_posix()}?mode=ro", uri=True, timeout=30)
    for cid, resp in verdad_db.execute(
            "SELECT catalog_id, resp FROM respuestas WHERE fuente IN ('usuario','ia') AND resp IN ('si','no') "
            "AND catalog_id > 0 ORDER BY id"):
        verdad[cid] = resp
    grupos: dict[str, dict] = {}
    for eid, resp in verdad.items():
        fila = cat.execute("SELECT dur FROM catalog WHERE id=? AND radio=?", (eid, a.radio)).fetchone()
        texto = textos.get(f"e{eid}", "").strip()
        if not fila or not texto:
            continue
        tiempos = [r[0] for r in cat.execute("SELECT t_start FROM detections WHERE catalog_id=?", (eid,))]
        rl = juez.reloj(tiempos)
        clave = re.sub(r"[^a-z0-9ñáéíóúü ]", "", texto.lower()).strip()
        g = grupos.setdefault(clave, {"resp": set(), "dur_s": round(fila[0]), "emisiones": 0, "reloj": None, "texto": texto})
        g["resp"].add(resp)
        if len(tiempos) > g["emisiones"]:
            g["emisiones"], g["reloj"] = len(tiempos), (None if rl is None else round(rl, 2))
    lista = [g for g in grupos.values() if len(g["resp"]) == 1 and g["texto"]]
    mixtos = len(grupos) - len(lista)
    dicho: list[str | None] = []
    for i in range(0, len(lista), a.lote):
        if i:
            time.sleep(5)
        lote = lista[i:i + a.lote]
        casos = [{"n": k + 1, **{c: g[c] for c in ("dur_s", "emisiones", "reloj", "texto")}} for k, g in enumerate(lote)]
        res = juez.preguntar(casos)
        dicho += [res.get(k + 1) for k in range(len(lote))]
    si = [g for g in lista if g["resp"] == {"si"}]
    es = lambda g: next(iter(g["resp"]))
    pares = list(zip(lista, dicho))
    dice_anuncio = [g for g, d in pares if d == "anuncio"]
    aciertos = [g for g in dice_anuncio if es(g) == "si"]
    print(f"textos distintos: {len(lista)} ({len(si)} Si, {len(lista) - len(si)} No); con respuestas mezcladas, fuera: {mixtos}")
    print(f"dice anuncio: {len(dice_anuncio)}; de ellos estaban como Si: {len(aciertos)}")
    print(f"Si encontrados: {len(aciertos)} de {len(si)} ({len(aciertos) / max(len(si), 1):.0%})")
    print(f"dudosas: {sum(1 for d in dicho if d == 'dudosa')}; sin respuesta: {sum(1 for d in dicho if d is None)}")
    out = sys.stdout
    if hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8", errors="replace")
    print("--- dice anuncio y estaba como No:")
    for g, d in pares:
        if d == "anuncio" and es(g) == "no":
            print(f"  [{g['dur_s']} s, {g['emisiones']} emisiones] {g['texto'][:200]}")
    print("--- estaba como Si y no dice anuncio:")
    for g, d in pares:
        if d != "anuncio" and es(g) == "si":
            print(f"  ({d}) [{g['dur_s']} s] {g['texto'][:200]}")


if __name__ == "__main__":
    main()
