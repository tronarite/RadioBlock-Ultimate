"""Crecimiento del catalogo por dia y lo que ocupa (solo lectura)."""
import sqlite3, sys, time
from collections import Counter, defaultdict
from pathlib import Path

c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30)
dia = lambda t: time.strftime("%a %d", time.localtime(t)) if t else "?"
por_dia = defaultdict(Counter)
for t, est, conf in c.execute("SELECT t_origen, estado, confirmado FROM catalog"):
    por_dia[dia(t)][f"{est}{'+' if conf else ''}"] += 1
for d in sorted(por_dia, key=lambda s: s[-2:]):
    print(f"  origen {d}: total {sum(por_dia[d].values()):5d}  {dict(por_dia[d])}")
n, bytes_huella = c.execute("SELECT COUNT(*), SUM(LENGTH(huella)) FROM catalog").fetchone()
print(f"entradas {n}, huellas {bytes_huella/1e6:.0f} MB en disco (en memoria ~{bytes_huella/8*110/1e9:.1f} GB si se cargan todas)")
ahora = c.execute("SELECT MAX(t_end) FROM detections").fetchone()[0]
vivas = c.execute("SELECT COUNT(*) FROM catalog WHERE estado='activo' AND confirmado=0 AND COALESCE(last_seen, t_origen) > ?",
                  (ahora - 86400,)).fetchone()[0]
print(f"pendientes oidas en las ultimas 24 h: {vivas}")
print("detecciones:", c.execute("SELECT COUNT(*) FROM detections").fetchone()[0])
wav = Counter()
tam = Counter()
for est, w in c.execute("SELECT estado, wav FROM catalog WHERE wav IS NOT NULL"):
    p = Path(w)
    if p.exists():
        wav[est] += 1
        tam[est] += p.stat().st_size
print("wav de entradas por estado:", {k: f"{wav[k]} ({tam[k]/1e9:.2f} GB)" for k in wav})
