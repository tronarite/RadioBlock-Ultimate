"""Copia los anuncios confirmados (Si) del catalogo a una BD pequena para el silenciador en vivo.
  python exportar_vivo.py data/catalogo.db data/vivo.db      (lee el catalogo en solo lectura)
"""
import sqlite3
import sys
from pathlib import Path

if __name__ == "__main__":
    origen, destino = sys.argv[1], Path(sys.argv[2])
    cat = sqlite3.connect(f"file:{Path(origen).as_posix()}?mode=ro", uri=True, timeout=30)
    filas = cat.execute("SELECT id, radio, huella, dur FROM catalog WHERE estado='activo' AND confirmado=1").fetchall()
    destino.unlink(missing_ok=True)
    out = sqlite3.connect(str(destino))
    out.execute("CREATE TABLE anuncios (id INTEGER PRIMARY KEY, radio TEXT, huella BLOB, dur REAL)")
    out.executemany("INSERT INTO anuncios VALUES (?,?,?,?)", filas)
    out.commit()
    print(f"{len(filas)} anuncios confirmados -> {destino}")
