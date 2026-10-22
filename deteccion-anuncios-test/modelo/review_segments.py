"""Revision por terminal de tramos candidatos de anuncios."""

from __future__ import annotations

import argparse
import os
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import audio
import store


MARGEN_S = 3.0


def _clave_grupo(fila) -> str:
    """Los tramos sin grupo no se consideran entre si repetidos."""
    return fila["group_key"] or f"__sin_grupo_{fila['id']}"


def _cuotas(cantidad: int) -> tuple[int, int, int]:
    """Reparte la tanda lo mas fielmente posible en 50/30/20."""
    pesos = (0.50, 0.30, 0.20)
    base = [int(cantidad * peso) for peso in pesos]
    restantes = cantidad - sum(base)
    orden = sorted(range(3), key=lambda i: cantidad * pesos[i] - base[i], reverse=True)
    for indice in orden[:restantes]:
        base[indice] += 1
    return tuple(base)


def _sin_grupos_consecutivos(filas):
    """Ordena y, si no hay alternativa, descarta repeticiones consecutivas."""
    pendientes = list(filas)
    resultado = []
    anterior = None
    while pendientes:
        indice = next(
            (i for i, fila in enumerate(pendientes) if _clave_grupo(fila) != anterior),
            None,
        )
        if indice is None:
            break
        fila = pendientes.pop(indice)
        resultado.append(fila)
        anterior = _clave_grupo(fila)
    return resultado


def seleccionar_tanda(c, cantidad: int):
    """Mezcla incertidumbre de modelo, repeticiones y exploracion aleatoria."""
    filas = c.execute(
        """
        SELECT id, radio, t_start, t_end, source, group_key, score, n_repeats,
               label, labeled_at, t_start_user, t_end_user
        FROM segments
        WHERE label IS NULL
        """
    ).fetchall()
    if cantidad <= 0 or not filas:
        return []

    cantidad = min(cantidad, len(filas))
    modelo = [fila for fila in filas if fila["source"] == "model" and fila["score"] is not None]
    inciertos = sorted(modelo, key=lambda fila: abs(fila["score"] - 0.5))
    repetidos = sorted(
        (fila for fila in filas if fila["source"] == "repeat"),
        key=lambda fila: fila["n_repeats"] or 0,
        reverse=True,
    )
    aleatorios = list(filas)
    random.shuffle(aleatorios)

    elegidos = []
    usados = set()

    def agregar(candidatos, limite):
        for fila in candidatos:
            if limite <= 0 or len(elegidos) >= cantidad:
                break
            if fila["id"] not in usados:
                elegidos.append(fila)
                usados.add(fila["id"])
                limite -= 1

    cuota_inciertos, cuota_repetidos, cuota_aleatorios = _cuotas(cantidad)
    agregar(inciertos, cuota_inciertos)
    agregar(repetidos, cuota_repetidos)
    agregar(aleatorios, cuota_aleatorios)

    # Si una fuente no tenia suficientes filas, completa con las otras fuentes.
    agregar(inciertos, cantidad - len(elegidos))
    agregar(repetidos, cantidad - len(elegidos))
    agregar(aleatorios, cantidad - len(elegidos))
    return _sin_grupos_consecutivos(elegidos)


def buscar_ffplay() -> str | None:
    """Localiza ffplay en PATH o en la instalacion habitual de WinGet."""
    encontrado = shutil.which("ffplay")
    if encontrado:
        return encontrado

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    paquetes = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if not paquetes.is_dir():
        return None
    for paquete in paquetes.glob("Gyan.FFmpeg*"):
        for candidato in paquete.rglob("ffplay.exe"):
            if candidato.parent.name.lower() == "bin":
                return str(candidato)
    return None


def _hora(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _bordes(fila) -> tuple[float, float]:
    return (
        fila["t_start_user"] if fila["t_start_user"] is not None else fila["t_start"],
        fila["t_end_user"] if fila["t_end_user"] is not None else fila["t_end"],
    )


def reproducir(c, fila, inicio: float, fin: float, ffplay: str | None) -> None:
    """Extrae el tramo con contexto y lo entrega a ffplay mediante un WAV temporal."""
    if ffplay is None:
        return
    descriptor, nombre = tempfile.mkstemp(prefix="review_segment_", suffix=".wav")
    os.close(descriptor)
    ruta = Path(nombre)
    try:
        pcm = audio.load_range(c, fila["radio"], inicio - MARGEN_S, fin + MARGEN_S)
        audio.write_wav(ruta, pcm)
        subprocess.run(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(ruta)],
            check=False,
        )
    except OSError as error:
        print(f"No se pudo reproducir el audio: {error}")
    finally:
        try:
            ruta.unlink(missing_ok=True)
        except OSError:
            pass


def guardar_bordes(c, segmento_id: int, inicio: float, fin: float) -> None:
    c.execute(
        "UPDATE segments SET t_start_user=?, t_end_user=? WHERE id=?",
        (inicio, fin, segmento_id),
    )
    c.commit()


def guardar_etiqueta(c, fila, etiqueta: int, propagar: bool):
    """Guarda una accion y devuelve su estado previo para poder deshacerla."""
    fecha = datetime.now().astimezone().isoformat(timespec="seconds")
    objetivos = [fila]
    if propagar and fila["group_key"]:
        objetivos = c.execute(
            "SELECT id, label, labeled_at FROM segments WHERE group_key=?",
            (fila["group_key"],),
        ).fetchall()
    anterior = [(x["id"], x["label"], x["labeled_at"]) for x in objetivos]
    c.executemany(
        "UPDATE segments SET label=?, labeled_at=? WHERE id=?",
        [(etiqueta, fecha, x[0]) for x in anterior],
    )
    c.commit()
    return anterior


def deshacer(c, accion) -> None:
    c.executemany(
        "UPDATE segments SET label=?, labeled_at=? WHERE id=?",
        [(label, fecha, segmento_id) for segmento_id, label, fecha in accion],
    )
    c.commit()


def leer_tecla() -> str:
    """Lee una tecla sin Enter, conservando las mayusculas para propagar."""
    try:
        import msvcrt
    except ImportError:
        return input("> ").strip()[:1]
    print("> ", end="", flush=True)
    return msvcrt.getwch()


def _mostrar(fila, inicio: float, fin: float, posicion: int, total: int) -> None:
    score = "sin score" if fila["score"] is None else f"{fila['score']:.3f}"
    print(
        f"\n[{posicion}/{total}] radio={fila['radio']} hora={_hora(inicio)} "
        f"duracion={fin - inicio:.1f}s origen={fila['source']} "
        f"n_repeats={fila['n_repeats'] or 0} score={score}"
    )


def revisar(c, cantidad: int) -> None:
    tanda = seleccionar_tanda(c, cantidad)
    if not tanda:
        print("No hay tramos sin revisar.")
        return

    ffplay = buscar_ffplay()
    if ffplay is None:
        print("Aviso: no se encontro ffplay; se mostraran los tramos sin reproducirlos.")

    historial = []
    for posicion, fila in enumerate(tanda, start=1):
        inicio, fin = _bordes(fila)
        while True:
            _mostrar(fila, inicio, fin, posicion, len(tanda))
            reproducir(c, fila, inicio, fin, ffplay)
            print("a=anuncio n=no anuncio A/N=etiquetar y propagar r=repetir s=saltar")
            print("u=deshacer [/] inicio -/+1s ,/. fin -/+1s q=salir")
            tecla = leer_tecla()
            print(tecla)

            if tecla == "r":
                continue
            if tecla in ("a", "n", "A", "N"):
                etiqueta = 1 if tecla.lower() == "a" else 0
                historial.append(guardar_etiqueta(c, fila, etiqueta, tecla.isupper()))
                break
            if tecla == "s":
                break
            if tecla == "u":
                if historial:
                    deshacer(c, historial.pop())
                    print("Deshecha la ultima etiqueta de esta sesion.")
                else:
                    print("No hay etiquetas de esta sesion para deshacer.")
                continue
            if tecla == "[":
                if inicio - 1 < fin:
                    inicio -= 1
                    guardar_bordes(c, fila["id"], inicio, fin)
                else:
                    print("El inicio no puede alcanzar el final.")
                continue
            if tecla == "]":
                if inicio + 1 < fin:
                    inicio += 1
                    guardar_bordes(c, fila["id"], inicio, fin)
                else:
                    print("El inicio no puede alcanzar el final.")
                continue
            if tecla == ",":
                if fin - 1 > inicio:
                    fin -= 1
                    guardar_bordes(c, fila["id"], inicio, fin)
                else:
                    print("El final no puede alcanzar el inicio.")
                continue
            if tecla == ".":
                fin += 1
                guardar_bordes(c, fila["id"], inicio, fin)
                continue
            if tecla == "q":
                return
            print("Tecla no valida.")


def mostrar_estadisticas(c) -> None:
    filas = c.execute(
        """
        SELECT source, radio,
               SUM(CASE WHEN label=1 THEN 1 ELSE 0 END) AS anuncios,
               SUM(CASE WHEN label=0 THEN 1 ELSE 0 END) AS no_anuncios
        FROM segments
        GROUP BY source, radio
        ORDER BY source, radio
        """
    ).fetchall()
    if not filas:
        print("No hay tramos ni etiquetas.")
        return
    print("Etiquetas por origen y radio:")
    for fila in filas:
        print(
            f"  origen={fila['source']} radio={fila['radio']}: "
            f"anuncio={fila['anuncios'] or 0}, no anuncio={fila['no_anuncios'] or 0}"
        )


def argumentos():
    parser = argparse.ArgumentParser(description="Revision humana de tramos de radio.")
    parser.add_argument("--n", type=int, default=10, help="tamano de la tanda (por defecto: 10)")
    parser.add_argument("--stats", action="store_true", help="muestra etiquetas por origen y radio")
    return parser.parse_args()


def main() -> None:
    args = argumentos()
    if args.n < 1:
        print("--n debe ser al menos 1.", file=sys.stderr)
        raise SystemExit(2)
    with store.conn2() as c:
        if args.stats:
            mostrar_estadisticas(c)
        else:
            revisar(c, args.n)


if __name__ == "__main__":
    main()
