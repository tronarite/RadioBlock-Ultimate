"""Panel web de solo lectura para ver el progreso de la captura y del catalogo.

  python monitor.py [--port 8060] [--host 127.0.0.1]

Solo usa la libreria estandar. Lee las bases en modo solo lectura y los logs;
no escribe nada ni toca los procesos. Por defecto escucha solo en local: para
verlo desde otro equipo, tunel SSH (ssh -L 8060:127.0.0.1:8060 asuka).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import shutil
import sqlite3
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
RUN = BASE.parent.parent.parent / "radioblock-run"   # ...\Proyectos\radioblock-run
CLIP_S = 10

_cache: dict[str, tuple[float, object]] = {}


def cached(clave: str, ttl: float, fn):
    t, v = _cache.get(clave, (0.0, None))
    if time.time() - t > ttl:
        try:
            v = fn()
        except Exception as e:  # el panel nunca debe caerse por un dato que falte
            v = f"error: {e}"
        _cache[clave] = (time.time(), v)
    return v


def abrir(ruta: Path):
    if not ruta.exists():
        return None
    c = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def procesos() -> dict[str, int]:
    salida = subprocess.run(["wmic", "process", "where", "name='python.exe' or name='ffmpeg.exe'",
                             "get", "CommandLine"], capture_output=True, text=True, timeout=20).stdout
    claves = {"capture.py": "grabacion (capture.py)", "ciclo_catalogo.py": "ciclo del catalogo",
              "trans_asuka.py": "transcripcion Whisper", "prueba_fuera.py": "prueba fuera de muestra",
              "ffmpeg": "ffmpeg (stream)"}
    cuenta = {v: 0 for v in claves.values()}
    for linea in salida.splitlines():
        for k, v in claves.items():
            if k in linea:
                cuenta[v] += 1
    return cuenta


def resumen_captura() -> dict:
    c = abrir(DATA / "modelo.db")
    if c is None:
        return {}
    n, a, b = c.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM clips WHERE radio='cadena_ser'").fetchone()
    por_hora = c.execute("SELECT substr(timestamp,1,13) h, COUNT(*) n FROM clips WHERE radio='cadena_ser' "
                         "GROUP BY h ORDER BY h DESC LIMIT 12").fetchall()
    c.close()
    return {"clips": n, "min": (n or 0) * CLIP_S / 60, "desde": a, "ultimo": b,
            "por_hora": [(r["h"], r["n"] * CLIP_S / 60) for r in por_hora]}


def resumen_catalogo() -> dict:
    c = abrir(DATA / "catalogo.db")
    if c is None:
        return {}
    out: dict = {}
    try:
        out["entradas"] = [dict(r) for r in c.execute(
            "SELECT id, estado, confirmado, ROUND(dur,1) dur, n_hashes, n_matches FROM catalog ORDER BY id DESC LIMIT 25")]
        out["totales"] = dict(c.execute("SELECT COUNT(*) total, SUM(estado='activo') activas, "
                                        "SUM(estado='lista_negra') negras, SUM(estado='caducado') caducadas FROM catalog").fetchone())
        out["detecciones"] = c.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    out["segmentos"] = {r["source"]: r["n"] for r in c.execute("SELECT source, COUNT(*) n FROM segments GROUP BY source")}
    out["grupos"] = c.execute("SELECT COUNT(DISTINCT group_key) FROM segments WHERE source='repeat'").fetchone()[0]
    c.close()
    return out


def cola(ruta: Path, n: int = 14) -> str:
    if not ruta.exists():
        return "(sin log)"
    return "\n".join(ruta.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])


def fila(*celdas) -> str:
    return "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in celdas) + "</tr>"


def pagina() -> str:
    cap = cached("cap", 10, resumen_captura)
    cat = cached("cat", 10, resumen_catalogo)
    proc = cached("proc", 15, procesos)
    libre = shutil.disk_usage(DATA).free / 1e9
    ahora = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def seccion(titulo, cuerpo):
        return f"<section><h2>{titulo}</h2>{cuerpo}</section>"

    if isinstance(proc, dict):
        p = "<table>" + "".join(fila(k, "en marcha" if v else "parado") for k, v in proc.items()) + "</table>"
    else:
        p = f"<p>{html.escape(str(proc))}</p>"

    if isinstance(cap, dict) and cap:
        edad = ""
        try:
            edad = f"hace {int((dt.datetime.now() - dt.datetime.fromisoformat(cap['ultimo'])).total_seconds())} s"
        except Exception:
            pass
        c = (f"<p><b>{cap['min']:.0f} min</b> de audio ({cap['clips']} clips) · desde {html.escape(str(cap['desde']))} · "
             f"ultimo clip {html.escape(str(cap['ultimo']))} <span class='dim'>{edad}</span></p>"
             "<table><tr><th>hora</th><th>minutos grabados</th></tr>"
             + "".join(fila(h, f"{m:.1f}") for h, m in cap["por_hora"]) + "</table>")
    else:
        c = f"<p>{html.escape(str(cap))}</p>" if cap else "<p>sin datos</p>"

    if isinstance(cat, dict) and cat:
        t = cat.get("totales", {})
        k = (f"<p>Entradas: <b>{t.get('total', 0)}</b> (activas {t.get('activas') or 0}, lista negra {t.get('negras') or 0}, "
             f"caducadas {t.get('caducadas') or 0}) · detecciones <b>{cat.get('detecciones', 0)}</b> · "
             f"grupos repetidos <b>{cat.get('grupos', 0)}</b> · tramos por origen: {html.escape(str(cat.get('segmentos', {})))}</p>")
        if cat.get("entradas"):
            k += ("<table><tr><th>id</th><th>estado</th><th>confirmada</th><th>dur (s)</th><th>hashes</th><th>coincidencias</th></tr>"
                  + "".join(fila(e["id"], e["estado"], "si" if e["confirmado"] else "no", e["dur"], e["n_hashes"], e["n_matches"])
                            for e in cat["entradas"]) + "</table>")
        else:
            k += "<p class='dim'>El catalogo aun esta vacio: hace falta algun grupo con 3 o mas apariciones.</p>"
    else:
        k = "<p>sin datos</p>"

    logs = "".join(seccion(t, f"<pre>{html.escape(cola(r))}</pre>") for t, r in [
        ("Ciclo del catalogo (log)", BASE / "ciclo.log"),
        ("Prueba fuera de muestra", RUN / "prueba.log"),
        ("Transcripcion Whisper", RUN / "trans.log")])

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="20"><title>Progreso en asuka</title><style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1c2230;--dim:#6b7385;--line:#dfe3ea;--ok:#1a8f55}}
@media(prefers-color-scheme:dark){{:root{{--bg:#12151c;--card:#1b2029;--ink:#e6e9f0;--dim:#8e97ab;--line:#2c3340;--ok:#4cc38a}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:900px;margin:0 auto;padding:16px}}h1{{font-size:1.3rem;margin:.2rem 0}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:12px 0;overflow-x:auto}}
h2{{font-size:1rem;margin:.2rem 0 .5rem}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:4px 10px 4px 0;border-bottom:1px solid var(--line);font-size:.9rem}}
pre{{font-size:.78rem;white-space:pre-wrap;margin:0}}.dim{{color:var(--dim)}}</style></head><body><main>
<h1>Progreso en asuka</h1><p class="dim">{ahora} · disco libre {libre:.0f} GB · se actualiza cada 20 s · solo lectura</p>
{seccion("Procesos", p)}{seccion("Grabacion de Cadena SER", c)}{seccion("Catalogo de anuncios", k)}{logs}
</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        cuerpo = pagina().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8060)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    print(f"panel en http://{a.host}:{a.port}/", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
