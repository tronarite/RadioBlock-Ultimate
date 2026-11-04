"""Catalogo de anuncios conocidos que se aprende solo.

Idea: los anuncios cambian, pero cada campaña suena varias veces al dia. Los
grupos que la repeticion encuentra (segments_repeat.py) entran al catalogo; el
escaneo busca esas huellas en el audio nuevo y, si dos anuncios conocidos
rodean un hueco corto, ese hueco se propone tambien (source='hueco').

  python catalogo.py build  --radio cadena_ser    # grupos repetidos y anuncios etiquetados -> catalogo
  python catalogo.py scan   --radio cadena_ser [--desde EPOCH]   # busca el catalogo en el audio
  python catalogo.py huecos --radio cadena_ser    # propone huecos entre anuncios conocidos
  python catalogo.py calibrar --radio cadena_ser  # inliers de aciertos frente a ruido
  python catalogo.py eval   --radio cadena_ser    # cobertura con division temporal
  python catalogo.py caducar --dias 14            # retira entradas sin verse

Las etiquetas del usuario mandan: lo marcado "no anuncio" va a la lista negra.
La identidad de una entrada es su huella, no group_key (que cambia al crecer el audio).
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict

import numpy as np

import audio
import repeats
import store

fp = repeats.fp

WIN_S = 30            # ventana de escaneo
HOP_S = 10
MIN_REPETICIONES = 3  # un grupo sin etiquetar entra solo con tantas apariciones
MAX_DUR_ENTRADA_S = 90
MIN_INLIERS_ABS = 25  # suelo absoluto de hashes alineados (se ajusta con `calibrar`)
FRAC_ENTRADA = 0.10   # y al menos esta fraccion de los hashes de la entrada
MAX_FANOUT = 60
MISMA_ENTRADA_INLIERS = 40  # dos huellas con tantos inliers son la misma entrada
HUECO_MIN_S, HUECO_MAX_S = 3.0, 60.0
PAD_INI_S, PAD_FIN_S = 0.0, 0.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    radio TEXT NOT NULL,
    huella BLOB NOT NULL,
    n_hashes INTEGER NOT NULL,
    dur REAL NOT NULL,
    estado TEXT NOT NULL,              -- activo | lista_negra | caducado
    confirmado INTEGER NOT NULL DEFAULT 0,  -- 1 = lo ha confirmado el usuario
    t_origen REAL,                     -- epoch del audio del que sale
    last_seen REAL,                    -- epoch de la ultima coincidencia
    n_matches INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    radio TEXT NOT NULL,
    catalog_id INTEGER NOT NULL,
    t_start REAL NOT NULL,
    t_end REAL NOT NULL,
    inliers INTEGER NOT NULL,
    UNIQUE(radio, catalog_id, t_start)
);
"""


def ensure(c) -> None:
    c.executescript(SCHEMA)


def _bordes(f):
    return (f["t_start_user"] if f["t_start_user"] is not None else f["t_start"],
            f["t_end_user"] if f["t_end_user"] is not None else f["t_end"])


# ---------------------------------------------------------------- construccion

def _huella_rango(c, radio: str, a: float, b: float) -> dict[int, int]:
    return fp.fingerprint(audio.load_range(c, radio, a, b), store.SAMPLE_RATE)


def _misma(huella: dict[int, int], existentes: list[tuple[int, dict[int, int]]]) -> int | None:
    for eid, otra in existentes:
        res = fp.align(huella, otra)
        if res and res[0] >= MISMA_ENTRADA_INLIERS:
            return eid
    return None


def construir(c, radio: str) -> dict[str, int]:
    """Alta/actualizacion de entradas desde grupos repetidos y tramos etiquetados."""
    ensure(c)
    filas = c.execute(
        """SELECT id, radio, t_start, t_end, t_start_user, t_end_user, label, group_key, source, n_repeats
           FROM segments WHERE radio=? AND (group_key IS NOT NULL OR label=1)""", (radio,)).fetchall()
    grupos: dict[str, list] = defaultdict(list)
    for f in filas:
        grupos[f["group_key"] or f"solo:{f['id']}"].append(f)

    existentes = [(r["id"], fp.bytes_to_fingerprint(r["huella"])) for r in
                  c.execute("SELECT id, huella FROM catalog WHERE radio=?", (radio,))]
    stats = {"nuevas": 0, "ya_estaban": 0, "lista_negra": 0, "saltadas": 0}
    for clave, miembros in grupos.items():
        # un group_key puede juntar cuñas distintas (transitividad): una entrada por huella distinta
        for m in miembros:
            if m["label"] == 0:
                estado, confirmado = "lista_negra", 1
            elif m["label"] == 1:
                estado, confirmado = "activo", 1
            elif len(miembros) >= MIN_REPETICIONES and not any(x["label"] == 0 for x in miembros):
                estado, confirmado = "activo", 0
            else:
                stats["saltadas"] += 1
                continue
            a, b = _bordes(m)
            if not 3 <= b - a <= MAX_DUR_ENTRADA_S:
                stats["saltadas"] += 1
                continue
            huella = _huella_rango(c, radio, a, b)
            if len(huella) < 30:
                stats["saltadas"] += 1
                continue
            eid = _misma(huella, existentes)
            if eid is not None:
                # misma entrada: solo se puede endurecer el estado (negra gana; confirmar sube)
                c.execute("UPDATE catalog SET confirmado=MAX(confirmado, ?), "
                          "estado=CASE WHEN ?='lista_negra' THEN 'lista_negra' ELSE estado END WHERE id=?",
                          (confirmado, estado, eid))
                stats["ya_estaban"] += 1
                continue
            cur = c.execute(
                "INSERT INTO catalog(radio, huella, n_hashes, dur, estado, confirmado, t_origen) VALUES (?,?,?,?,?,?,?)",
                (radio, fp.fingerprint_to_bytes(huella), len(huella), b - a, estado, confirmado, a))
            existentes.append((cur.lastrowid, huella))
            stats["lista_negra" if estado == "lista_negra" else "nuevas"] += 1
    c.commit()
    print(f"[{radio}] catalogo: {stats}")
    return stats


# --------------------------------------------------------------------- escaneo

class Indice:
    def __init__(self, entradas: dict[int, tuple[dict[int, int], float, str]]):
        """entradas: id -> (huella, duracion, estado)."""
        self.entradas = entradas
        self.por_hash: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for eid, (huella, _dur, _est) in entradas.items():
            for h, t in huella.items():
                self.por_hash[h].append((eid, t))

    @classmethod
    def desde_bd(cls, c, radio: str, estados=("activo", "lista_negra")):
        ensure(c)
        marcas = ",".join("?" * len(estados))
        filas = c.execute(f"SELECT id, huella, dur, estado FROM catalog WHERE radio=? AND estado IN ({marcas})",
                          (radio, *estados)).fetchall()
        return cls({f["id"]: (fp.bytes_to_fingerprint(f["huella"]), f["dur"], f["estado"]) for f in filas})


def votar(indice: Indice, ventana: dict[int, int]) -> dict[int, tuple[int, int]]:
    """Por entrada: (inliers, desfase en frames) del mejor alineamiento."""
    votos: dict[tuple[int, int], int] = defaultdict(int)
    for h, tw in ventana.items():
        lista = indice.por_hash.get(h)
        if not lista or len(lista) > MAX_FANOUT:
            continue
        for eid, te in lista:
            votos[(eid, tw - te)] += 1
    mejor: dict[int, tuple[int, int]] = {}
    for (eid, off), n in votos.items():
        # tolera +-1 frame de desfase entre emisiones (la rejilla del STFT no coincide)
        total = n + votos.get((eid, off - 1), 0) + votos.get((eid, off + 1), 0)
        if eid not in mejor or total > mejor[eid][0]:
            mejor[eid] = (total, off)
    return mejor


def umbral(indice: Indice, eid: int) -> int:
    return max(MIN_INLIERS_ABS, int(FRAC_ENTRADA * len(indice.entradas[eid][0])))


def escanear(c, radio: str, t0: float, t1: float, indice: Indice,
             sin_umbral: bool = False) -> list[dict]:
    """Detecciones [{eid, ini, fin, inliers, estado}] en el audio [t0, t1]."""
    crudas: list[dict] = []
    t = t0
    while t + WIN_S <= t1 + 1e-6:
        ventana = fp.fingerprint(audio.load_range(c, radio, t, t + WIN_S), store.SAMPLE_RATE)
        for eid, (n, off) in votar(indice, ventana).items():
            if sin_umbral or n >= umbral(indice, eid):
                dur = indice.entradas[eid][1]
                ini = t + off * fp.FRAME_SECONDS
                crudas.append({"eid": eid, "ini": ini - PAD_INI_S, "fin": ini + dur + PAD_FIN_S,
                               "inliers": n, "estado": indice.entradas[eid][2]})
        t += HOP_S
    # une las detecciones solapadas de la misma entrada
    crudas.sort(key=lambda d: (d["eid"], d["ini"]))
    unidas: list[dict] = []
    for d in crudas:
        if unidas and unidas[-1]["eid"] == d["eid"] and d["ini"] <= unidas[-1]["fin"] + 2.0:
            u = unidas[-1]
            u["fin"] = max(u["fin"], d["fin"])
            u["inliers"] = max(u["inliers"], d["inliers"])
        else:
            unidas.append(dict(d))
    return sorted(unidas, key=lambda d: d["ini"])


def guardar_detecciones(c, radio: str, dets: list[dict]) -> int:
    n = 0
    for d in dets:
        cur = c.execute("INSERT OR IGNORE INTO detections(radio, catalog_id, t_start, t_end, inliers) VALUES (?,?,?,?,?)",
                        (radio, d["eid"], round(d["ini"], 1), d["fin"], d["inliers"]))
        if cur.rowcount and d["estado"] == "activo":
            c.execute("UPDATE catalog SET n_matches=n_matches+1, last_seen=MAX(COALESCE(last_seen,0), ?) WHERE id=?",
                      (d["fin"], d["eid"]))
        n += cur.rowcount
    c.commit()
    return n


# ----------------------------------------------------------------- huecos

def huecos(dets: list[dict], max_s: float = HUECO_MAX_S, min_s: float = HUECO_MIN_S) -> list[tuple[float, float]]:
    """Huecos entre dos anuncios conocidos (activos) consecutivos."""
    activos = sorted((d for d in dets if d["estado"] == "activo"), key=lambda d: d["ini"])
    out = []
    fin = None
    for d in activos:
        if fin is not None and min_s <= d["ini"] - fin <= max_s:
            out.append((fin, d["ini"]))
        fin = d["fin"] if fin is None else max(fin, d["fin"])
    return out


def proponer_huecos(c, radio: str) -> int:
    filas = c.execute(
        "SELECT d.catalog_id eid, d.t_start ini, d.t_end fin, d.inliers, k.estado FROM detections d "
        "JOIN catalog k ON k.id=d.catalog_id WHERE d.radio=?", (radio,)).fetchall()
    dets = [dict(f) for f in filas]
    n = 0
    for a, b in huecos(dets):
        ya = c.execute("SELECT 1 FROM segments WHERE radio=? AND COALESCE(t_start_user,t_start) < ? "
                       "AND COALESCE(t_end_user,t_end) > ? AND label IS NOT NULL LIMIT 1", (radio, b, a)).fetchone()
        if ya:
            continue
        n += c.execute("INSERT OR IGNORE INTO segments(radio, t_start, t_end, source) VALUES (?,?,?,'hueco')",
                       (radio, a, b)).rowcount
    c.commit()
    print(f"[{radio}] {n} huecos propuestos para revision")
    return n


# --------------------------------------------------------- caducidad / calibrar / eval

def caducar(c, dias: float) -> int:
    ensure(c)
    ref = c.execute("SELECT MAX(last_seen) FROM catalog").fetchone()[0]
    if ref is None:
        print("sin coincidencias registradas: no se caduca nada")
        return 0
    cur = c.execute("UPDATE catalog SET estado='caducado' WHERE estado='activo' AND confirmado=0 "
                    "AND COALESCE(last_seen, t_origen) < ?", (ref - dias * 86400,))
    c.commit()
    print(f"{cur.rowcount} entradas caducadas (sin verse en {dias} dias; las confirmadas no caducan)")
    return cur.rowcount


def _solapa(a0, a1, b0, b1, frac=0.5) -> bool:
    return min(a1, b1) - max(a0, b0) >= frac * min(a1 - a0, b1 - b0)


def calibrar(c, radio: str, muestras: int = 60) -> None:
    """Inliers de aciertos conocidos frente a ventanas sin ningun anuncio del catalogo."""
    indice = Indice.desde_bd(c, radio, ("activo",))
    if not indice.entradas:
        print("catalogo vacio")
        return
    span = audio.radio_span(c, radio)
    crudas = escanear(c, radio, span[0], span[1], indice, sin_umbral=True)
    todas = sorted(((f["ini"], f["fin"]) for f in crudas if f["inliers"] >= 60), key=lambda x: x[0])
    rng = random.Random(1)
    neg = []
    for _ in range(muestras * 4):
        if len(neg) >= muestras:
            break
        t = rng.uniform(span[0], span[1] - WIN_S)
        if any(_solapa(t, t + WIN_S, a - 5, b + 5, 0.0001) for a, b in todas):
            continue
        v = fp.fingerprint(audio.load_range(c, radio, t, t + WIN_S), store.SAMPLE_RATE)
        m = votar(indice, v)
        neg.append(max((n for n, _ in m.values()), default=0))
    pos = [d["inliers"] for d in crudas if d["inliers"] >= 60]
    print(f"negativos ({len(neg)} ventanas): p50={np.percentile(neg,50):.0f} p99={np.percentile(neg,99):.0f} max={max(neg)}")
    if pos:
        print(f"aciertos fuertes ({len(pos)}): min={min(pos)} p10={np.percentile(pos,10):.0f} mediana={np.percentile(pos,50):.0f}")
    print(f"umbral actual: max({MIN_INLIERS_ABS}, {FRAC_ENTRADA:.0%} de los hashes de la entrada)")


def evaluar(c, radio: str, frac: float = 0.5) -> None:
    """Division temporal: el catalogo sale solo del audio anterior a t_corte."""
    span = audio.radio_span(c, radio)
    corte = span[0] + frac * (span[1] - span[0])
    filas = c.execute("SELECT * FROM segments WHERE source='repeat' AND group_key IS NOT NULL AND radio=?",
                      (radio,)).fetchall()
    grupos: dict[str, list] = defaultdict(list)
    for f in filas:
        grupos[f["group_key"]].append(f)
    entradas: dict[int, tuple] = {}
    previas = posteriores_conocidas = posteriores_nuevas = 0
    test: list[tuple[float, float, bool]] = []  # (ini, fin, hay una entrada previa de ese audio)
    for ms in grupos.values():
        for m in (m for m in ms if _bordes(m)[1] <= corte):
            a, b = _bordes(m)
            h = _huella_rango(c, radio, a, b)
            if len(h) >= 30 and _misma(h, [(k, v[0]) for k, v in entradas.items()]) is None:
                entradas[len(entradas) + 1] = (h, b - a, "activo")
                previas += 1
    lista = [(k, v[0]) for k, v in entradas.items()]
    for ms in grupos.values():
        for m in (m for m in ms if _bordes(m)[0] >= corte):
            a, b = _bordes(m)
            conocido = _misma(_huella_rango(c, radio, a, b), lista) is not None
            test.append((a, b, conocido))
            posteriores_conocidas += conocido
            posteriores_nuevas += not conocido
    print(f"corte={corte:.0f}  entradas previas={previas}  apariciones posteriores: "
          f"con entrada={posteriores_conocidas}, primera emision={posteriores_nuevas}")
    if not entradas:
        print("No hay grupos con apariciones antes del corte: audio insuficiente para evaluar.")
        return
    dets = escanear(c, radio, corte, span[1], Indice(entradas))
    ok = sum(1 for (a, b, conocido) in test if conocido and any(_solapa(a, b, d["ini"], d["fin"]) for d in dets))
    print(f"recall sobre apariciones con entrada previa: {ok}/{posteriores_conocidas}")
    fp_etiq = 0
    for d in dets:
        r = c.execute("SELECT 1 FROM segments WHERE radio=? AND label=0 AND COALESCE(t_start_user,t_start) < ? "
                      "AND COALESCE(t_end_user,t_end) > ? LIMIT 1", (radio, d["fin"], d["ini"])).fetchone()
        fp_etiq += 1 if r else 0
    fuera = sum(1 for d in dets if not any(_solapa(a, b, d["ini"], d["fin"]) for a, b, _ in test))
    print(f"detecciones={len(dets)}  sobre tramos que marcaste 'no anuncio'={fp_etiq}  "
          f"fuera de los grupos conocidos (a revisar)={fuera}")
    print(f"huecos entre anuncios conocidos: {len(huecos(dets))}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "scan", "huecos", "calibrar", "eval", "caducar"])
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--desde", type=float, default=None, help="epoch desde el que escanear")
    ap.add_argument("--dias", type=float, default=14)
    ap.add_argument("--frac", type=float, default=0.5)
    a = ap.parse_args()
    c = store.conn2()
    ensure(c)
    if a.cmd == "build":
        construir(c, a.radio)
    elif a.cmd == "scan":
        span = audio.radio_span(c, a.radio)
        t0 = a.desde if a.desde is not None else (c.execute("SELECT MAX(t_end) FROM detections WHERE radio=?",
                                                            (a.radio,)).fetchone()[0] or span[0])
        dets = escanear(c, a.radio, t0, span[1], Indice.desde_bd(c, a.radio))
        print(f"{len(dets)} detecciones, {guardar_detecciones(c, a.radio, dets)} nuevas")
    elif a.cmd == "huecos":
        proponer_huecos(c, a.radio)
    elif a.cmd == "calibrar":
        calibrar(c, a.radio)
    elif a.cmd == "eval":
        evaluar(c, a.radio, a.frac)
    else:
        caducar(c, a.dias)


if __name__ == "__main__":
    main()
