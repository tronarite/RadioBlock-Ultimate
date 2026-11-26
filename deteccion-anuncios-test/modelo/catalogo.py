"""Catalogo de anuncios conocidos que se aprende y se afina solo.

Los anuncios cambian, pero cada campaña suena varias veces al dia. Los grupos
que la repeticion encuentra (segments_repeat.py) entran al catalogo; el
escaneo busca esas huellas en el audio nuevo; y cada vez que una entrada se
oye mas veces se REAFINA: su recorte pasa a ser lo que coincide en la mayoria
de emisiones (asi un anuncio que salio pegado a otro acaba cortado limpio).

  python catalogo.py build  --radio cadena_ser    # grupos repetidos -> catalogo
  python catalogo.py scan   --radio cadena_ser    # busca el catalogo en el audio
  python catalogo.py afinar --radio cadena_ser    # reafina entradas ya oidas varias veces
  python catalogo.py caducar --radio cadena_ser [--dias 30]

Identidad de una entrada = su huella: dos entradas son la misma solo si lo que
coincide cubre ~80 % de AMBAS (un anuncio limpio dentro de un bloque no se
confunde con el bloque).
"""

from __future__ import annotations

import argparse
import math
import time
from collections import defaultdict

import numpy as np

import audio
import fingerprint as fp
import reglas
import store

WIN_S = 30            # ventana de escaneo
HOP_S = 10
MIN_REPETICIONES = 3  # un grupo sin etiquetar entra solo con tantas apariciones
MAX_DUR_ENTRADA_S = 90
MIN_DUR_ENTRADA_S = 6.0   # por debajo solo hay sintonias, la hora... (ningun Si del usuario dura menos de 8 s)
MIN_INLIERS_ABS = 25  # suelo absoluto de hashes alineados
FRAC_ENTRADA = 0.10   # y al menos esta fraccion de los hashes de la entrada
MAX_FANOUT = 60
MISMA_ENTRADA_INLIERS = 40   # y cobertura >= COBERTURA_MISMA de ambas duraciones
COBERTURA_MISMA = 0.80
PAD_INI_S, PAD_FIN_S = 1.0, 0.8   # las anclas de la huella llegan tarde al inicio y acaban antes del final
AFINAR_MIN_DET = 3            # emisiones necesarias para afinar
AFINAR_FRAC = 0.65            # el recorte es lo que cubre esta fraccion de las emisiones
AFINAR_MAX_DET = 40
MARGEN_AFINAR_S = 12.0
CORTE_MIN_APOYOS = 2          # emisiones que deben empezar/acabar en el mismo punto interior
CORTE_TOL_S = 1.5             # tolerancia entre emisiones y hasta la pausa
CORTE_BORDE_S = 3.0           # un corte dentro de los primeros/ultimos segundos no cuenta
PAUSA_PROF = 0.10             # una pausa: energia por debajo del 10 % de la mediana
ATOMO_MIN_S = MIN_DUR_ENTRADA_S

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
                  "wav": "TEXT",                             # no vacio = existe data/entradas/<id>.wav
                  "grupo": "INTEGER",
                  "intento": "INTEGER NOT NULL DEFAULT 0",   # emisiones que habia la ultima vez que se intento afinar
                  "fuente": "TEXT",                          # quien dio el veredicto: usuario | ia | regla | herencia | conducta | salud
                  "t_veredicto": "REAL"}                     # epoch del ultimo veredicto


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


def heredar(c, radio: str) -> tuple[int, int]:
    """Una entrada nueva que ya es (en su mayor parte) un audio que el usuario contesto hereda esa respuesta,
    para no preguntar dos veces lo mismo. Si coincide con un Si y con un No a la vez, se pregunta."""
    resp = c.execute("SELECT id, huella, estado FROM catalog WHERE radio=? AND confirmado=1 "
                     "AND estado IN ('activo','lista_negra')", (radio,)).fetchall()
    ref = [(r["estado"], fp.bytes_to_fingerprint(r["huella"])) for r in resp]
    si = no = 0
    for e in c.execute("SELECT id, huella, dur FROM catalog WHERE radio=? AND estado='activo' AND confirmado=0 "
                       "AND COALESCE(fuente, '') NOT IN ('conducta', 'salud')",
                       (radio,)).fetchall():
        h = fp.bytes_to_fingerprint(e["huella"])
        vistos = set()
        for estado, otra in ref:
            res = fp.align(h, otra)
            if res and res[0] >= MIN_INLIERS_ABS and (res[1][1] - res[1][0]) / max(e["dur"], 1e-6) >= 0.6:
                vistos.add(estado)
        if vistos == {"lista_negra"}:
            c.execute("UPDATE catalog SET estado='lista_negra', confirmado=1 WHERE id=?", (e["id"],))
            no += 1
        elif vistos == {"activo"}:
            c.execute("UPDATE catalog SET confirmado=1 WHERE id=?", (e["id"],))
            si += 1
    c.commit()
    return si, no


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
            if not MIN_DUR_ENTRADA_S <= b - a <= MAX_DUR_ENTRADA_S:
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
        # todas las clases: la caducidad mira last_seen tambien en la lista negra
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
        wav = f"{eid}.wav"   # solo el nombre: el audio se busca siempre en data/entradas/<id>.wav
        audio.write_wav(str(carpeta / wav), audio.load_range(c, radio, mejor[1], mejor[2]))
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
    el numero de emisiones se duplica (3, 6, 12, 24...). Si el afinado no sale, tampoco se reintenta
    hasta que se dupliquen (`intento`): antes se repetia en cada vuelta con cientos de entradas."""
    n = 0
    for e in c.execute("SELECT id, n_matches FROM catalog WHERE radio=? AND estado='activo' "
                       "AND n_matches >= ? AND n_matches >= 2 * MAX(refinado, intento)", (radio, AFINAR_MIN_DET)).fetchall():
        n += afinar(c, radio, e["id"])
        c.execute("UPDATE catalog SET intento=? WHERE id=?", (e["n_matches"], e["id"]))
    # si lo que se repite de una entrada sin revisar dura menos del minimo, era una sintonia: baja
    # (caducado, no lista negra: un "No" a un trozo corto contagiaria a cuñas cortas que si son anuncios)
    cur = c.execute("UPDATE catalog SET estado='caducado' WHERE radio=? AND estado='activo' AND confirmado=0 "
                    "AND dur < ?", (radio, MIN_DUR_ENTRADA_S))
    c.commit()
    if cur.rowcount:
        print(f"  {cur.rowcount} entradas de menos de {MIN_DUR_ENTRADA_S:.0f}s dadas de baja (sintonias)", flush=True)
    return n


def completar_wavs(c, radio: str) -> int:
    """Da audio (data/entradas/<id>.wav) a las entradas activas que no lo tienen, sin esperar al
    afinado: de su ultima emision detectada o, si no hay, del audio del que salieron. Sin audio no
    hay transcripcion ni veredicto (el 6-nov, 657 de 847 pendientes estaban asi)."""
    carpeta = store.DATA_DIR / "entradas"
    carpeta.mkdir(parents=True, exist_ok=True)
    n = 0
    for e in c.execute("SELECT id, dur, t_origen FROM catalog WHERE radio=? AND estado='activo' "
                       "AND wav IS NULL AND dur >= ?", (radio, MIN_DUR_ENTRADA_S)).fetchall():
        d = c.execute("SELECT t_start, t_end FROM detections WHERE radio=? AND catalog_id=? "
                      "ORDER BY t_start DESC LIMIT 1", (radio, e["id"])).fetchone()
        tramos = [(d["t_start"], d["t_end"])] if d else []
        if e["t_origen"]:
            tramos.append((e["t_origen"], e["t_origen"] + e["dur"]))
        for a, b in tramos:
            pcm = audio.load_range(c, radio, a, b)
            if pcm.size and float(np.abs(pcm).max()) > 0:
                audio.write_wav(str(carpeta / f"{e['id']}.wav"), pcm)
                c.execute("UPDATE catalog SET wav=? WHERE id=?", (f"{e['id']}.wav", e["id"]))
                n += 1
                break
    c.commit()
    return n


def podar(c, radio: str, horas: float) -> tuple[int, int]:
    """Borra lo que ya no sirve: tramos repetidos anteriores a la ventana de analisis (construir()
    volvia a sacar su huella en cada vuelta), los huecos (ya no se revisan) y las detecciones de
    mas de 30 dias. Devuelve (tramos, detecciones) borrados."""
    span = audio.radio_span(c, radio)
    if not span:
        return 0, 0
    ref = span[1]
    s = c.execute("DELETE FROM segments WHERE radio=? AND ((source='repeat' AND label IS NULL AND t_end < ?) "
                  "OR source='hueco')", (radio, ref - (horas + 12) * 3600)).rowcount
    d = c.execute("DELETE FROM detections WHERE radio=? AND t_end < ?", (radio, ref - 30 * 86400)).rowcount
    c.commit()
    return s, d


CONDUCTA_MIN_DET = 6          # detecciones necesarias para juzgar la conducta de una entrada
CONDUCTA_ULTIMAS = 20         # se miran sus ultimas emisiones
CONDUCTA_VECINO_S = 15.0      # una emision tiene vecino si otra entrada activa suena a esta distancia
CONDUCTA_AISLADA = 0.80       # fraccion de emisiones aisladas a partir de la que deja de ser anuncio
RELOJ_FIJO = 0.9              # como en juez.py: suena siempre en el mismo minuto de la hora


def revisar_conducta(c, radio: str) -> list[tuple[int, str]]:
    """Autocorreccion (docs/GUIA_IA.md, 6.6). Devuelve (id, 'conducta' | 'regla') de lo que cambia:
    - conducta: un anuncio (no del usuario) con >= 6 emisiones que suena aislado en >= 80 % de las 20
      ultimas (ninguna otra entrada activa a 15 s o menos de su inicio o su final; las copias del mismo
      audio, que se solapan con ella en mas de la mitad de la mas corta, no cuentan) vuelve a pendiente.
    - reloj tardio: un anuncio o pendiente con >= 6 emisiones que suena siempre en el mismo minuto de la
      hora pasa a programa."""
    cambios: list[tuple[int, str]] = []
    ahora = time.time()
    for e in c.execute("SELECT id, confirmado, fuente FROM catalog WHERE radio=? AND estado='activo' "
                       "AND n_matches >= ?", (radio, CONDUCTA_MIN_DET)).fetchall():
        tiempos = [r[0] for r in c.execute("SELECT t_start FROM detections WHERE radio=? AND catalog_id=?",
                                           (radio, e["id"]))]
        if len(tiempos) < CONDUCTA_MIN_DET:
            continue
        rl = reglas.reloj(tiempos)
        if rl is not None and rl >= RELOJ_FIJO:
            c.execute("UPDATE catalog SET estado='lista_negra', confirmado=1, fuente='regla', t_veredicto=? WHERE id=?",
                      (ahora, e["id"]))
            cambios.append((e["id"], "regla"))
            continue
        if not e["confirmado"] or e["fuente"] == "usuario":
            continue
        ultimas = c.execute("SELECT t_start, t_end FROM detections WHERE radio=? AND catalog_id=? "
                            "ORDER BY t_start DESC LIMIT ?", (radio, e["id"], CONDUCTA_ULTIMAS)).fetchall()
        aisladas = 0
        for a, b in ultimas:
            v = CONDUCTA_VECINO_S
            vecinos = c.execute(
                "SELECT d.t_start, d.t_end FROM detections d JOIN catalog k ON k.id=d.catalog_id "
                "WHERE d.radio=? AND d.catalog_id<>? AND k.estado='activo' AND d.t_start <= ? AND d.t_end >= ?",
                (radio, e["id"], b + v, a - v)).fetchall()
            hay = False
            for x, y in vecinos:
                if min(b, y) - max(a, x) > 0.5 * min(b - a, y - x):
                    continue        # copia del mismo audio
                if (y >= a - v and x <= a + v) or (y >= b - v and x <= b + v):
                    hay = True
                    break
            aisladas += not hay
        if aisladas >= CONDUCTA_AISLADA * len(ultimas):
            c.execute("UPDATE catalog SET confirmado=0, fuente='conducta', t_veredicto=? WHERE id=?", (ahora, e["id"]))
            cambios.append((e["id"], "conducta"))
    c.commit()
    return cambios


# ------------------------------------------------------------------ caducidad

def caducar(c, radio: str, dias: float) -> int:
    """Caduca lo que lleva `dias` sin sonar en esa emisora, sea anuncio, programa o pendiente. La referencia
    es lo ultimo que sono en esa emisora (no la hora actual), asi vale tambien sobre audio antiguo."""
    ensure(c)
    ref = c.execute("SELECT MAX(COALESCE(last_seen, t_origen)) FROM catalog WHERE radio=?", (radio,)).fetchone()[0]
    if ref is None:
        print(f"[{radio}] catalogo vacio: no se caduca nada")
        return 0
    cur = c.execute("UPDATE catalog SET estado='caducado' WHERE radio=? AND estado IN ('activo','lista_negra') "
                    "AND COALESCE(last_seen, t_origen) < ?", (radio, ref - dias * 86400))
    c.commit()
    print(f"[{radio}] {cur.rowcount} entradas caducadas (sin sonar en {dias:g} dias)")
    return cur.rowcount


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "scan", "afinar", "caducar"])
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--desde", type=float, default=None, help="epoch desde el que escanear")
    ap.add_argument("--dias", type=float, default=30)
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
    else:
        caducar(c, a.radio, a.dias)


if __name__ == "__main__":
    main()
