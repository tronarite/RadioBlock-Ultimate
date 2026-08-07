"""Rotación de archivos de audio de segmentos.

Los .wav de muestra sólo existen para poder escucharlos en el panel (cola
de patrones nuevos y gestión de clusters). Pasados SEGMENT_RETENTION_DAYS
ya no aportan nada, así que se borran del disco para no acumular espacio
indefinidamente — pero nunca se toca el segmento que sea la muestra
representativa de un cluster todavía vivo, revisado o no, porque el panel
sigue necesitando poder reproducirlo.
"""

from __future__ import annotations

import datetime
import logging
import threading

from app.config import SEGMENT_RETENTION_DAYS, SEGMENTS_DIR
from app.db.models import Cluster, Segmento

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60  # cada 6 horas basta de sobra


def run_once(session_factory) -> int:
    """Borra los .wav de segmentos antiguos y desvincula `archivo_audio`.

    Devuelve cuántos archivos se han borrado.
    """
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=SEGMENT_RETENTION_DAYS)
    db = session_factory()
    borrados = 0
    try:
        referenciados = {
            row[0]
            for row in db.query(Cluster.representative_segment_id).filter(
                Cluster.representative_segment_id.isnot(None)
            )
        }
        candidatos = (
            db.query(Segmento)
            .filter(Segmento.archivo_audio.isnot(None), Segmento.timestamp < cutoff)
            .all()
        )
        for seg in candidatos:
            if seg.id in referenciados:
                continue
            path = SEGMENTS_DIR / seg.archivo_audio
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("no se pudo borrar %s", path)
                continue
            seg.archivo_audio = None
            borrados += 1
        db.commit()
    finally:
        db.close()
    return borrados


def start_background_cleanup(session_factory) -> None:
    """Lanza un hilo daemon que rota los .wav antiguos periódicamente."""

    def _loop():
        while True:
            try:
                run_once(session_factory)
            except Exception:
                logger.exception("fallo en la rotación de segmentos")
            threading.Event().wait(CLEANUP_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()
