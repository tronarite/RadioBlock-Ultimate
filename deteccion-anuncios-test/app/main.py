import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.cleanup import start_background_cleanup
from app.db import SEGMENTS_DIR, get_conn, init_db
from app.radios import RADIOS, RADIOS_BY_KEY
from app.worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).parent / "static"

WINDOW_SECONDS = int(os.environ.get("WINDOW_SECONDS", "50"))
HOP_SECONDS = int(os.environ.get("HOP_SECONDS", "20"))

app = FastAPI(title="Ad Detector Test")
workers = {
    r["key"]: Worker(
        radio_key=r["key"], nombre=r["nombre"], url=r["url"],
        window_seconds=WINDOW_SECONDS, hop_seconds=HOP_SECONDS,
    )
    for r in RADIOS
}

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _radio_or_404(radio_key: str) -> None:
    if radio_key not in RADIOS_BY_KEY:
        raise HTTPException(404, "emisora no encontrada")


@app.on_event("startup")
def on_startup():
    init_db()
    start_background_cleanup()
    for w in workers.values():
        w.start()


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/radios")
def listar_radios():
    return [w.status() for w in workers.values()]


@app.get("/api/radios/{radio_key}/grupos")
def listar_grupos(radio_key: str):
    _radio_or_404(radio_key)
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, n_segmentos, n_apariciones, primera_vez, ultima_vez, representative_segment_id,"
            " inicio_estimado, fin_estimado, n_estimaciones"
            " FROM grupos WHERE radio = ? ORDER BY n_apariciones DESC, n_segmentos DESC",
            (radio_key,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/radios/{radio_key}/grupos/{grupo_id}/apariciones")
def apariciones_grupo(radio_key: str, grupo_id: int):
    _radio_or_404(radio_key)
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, timestamp, archivo_audio FROM segmentos WHERE grupo_id = ? AND radio = ? ORDER BY timestamp",
            (grupo_id, radio_key),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/segmentos/{segmento_id}/audio")
def audio_segmento(segmento_id: int):
    conn = get_conn()
    try:
        row = conn.execute("SELECT archivo_audio FROM segmentos WHERE id = ?", (segmento_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row["archivo_audio"]:
        raise HTTPException(404, "audio no disponible (puede haberse rotado ya)")
    path = SEGMENTS_DIR / row["archivo_audio"]
    if not path.exists():
        raise HTTPException(404, "audio no encontrado en disco")
    # Coexisten archivos .wav (formato actual) y .flac (de antes del
    # cambio, todavía sirviendo muestras representativas viejas).
    media_type = "audio/wav" if path.suffix == ".wav" else "audio/flac"
    return FileResponse(str(path), media_type=media_type)
