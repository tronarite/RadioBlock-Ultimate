"""Interfaz web local para la revision humana de tramos de radio.

Ejecutar: python web_review.py [--port 8050]
"""

from __future__ import annotations

import argparse
import io
import math
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import audio
import review_segments
import store


BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
MARGEN_S = 3.0


class Etiqueta(BaseModel):
    segment_id: int
    etiqueta: int = Field(ge=0, le=1)
    propagar: bool = False


class Bordes(BaseModel):
    segment_id: int
    ini: float
    fin: float


def _bordes(fila) -> tuple[float, float]:
    return (
        fila["t_start_user"] if fila["t_start_user"] is not None else fila["t_start"],
        fila["t_end_user"] if fila["t_end_user"] is not None else fila["t_end"],
    )


def _segmento(c, segment_id: int):
    fila = c.execute(
        """SELECT id, radio, t_start, t_end, source, group_key, score, n_repeats,
                  label, labeled_at, t_start_user, t_end_user
           FROM segments WHERE id=?""",
        (segment_id,),
    ).fetchone()
    if fila is None:
        raise HTTPException(404, "Tramo no encontrado")
    return fila


def _publico(c, fila) -> dict:
    ini, fin = _bordes(fila)
    grupo_total = 1
    if fila["group_key"]:
        grupo_total = c.execute(
            "SELECT COUNT(*) FROM segments WHERE group_key=?", (fila["group_key"],)
        ).fetchone()[0]
    return {
        "id": fila["id"], "radio": fila["radio"], "t_start": fila["t_start"],
        "t_end": fila["t_end"], "ini": ini, "fin": fin, "source": fila["source"],
        "group_key": fila["group_key"], "group_total": grupo_total,
        "score": fila["score"], "n_repeats": fila["n_repeats"] or 0,
    }


def _stats(c) -> dict:
    fila = c.execute(
        """SELECT SUM(label=1) anuncios, SUM(label=0) no_anuncios,
                  SUM(label IS NULL) pendientes FROM segments"""
    ).fetchone()
    return {clave: int(fila[clave] or 0) for clave in ("anuncios", "no_anuncios", "pendientes")}


def _wav_en_memoria(c, fila, ini: float, fin: float) -> bytes:
    """Genera el WAV con las funciones compartidas y lo devuelve para HTTP."""
    pcm = audio.load_range(c, fila["radio"], ini - MARGEN_S, fin + MARGEN_S)
    # write_wav recibe una ruta en el contrato actual de audio.py. El fichero
    # temporal solo existe durante esta llamada; la respuesta queda en memoria.
    descriptor, nombre = tempfile.mkstemp(prefix="web_review_", suffix=".wav")
    os.close(descriptor)
    try:
        audio.write_wav(nombre, pcm)
        with open(nombre, "rb") as fichero:
            return fichero.read()
    finally:
        try:
            os.unlink(nombre)
        except FileNotFoundError:
            pass


def _respuesta_rango(request: Request, contenido: bytes) -> Response:
    cabecera = request.headers.get("range")
    total = len(contenido)
    comunes = {"Accept-Ranges": "bytes", "Content-Type": "audio/wav"}
    if not cabecera:
        return Response(contenido, headers={**comunes, "Content-Length": str(total)})
    coincidencia = re.fullmatch(r"bytes=(\d*)-(\d*)", cabecera.strip())
    if not coincidencia:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
    inicio_txt, fin_txt = coincidencia.groups()
    if not inicio_txt and not fin_txt:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
    if inicio_txt:
        inicio = int(inicio_txt)
        fin = int(fin_txt) if fin_txt else total - 1
    else:
        longitud = int(fin_txt)
        inicio, fin = max(0, total - longitud), total - 1
    if inicio >= total or inicio > fin:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
    fin = min(fin, total - 1)
    parte = contenido[inicio : fin + 1]
    return Response(parte, status_code=206, headers={
        **comunes, "Content-Length": str(len(parte)), "Content-Range": f"bytes {inicio}-{fin}/{total}",
    })


def crear_app() -> FastAPI:
    app = FastAPI(title="Revision de anuncios", docs_url=None, redoc_url=None)
    app.state.historial = []
    app.state.lock = threading.Lock()

    @app.get("/api/tanda")
    def tanda(n: int = 20):
        if not 1 <= n <= 100:
            raise HTTPException(422, "n debe estar entre 1 y 100")
        with store.conn2() as c:
            return {"tramos": [_publico(c, fila) for fila in review_segments.seleccionar_tanda(c, n)], "stats": _stats(c)}

    @app.get("/api/audio/{segment_id}")
    def obtener_audio(segment_id: int, request: Request, ini: float | None = None, fin: float | None = None):
        with store.conn2() as c:
            fila = _segmento(c, segment_id)
            por_defecto_ini, por_defecto_fin = _bordes(fila)
            ini = por_defecto_ini if ini is None else ini
            fin = por_defecto_fin if fin is None else fin
            if not (math.isfinite(ini) and math.isfinite(fin) and ini < fin):
                raise HTTPException(422, "Los bordes no son validos")
            return _respuesta_rango(request, _wav_en_memoria(c, fila, ini, fin))

    @app.post("/api/etiqueta")
    def etiqueta(datos: Etiqueta):
        with app.state.lock, store.conn2() as c:
            fila = _segmento(c, datos.segment_id)
            anterior = review_segments.guardar_etiqueta(c, fila, datos.etiqueta, datos.propagar)
            app.state.historial.append(anterior)
            return {"afectados": [x[0] for x in anterior], "stats": _stats(c)}

    @app.post("/api/bordes")
    def bordes(datos: Bordes):
        if not (math.isfinite(datos.ini) and math.isfinite(datos.fin) and datos.ini < datos.fin):
            raise HTTPException(422, "El inicio debe ser anterior al final")
        with store.conn2() as c:
            _segmento(c, datos.segment_id)
            review_segments.guardar_bordes(c, datos.segment_id, datos.ini, datos.fin)
            return {"ini": datos.ini, "fin": datos.fin}

    @app.post("/api/deshacer")
    def deshacer():
        with app.state.lock, store.conn2() as c:
            if not app.state.historial:
                raise HTTPException(409, "No hay ninguna etiqueta que deshacer")
            accion = app.state.historial.pop()
            review_segments.deshacer(c, accion)
            return {"restaurados": [x[0] for x in accion], "stats": _stats(c)}

    @app.get("/api/stats")
    def stats():
        with store.conn2() as c:
            return _stats(c)

    @app.get("/")
    def inicio():
        return FileResponse(STATIC / "review.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = crear_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Revision web local de tramos de radio")
    parser.add_argument("--port", type=int, default=8050)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port debe estar entre 1 y 65535")
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
