"""Mete anuncios confirmados entre radio normal y comprueba que el analizador en vivo los reconoce y los situa."""
import io, sys, sqlite3
from pathlib import Path
import numpy as np, soundfile as sf

MODELO = Path(sys.argv[1]); SP = Path(sys.argv[2])
sys.path.insert(0, str(MODELO))
import catalogo, en_vivo, fingerprint as fp, store

SR = store.SAMPLE_RATE
radio, _ = sf.read(str(SP / "prueba.wav"), dtype="float32")
bd = sqlite3.connect(str(MODELO / "data" / "vivo.db"))
indice = catalogo.Indice({i: (fp.bytes_to_fingerprint(h), d, "activo") for i, h, d in bd.execute("SELECT id, huella, dur FROM anuncios")})

partes, esperado, t = [], [], 0.0
def poner(x):
    global t
    partes.append(x); t += len(x) / SR
poner(radio[:20 * SR])
ads = sorted(SP.glob("anuncios/*.wav"))
for k, f in enumerate(ads):
    x, sr = sf.read(str(f), dtype="float32")
    assert sr == SR
    esperado.append((int(f.stem), t, t + len(x) / SR))
    poner(x)
    if k == 1:                      # entre el 2o y el 3o, 8 s de radio (debe rellenarse: bloque)
        poner(radio[25 * SR:33 * SR])
poner(radio[35 * SR:65 * SR])
pcm = np.concatenate(partes)

d, s = en_vivo.Directo(), en_vivo.Silencios()
d.anadir((pcm * 32767).astype(np.int16).tobytes()); d.fin = True
en_vivo.analizar(d, s, indice, 0.0, io.StringIO())
print("esperado:", [(e, round(a, 1), round(b, 1)) for e, a, b in esperado])
print("bloques a silenciar:", [(round(a, 1), round(b, 1)) for a, b in s.bloques()])
for e, a, b in esperado:
    tapado = sum(min(b, y) - max(a, x) for x, y in s.bloques() if y > a and x < b)
    print(f"  anuncio {e}: {tapado / (b - a):.0%} tapado")
