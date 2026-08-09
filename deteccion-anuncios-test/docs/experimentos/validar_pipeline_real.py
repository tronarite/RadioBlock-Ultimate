"""Igual que validar_alineacion.py pero usando literalmente las funciones
de app/grouping.py del proyecto (no una reimplementación de prueba), para
confirmar que el pipeline real detecta el caso. Requiere un captura.wav
en el directorio de ejecución — ver validar_alineacion.py."""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import soundfile as sf

from app.fingerprint import fingerprint
from app.grouping import MIN_SHARED_HASHES, _count_apariciones, _group_by_fingerprint

SR = 22050
WINDOW = 50.0
HOP = 20.0

pcm, sr = sf.read("captura.wav", dtype="float32")
assert sr == SR

CLIP_START, CLIP_DUR = 30.0, 25.0
INSERT_AT = 150.37
clip = pcm[int(CLIP_START * SR): int((CLIP_START + CLIP_DUR) * SR)]
base = pcm[: int(300 * SR)].copy()
insert_sample = int(INSERT_AT * SR)
test_buf = np.concatenate([base[:insert_sample], clip, base[insert_sample:]])

base_time = datetime.datetime(2026, 1, 1, 12, 0, 0)
fingerprints, timestamps = [], []
t = 0.0
while t + WINDOW <= len(test_buf) / SR:
    seg = test_buf[int(t * SR): int((t + WINDOW) * SR)]
    fingerprints.append(fingerprint(seg, SR))
    timestamps.append(base_time + datetime.timedelta(seconds=t))
    t += HOP

print(f"{len(fingerprints)} ventanas generadas, MIN_SHARED_HASHES={MIN_SHARED_HASHES}")

groups = _group_by_fingerprint(len(fingerprints), fingerprints, timestamps, WINDOW)
print(f"grupos encontrados: {len(groups)}")
for g in groups:
    times = [timestamps[i] for i in g]
    n_ap = _count_apariciones(times, WINDOW)
    offsets = sorted((ts - base_time).total_seconds() for ts in times)
    print(f"  grupo con {len(g)} miembros, {n_ap} apariciones, empiezan en: {offsets}")

if groups:
    print("\n>>> EL PIPELINE REAL DETECTA LA REPETICIÓN <<<")
else:
    print("\n>>> el pipeline real NO ha agrupado nada (raro dado el test anterior) <<<")
