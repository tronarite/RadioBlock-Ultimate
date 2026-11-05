"""Revision manual minima: un anuncio recortado, Si o No.

  python revisar.py [--port 8070]      (escucha solo en 127.0.0.1; --host <ip> para Tailscale)

Solo ensena anuncios ya afinados (recortados con varias emisiones) y sin confirmar.
Las respuestas van a data/respuestas.db; el ciclo las aplica al catalogo en su
siguiente vuelta (asi el ciclo sigue siendo el unico que escribe en catalogo.db).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

PAGINA = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Revisar anuncios</title><style>
:root{--bg:#eef1f2;--card:#fff;--ink:#13201f;--dim:#586664;--si:#0b7a75;--no:#d2431f}
@media(prefers-color-scheme:dark){:root{--bg:#0f1818;--card:#162221;--ink:#e4ecea;--dim:#97a8a5;--si:#2aa79f;--no:#e0603c}}
body{margin:0;background:var(--bg);color:var(--ink);font:18px/1.5 system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;padding:16px}
main{background:var(--card);border-radius:14px;padding:22px;width:min(520px,100%);text-align:center}
h1{font-size:1.25rem;margin:.2rem 0 .4rem}p{margin:.3rem 0;color:var(--dim)}audio{width:100%;margin:14px 0}
.b{display:flex;gap:12px}button{flex:1;font:700 1.3rem system-ui;padding:18px;border:0;border-radius:12px;color:#fff;cursor:pointer}
#si{background:var(--si)}#no{background:var(--no)}small{color:var(--dim)}</style></head><body><main>
<h1 id="t">Cargando...</h1><p id="d"></p><p id="x" style="font-style:italic;color:var(--ink)"></p><audio id="a" controls></audio>
<div class="b"><button id="si">Sí</button><button id="no">No</button></div>
<p><small>Sí = todo lo que suena es publicidad (aunque falte un trozo del anuncio) · No = tiene radio normal o no es publicidad · teclas S / N</small></p></main>
<script>
let cur=null;const $=i=>document.getElementById(i);
async function next(){const r=await (await fetch('/api/siguiente')).json();
 if(!r.id){cur=null;$('t').textContent='No quedan anuncios por revisar';$('d').textContent='';$('a').removeAttribute('src');return}
 cur=r.id;$('t').textContent='¿Todo esto es publicidad?';
 $('d').textContent=r.dur.toFixed(1)+' s · '+(r.n?('ha sonado '+r.n+' veces'):'tramo entre dos anuncios conocidos')+' · quedan '+r.restantes;
 $('x').textContent=r.texto?('«'+r.texto+'»'):'';$('a').src='/audio/'+r.id+'.wav';$('a').play().catch(()=>{})}
async function resp(v){if(!cur)return;await fetch('/api/respuesta',{method:'POST',body:JSON.stringify({id:cur,resp:v})});next()}
$('si').onclick=()=>resp('si');$('no').onclick=()=>resp('no');
document.addEventListener('keydown',e=>{const k=e.key.toLowerCase();if(k==='s')resp('si');if(k==='n')resp('no')});
next();</script></body></html>"""


def respuestas_db() -> sqlite3.Connection:
    c = sqlite3.connect(str(DATA / "respuestas.db"), timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS respuestas (id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id INTEGER NOT NULL, "
              "resp TEXT NOT NULL, ts TEXT DEFAULT CURRENT_TIMESTAMP, aplicada INTEGER NOT NULL DEFAULT 0)")
    if "fuente" not in {x[1] for x in c.execute("PRAGMA table_info(respuestas)")}:
        c.execute("ALTER TABLE respuestas ADD COLUMN fuente TEXT NOT NULL DEFAULT 'usuario'")
    return c


CON_HUECOS = False   # --huecos: tambien pregunta por tramos entre anuncios conocidos (hay muchos repetidos)


def pendientes() -> list[sqlite3.Row]:
    ruta = DATA / "catalogo.db"
    if not ruta.exists():
        return []
    cat = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True, timeout=30)
    cat.row_factory = sqlite3.Row
    try:
        filas = cat.execute("SELECT id, dur, n_matches, wav FROM catalog WHERE estado='activo' AND confirmado=0 "
                            "AND refinado>=1 AND wav IS NOT NULL AND (grupo IS NULL OR id=(SELECT c2.id FROM catalog c2 WHERE c2.grupo=catalog.grupo AND c2.estado='activo' AND c2.confirmado=0 AND c2.refinado>=1 AND c2.wav IS NOT NULL ORDER BY c2.n_matches DESC, c2.id LIMIT 1)) ORDER BY n_matches DESC").fetchall()
    except sqlite3.OperationalError:   # todavia no hay columnas de afinado
        cat.close()
        return []
    r = respuestas_db()
    hechas = {x[0] for x in r.execute("SELECT catalog_id FROM respuestas")}
    r.close()
    try:   # tramos entre dos anuncios conocidos (id negativo = -id del segmento)
        if not CON_HUECOS:
            raise sqlite3.OperationalError
        huecos = [{"id": -f["id"], "dur": f["t_end"] - f["t_start"], "n_matches": 0, "wav": None} for f in cat.execute(
            "SELECT id, t_start, t_end FROM segments WHERE source='hueco' AND label IS NULL ORDER BY t_start DESC")
            if (DATA / "huecos" / f"{f['id']}.wav").exists()]
    except sqlite3.OperationalError:
        huecos = []
    cat.close()
    return [f for f in list(filas) + huecos if f["id"] not in hechas]


def texto_de(eid: int) -> str:
    """Transcripcion (ia_transcribir.py) para leer en vez de escuchar; vacia si no hay."""
    ruta = DATA / "transcripciones.db"
    if eid < 0 or not ruta.exists():
        return ""
    try:
        t = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True, timeout=10)
        f = t.execute("SELECT texto FROM t WHERE clave=?", (f"e{eid}",)).fetchone()
        t.close()
        return f[0] if f else ""
    except sqlite3.OperationalError:
        return ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, codigo: int, tipo: str, cuerpo: bytes):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path == "/":
            self._enviar(200, "text/html; charset=utf-8", PAGINA.encode("utf-8"))
        elif self.path == "/api/siguiente":
            p = pendientes()
            d = {} if not p else {"id": p[0]["id"], "dur": p[0]["dur"], "n": p[0]["n_matches"], "restantes": len(p),
                                      "texto": texto_de(p[0]["id"])}
            self._enviar(200, "application/json", json.dumps(d).encode())
        elif self.path.startswith("/audio/") and self.path.endswith(".wav"):
            try:
                eid = int(self.path[7:-4])
            except ValueError:
                return self.send_error(404)
            fichero = DATA / ("huecos" if eid < 0 else "entradas") / f"{abs(eid)}.wav"
            if not fichero.exists():
                return self.send_error(404)
            self._enviar(200, "audio/wav", fichero.read_bytes())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/respuesta":
            return self.send_error(404)
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            eid, resp = int(d["id"]), d["resp"]
            assert resp in ("si", "no")
        except Exception:
            return self.send_error(400)
        r = respuestas_db()
        r.execute("INSERT INTO respuestas(catalog_id, resp) VALUES (?,?)", (eid, resp))
        r.commit()
        r.close()
        self._enviar(200, "application/json", b"{}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8070)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--huecos", action="store_true")
    a = ap.parse_args()
    global CON_HUECOS
    CON_HUECOS = a.huecos
    print(f"revisar en http://{a.host}:{a.port}/", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
