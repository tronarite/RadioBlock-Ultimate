"""Duracion de las entradas contestadas: Si (activo+confirmado) frente a No (lista_negra). Solo lectura."""
import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
r = sqlite3.connect(f"file:{sys.argv[2]}?mode=ro", uri=True)
directas = dict(r.execute("SELECT catalog_id, resp FROM respuestas WHERE fuente='usuario' AND catalog_id>0"))
cortes = [4, 6, 8, 10, 12, 15]
for nombre, filtro in (("todas las contestadas", None), ("solo contestadas por ti en la web", directas)):
    filas = c.execute("SELECT id, dur, estado FROM catalog WHERE confirmado=1 AND estado IN ('activo','lista_negra')").fetchall()
    if filtro is not None:
        filas = [f for f in filas if f[0] in filtro]
    si = sorted(d for _, d, e in filas if e == "activo")
    no = sorted(d for _, d, e in filas if e == "lista_negra")
    print(f"== {nombre}: Si={len(si)} No={len(no)}")
    for t in cortes:
        print(f"  < {t:>2}s: Si {sum(d < t for d in si):>3}/{len(si)}   No {sum(d < t for d in no):>3}/{len(no)}")
    if si:
        print(f"  Si mas cortos: {[round(d,1) for d in si[:8]]}")
pend = [d for (d,) in c.execute("SELECT dur FROM catalog WHERE estado='activo' AND confirmado=0")]
print(f"== pendientes={len(pend)}: " + "  ".join(f"<{t}s={sum(d < t for d in pend)}" for t in cortes))
