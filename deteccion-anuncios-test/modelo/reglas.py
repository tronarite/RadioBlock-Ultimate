"""Reglas de silenciado: de las detecciones a los tramos que salen mudos.

Funciones puras (sin audio, sin BD, sin reloj): las usa el servicio en directo y las
prueba pruebas/simular_reglas.py sobre las detecciones guardadas. Si se cambia una regla
o una constante, repetir esa simulacion y anotar el resultado en docs/GUIA_IA.md.

Clases de una entrada del catalogo (ver clase()):
  A = anuncio    (estado 'activo', confirmado 1)
  P = programa   (estado 'lista_negra'): sintonias, promos de la emisora, cortes de voz...
  N = pendiente  (estado 'activo', confirmado 0): nueva o dudosa, aun sin veredicto firme

Reglas, en este orden:
  1. Todo tramo A sale mudo.
  2. Relleno: el hueco entre dos tramos A de hasta RELLENO_S sale mudo, suene lo que suene
     (una cuna que aun no esta en el catalogo, o una promo encajada entre dos anuncios).
  3. Puente: un hueco mayor, de hasta PUENTE_MAX_S, sale mudo solo si esta ocupado por
     entradas conocidas (P o N) sin dejar restos de mas de PEGADO_S sin explicar.
  4. Borde: una N que toca (a PEGADO_S o menos) un tramo mudo sale muda con el. Un solo
     salto (no se encadena) y nunca por encima de una P: lo que se sabe que es radio solo
     se silencia si queda entre dos anuncios (reglas 2 y 3).
  5. limitar(): ningun tramo mudo dura mas de MAX_MUDO_S; lo que pase vuelve a sonar.
"""
from __future__ import annotations

import math

RELLENO_S = 20.0
PEGADO_S = 5.0
PUENTE_MAX_S = 40.0     # debe caber en el retraso del servicio: PUENTE_MAX_S + ventana + salto <= retraso
MAX_MUDO_S = 480.0

Tramo = tuple[float, float]


def clase(estado: str, confirmado: int) -> str | None:
    """Clase de una fila de `catalog`; None si no cuenta (caducado)."""
    if estado == "lista_negra":
        return "P"
    if estado == "activo":
        return "A" if confirmado else "N"
    return None


def unir(tramos) -> list[list[float]]:
    """Union de intervalos: ordenados y sin solapes."""
    out: list[list[float]] = []
    for a, b in sorted(tramos):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def restar(x, y) -> list[list[float]]:
    """Lo que queda de los intervalos x al quitarles los de y."""
    y = unir(y)
    res: list[list[float]] = []
    for a, b in unir(x):
        p = a
        for ya, yb in y:
            if yb <= p or ya >= b:
                continue
            if ya > p:
                res.append([p, ya])
            p = max(p, yb)
        if b > p:
            res.append([p, b])
    return res


def planificar(dets, relleno: float = RELLENO_S, pegado: float = PEGADO_S,
               puente: float = PUENTE_MAX_S, borde: bool = True) -> list[Tramo]:
    """dets: iterable de (ini, fin, clase) en segundos, clase 'A' | 'P' | 'N'.
    Devuelve los tramos mudos, ordenados y sin solapes (sin aplicar limitar())."""
    dets = list(dets)
    A = unir((d[0], d[1]) for d in dets if d[2] == "A")
    if not A:
        return []
    P = [(d[0], d[1]) for d in dets if d[2] == "P"]
    N = [(d[0], d[1]) for d in dets if d[2] == "N"]
    conocido = unir(P + N)
    mudo = [A[0][:]]
    for a, b in A[1:]:
        h0 = mudo[-1][1]
        hueco = a - h0
        ok = hueco <= relleno
        if not ok and hueco <= puente and conocido:
            ok = all(y - x <= pegado for x, y in restar([[h0, a]], conocido))
        if ok:
            mudo[-1][1] = max(mudo[-1][1], b)
        else:
            mudo.append([a, b])
    if borde:
        extra: list[list[float]] = []
        for n in restar(N, P):
            for m in mudo:
                if n[0] <= m[1] + pegado and n[1] >= m[0] - pegado:
                    extra.append(n)
                    # el trocito que queda entre los dos tambien sale mudo, salvo lo que sea P
                    if n[0] > m[1]:
                        extra += restar([[m[1], n[0]]], P)
                    elif n[1] < m[0]:
                        extra += restar([[n[1], m[0]]], P)
        mudo = unir(mudo + extra)
    return [(a, b) for a, b in mudo]


def reloj(tiempos: list[float]) -> float | None:
    """Concentracion (0..1) del minuto de la hora en que suena; None con menos de 3 emisiones."""
    if len(tiempos) < 3:
        return None
    f = [(t % 3600) / 3600 * 2 * math.pi for t in tiempos]
    return math.hypot(sum(map(math.cos, f)), sum(map(math.sin, f))) / len(f)


def limitar(tramos, max_s: float = MAX_MUDO_S) -> list[Tramo]:
    """Recorta cada tramo a max_s: pasado ese tiempo vuelve el sonido (seguro contra un fallo)."""
    return [(a, min(b, a + max_s)) for a, b in tramos]


def _autoprueba() -> None:
    ig = lambda x, y: [(round(a, 3), round(b, 3)) for a, b in x] == y
    assert ig(planificar([]), [])
    assert ig(planificar([(0, 10, "N"), (20, 30, "P")]), [])                          # sin anuncios no hay silencio
    assert ig(planificar([(0, 10, "A"), (25, 35, "A")]), [(0, 35)])                   # relleno: hueco de 15 s
    assert ig(planificar([(0, 10, "A"), (31, 40, "A")]), [(0, 10), (31, 40)])         # hueco de 21 s sin explicar
    assert ig(planificar([(0, 10, "A"), (12, 28, "P"), (31, 40, "A")]), [(0, 40)])    # promo entre dos anuncios
    assert ig(planificar([(0, 10, "A"), (12, 20, "P"), (31, 40, "A")]), [(0, 10), (31, 40)])  # resto de 11 s
    assert ig(planificar([(0, 10, "A"), (12, 30, "N")]), [(0, 30)])                   # borde: pendiente pegada
    assert ig(planificar([(0, 10, "A"), (12, 30, "N"), (32, 50, "N")]), [(0, 30)])    # un solo salto
    assert ig(planificar([(0, 10, "A"), (12, 30, "N"), (20, 30, "P")]), [(0, 20)])    # nunca por encima de una P
    assert ig(planificar([(0, 10, "A"), (12, 30, "P")]), [(0, 10)])                   # P en el borde: suena
    assert ig(planificar([(0, 10, "A"), (17, 30, "N")]), [(0, 10)])                   # pendiente a 7 s: no toca
    assert ig(limitar([(0, 1000), (2000, 2010)]), [(0, 480), (2000, 2010)])
    print("reglas: autoprueba correcta")


if __name__ == "__main__":
    _autoprueba()
