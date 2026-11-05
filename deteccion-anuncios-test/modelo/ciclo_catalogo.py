"""Ciclo continuo del catalogo (pensado para dejarlo corriendo en un servidor).

Trabaja sobre su propia base (data/catalogo.db) para no competir por el
bloqueo de SQLite con capture.py, que escribe en data/modelo.db. En cada
vuelta: copia los clips nuevos, busca repeticiones, actualiza el catalogo,
escanea el audio nuevo, propone huecos y deja las cifras en el log.

  python ciclo_catalogo.py --radio cadena_ser --cada 30 --caducar-dias 14
"""

from __future__ import annotations

import argparse
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
    """Aplica las respuestas Si/No de revisar.py. El ciclo es el unico que escribe en catalogo.db."""
    ruta = BASE / "data" / "respuestas.db"
    if not ruta.exists():
        return 0
    r = sqlite3.connect(str(ruta), timeout=30)
    filas = r.execute("SELECT id, catalog_id, resp FROM respuestas WHERE aplicada=0").fetchall()
    for rid, eid, resp in filas:
        if eid < 0:   # tramo entre anuncios: la etiqueta la recoge construir() (Si = entrada confirmada, No = lista negra)
            c.execute("UPDATE segments SET label=?, labeled_at=datetime('now') WHERE id=?", (1 if resp == "si" else 0, -eid))
            r.execute("UPDATE respuestas SET aplicada=1 WHERE id=?", (rid,))
            continue
        if resp == "si":
            c.execute("UPDATE catalog SET confirmado=1 WHERE id=?", (eid,))
            n = catalogo.propagar_grupo(c, eid, negra=False)
            if n:
                print(f"  'Si' a la entrada {eid}: {n} copias mas confirmadas", flush=True)
        elif resp == "no":
            c.execute("UPDATE catalog SET estado='lista_negra', confirmado=1 WHERE id=?", (eid,))
            n = catalogo.propagar_negra(c, eid) + catalogo.propagar_grupo(c, eid, negra=True)
            if n:
                print(f"  'No' a la entrada {eid}: {n} copias mas a lista negra", flush=True)
        r.execute("UPDATE respuestas SET aplicada=1 WHERE id=?", (rid,))
    c.commit()
    r.commit()
    r.close()
    if filas:
        print(f"respuestas aplicadas: {len(filas)}", flush=True)
    return len(filas)


def limpiar_clips(c, dias: float) -> int:
    """Borra los clips (ficheros y filas, en las dos bases) con mas de `dias` dias."""
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


def vuelta(radio: str, caducar_dias: float, horas: float, retener_dias: float) -> None:
    c = store.conn2()
    catalogo.ensure(c)
    n = sincronizar_clips(c)
    aplicar_respuestas(c)
    limpiar_clips(c, retener_dias)
    span = audio.radio_span(c, radio)
    if not span:
        print("sin audio todavia", flush=True)
        return
    print(f"clips nuevos={n}  audio={(span[1]-span[0])/60:.0f} min", flush=True)
    segments_repeat.find_segments(c, radio, horas=horas)
    catalogo.construir(c, radio)
    catalogo.caducar(c, caducar_dias)
    indice = catalogo.Indice.desde_bd(c, radio)
    desde = c.execute("SELECT MAX(t_end) FROM detections WHERE radio=?", (radio,)).fetchone()[0]
    # con entradas nuevas conviene reescanear: se hace sobre todo el audio (barato frente a la repeticion)
    dets = catalogo.escanear(c, radio, max(span[0], span[1] - horas * 3600), span[1], indice)
    print(f"detecciones={len(dets)} nuevas={catalogo.guardar_detecciones(c, radio, dets)}", flush=True)
    catalogo.afinar_pendientes(c, radio)
    si, no = catalogo.heredar(c, radio)
    print(f"heredadas de respuestas anteriores: {si} Si, {no} No", flush=True)
    try:   # transcribe lo nuevo para poder leerlo (y para que la IA lo clasifique desde el PC)
        import subprocess, sys
        subprocess.run([sys.executable, str(BASE / "ia_transcribir.py")], cwd=str(BASE), timeout=1500)
    except Exception as e:
        print(f"transcripcion omitida: {e}", flush=True)
    print(f"grupos por revisar: {catalogo.agrupar(c, radio)}", flush=True)
    catalogo.proponer_huecos(c, radio)
    catalogo.exportar_huecos(c, radio)
    try:
        catalogo.evaluar(c, radio)
    except Exception as e:  # la evaluacion es informativa
        print(f"eval omitida: {e}", flush=True)
    c.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--cada", type=float, default=30, help="minutos entre vueltas")
    ap.add_argument("--caducar-dias", type=float, default=14)
    ap.add_argument("--horas", type=float, default=36, help="ventana de analisis (horas hacia atras)")
    ap.add_argument("--retener-dias", type=float, default=14, help="dias de audio que se conservan")
    ap.add_argument("--una-vez", action="store_true")
    a = ap.parse_args()
    while True:
        print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)
        try:
            vuelta(a.radio, a.caducar_dias, a.horas, a.retener_dias)
        except Exception as e:
            print(f"error en la vuelta: {e!r}", flush=True)
        if a.una_vez:
            return
        time.sleep(a.cada * 60)


if __name__ == "__main__":
    main()
