"""En asuka: graba 30 s del directo por tuberia (como en_vivo.py) y compara su huella con la de los clips
que la captura grabo a la vez. Si coinciden miles de hashes, el silenciador puede ir en asuka."""
import glob, subprocess, sys, time
from pathlib import Path
import numpy as np, soundfile as sf

MODELO = Path(sys.argv[1]); sys.path.insert(0, str(MODELO))
import fingerprint as fp, radios, store

FF = r"C:\Otros archivos de programa\ffmpeg\bin\ffmpeg.exe"
t0 = time.time()
raw = subprocess.run([FF, "-loglevel", "error", "-i", radios.RADIOS_BY_KEY["cadena_ser"]["url"], "-t", "30", "-vn", "-ac", "1",
                      "-ar", str(store.SAMPLE_RATE), "-f", "s16le", "-"], capture_output=True).stdout
pipe = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
time.sleep(30)
def ts(f):
    return time.mktime(time.strptime(Path(f).stem, "%Y%m%d_%H%M%S"))
todos = sorted(glob.glob(str(MODELO / "data" / "clips" / "cadena_ser" / "*.wav")))[:-1]   # el ultimo aun se escribe
sel = [f for f in todos if t0 - 20 <= ts(f) <= t0 + 40]
clips = np.concatenate([sf.read(f, dtype="float32")[0] for f in sel])
hp, hc = fp.fingerprint(pipe, store.SAMPLE_RATE), fp.fingerprint(clips, store.SAMPLE_RATE)
r = fp.align(hp, hc)
print(f"tuberia {len(pipe)/store.SAMPLE_RATE:.0f}s {len(hp)} hashes | clips {len(sel)} ({len(clips)/store.SAMPLE_RATE:.0f}s) {len(hc)} hashes | "
      f"compartidos {len(hp.keys() & hc.keys())} | alineados {r[0] if r else 0} ({(r[0] if r else 0)/max(len(hp),1):.0%} de la tuberia)")
