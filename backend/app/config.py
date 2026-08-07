import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
SEGMENTS_DIR = DATA_DIR / "segments"
DB_PATH = DATA_DIR / "radio_adblocker.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)

# 20s en vez de 10s: un segmento demasiado corto captura solo un trozo suelto
# de un anuncio (o de una frase de tertulia) en vez de su forma completa,
# lo que hace que segmentos de cosas distintas se parezcan más de la cuenta.
SEGMENT_DURATION_SECONDS = int(os.environ.get("SEGMENT_DURATION_SECONDS", "20"))
DEFAULT_CONFIDENCE_THRESHOLD = float(os.environ.get("DEFAULT_CONFIDENCE_THRESHOLD", "0.75"))
SEGMENT_RETENTION_DAYS = int(os.environ.get("SEGMENT_RETENTION_DAYS", "7"))

# Cada cuántos segmentos nuevos se relanza el reentrenamiento no supervisado
# (independiente de que el usuario haya etiquetado nada). Es lo que permite
# que el sistema descubra patrones repetidos por sí solo con solo escuchar.
RETRAIN_EVERY_N_SEGMENTS = int(os.environ.get("RETRAIN_EVERY_N_SEGMENTS", "8"))

# Puertos de proxy asignados dinámicamente a partir de este valor.
PROXY_PORT_BASE = int(os.environ.get("PROXY_PORT_BASE", "8001"))
PROXY_PORT_MAX = int(os.environ.get("PROXY_PORT_MAX", "8010"))

SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "22050"))
