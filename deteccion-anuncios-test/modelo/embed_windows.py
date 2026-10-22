"""Calcula y guarda embeddings de ventanas solapadas de una radio."""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

import audio
import embeddings
import store


TAM_LOTE = 32


def _instantes(t0: float, t1: float) -> list[float]:
    n = int((t1 - t0 - store.WINDOW_S) // store.HOP_S) + 1
    return [t0 + i * store.HOP_S for i in range(max(n, 0))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radio", required=True)
    parser.add_argument("--backend", default="panns", choices=("panns", "handcrafted"))
    parser.add_argument("--max-minutes", type=float, metavar="N")
    args = parser.parse_args()
    if args.max_minutes is not None and args.max_minutes <= 0:
        parser.error("--max-minutes debe ser mayor que cero")

    c = store.conn2()
    try:
        span = audio.radio_span(c, args.radio)
        if span is None:
            print(f"No hay clips para la radio {args.radio!r}.", file=sys.stderr)
            return 1
        t0, t1 = span
        if args.max_minutes is not None:
            t1 = min(t1, t0 + args.max_minutes * 60)
        instantes = _instantes(t0, t1)
        existentes = {
            row[0] for row in c.execute(
                "SELECT t_start FROM windows WHERE radio=? AND backend=? AND t_start>=? AND t_start<=?",
                (args.radio, args.backend, t0, t1),
            )
        }
        pendientes = [t for t in instantes if t not in existentes]
        print(f"{len(instantes)} ventanas; {len(existentes)} ya estaban guardadas; {len(pendientes)} pendientes.")
        if not pendientes:
            return 0
        backend = embeddings.get_backend(args.backend)
        inicio = time.perf_counter()
        insertadas = 0
        n_muestras = int(round(store.WINDOW_S * store.SAMPLE_RATE))
        for primero in range(0, len(pendientes), TAM_LOTE):
            lote_t = pendientes[primero:primero + TAM_LOTE]
            continuo = audio.load_range(c, args.radio, lote_t[0], lote_t[-1] + store.WINDOW_S)
            lote_pcm = [
                continuo[int(round((t - lote_t[0]) * store.SAMPLE_RATE)):
                         int(round((t - lote_t[0]) * store.SAMPLE_RATE)) + n_muestras]
                for t in lote_t
            ]
            try:
                vectores = backend.embed(lote_pcm)
            except RuntimeError as exc:
                print(f"No se pudieron calcular embeddings: {exc}", file=sys.stderr)
                return 2
            antes = c.total_changes
            c.executemany(
                "INSERT OR IGNORE INTO windows (radio, t_start, backend, emb) VALUES (?, ?, ?, ?)",
                [(args.radio, t, args.backend, store.pack(v)) for t, v in zip(lote_t, vectores)],
            )
            c.commit()
            insertadas += c.total_changes - antes
        transcurrido = time.perf_counter() - inicio
        minutos_audio = len(pendientes) * store.HOP_S / 60
        ritmo = transcurrido / minutos_audio if minutos_audio else 0.0
        print(f"Guardadas {insertadas} ventanas en {transcurrido:.1f} s ({ritmo:.2f} s por minuto de audio).")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
