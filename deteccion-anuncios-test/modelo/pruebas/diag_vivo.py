"""Graba N s del directo y muestra, por ventana, las mejores coincidencias con los anuncios confirmados."""
import subprocess, sys, sqlite3
from pathlib import Path
import numpy as np

MODELO = Path(sys.argv[1]); N = int(sys.argv[2])
sys.path.insert(0, str(MODELO))
import catalogo, fingerprint as fp, radios, store
from capture import find_ffmpeg

SR = store.SAMPLE_RATE
raw = subprocess.run([find_ffmpeg(), "-loglevel", "error", "-i", radios.RADIOS_BY_KEY["cadena_ser"]["url"], "-t", str(N),
                      "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True).stdout
pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
print(f"grabados {len(pcm)/SR:.0f} s, nivel rms={np.sqrt((pcm**2).mean()):.3f}")
bd = sqlite3.connect(str(MODELO / "data" / "vivo.db"))
idx = catalogo.Indice({i: (fp.bytes_to_fingerprint(h), d, "activo") for i, h, d in bd.execute("SELECT id, huella, dur FROM anuncios")})
for t in np.arange(0, len(pcm) / SR - 15 + 1e-6, 5.0):
    v = fp.fingerprint(pcm[int(t * SR):int((t + 15) * SR)], SR)
    m = sorted(((n, e) for e, (n, _o) in catalogo.votar(idx, v).items()), reverse=True)[:3]
    print(f"  {t:5.0f}s hashes={len(v):5d}  mejores: " + ", ".join(f"{e}:{n}/{catalogo.umbral(idx, e)}" for n, e in m))
