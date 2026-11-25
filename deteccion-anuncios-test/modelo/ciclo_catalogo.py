"""Ciclo continuo del catalogo (pensado para dejarlo corriendo en un servidor).

Trabaja sobre su propia base (data/catalogo.db) para no competir por el
bloqueo de SQLite con capture.py, que escribe en data/modelo.db. En cada
vuelta: copia los clips nuevos, busca repeticiones, actualiza el catalogo,
escanea el audio nuevo, afina, da audio a las entradas, hereda veredictos, transcribe,
llama al juez, aplica sus veredictos y poda lo antiguo. Cada fase deja en el log lo que tarda.

  python ciclo_catalogo.py --radios cadena_ser [otra ...] --cada 120 --caducar-dias 30 [--retener-dias 0]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import datetime as dt
import os
import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
os.environ.setdefault("RB_DB", str(BASE / "data" / "catalogo.db"))

import audio  # noqa: E402
import catalogo  # noqa: E402
import segments_repeat  # noqa: E402
import store  # noqa: E402


def sincronizar_clips(c) -> int:
    origen = BASE / "data" / "modelo.db"
    if not origen.exists():
        return 0
    c.execute("ATTACH DATABASE ? AS cap", (str(origen),))
    try:
        cur = c.execute("INSERT OR IGNORE INTO clips(radio, timestamp, path) "
                        "SELECT radio, timestamp, path FROM cap.clips ORDER BY id")
        c.commit()
        return cur.rowcount
    finally:
        c.execute("DETACH DATABASE cap")


def aplicar_respuestas(c) -> int:
    """Aplica al catalogo los veredictos de respuestas.db (juez, reglas). El ciclo es el unico que escribe en catalogo.db."""
    ruta = BASE / "data" / "respuestas.db"
    if not ruta.exists():
        return 0
    r = sqlite3.connect(str(ruta), timeout=30)
    cols = {x[1] for x in r.execute("PRAGMA table_info(respuestas)")}
    filas = r.execute("SELECT id, catalog_id, resp, %s FROM respuestas WHERE aplicada=0"
                      % ("fuente" if "fuente" in cols else "'usuario'")).fetchall()
    for rid, eid, resp, fuente in filas:
        # el juez (ia, regla) contesta cada entrada por separado: no se extiende al grupo (hay grupos que
        # mezclan audios distintos, p. ej. una sintonia con anuncios). Al mismo audio (propagar_negra) si.
        por_grupo = fuente == "usuario"
        if eid < 0:   # tramo entre anuncios: la etiqueta la recoge construir() (Si = entrada confirmada, No = lista negra)
            c.execute("UPDATE segments SET label=?, labeled_at=datetime('now') WHERE id=?", (1 if resp == "si" else 0, -eid))
            r.execute("UPDATE respuestas SET aplicada=1 WHERE id=?", (rid,))
            continue
        if resp == "si":
            c.execute("UPDATE catalog SET confirmado=1, fuente=?, t_veredicto=? WHERE id=?", (fuente, time.time(), eid))
            n = catalogo.propagar_grupo(c, eid, negra=False) if por_grupo else 0
            if n:
                print(f"  'Si' a la entrada {eid}: {n} copias mas confirmadas", flush=True)
        elif resp == "no":
            c.execute("UPDATE catalog SET estado='lista_negra', confirmado=1, fuente=?, t_veredicto=? WHERE id=?",
                      (fuente, time.time(), eid))
            n = catalogo.propagar_negra(c, eid) + (catalogo.propagar_grupo(c, eid, negra=True) if por_grupo else 0)
            if n:
                print(f"  'No' a la entrada {eid}: {n} copias mas a lista negra", flush=True)
        r.execute("UPDATE respuestas SET aplicada=1 WHERE id=?", (rid,))
    c.commit()
    r.commit()
    r.close()
    if filas:
        print(f"respuestas aplicadas: {len(filas)}", flush=True)
    completar_fuente(c)
    return len(filas)


def completar_fuente(c) -> int:
    """Toda entrada con veredicto firme y sin `fuente` la recibe: la de su ultima respuesta o, si no
    tiene ninguna, 'herencia' (las copias que marca propagar_negra y lo que confirma heredar)."""
    sin = [r[0] for r in c.execute("SELECT id FROM catalog WHERE confirmado=1 AND fuente IS NULL")]
    if not sin:
        return 0
    ultima: dict[int, str] = {}
    ruta = BASE / "data" / "respuestas.db"
    if ruta.exists():
        r = sqlite3.connect(str(ruta), timeout=30)
        con_fuente = "fuente" in {x[1] for x in r.execute("PRAGMA table_info(respuestas)")}
        for cid, f in r.execute("SELECT catalog_id, %s FROM respuestas WHERE resp IN ('si','no') ORDER BY id"
                                % ("fuente" if con_fuente else "'usuario'")):
            ultima[cid] = f
        r.close()
    ahora = time.time()
    c.executemany("UPDATE catalog SET fuente=?, t_veredicto=COALESCE(t_veredicto, ?) WHERE id=?",
                  [(ultima.get(i, "herencia"), ahora, i) for i in sin])
    c.commit()
    return len(sin)


def limpiar_clips(c, dias: float) -> int:
    """Borra los clips (ficheros y filas, en las dos bases) con mas de `dias` dias. Con dias <= 0 no borra nada
    (imprescindible al probar con audio antiguo: la edad se cuenta desde hoy)."""
    if dias <= 0:
        return 0
    corte = (dt.datetime.now() - dt.timedelta(days=dias)).isoformat(timespec="seconds")
    viejos = c.execute("SELECT id, path FROM clips WHERE timestamp < ?", (corte,)).fetchall()
    if not viejos:
        return 0
    c.execute("DELETE FROM clips WHERE timestamp < ?", (corte,))
    c.commit()
    origen = BASE / "data" / "modelo.db"
    if origen.exists():
        o = sqlite3.connect(str(origen), timeout=60)
        o.execute("DELETE FROM clips WHERE timestamp < ?", (corte,))
        o.commit()
        o.close()
    borrados = 0
    for f in viejos:
        try:
            (store.CLIPS_DIR / f["path"]).unlink()
            borrados += 1
        except FileNotFoundError:
            pass
    print(f"retencion: {borrados} clips de mas de {dias:g} dias borrados", flush=True)
    return borrados


def anotar_dudosas(ids: list[int], fuente: str) -> None:
    """Deja en respuestas.db una 'dudosa' ya aplicada por cada entrada que la autocorreccion devuelve a
    pendiente: asi el juez no la vuelve a juzgar hasta que cambie su transcripcion."""
    if not ids:
        return
    r = sqlite3.connect(str(BASE / "data" / "respuestas.db"), timeout=30)
    r.execute("CREATE TABLE IF NOT EXISTS respuestas (id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id INTEGER NOT NULL, "
              "resp TEXT NOT NULL, ts TEXT DEFAULT CURRENT_TIMESTAMP, aplicada INTEGER NOT NULL DEFAULT 0, "
              "fuente TEXT NOT NULL DEFAULT 'usuario')")
    r.executemany("INSERT INTO respuestas(catalog_id, resp, fuente, aplicada) VALUES (?, 'dudosa', ?, 1)",
                  [(i, fuente) for i in ids])
    r.commit()
    r.close()


def salud(c, radio: str, ahora: float | None = None) -> list[int]:
    """Si las ultimas 24 h han salido mudas mas del doble de la mediana diaria de los 7 dias anteriores (y mas
    de 30 min), los anuncios que el juez (fuente 'ia') decidio en esas 24 h vuelven a pendiente. Necesita al
    menos 3 dias completos en data/silencios_<radio>.csv. Devuelve los ids degradados."""
    ruta = BASE / "data" / f"silencios_{radio}.csv"
    if not ruta.exists():
        return []
    ahora = time.time() if ahora is None else ahora
    tramos = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        p = linea.split(",")
        try:
            tramos.append((float(p[0]), float(p[1])))
        except (ValueError, IndexError):
            continue
    if not tramos:
        return []
    primero = min(a for a, _b in tramos)
    dia = 86400.0

    def mudo(x: float, y: float) -> float:
        return sum(max(0.0, min(b, y) - max(a, x)) for a, b in tramos) / 60

    previos = [mudo(ahora - (k + 1) * dia, ahora - k * dia) for k in range(1, 8) if ahora - (k + 1) * dia >= primero]
    if len(previos) < 3:
        return []
    ultimo = mudo(ahora - dia, ahora)
    mediana = sorted(previos)[len(previos) // 2] if len(previos) % 2 else \
        sum(sorted(previos)[len(previos) // 2 - 1:len(previos) // 2 + 1]) / 2
    if ultimo <= 2 * mediana or ultimo <= 30:
        return []
    ids = [i for (i,) in c.execute("SELECT id FROM catalog WHERE radio=? AND estado='activo' AND confirmado=1 "
                                   "AND fuente='ia' AND t_veredicto >= ?", (radio, ahora - dia))]
    c.executemany("UPDATE catalog SET confirmado=0, fuente='salud', t_veredicto=? WHERE id=?", [(ahora, i) for i in ids])
    c.commit()
    print(f"SALUD: [{radio}] {ultimo:.0f} min mudos en 24 h frente a una mediana de {mediana:.0f} min/dia; "
          f"{len(ids)} anuncios decididos por el juez en ese tiempo vuelven a pendiente", flush=True)
    return ids


class _Crono:
    """Imprime cuanto ha tardado cada fase de la vuelta."""

    def __init__(self) -> None:
        self.t = time.perf_counter()

    def fase(self, nombre: str) -> None:
        ahora = time.perf_counter()
        print(f"fase {nombre}: {ahora - self.t:.1f} s", flush=True)
        self.t = ahora


def vuelta(radios: list[str], caducar_dias: float, horas: float, retener_dias: float) -> None:
    cr = _Crono()
    c = store.conn2()
    catalogo.ensure(c)
    n = sincronizar_clips(c)
    cr.fase("sincronizar")
    aplicar_respuestas(c)
    cr.fase("respuestas")
    limpiar_clips(c, retener_dias)
    cr.fase("retencion")
    con_audio = []
    for radio in radios:
        span = audio.radio_span(c, radio)
        if not span:
            print(f"[{radio}] sin audio todavia", flush=True)
            continue
        con_audio.append(radio)
        print(f"[{radio}] clips nuevos (todas las emisoras)={n}  audio={(span[1]-span[0])/60:.0f} min", flush=True)
        segments_repeat.find_segments(c, radio, horas=horas)
        cr.fase("repeticion")
        catalogo.construir(c, radio)
        cr.fase("construir")
        catalogo.caducar(c, radio, caducar_dias)
        indice = catalogo.Indice.desde_bd(c, radio)
        # con entradas nuevas conviene reescanear: se hace sobre todo el audio (barato frente a la repeticion)
        dets = catalogo.escanear(c, radio, max(span[0], span[1] - horas * 3600), span[1], indice)
        print(f"detecciones={len(dets)} nuevas={catalogo.guardar_detecciones(c, radio, dets)}", flush=True)
        cr.fase("escaneo")
        catalogo.afinar_pendientes(c, radio)
        cr.fase("afinado")
        print(f"audios de entradas creados: {catalogo.completar_wavs(c, radio)}", flush=True)
        cr.fase("audios")
        si, no = catalogo.heredar(c, radio)
        completar_fuente(c)
        print(f"heredadas de respuestas anteriores: {si} Si, {no} No", flush=True)
        cr.fase("herencia")
    if con_audio:
        try:   # transcribe lo nuevo de todas las emisoras, una sola vez por vuelta
            subprocess.run([sys.executable, str(BASE / "ia_transcribir.py")], cwd=str(BASE), timeout=1500)
        except Exception as e:
            print(f"transcripcion omitida: {e}", flush=True)
        cr.fase("transcripcion")
        for radio in con_audio:   # el juez decide las pendientes ya transcritas; se aplican justo despues
            try:
                subprocess.run([sys.executable, str(BASE / "juez.py"), "--radio", radio], cwd=str(BASE), timeout=900)
            except Exception as e:
                print(f"[{radio}] juez omitido: {e}", flush=True)
        aplicar_respuestas(c)
        cr.fase("juez")
        for radio in con_audio:
            print(f"[{radio}] poda: %d tramos y %d detecciones antiguos borrados" % catalogo.podar(c, radio, horas),
                  flush=True)
        cr.fase("poda")
    print(f"vuelta terminada {time.strftime('%H:%M:%S')}", flush=True)
    c.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radios", nargs="+", default=["cadena_ser"], help="emisoras, una tras otra en cada vuelta")
    ap.add_argument("--cada", type=float, default=30, help="minutos entre vueltas")
    ap.add_argument("--caducar-dias", type=float, default=30)
    ap.add_argument("--horas", type=float, default=36, help="ventana de analisis (horas hacia atras)")
    ap.add_argument("--retener-dias", type=float, default=14, help="dias de audio que se conservan (0 = no borrar)")
    ap.add_argument("--una-vez", action="store_true")
    a = ap.parse_args()
    while True:
        print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)
        try:
            vuelta(a.radios, a.caducar_dias, a.horas, a.retener_dias)
        except Exception as e:
            print(f"error en la vuelta: {e!r}", flush=True)
        if a.una_vez:
            return
        time.sleep(a.cada * 60)


if __name__ == "__main__":
    main()
