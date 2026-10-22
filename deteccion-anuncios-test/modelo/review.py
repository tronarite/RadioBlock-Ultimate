"""Revision humana de clips para el aprendizaje activo del modelo."""

from __future__ import annotations

import argparse
import random
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from store import clip_path, conn


def _minuto(row) -> str:
    """Devuelve radio y minuto para no encadenar fragmentos muy parecidos."""
    return f"{row['radio']}\x00{row['timestamp'][:16]}"


def _aleatorios_con_repetidos(rows):
    """Baraja favoreciendo los clips que se repiten en distintas emisiones."""
    def clave(row):
        peso = max(0, row["repeat_count"] or 0) + 1
        return random.random() ** (1 / peso)

    return sorted(rows, key=clave, reverse=True)


def _sin_consecutivos(rows):
    """Reordena una tanda para separar la misma radio y minuto cuando se pueda."""
    pendientes = list(rows)
    resultado = []
    anterior = None

    while pendientes:
        indice = next(
            (i for i, row in enumerate(pendientes) if _minuto(row) != anterior),
            0,
        )
        fila = pendientes.pop(indice)
        resultado.append(fila)
        anterior = _minuto(fila)
    return resultado


def seleccionar_tanda(c, cantidad: int):
    """Elige clips sin revisar combinando incertidumbre, positivos y exploracion."""
    filas = c.execute(
        """
        SELECT id, radio, timestamp, path, repeat_count, score, label, labeled_at
        FROM clips
        WHERE label IS NULL
        """
    ).fetchall()
    if not filas or cantidad <= 0:
        return []

    cantidad = min(cantidad, len(filas))
    con_score = [fila for fila in filas if fila["score"] is not None]
    aleatorios = _aleatorios_con_repetidos(filas)

    # Si el modelo aun no ha puntuado clips, la exploracion prioriza repeticiones.
    if not con_score:
        return _sin_consecutivos(aleatorios[:cantidad])

    incertidumbre = sorted(
        con_score, key=lambda fila: abs(fila["score"] - 0.5))
    anuncios_probables = sorted(con_score, key=lambda fila: fila["score"], reverse=True)
    cuotas = (round(cantidad * 0.60), round(cantidad * 0.20))

    elegidos = []
    usados = set()

    def agregar(candidatos, limite):
        for fila in candidatos:
            if len(elegidos) >= cantidad or limite <= 0:
                break
            if fila["id"] not in usados:
                elegidos.append(fila)
                usados.add(fila["id"])
                limite -= 1

    agregar(incertidumbre, cuotas[0])
    agregar(anuncios_probables, cuotas[1])
    agregar(aleatorios, cantidad - len(elegidos))

    # Puede faltar alguna cuota si hay pocos clips con score.
    agregar(incertidumbre, cantidad - len(elegidos))
    agregar(anuncios_probables, cantidad - len(elegidos))
    return _sin_consecutivos(elegidos)


def mostrar_estadisticas(c) -> None:
    total = c.execute(
        """
        SELECT
            SUM(CASE WHEN label = 1 THEN 1 ELSE 0 END) AS anuncios,
            SUM(CASE WHEN label = 0 THEN 1 ELSE 0 END) AS no_anuncios
        FROM clips
        """
    ).fetchone()
    print(f"Etiquetas: anuncio={total['anuncios'] or 0}, no anuncio={total['no_anuncios'] or 0}")
    print("Por radio:")
    for fila in c.execute(
        """
        SELECT radio,
               SUM(CASE WHEN label = 1 THEN 1 ELSE 0 END) AS anuncios,
               SUM(CASE WHEN label = 0 THEN 1 ELSE 0 END) AS no_anuncios
        FROM clips
        GROUP BY radio
        ORDER BY radio
        """
    ):
        print(f"  {fila['radio']}: anuncio={fila['anuncios'] or 0}, no anuncio={fila['no_anuncios'] or 0}")


def reproducir(ruta: Path, ffplay: str | None) -> None:
    if ffplay is None:
        return
    if not ruta.exists():
        print(f"Audio no encontrado: {ruta}")
        return
    try:
        subprocess.run(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(ruta)],
            check=False,
        )
    except OSError as error:
        print(f"No se pudo reproducir el audio: {error}")


def leer_tecla() -> str:
    """Lee una tecla sin requerir Enter en Windows."""
    try:
        import msvcrt
    except ImportError:
        return input("> ").strip()[:1].lower()
    print("> ", end="", flush=True)
    return msvcrt.getwch().lower()


def guardar_etiqueta(c, clip_id: int, etiqueta: int | None) -> None:
    fecha = datetime.now().astimezone().isoformat(timespec="seconds") if etiqueta is not None else None
    c.execute(
        "UPDATE clips SET label = ?, labeled_at = ? WHERE id = ?",
        (etiqueta, fecha, clip_id),
    )
    c.commit()


def revisar(c, cantidad: int) -> None:
    tanda = seleccionar_tanda(c, cantidad)
    if not tanda:
        print("No hay clips sin revisar.")
        return

    ffplay = shutil.which("ffplay")
    if ffplay is None:
        print("Aviso: no se encontro ffplay; se mostraran los clips sin reproducirlos.")

    historial = []
    for posicion, fila in enumerate(tanda, start=1):
        while True:
            score = "sin score" if fila["score"] is None else f"{fila['score']:.3f}"
            repetidos = fila["repeat_count"] or 0
            print(
                f"\n[{posicion}/{len(tanda)}] radio={fila['radio']} hora={fila['timestamp']} "
                f"score={score} repeat_count={repetidos}"
            )
            reproducir(clip_path(fila), ffplay)
            print("a=anuncio n=no anuncio r=repetir s=saltar u=deshacer ultima q=salir")
            tecla = leer_tecla()
            print(tecla)

            if tecla == "r":
                continue
            if tecla == "a" or tecla == "n":
                etiqueta = 1 if tecla == "a" else 0
                guardar_etiqueta(c, fila["id"], etiqueta)
                historial.append(fila["id"])
                break
            if tecla == "s":
                break
            if tecla == "u":
                if historial:
                    clip_id = historial.pop()
                    guardar_etiqueta(c, clip_id, None)
                    print(f"Deshecha la etiqueta del clip {clip_id}.")
                else:
                    print("No hay etiquetas de esta sesion para deshacer.")
                continue
            if tecla == "q":
                return
            print("Tecla no valida.")


def argumentos():
    parser = argparse.ArgumentParser(description="Revision humana de clips de radio.")
    parser.add_argument("--n", type=int, default=10, help="tamano de la tanda (por defecto: 10)")
    parser.add_argument("--stats", action="store_true", help="muestra las etiquetas existentes")
    return parser.parse_args()


def main() -> None:
    args = argumentos()
    if args.n < 1:
        print("--n debe ser al menos 1.", file=sys.stderr)
        raise SystemExit(2)
    with conn() as c:
        if args.stats:
            mostrar_estadisticas(c)
            return
        revisar(c, args.n)


if __name__ == "__main__":
    main()
