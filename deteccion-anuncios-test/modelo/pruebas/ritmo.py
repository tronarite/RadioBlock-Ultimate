"""Ritmo de los anuncios confirmados: emisiones por dia y cuando aparecieron por primera vez (solo lectura)."""
import sqlite3, sys, time
from collections import Counter

c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30)
t0, t1 = c.execute("SELECT MIN(t_start), MAX(t_end) FROM detections").fetchone()
horas = (t1 - t0) / 3600
filas = c.execute("SELECT k.id, MIN(d.t_start), COUNT(*) FROM catalog k JOIN detections d ON d.catalog_id=k.id "
                  "WHERE k.estado='activo' AND k.confirmado=1 GROUP BY k.id").fetchall()
por_dia = sorted(n / (horas / 24) for _, _, n in filas)
q = lambda p: por_dia[int(p * (len(por_dia) - 1))]
print(f"detecciones desde {time.strftime('%a %d %H:%M', time.localtime(t0))} hasta {time.strftime('%a %d %H:%M', time.localtime(t1))} ({horas:.0f} h)")
print(f"anuncios confirmados con detecciones: {len(filas)}")
print(f"emisiones por dia de cada uno: p25={q(.25):.1f}  mediana={q(.5):.1f}  p75={q(.75):.1f}")
tramos = Counter(int((f - t0) // (12 * 3600)) for _, f, _ in filas)
for k in sorted(tramos):
    a = time.strftime('%a %d %H:%M', time.localtime(t0 + k * 12 * 3600))
    print(f"  primera emision en el tramo de 12 h desde {a}: {tramos[k]}")
pend = c.execute("SELECT COUNT(*) FROM catalog WHERE estado='activo' AND confirmado=0").fetchone()[0]
nuevas = Counter(int(((r[0] or t0) - t0) // (12 * 3600)) for r in c.execute(
    "SELECT t_origen FROM catalog WHERE estado='activo' AND confirmado=0 AND dur >= 6"))
print(f"pendientes de revisar: {pend}; de >= 6 s por tramo de 12 h de origen: {dict(sorted(nuevas.items()))}")
