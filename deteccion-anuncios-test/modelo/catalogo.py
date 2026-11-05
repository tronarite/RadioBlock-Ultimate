"""Catalogo de anuncios conocidos que se aprende y se afina solo.

Los anuncios cambian, pero cada campaña suena varias veces al dia. Los grupos
que la repeticion encuentra (segments_repeat.py) entran al catalogo; el
escaneo busca esas huellas en el audio nuevo; y cada vez que una entrada se
oye mas veces se REAFINA: su recorte pasa a ser lo que coincide en la mayoria
de emisiones (asi un anuncio que salio pegado a otro acaba cortado limpio).
Si dos anuncios conocidos rodean un hueco corto, el hueco se propone tambien.

  python catalogo.py build  --radio cadena_ser    # grupos repetidos -> catalogo
  python catalogo.py scan   --radio cadena_ser    # busca el catalogo en el audio
  python catalogo.py afinar --radio cadena_ser    # reafina entradas ya oidas varias veces
  python catalogo.py huecos --radio cadena_ser    # propone huecos entre anuncios conocidos
  python catalogo.py calibrar|eval|caducar

Identidad de una entrada = su huella: dos entradas son la misma solo si lo que
coincide cubre ~80 % de AMBAS (un anuncio limpio dentro de un bloque no se
confunde con el bloque).
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path
from collections import defaultdict

import numpy as np

import audio
import fingerprint as fp
import store

WIN_S = 30            # ventana de escaneo
HOP_S = 10
MIN_REPETICIONES = 3  # un grupo sin etiquetar entra solo con tantas apariciones
MAX_DUR_ENTRADA_S = 90
MIN_INLIERS_ABS = 25  # suelo absoluto de hashes alineados (se ajusta con `calibrar`)
FRAC_ENTRADA = 0.10   # y al menos esta fraccion de los hashes de la entrada
MAX_FANOUT = 60
MISMA_ENTRADA_INLIERS = 40   # y cobertura >= COBERTURA_MISMA de ambas duraciones
COBERTURA_MISMA = 0.80
HUECO_MIN_S, HUECO_MAX_S = 3.0, 60.0
PAD_INI_S, PAD_FIN_S = 1.0, 0.8   # las anclas de la huella llegan tarde al inicio y acaban antes del final
AFINAR_MIN_DET = 3            # emisiones necesarias para afinar
AFINAR_FRAC = 0.65            # el recorte es lo que cubre esta fraccion de las emisiones
AFINAR_MAX_DET = 40
MARGEN_AFINAR_S = 12.0
CORTE_MIN_APOYOS = 2          # emisiones que deben empezar/acabar en el mismo punto interior
CORTE_TOL_S = 1.5             # tolerancia entre emisiones y hasta la pausa
CORTE_BORDE_S = 3.0           # un corte dentro de los primeros/ultimos segundos no cuenta
PAUSA_PROF = 0.10             # una pausa: energia por debajo del 10 % de la mediana
ATOMO_MIN_S = 4.0

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
COLUMNAS_EXTRA = {"refinado": "INTEGER NOT NULL DEFAULT 0",  # emisiones usadas en el ultimo afinado
                  "dispersion": "REAL",                      # desviacion (s) de inicio/fin entre emisiones
                  "wav": "TEXT",
                  "grupo": "INTEGER"}                             # copia corta del audio, para revisarla


def ensure(c) -> None:
    c.executescript(SCHEMA)
    existentes = {r[1] for r in c.execute("PRAGMA table_info(catalog)")}
    for nombre, tipo in COLUMNAS_EXTRA.items():
        if nombre not in existentes:
            c.execute(f"ALTER TABLE catalog ADD COLUMN {nombre} {tipo}")
    c.commit()


def _bordes(f):
    return (f["t_start_user"] if f["t_start_user"] is not None else f["t_start"],
            f["t_end_user"] if f["t_end_user"] is not None else f["t_end"])


def umbral_para(n_hashes: int) -> int:
    return max(MIN_INLIERS_ABS, int(FRAC_ENTRADA * n_hashes))


# ---------------------------------------------------------------- construccion

def _huella_rango(c, radio: str, a: float, b: float) -> dict[int, int]:
    return fp.fingerprint(audio.load_range(c, radio, a, b), store.SAMPLE_RATE)


def _misma(huella: dict[int, int], dur: float, existentes: list[tuple[int, dict[int, int], float]]) -> int | None:
    """Misma entrada = lo que coincide cubre ~80 % de AMBAS duraciones."""
    for eid, otra, dur_otra in existentes:
        res = fp.align(huella, otra)
        if not res or res[0] < MISMA_ENTRADA_INLIERS:
            continue
        cub_a = (res[1][1] - res[1][0]) / max(dur, 1e-6)
        cub_b = (res[2][1] - res[2][0]) / max(dur_otra, 1e-6)
        if cub_a >= COBERTURA_MISMA and cub_b >= COBERTURA_MISMA:
            return eid
    return None


def agrupar(c, radio: str) -> int:
    """Marca en `grupo` las copias del mismo audio entre las entradas por revisar (grupo = id del representante,
    el mas sonado), para que la revision pregunte una sola vez por audio."""
    filas = c.execute("SELECT id, huella, dur, n_matches FROM catalog WHERE radio=? AND estado='activo' AND confirmado=0",
                      (radio,)).fetchall()
    ids = [r["id"] for r in filas]
    H = {r["id"]: fp.bytes_to_fingerprint(r["huella"]) for r in filas}
    D = {r["id"]: r["dur"] for r in filas}
    padre = {i: i for i in ids}

    def raiz(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            res = fp.align(H[ids[a]], H[ids[b]])
            if res and res[0] >= MIN_INLIERS_ABS and max((res[1][1] - res[1][0]) / D[ids[a]],
                                                         (res[2][1] - res[2][0]) / D[ids[b]]) >= 0.6:
                padre[raiz(ids[a])] = raiz(ids[b])
    grupos: dict[int, list] = defaultdict(list)
    for r in filas:
        grupos[raiz(r["id"])].append(r)
    for miembros in grupos.values():
        rep = max(miembros, key=lambda r: (r["n_matches"], -r["id"]))["id"]
        for r in miembros:
            c.execute("UPDATE catalog SET grupo=? WHERE id=?", (rep, r["id"]))
    c.commit()
    return len(grupos)


def propagar_grupo(c, eid: int, negra: bool) -> int:
    """La respuesta a una entrada vale para las demas copias de su grupo."""
    g = c.execute("SELECT grupo FROM catalog WHERE id=?", (eid,)).fetchone()
    if not g or g["grupo"] is None:
        return 0
    if negra:
        cur = c.execute("UPDATE catalog SET estado='lista_negra', confirmado=1 WHERE grupo=? AND id<>? "
                        "AND estado='activo' AND confirmado=0", (g["grupo"], eid))
    else:
        cur = c.execute("UPDATE catalog SET confirmado=1 WHERE grupo=? AND id<>? AND estado='activo'", (g["grupo"], eid))
    return cur.rowcount


def propagar_negra(c, eid: int) -> int:
    """Un "No" vale para todas las entradas que son ese mismo audio (o casi todo contenido en el):
    las cuñas cortas dan pocos hashes y no se fusionan solas, asi que salian decenas de copias."""
    ref = c.execute("SELECT radio, huella FROM catalog WHERE id=?", (eid,)).fetchone()
    if not ref:
        return 0
    huella = fp.bytes_to_fingerprint(ref["huella"])
    n = 0
    for r in c.execute("SELECT id, huella, dur FROM catalog WHERE radio=? AND id<>? AND estado='activo' AND confirmado=0",
                       (ref["radio"], eid)).fetchall():
        res = fp.align(huella, fp.bytes_to_fingerprint(r["huella"]))
        if not res or res[0] < MIN_INLIERS_ABS:
            continue
        if (res[2][1] - res[2][0]) / max(r["dur"], 1e-6) >= 0.6:   # la otra entrada es sobre todo este audio
            c.execute("UPDATE catalog SET estado='lista_negra', confirmado=1 WHERE id=?", (r["id"],))
            n += 1
    return n


def construir(c, radio: str) -> dict[str, int]:
    """Alta de entradas desde grupos repetidos (y tramos etiquetados si los hay)."""
    ensure(c)
    filas = c.execute(
        """SELECT id, radio, t_start, t_end, t_start_user, t_end_user, label, group_key, source, n_repeats
           FROM segments WHERE radio=? AND (group_key IS NOT NULL OR label=1)""", (radio,)).fetchall()
    grupos: dict[str, list] = defaultdict(list)
    for f in filas:
        grupos[f["group_key"] or f"solo:{f['id']}"].append(f)

    existentes = [(r["id"], fp.bytes_to_fingerprint(r["huella"]), r["dur"]) for r in
                  c.execute("SELECT id, huella, dur FROM catalog WHERE radio=?", (radio,))]
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
            eid = _misma(huella, b - a, existentes)
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
            existentes.append((cur.lastrowid, huella, b - a))
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
    return umbral_para(len(indice.entradas[eid][0]))


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
    """Inserta las detecciones nuevas; una que solapa con otra de la misma entrada ya guardada no cuenta."""
    n = 0
    for d in dets:
        ya = c.execute("SELECT 1 FROM detections WHERE radio=? AND catalog_id=? AND t_start < ? AND t_end > ? LIMIT 1",
                       (radio, d["eid"], d["fin"], d["ini"])).fetchone()
        if ya:
            continue
        c.execute("INSERT OR IGNORE INTO detections(radio, catalog_id, t_start, t_end, inliers) VALUES (?,?,?,?,?)",
                  (radio, d["eid"], round(d["ini"], 1), d["fin"], d["inliers"]))
        if d["estado"] == "activo":
            c.execute("UPDATE catalog SET n_matches=n_matches+1, last_seen=MAX(COALESCE(last_seen,0), ?) WHERE id=?",
                      (d["fin"], d["eid"]))
        n += 1
    c.commit()
    return n


# ------------------------------------------------------------------- afinado

def consenso(rangos: list[tuple[float, float]], frac: float = AFINAR_FRAC) -> tuple[float, float] | None:
    """Tramo cubierto por al menos `frac` de los rangos. No es la interseccion: una emision
    truncada o con un locutor encima no debe recortar la entrada para siempre."""
    if not rangos:
        return None
    k = max(1, math.ceil(frac * len(rangos)))
    inicio = sorted(r[0] for r in rangos)[k - 1]
    fin = sorted((r[1] for r in rangos), reverse=True)[k - 1]
    return (inicio, fin) if fin > inicio else None


def pausas(pcm: np.ndarray, sr: int, ini: float, fin: float) -> list[float]:
    """Instantes (s, relativos a `ini`) de las pausas profundas dentro de [ini, fin]."""
    seg = pcm[max(0, int(ini * sr)):int(fin * sr)]
    h = max(1, int(sr * 0.05))
    n = len(seg) // h
    if n < 10:
        return []
    r = np.sqrt((seg[:n * h].reshape(n, h).astype(np.float64) ** 2).mean(1))
    med = float(np.median(r))
    if med <= 0:
        return []
    out, ult = [], -99
    for i in range(1, n - 1):
        if r[i] < PAUSA_PROF * med and r[i] <= r[i - 1] and r[i] <= r[i + 1] and i - ult >= 10:
            out.append(i * 0.05)
            ult = i
    return out


def cortes_confirmados(rangos: list[tuple[float, float]], dur: float, pausas_s: list[float]) -> list[float]:
    """Puntos interiores donde >= CORTE_MIN_APOYOS emisiones empiezan o acaban (el anuncio sale con
    otros vecinos) Y hay una pausa cerca. Solo el apoyo de las emisiones no basta (una emision recortada
    por un locutor encima tambien da un borde) y solo la pausa tampoco (hay pausas dentro de un anuncio)."""
    cand = []
    for a, b in rangos:
        if a > CORTE_BORDE_S:
            cand.append(a - PAD_INI_S)
        if b < dur - CORTE_BORDE_S:
            cand.append(b + PAD_FIN_S)
    cand = sorted(c for c in cand if CORTE_BORDE_S < c < dur - CORTE_BORDE_S)
    cortes, grupo = [], []
    for c in cand + [None]:
        if c is not None and grupo and c - grupo[-1] <= CORTE_TOL_S:
            grupo.append(c)
            continue
        if len(grupo) >= CORTE_MIN_APOYOS:
            centro = float(np.median(grupo))
            cerca = [p for p in pausas_s if abs(p - centro) <= CORTE_TOL_S]
            if cerca:
                cortes.append(min(cerca, key=lambda p: abs(p - centro)))
        grupo = [c] if c is not None else []
    return cortes


def recortar_por_consenso(huella: dict[int, int], ocurrencias: list[tuple[np.ndarray, int]],
                          min_inliers: int) -> dict | None:
    """Alinea la entrada contra el audio de cada emision (con margen) y devuelve el recorte comun.
    `alineos[i]` es None si la emision i no alinea; si no, (inliers, rango en la entrada, rango en su audio)."""
    alineos = []
    for pcm, sr in ocurrencias:
        res = fp.align(huella, fp.fingerprint(pcm, sr))
        alineos.append(res if res and res[0] >= min_inliers else None)
    validos = [r for r in alineos if r]
    if len(validos) < AFINAR_MIN_DET:
        return None
    span = consenso([r[1] for r in validos])
    if span is None:
        return None
    ini = max(0.0, span[0] - PAD_INI_S)
    fin = span[1] + PAD_FIN_S
    f0, f1 = ini / fp.FRAME_SECONDS, fin / fp.FRAME_SECONDS
    nueva = {h: int(t - f0) for h, t in huella.items() if f0 <= t <= f1}
    disp = float(np.mean([np.std([r[1][0] for r in validos]), np.std([r[1][1] for r in validos])]))
    return {"span": (ini, fin), "huella": nueva, "dispersion": disp, "alineos": alineos, "n": len(validos)}


def afinar(c, radio: str, eid: int) -> bool:
    """Reafina una entrada con el audio de sus ultimas emisiones. True si cambio."""
    ent = c.execute("SELECT * FROM catalog WHERE id=?", (eid,)).fetchone()
    huella = fp.bytes_to_fingerprint(ent["huella"])
    dets = c.execute("SELECT * FROM detections WHERE radio=? AND catalog_id=? ORDER BY t_start DESC LIMIT ?",
                     (radio, eid, AFINAR_MAX_DET)).fetchall()
    margen = max(MARGEN_AFINAR_S, ent["dur"])
    audios, origen = [], []
    for d in dets:
        a = d["t_start"] - margen
        pcm = audio.load_range(c, radio, a, d["t_end"] + margen)
        if pcm.size and float(np.abs(pcm).max()) > 0:
            audios.append((pcm, store.SAMPLE_RATE))
            origen.append((d, a))
    res = recortar_por_consenso(huella, audios, umbral_para(len(huella)))
    if res is None:
        return False
    ini, fin = res["span"]
    if len(res["huella"]) < 30 or fin - ini < 2:
        return False
    # ¿es un bloque de varios anuncios? se divide donde las emisiones lo respaldan y hay pausa
    mejor_i = max((i for i, r in enumerate(res["alineos"]) if r), key=lambda i: res["alineos"][i][0])
    r_m = res["alineos"][mejor_i]
    delta_m = r_m[2][0] - r_m[1][0]
    cortes = cortes_confirmados([r[1] for r in res["alineos"] if r], ent["dur"],
                                [p + 0 for p in pausas(audios[mejor_i][0], store.SAMPLE_RATE, delta_m, delta_m + ent["dur"])])
    cortes = [x for x in cortes if ini < x < fin]
    if cortes:
        return dividir(c, radio, ent, huella, [ini] + cortes + [fin], res)
    # detecciones con los limites reales (rango alineado en el audio), no extrapolados
    nuevas, mejor = [], None
    for (d, a), r in zip(origen, res["alineos"]):
        if r is None:
            continue
        delta = r[2][0] - r[1][0]           # tiempo en el audio = tiempo en la entrada + delta
        t_ini, t_fin = a + ini + delta, a + fin + delta
        nuevas.append((round(t_ini, 1), t_fin, r[0]))
        if mejor is None or r[0] > mejor[0]:
            mejor = (r[0], t_ini, t_fin)
    c.execute("DELETE FROM detections WHERE id IN (%s)" % ",".join(str(d["id"]) for d, _ in origen))
    for t_ini, t_fin, inl in nuevas:
        c.execute("INSERT OR IGNORE INTO detections(radio, catalog_id, t_start, t_end, inliers) VALUES (?,?,?,?,?)",
                  (radio, eid, t_ini, t_fin, inl))
    wav = None
    if mejor:
        carpeta = store.DATA_DIR / "entradas"
        carpeta.mkdir(parents=True, exist_ok=True)
        wav = str(carpeta / f"{eid}.wav")
        audio.write_wav(wav, audio.load_range(c, radio, mejor[1], mejor[2]))
    c.execute("UPDATE catalog SET huella=?, n_hashes=?, dur=?, t_origen=COALESCE(t_origen,0)+?, refinado=?, "
              "dispersion=?, wav=COALESCE(?, wav) WHERE id=?",
              (fp.fingerprint_to_bytes(res["huella"]), len(res["huella"]), fin - ini, ini, res["n"],
               res["dispersion"], wav, eid))
    c.commit()
    print(f"  entrada {eid}: {ent['dur']:.1f}s -> {fin - ini:.1f}s con {res['n']} emisiones, "
          f"dispersion {res['dispersion']:.2f}s", flush=True)
    return True


def dividir(c, radio: str, ent, huella: dict[int, int], limites: list[float], res: dict) -> bool:
    """Sustituye un bloque por los anuncios sueltos que lo forman. El bloque se da de baja."""
    creadas = 0
    for a, b in zip(limites[:-1], limites[1:]):
        if b - a < ATOMO_MIN_S:
            continue
        f0, f1 = a / fp.FRAME_SECONDS, b / fp.FRAME_SECONDS
        h = {k: int(t - f0) for k, t in huella.items() if f0 <= t <= f1}
        if len(h) < 30:
            continue
        c.execute("INSERT INTO catalog(radio, huella, n_hashes, dur, estado, confirmado, t_origen) VALUES (?,?,?,?,?,?,?)",
                  (radio, fp.fingerprint_to_bytes(h), len(h), b - a, "activo", 0, (ent["t_origen"] or 0) + a))
        creadas += 1
    if not creadas:
        return False
    c.execute("DELETE FROM detections WHERE radio=? AND catalog_id=?", (radio, ent["id"]))
    c.execute("UPDATE catalog SET estado='caducado' WHERE id=?", (ent["id"],))
    c.commit()
    print(f"  entrada {ent['id']} ({ent['dur']:.1f}s) dividida en {creadas} anuncios sueltos, cortes en "
          + ", ".join(f"{x:.1f}s" for x in limites[1:-1]), flush=True)
    return True


def afinar_pendientes(c, radio: str) -> int:
    """Afina las entradas activas que ya se oyeron AFINAR_MIN_DET veces, y de nuevo cada vez que
    el numero de emisiones se duplica (3, 6, 12, 24...)."""
    n = 0
    for e in c.execute("SELECT id FROM catalog WHERE radio=? AND estado='activo' "
                       "AND n_matches >= ? AND n_matches >= 2 * refinado", (radio, AFINAR_MIN_DET)).fetchall():
        n += afinar(c, radio, e["id"])
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
    print(f"[{radio}] {n} huecos propuestos")
    return n


def exportar_huecos(c, radio: str) -> int:
    """Recorta el audio de los huecos sin revisar (data/huecos/<id>.wav) para la web de revision."""
    carpeta = Path(c.execute("PRAGMA database_list").fetchone()[2]).parent / "huecos"
    carpeta.mkdir(exist_ok=True)
    n = 0
    for s in c.execute("SELECT id, t_start, t_end FROM segments WHERE radio=? AND source='hueco' AND label IS NULL", (radio,)).fetchall():
        f = carpeta / f"{s['id']}.wav"
        if not f.exists():
            pcm = audio.load_range(c, radio, s["t_start"], s["t_end"])
            if not len(pcm) or not np.abs(pcm).max() > 0:   # sin audio grabado: nada que revisar
                continue
            audio.write_wav(str(f), pcm)
            n += 1
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
    if neg:
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
            if len(h) >= 30 and _misma(h, b - a, [(k, v[0], v[1]) for k, v in entradas.items()]) is None:
                entradas[len(entradas) + 1] = (h, b - a, "activo")
                previas += 1
    lista = [(k, v[0], v[1]) for k, v in entradas.items()]
    for ms in grupos.values():
        for m in (m for m in ms if _bordes(m)[0] >= corte):
            a, b = _bordes(m)
            conocido = _misma(_huella_rango(c, radio, a, b), b - a, lista) is not None
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
    fuera = sum(1 for d in dets if not any(_solapa(a, b, d["ini"], d["fin"]) for a, b, _ in test))
    print(f"detecciones={len(dets)}  fuera de los grupos conocidos (a revisar)={fuera}")
    print(f"huecos entre anuncios conocidos: {len(huecos(dets))}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "scan", "afinar", "huecos", "calibrar", "eval", "caducar"])
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
    elif a.cmd == "afinar":
        print(f"{afinar_pendientes(c, a.radio)} entradas afinadas")
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
