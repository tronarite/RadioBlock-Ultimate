"""Rotación de los audios de segmentos: dejar esto corriendo mucho tiempo
generaría varios GB al día si no se borrara nada. La detección solo
necesita la huella (guardada como BLOB en la BD, no el audio), así que se
puede ser agresivo: se conservan solo las últimas RETENTION_HOURS y los
que sean muestra representativa de algún grupo sospechoso ya detectado
(para poder escucharlos en el panel) — esos se conservan siempre, sin
importar su antigüedad."""

from __future__ import annotations

import datetime
import logging
import os
import threading

from app.db import SEGMENTS_DIR, get_conn

logger = logging.getLogger(__name__)

RETENTION_HOURS = float(os.environ.get("RETENTION_HOURS", "2"))
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("CLEANUP_INTERVAL_SECONDS", str(15 * 60)))


def run_once() -> int:
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=RETENTION_HOURS)).isoformat()
    conn = get_conn()
    borrados = 0
    try:
        referenciados = {
            row["representative_segment_id"]
            for row in conn.execute("SELECT representative_segment_id FROM grupos WHERE representative_segment_id IS NOT NULL")
        }
        candidatos = conn.execute(
            "SELECT id, archivo_audio FROM segmentos WHERE archivo_audio IS NOT NULL AND timestamp < ?",
            (cutoff,),
        ).fetchall()
        for row in candidatos:
            if row["id"] in referenciados:
                continue
            path = SEGMENTS_DIR / row["archivo_audio"]
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("no se pudo borrar %s", path)
                continue
            conn.execute("UPDATE segmentos SET archivo_audio = NULL WHERE id = ?", (row["id"],))
            borrados += 1
        conn.commit()
    finally:
        conn.close()
    return borrados


def start_background_cleanup() -> None:
    def _loop():
        while True:
            try:
                run_once()
            except Exception:
                logger.exception("fallo en la rotación de segmentos")
            threading.Event().wait(CLEANUP_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()
