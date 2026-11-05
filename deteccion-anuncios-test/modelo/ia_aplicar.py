"""Guarda en respuestas.db los veredictos de la IA (fuente='ia'); el ciclo los aplica como cualquier respuesta.
  python ia_aplicar.py veredictos.json     {"123": "si", "124": "no"}   (solo los claros; los dudosos se dejan fuera)
"""
import json
import sys

from revisar import respuestas_db

if __name__ == "__main__":
    v = json.load(open(sys.argv[1], encoding="utf-8"))
    r = respuestas_db()
    hechas = {x[0] for x in r.execute("SELECT catalog_id FROM respuestas")}
    n = 0
    for k, resp in v.items():
        if int(k) not in hechas and resp in ("si", "no"):
            r.execute("INSERT INTO respuestas(catalog_id, resp, fuente) VALUES (?,?,'ia')", (int(k), resp))
            n += 1
    r.commit()
    print(n, "veredictos IA guardados")
