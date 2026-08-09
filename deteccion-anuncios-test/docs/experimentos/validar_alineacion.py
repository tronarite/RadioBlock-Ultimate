"""Valida con audio REAL de Cadena SER si el problema de detección es de
alineación de ventanas: toma un trozo real de la grabación, lo vuelve a
insertar más adelante en un punto SIN sincronía con la rejilla de 20s
original (simulando que el mismo anuncio suena dos veces en momentos
cualesquiera), y compara si el método VIEJO (bloques fijos disjuntos) y
el NUEVO (ventanas solapadas) lo detectan.

Requiere un `captura.wav` (mono, 22050Hz) en el directorio desde el que se
ejecute — grábalo con, por ejemplo:
  ffmpeg -re -i <URL_STREAM> -t 400 -vn -ar 22050 -ac 1 captura.wav
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import soundfile as sf

from app.fingerprint import fingerprint, similarity

SR = 22050

pcm, sr = sf.read("captura.wav", dtype="float32")
if sr != SR:
    raise SystemExit(f"esperaba {SR}Hz, el archivo tiene {sr}Hz")
print(f"captura cargada: {len(pcm) / SR:.1f}s")

# --- construir el caso de prueba -----------------------------------------
CLIP_START = 30.0
CLIP_DUR = 25.0
INSERT_AT = 150.37  # deliberadamente NO alineado con ningún múltiplo de 20s

clip = pcm[int(CLIP_START * SR): int((CLIP_START + CLIP_DUR) * SR)]

base = pcm[: int(300 * SR)].copy()
insert_sample = int(INSERT_AT * SR)
test_buf = np.concatenate([base[:insert_sample], clip, base[insert_sample:]])

original_range = (CLIP_START, CLIP_START + CLIP_DUR)
inserted_range = (INSERT_AT, INSERT_AT + CLIP_DUR)
print(f"clip original: {original_range[0]:.1f}s-{original_range[1]:.1f}s")
print(f"clip insertado (desalineado): {inserted_range[0]:.1f}s-{inserted_range[1]:.1f}s")
print(f"duración del buffer de prueba: {len(test_buf) / SR:.1f}s\n")


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


# --- método VIEJO: bloques fijos de 20s, sin solape -----------------------
def metodo_viejo(buf, block_seconds=20.0):
    n_blocks = int(len(buf) / SR // block_seconds)
    fps = []
    for i in range(n_blocks):
        start = i * block_seconds
        seg = buf[int(start * SR): int((start + block_seconds) * SR)]
        fps.append((start, start + block_seconds, fingerprint(seg, SR)))
    return fps


# --- método NUEVO: ventanas de 50s con salto de 20s (solapadas) -----------
def metodo_nuevo(buf, window_seconds=50.0, hop_seconds=20.0):
    fps = []
    t = 0.0
    while t + window_seconds <= len(buf) / SR:
        seg = buf[int(t * SR): int((t + window_seconds) * SR)]
        fps.append((t, t + window_seconds, fingerprint(seg, SR)))
        t += hop_seconds
    return fps


def mejor_match_entre_regiones(fps, region_a, region_b, min_gap=0.0):
    """De entre todas las ventanas que tocan region_a y todas las que
    tocan region_b (sin solaparse entre sí), la mayor similitud encontrada."""
    cand_a = [f for f in fps if overlaps(f[0], f[1], *region_a)]
    cand_b = [f for f in fps if overlaps(f[0], f[1], *region_b)]
    best = 0.0
    best_pair = None
    for sa, ea, fa in cand_a:
        for sb, eb, fb in cand_b:
            if abs(sa - sb) < min_gap:
                continue
            sim = similarity(fa, fb)
            if sim > best:
                best = sim
                best_pair = ((sa, ea), (sb, eb))
    return best, best_pair


print("=== MÉTODO VIEJO (bloques fijos de 20s, disjuntos) ===")
fps_viejo = metodo_viejo(test_buf)
for s, e, fp in fps_viejo:
    print(f"  bloque {s:6.1f}s-{e:6.1f}s -> {len(fp)} hashes")
best, pair = mejor_match_entre_regiones(fps_viejo, original_range, inserted_range)
print(f"\n  mejor similitud original<->insertado: {best:.3f}  (umbral: 0.4)  {pair}")
print(f"  {'DETECTADO' if best >= 0.4 else 'NO DETECTADO'}\n")

print("=== MÉTODO NUEVO (ventanas de 50s, salto de 20s, solapadas) ===")
fps_nuevo = metodo_nuevo(test_buf)
for s, e, fp in fps_nuevo:
    print(f"  ventana {s:6.1f}s-{e:6.1f}s -> {len(fp)} hashes")
best2, pair2 = mejor_match_entre_regiones(fps_nuevo, original_range, inserted_range, min_gap=50.0)
print(f"\n  mejor similitud original<->insertado: {best2:.3f}  (umbral: 0.4)  {pair2}")
print(f"  {'DETECTADO' if best2 >= 0.4 else 'NO DETECTADO'}")


def n_hashes_compartidos(fa, fb):
    return len(fa & fb)


print("\n=== CALIBRACIÓN: nº de hashes COMPARTIDOS en pares SIN relación real ===")
# ventanas del método nuevo, bien separadas entre sí (contenido real distinto,
# no debería haber repetición) -> "suelo de ruido" para un umbral absoluto
sin_relacion = []
for i in range(len(fps_nuevo)):
    for j in range(i + 1, len(fps_nuevo)):
        sa, ea, fa = fps_nuevo[i]
        sb, eb, fb = fps_nuevo[j]
        if abs(sa - sb) < 50.0:
            continue
        # evita contar el par que SÍ contiene el clip insertado
        if overlaps(sa, ea, *original_range) and overlaps(sb, eb, *inserted_range):
            continue
        if overlaps(sb, eb, *original_range) and overlaps(sa, ea, *inserted_range):
            continue
        sin_relacion.append(n_hashes_compartidos(fa, fb))

sin_relacion.sort()
print(f"  pares sin relación comparados: {len(sin_relacion)}")
print(f"  hashes compartidos -> min={sin_relacion[0]} mediana={sin_relacion[len(sin_relacion)//2]} "
      f"p90={sin_relacion[int(len(sin_relacion)*0.9)]} max={sin_relacion[-1]}")

best_a = next(f for f in fps_nuevo if (f[0], f[1]) == pair2[0])
best_b = next(f for f in fps_nuevo if (f[0], f[1]) == pair2[1])
n_compartidos_match = n_hashes_compartidos(best_a[2], best_b[2])
print(f"\n  hashes compartidos en el par CON relación real (original<->insertado): {n_compartidos_match}")
print(f"  eso es {n_compartidos_match / (sin_relacion[len(sin_relacion)//2] or 1):.1f}x la mediana del ruido de fondo")
