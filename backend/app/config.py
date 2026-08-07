import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
SEGMENTS_DIR = DATA_DIR / "segments"
DB_PATH = DATA_DIR / "radio_adblocker.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)

SEGMENT_DURATION_SECONDS = int(os.environ.get("SEGMENT_DURATION_SECONDS", "10"))
DEFAULT_CONFIDENCE_THRESHOLD = float(os.environ.get("DEFAULT_CONFIDENCE_THRESHOLD", "0.75"))
SEGMENT_RETENTION_DAYS = int(os.environ.get("SEGMENT_RETENTION_DAYS", "7"))

# Puertos de proxy asignados dinámicamente a partir de este valor.
PROXY_PORT_BASE = int(os.environ.get("PROXY_PORT_BASE", "8001"))
PROXY_PORT_MAX = int(os.environ.get("PROXY_PORT_MAX", "8010"))

SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "22050"))
