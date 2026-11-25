"""Pruebas del Motor del servicio (docs/GUIA_IA.md, T6). Solo lectura.

  python pruebas/prueba_motor.py [--bd data/catalogo.db]
      Sinteticas, con audio real: (1) radio, anuncio, 8 s de radio, anuncio, radio, una promo (P), radio;
      (2) una entrada de mas de 50 s (cuatro anuncios seguidos).
  python pruebas/prueba_motor.py --comparar <silencios.csv> --desde 20261106_080000 --hasta 20261106_110000
      Compara lo que silencio servicio.py --fuente con lo que dicen las reglas sobre las detecciones guardadas.
"""
import argparse
import bisect
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import catalogo  # noqa: E402
import fingerprint as fp  # noqa: E402
import reglas  # noqa: E402
import servicio  # noqa: E402
import store  # noqa: E402

SR = store.SAMPLE_RATE
DATA = store.DATA_DIR
RADIO = "cadena_ser"


def ro(ruta) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(ruta).as_posix()}?mode=ro", uri=True, timeout=30)


def leer(f) -> np.ndarray:
    x, sr = sf.read(str(f), dtype="int16")
    assert sr == SR, f"{f}: {sr} Hz"
    return x if x.ndim == 1 else x[:, 0]


def epoch(nombre: str) -> float:
    return dt.datetime.strptime(nombre[:15], "%Y%m%d_%H%M%S").timestamp()


def total(iv) -> float:
    return sum(b - a for a, b in reglas.unir(iv))


# ------------------------------------------------------------------ sinteticas

def radio_limpia(c, segundos: float) -> np.ndarray:
    """Audio de clips seguidos en los que no suena nada del catalogo (ni anuncio, ni programa, ni pendiente)."""
    clips = sorted((DATA / "clips" / RADIO).glob("*.wav"))
    dets = sorted(c.execute("SELECT t_start, t_end FROM detections WHERE radio=?", (RADIO,)).fetchall())
    inicios = [d[0] for d in dets]
    n = int(np.ceil(segundos / store.CLIP_SECONDS))
    for i in range(len(clips) // 3, len(clips) - n, n):
        grupo = clips[i:i + n]
        e0, e1 = epoch(grupo[0].stem), epoch(grupo[-1].stem) + store.CLIP_SECONDS
        if e1 - e0 > n * store.CLIP_SECONDS + 3:
            continue                    # falta algun clip
        k = bisect.bisect_left(inicios, e0 - 300)
        libre = True
        while k < len(dets) and dets[k][0] < e1 + 30:
            if dets[k][1] > e0 - 30:
                libre = False
                break
            k += 1
        if libre:
            print(f"  radio sin nada del catalogo: {grupo[0].stem} ({n} clips)")
            return np.concatenate([leer(f) for f in grupo])[:int(segundos * SR)]
    raise SystemExit("no hay un tramo de radio sin detecciones")


def entradas(c, estado: str, confirmado: int, cuantas: int, evitar=()) -> list[tuple[int, np.ndarray]]:
    """Audio de entradas del catalogo (data/entradas/<id>.wav) de 10-25 s que no son el mismo audio que `evitar`."""
    out = []
    huellas = [fp.fingerprint(x.astype(np.float32) / 32768, SR) for _i, x in evitar]
    for eid, _dur in c.execute("SELECT id, dur FROM catalog WHERE radio=? AND estado=? AND confirmado=? "
                               "AND dur BETWEEN 10 AND 25 ORDER BY n_matches DESC", (RADIO, estado, confirmado)):
        f = DATA / "entradas" / f"{eid}.wav"
        if not f.exists():
            continue
        x = leer(f)
        h = fp.fingerprint(x.astype(np.float32) / 32768, SR)
        if any((r := fp.align(h, o)) and r[0] >= catalogo.MIN_INLIERS_ABS for o in huellas):
            continue                    # copia de otra ya elegida
        out.append((eid, x))
        huellas.append(h)
        if len(out) == cuantas:
            return out
    raise SystemExit(f"faltan entradas {estado}/{confirmado} con audio")


def pasar(indice, pcm: np.ndarray) -> np.ndarray:
    m = servicio.Motor(indice)
    trozos = [m.procesar(pcm[i:i + m.bloque].tobytes()) for i in range(0, len(pcm), m.bloque)]
    trozos.append(m.cerrar())
    return np.frombuffer(b"".join(trozos), dtype=np.int16)


def mudo(entra: np.ndarray, sale: np.ndarray, a: float, b: float) -> float:
    """Fraccion del tramo [a, b] (s) que sale muda (solo cuenta donde la entrada no es silencio)."""
    x, y = entra[int(a * SR):int(b * SR)], sale[int(a * SR):int(b * SR)]
    vivo = x != 0
    return float(np.mean(y[vivo] == 0)) if vivo.any() else 1.0


def huella_de(x: np.ndarray) -> dict:
    return fp.fingerprint(x.astype(np.float32) / 32768, SR)


def sinteticas(bd) -> bool:
    c = ro(bd)
    ok = True

    def mira(cond: bool, texto: str) -> None:
        nonlocal ok
        ok &= cond
        print(f"  [{'ok' if cond else 'FALLA'}] {texto}")

    print("1) radio, A1, 8 s de radio, A2, radio, promo (P), radio")
    radio = radio_limpia(c, 100)
    (i1, a1), (i2, a2) = entradas(c, "activo", 1, 2)
    (ip, p), = entradas(c, "lista_negra", 1, 1, evitar=[(i1, a1), (i2, a2)])
    trozos, marcas, t = [], {}, 0.0
    for nombre, x in [("radio1", radio[:30 * SR]), ("A1", a1), ("medio", radio[30 * SR:38 * SR]), ("A2", a2),
                      ("radio2", radio[38 * SR:68 * SR]), ("P", p), ("radio3", radio[68 * SR:98 * SR])]:
        marcas[nombre] = (t, t + len(x) / SR)
        trozos.append(x)
        t += len(x) / SR
    entra = np.concatenate(trozos)
    indice = catalogo.Indice({i1: (huella_de(a1), len(a1) / SR, "A"), i2: (huella_de(a2), len(a2) / SR, "A"),
                              ip: (huella_de(p), len(p) / SR, "P")})
    sale = pasar(indice, entra)
    mira(len(sale) == len(entra), f"misma longitud ({len(sale)} y {len(entra)} muestras)")
    for k in ("A1", "A2"):
        f = mudo(entra, sale, *marcas[k])
        mira(f >= 0.95, f"anuncio {k} ({marcas[k][1] - marcas[k][0]:.1f} s) mudo {f:.1%}")
    f = mudo(entra, sale, *marcas["medio"])
    mira(f >= 0.95, f"los 8 s entre los dos anuncios mudos {f:.1%}")
    f = mudo(entra, sale, *marcas["P"])
    mira(f <= 0.05, f"la promo (P), lejos de los anuncios, suena (muda {f:.1%})")
    mira(np.array_equal(sale[:15 * SR], entra[:15 * SR]), "los primeros 15 s salen identicos")
    mira(np.array_equal(sale[-15 * SR:], entra[-15 * SR:]), "los ultimos 15 s salen identicos")

    print("2) entrada larga (D9): cuatro anuncios seguidos como una sola entrada")
    cuatro = entradas(c, "activo", 1, 4, evitar=[(i1, a1), (i2, a2)])
    larga = np.concatenate([x for _i, x in cuatro])
    if len(larga) / SR <= 50:
        cuatro += entradas(c, "activo", 1, 1, evitar=cuatro + [(i1, a1), (i2, a2)])
        larga = np.concatenate([x for _i, x in cuatro])
    entra = np.concatenate([radio[:30 * SR], larga, radio[30 * SR:60 * SR]])
    ini, fin = 30.0, 30.0 + len(larga) / SR
    for nombre, umbral in (("umbral_vivo", None), ("catalogo.umbral (sin el arreglo, solo informa)", catalogo.umbral)):
        indice = catalogo.Indice({1: (huella_de(larga), len(larga) / SR, "A")})
        viejo = servicio.umbral_vivo
        if umbral:
            servicio.umbral_vivo = umbral
        try:
            sale = pasar(indice, entra)
        finally:
            servicio.umbral_vivo = viejo
        f = mudo(entra, sale, ini, fin)
        if umbral:
            print(f"  [info] con {nombre}: muda {f:.1%}")
        else:
            mira(f >= 0.90, f"entrada de {fin - ini:.0f} s muda {f:.1%}")
            mira(np.array_equal(sale[:15 * SR], entra[:15 * SR]) and np.array_equal(sale[-15 * SR:], entra[-15 * SR:]),
                 "los 15 s del principio y del final salen identicos")
    return ok


# ------------------------------------------------------------------ comparacion con 3 h reales

def comparar(bd, csv, desde: str, hasta: str) -> bool:
    e0, e1 = epoch(desde), epoch(hasta) + store.CLIP_SECONDS
    servicio_mudo = []
    for linea in Path(csv).read_text(encoding="utf-8").splitlines():
        a, b = (float(x) for x in linea.split(",")[:2])
        if b > e0 and a < e1:
            servicio_mudo.append((max(a, e0), min(b, e1)))
    c = ro(bd)
    filas = []
    # misma poblacion que carga el indice del servicio: sin caducadas y pendientes solo de >= 6 s
    for a, b, estado, conf, dur in c.execute(
            "SELECT d.t_start, d.t_end, k.estado, k.confirmado, k.dur FROM detections d JOIN catalog k ON k.id=d.catalog_id "
            "WHERE d.radio=? AND d.t_end > ? AND d.t_start < ? ORDER BY d.t_start", (RADIO, e0 - 600, e1 + 600)):
        cl = reglas.clase(estado, conf)
        if cl and not (cl == "N" and dur < catalogo.MIN_DUR_ENTRADA_S):
            filas.append((a, b, cl))
    plan = [(max(a, e0), min(b, e1)) for a, b in reglas.limitar(reglas.planificar(filas)) if b > e0 and a < e1]
    tp, ts = total(plan), total(servicio_mudo)
    comun = tp - total(reglas.restar(plan, servicio_mudo))
    fuera = total(reglas.restar(servicio_mudo, plan))
    print(f"{(e1 - e0) / 3600:.1f} h: plan de las reglas {tp / 60:.1f} min mudos; servicio {ts / 60:.1f} min mudos")
    cubre, sobra = comun / max(tp, 1e-9), fuera / max(ts, 1e-9)
    print(f"  [{'ok' if cubre >= 0.90 else 'FALLA'}] el servicio deja mudo el {cubre:.1%} del plan (>= 90 %)")
    print(f"  [{'ok' if sobra <= 0.15 else 'FALLA'}] mudo del servicio fuera del plan: {sobra:.1%} de su total (<= 15 %)")
    return cubre >= 0.90 and sobra <= 0.15


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bd", default=str(DATA / "catalogo.db"))
    ap.add_argument("--comparar", default=None, help="csv de silencios de servicio.py --fuente")
    ap.add_argument("--desde", default="20261106_080000")
    ap.add_argument("--hasta", default="20261106_110000")
    a = ap.parse_args()
    ok = comparar(a.bd, a.comparar, a.desde, a.hasta) if a.comparar else sinteticas(a.bd)
    print("prueba_motor: " + ("correcta" if ok else "FALLA"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
