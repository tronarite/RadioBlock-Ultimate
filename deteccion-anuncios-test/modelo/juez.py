"""Juez automatico: decide si cada entrada pendiente del catalogo es anuncio, sin revision manual.

  python juez.py [--radio cadena_ser] [--n 400] [--lote 40] [--data <carpeta>] [--seco]

Para cada entrada sin revisar de >= 6 s con transcripcion y sin veredicto:
  1. Regla del reloj: si suena siempre en el mismo minuto de la hora es una sintonia -> "no".
  2. Modelo de lenguaje: lee el texto y contesta anuncio / programa / dudosa.
Guarda el veredicto en respuestas.db (fuente 'regla' o 'ia'); el ciclo lo aplica en su vuelta
siguiente. Las dudosas se guardan como 'dudosa' (no cambian nada, solo evitan volver a preguntar).

El modelo se alcanza por cualquier API compatible con OpenAI:
  RB_JUEZ_URL     (por defecto http://127.0.0.1:20128/v1, el gateway OmniRoute local)
  RB_JUEZ_KEY     (por defecto el contenido de ~/.omniroute/worker.key)
  RB_JUEZ_MODELO  (por defecto combo/chat)
Si el servicio no responde no se guarda nada y las entradas esperan a la vuelta siguiente.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
MIN_DUR_S = 6.0
RELOJ_MIN_EMISIONES = 6     # emisiones necesarias para fiarse de la regla del reloj
RELOJ_FIJO = 0.9            # medido el 13-nov: el 90 % de los anuncios no pasa de 0,52
VEREDICTO = {"anuncio": "si", "programa": "no", "dudosa": "dudosa"}

PROMPT = """Eres el juez de un sistema que silencia los anuncios de una emisora de radio española.
Recibes fragmentos de audio que se repiten varias veces al dia, transcritos automaticamente (la transcripcion tiene errores y puede empezar o acabar a media frase). Clasifica cada uno:

- "anuncio": cuña publicitaria de un tercero (marca, tienda, producto, servicio, banco, seguro, coche, campaña institucional o de una administracion, loteria, ONG). Vale aunque no este entera.
- "programa": cualquier otra cosa: contenido de la emisora (noticias, cortes de voz de boletines, tertulia, deportes), sintonias, indicativos, la hora, y tambien las PROMOS de la propia emisora o de su grupo (sus programas, podcasts, su app, sus eventos).
- "dudosa": solo si de verdad no se puede saber con el texto.

Pistas de cada caso: dur_s (duracion), emisiones (veces oido), reloj (0 a 1: 1 = suena siempre en el mismo minuto de la hora, tipico de sintonias e indicativos; null = pocos datos).

Responde SOLO con un array JSON, un objeto por caso y en el mismo orden, sin texto alrededor ni bloque de codigo:
[{"n": 1, "v": "anuncio"}, {"n": 2, "v": "programa"}, ...]

Casos:
"""


def reloj(tiempos: list[float]) -> float | None:
    """Concentracion (0..1) del minuto de la hora en que suena; None con menos de 3 emisiones."""
    if len(tiempos) < 3:
        return None
    f = [(t % 3600) / 3600 * 2 * math.pi for t in tiempos]
    return math.hypot(sum(map(math.cos, f)), sum(map(math.sin, f))) / len(f)


def preguntar(casos: list[dict]) -> dict[int, str]:
    """{n: anuncio|programa|dudosa} segun el modelo. Lanza una excepcion si el servicio falla."""
    url = os.environ.get("RB_JUEZ_URL", "http://127.0.0.1:20128/v1").rstrip("/") + "/chat/completions"
    key = os.environ.get("RB_JUEZ_KEY")
    if not key:
        f = Path.home() / ".omniroute" / "worker.key"
        key = f.read_text(encoding="utf-8").strip() if f.exists() else ""
    cuerpo = {"model": os.environ.get("RB_JUEZ_MODELO", "combo/chat"), "max_tokens": 4096, "temperature": 0,
              "messages": [{"role": "user", "content": PROMPT + json.dumps(casos, ensure_ascii=False)}]}
    req = urllib.request.Request(url, json.dumps(cuerpo).encode("utf-8"),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        texto = json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]
    pares = re.findall(r'"n"\s*:\s*(\d+)\s*,\s*"v"\s*:\s*"(\w+)"', texto)
    return {int(n): v for n, v in pares if v in VEREDICTO}


def respuestas_db(data: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(data / "respuestas.db"), timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS respuestas (id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id INTEGER NOT NULL, "
              "resp TEXT NOT NULL, ts TEXT DEFAULT CURRENT_TIMESTAMP, aplicada INTEGER NOT NULL DEFAULT 0, "
              "fuente TEXT NOT NULL DEFAULT 'usuario')")
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--n", type=int, default=400, help="entradas como maximo por ejecucion")
    ap.add_argument("--lote", type=int, default=40, help="textos por peticion al modelo")
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--seco", action="store_true", help="no guarda nada, solo informa")
    a = ap.parse_args()
    data = Path(a.data)
    ro = lambda nombre: sqlite3.connect(f"file:{(data / nombre).as_posix()}?mode=ro", uri=True, timeout=30)
    if not (data / "transcripciones.db").exists():
        print("juez: sin transcripciones todavia", flush=True)
        return
    cat = ro("catalogo.db")
    textos = dict(ro("transcripciones.db").execute("SELECT clave, texto FROM t"))
    resp = respuestas_db(data)
    ya = {r[0] for r in resp.execute("SELECT catalog_id FROM respuestas")}
    pend = [r for r in cat.execute("SELECT id, dur, n_matches FROM catalog WHERE radio=? AND estado='activo' "
                                   "AND confirmado=0 AND dur >= ? ORDER BY n_matches DESC", (a.radio, MIN_DUR_S))
            if r[0] not in ya and textos.get(f"e{r[0]}", "").strip()][:a.n]
    veredictos: dict[int, tuple[str, str]] = {}   # id -> (resp, fuente)
    grupos: dict[str, dict] = {}                  # texto normalizado -> caso para el modelo
    for eid, dur, _n in pend:
        tiempos = [r[0] for r in cat.execute("SELECT t_start FROM detections WHERE catalog_id=?", (eid,))]
        rl = reloj(tiempos)
        if rl is not None and len(tiempos) >= RELOJ_MIN_EMISIONES and rl >= RELOJ_FIJO:
            veredictos[eid] = ("no", "regla")
            continue
        texto = textos[f"e{eid}"].strip()
        clave = re.sub(r"[^a-z0-9ñáéíóúü ]", "", texto.lower()).strip() or f"(vacio {eid})"
        g = grupos.setdefault(clave, {"ids": [], "dur_s": round(dur), "emisiones": 0, "reloj": None, "texto": texto})
        g["ids"].append(eid)
        if len(tiempos) > g["emisiones"]:
            g["emisiones"], g["reloj"] = len(tiempos), (None if rl is None else round(rl, 2))
    lista = list(grupos.values())
    fallos = 0
    for i in range(0, len(lista), a.lote):
        lote = lista[i:i + a.lote]
        casos = [{"n": k + 1, **{c: g[c] for c in ("dur_s", "emisiones", "reloj", "texto")}} for k, g in enumerate(lote)]
        try:
            res = preguntar(casos)
        except Exception as e:
            fallos += 1
            print(f"juez: el modelo no responde ({e!r}); estas entradas esperan", flush=True)
            if fallos >= 2:
                break
            continue
        for k, g in enumerate(lote):
            if k + 1 in res:
                for eid in g["ids"]:
                    veredictos[eid] = (VEREDICTO[res[k + 1]], "ia")
    cuenta = {}
    for r_, f in veredictos.values():
        cuenta[f"{f}:{r_}"] = cuenta.get(f"{f}:{r_}", 0) + 1
    print(f"juez: {len(pend)} pendientes con texto, {len(veredictos)} veredictos {cuenta}", flush=True)
    if a.seco:
        json.dump({str(k): v for k, v in veredictos.items()}, sys.stdout)
        return
    resp.executemany("INSERT INTO respuestas(catalog_id, resp, fuente) VALUES (?,?,?)",
                     [(eid, r_, f) for eid, (r_, f) in veredictos.items()])
    resp.commit()


if __name__ == "__main__":
    main()
