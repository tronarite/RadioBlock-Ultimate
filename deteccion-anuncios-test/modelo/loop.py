"""Ciclo de aprendizaje activo: capturar -> características -> repetición ->
entrenar -> puntuar -> revisar (tú) -> repetir.

  python loop.py status     estado de datos y etiquetas
  python loop.py refresh    características + repetición + (re)entrenar + puntuar
  python loop.py review     revisión por terminal (review.py)
  python loop.py cycle      refresh y después review

Las etiquetas SOLO salen de review.py (tú). El modelo propone, nunca decide.
"""

from __future__ import annotations

import argparse

import store


def status(conn) -> None:
    r = conn.execute(
        "SELECT COUNT(*) n, SUM(features IS NOT NULL) f, SUM(label=1) a, SUM(label=0) na, "
        "SUM(score IS NOT NULL) s FROM clips"
    ).fetchone()
    print(f"clips={r['n']} con_features={r['f'] or 0} con_score={r['s'] or 0} "
          f"etiquetados: anuncio={r['a'] or 0} no_anuncio={r['na'] or 0}")


def refresh(conn) -> None:
    import features
    import repeats
    import train

    features.compute_missing(conn)
    repeats.compute(conn)
    try:
        train.fit(conn)
        train.predict_all(conn)
    except Exception as e:  # pocas etiquetas aún: se sigue con muestreo por repetición
        print(f"[train] omitido: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "refresh", "review", "cycle"])
    ap.add_argument("--n", type=int, default=30)
    a = ap.parse_args()
    conn = store.conn()
    if a.cmd == "status":
        status(conn)
        return
    if a.cmd in ("refresh", "cycle"):
        refresh(conn)
    if a.cmd in ("review", "cycle"):
        import subprocess
        import sys

        subprocess.run([sys.executable, "review.py", "--n", str(a.n)], check=False)
    status(conn)


if __name__ == "__main__":
    main()
