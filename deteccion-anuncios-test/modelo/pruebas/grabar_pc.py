"""Graba N s del directo en el PC y lo guarda en wav con la hora de inicio en el nombre."""
import subprocess, sys, time
from pathlib import Path
import numpy as np, soundfile as sf
MODELO = Path(sys.argv[1]); N = int(sys.argv[2]); OUT = Path(sys.argv[3])
sys.path.insert(0, str(MODELO))
import radios, store
from capture import find_ffmpeg
t0 = time.time()
raw = subprocess.run([find_ffmpeg(), "-loglevel", "error", "-i", radios.RADIOS_BY_KEY["cadena_ser"]["url"], "-t", str(N),
                      "-vn", "-ac", "1", "-ar", str(store.SAMPLE_RATE), "-f", "s16le", "-"], capture_output=True).stdout
pcm = np.frombuffer(raw, dtype=np.int16)
f = OUT / f"pc_{time.strftime('%Y%m%d_%H%M%S', time.localtime(t0))}.wav"
sf.write(str(f), pcm, store.SAMPLE_RATE)
print(f)
