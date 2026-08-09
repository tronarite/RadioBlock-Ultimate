"""Reagrupa periódicamente todas las ventanas acumuladas por solape de
huella acústica, para ver qué fragmentos suenan repetidos — candidatos a
ser anuncios/cuñas. No hay revisión manual ni etiquetas: este proyecto
existe solo para comprobar, tras dejarlo escuchando mucho tiempo, si la
detección de repetición encuentra los anuncios reales y no genera falsos
positivos con la tertulia.

Las ventanas de análisis se SOLAPAN entre sí (ver worker.py) para que un
mismo fragmento quede siempre contenido entero en alguna ventana, sea
cual sea el instante exacto en que suene. Eso significa que dos ventanas
consecutivas comparten audio real por construcción — no es una
repetición, es la misma ventana desplazada un poco — así que al comparar
huellas se descarta cualquier par de ventanas cuyos rangos de tiempo se
solapen: solo cuenta como repetición real un emparejamiento entre
ventanas que no comparten ni un segundo de audio fuente.

AGRUPACIÓN POR SUB-TRAMOS: si dos anuncios distintos suenan pegados (a
veces A seguido de B, otras veces B seguido de C), una misma ventana de
50s puede contener el final de uno y el principio de otro. Agrupar por
VENTANA ENTERA fusionaría A y B por transitividad en cuanto una ventana
"puente" coincidiera con una aparición limpia de cada uno por separado.
Para evitarlo, cada comparación entre dos ventanas se alinea (ver
`fingerprint.align`) y se acota a la porción exacta que de verdad
coincide — así una ventana puede aportar a DOS grupos distintos (uno por
cada tramo suyo), en vez de arrastrar a todo el grupo consigo. Los
segmentos del formato viejo (sin tiempo por hash) siguen agrupándose por
ventana entera, como antes — solo los del formato nuevo se benefician de
la precisión por sub-tramo.
"""

from __future__ import annotations

import datetime
import threading
import time
from statistics import median

from app.db import get_conn
from app.fingerprint import (
    align,
    bytes_to_fingerprint,
    fingerprint_to_bytes,
    has_timing,
    shared_hash_count,
)

# Cuántas veces tiene que aparecer un fragmento, en momentos distintos y
# separados en el tiempo, antes de considerarlo sospechoso de ser anuncio.
MIN_APARICIONES = 3

# Nº mínimo de hashes que confirman un emparejamiento (alineados al mismo
# desfase si hay información de tiempo en ambos lados; en bruto si no) para
# considerar dos ventanas el mismo audio repetido. Conteo absoluto, no
# proporción — ver fingerprint.shared_hash_count para la justificación y
# la calibración con audio real de Cadena SER (ruido de fondo: decenas de
# hashes; un clip repetido de verdad: miles).
MIN_SHARED_HASHES = 400

MAX_SEGMENTS_PER_HASH = 150

# Dos rangos se consideran "el mismo tramo" (para no crear una región
# nueva por cada comparación) si se solapan al menos esta fracción del
# más corto de los dos.
REGION_OVERLAP_MIN_FRACTION = 0.3

# regroup() recalcula la agrupación desde cero cada vez (no hay forma
# incremental con este enfoque), así que sin límite el coste crece sin
# parar cuantas más horas lleve escuchando. Un anuncio que interesa
# detectar sigue repitiéndose dentro de este plazo, no hace falta mirar
# más atrás — esto mantiene el coste de cada reagrupado acotado en vez de
# degradarse cuanto más tiempo lleve corriendo. 24h de margen para que una
# noche entera de uso no pierda nada, pero sin crecer sin límite si esto
# se deja corriendo días.
REGROUP_LOOKBACK_HOURS = 24

# Segunda pasada: dos grupos que la primera pasada dejó separados (por
# azares de qué ventanas concretas se compararon entre sí — dentro de una
# misma aparición, las ventanas que se solapan en el tiempo nunca se
# comparan entre ellas) pero cuyo contenido sigue coincidiendo, se
# fusionan. El umbral es más laxo que MIN_SHARED_HASHES porque ambos
# grupos ya pasaron el filtro fuerte por separado — lo único que se busca
# aquí es confirmar que de verdad son el mismo patrón, no descartar ruido
# de fondo desde cero.
MIN_SHARED_HASHES_CONSOLIDACION = 150

_lock = threading.Lock()


def _count_apariciones(timestamps: list[datetime.datetime], duracion: float) -> int:
    ordered = sorted(timestamps)
    apariciones = 1
    contiguity_window = 1.5 * (duracion or 10)
    for prev, cur in zip(ordered, ordered[1:]):
        if (cur - prev).total_seconds() > contiguity_window:
            apariciones += 1
    return apariciones


def _overlaps(ts_a: datetime.datetime, ts_b: datetime.datetime, window_seconds: float) -> bool:
    """Dos ventanas de duración `window_seconds` que empiezan en ts_a/ts_b
    comparten audio fuente si sus rangos [ts, ts+window] se cruzan."""
    return abs((ts_a - ts_b).total_seconds()) < window_seconds


def _ranges_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    shorter = min(a[1] - a[0], b[1] - b[0])
    if shorter <= 0:
        return inter > 0
    return inter / shorter >= REGION_OVERLAP_MIN_FRACTION


# Con audio real, cada ventana tiene miles de hashes y muchísimos pares de
# ventanas comparten AL MENOS uno por pura coincidencia (con 400 segmentos
# de Cadena SER, más de 2 millones de pares candidatos, de los que casi
# ninguno acaba siendo una repetición real). Calcular la huella completa
# (o la alineación) para cada uno sería demasiado lento para reagrupar
# cada pocos minutos toda la noche. Antes de pagar ese coste, se cuenta
# barato en cuántos hashes distintos coincide cada par (sin comparar las
# huellas enteras) y solo se hace el cálculo exacto para los que ya
# apuntan a algo — un desfase real comparte miles; los pares que no son
# nada se quedan casi siempre en un puñado.
MIN_VOTOS_PRECHECK = 50

# Emparejar TODOS los pares dentro de cada bucket de hash (como se hacía
# antes) es O(k²) por hash — con horas de audio real acumulado, un hash
# de una cuña que suena constantemente puede aparecer en 100+ ventanas, y
# con miles de esos hashes el coste se dispara a decenas de millones de
# operaciones (medido: 163s para reagrupar 24h de una sola radio, y solo
# empeora cuantas más horas se acumulen). No hace falta emparejar cada
# ventana con TODAS las demás del mismo bucket: para el mismo motivo que
# el fan-out limitado ya funciona al generar los hashes en fingerprint.py
# (no hace falta comparar cada pico con todos los demás, solo con los
# cercanos), aquí basta con emparejar cada aparición de un hash con sus
# pocas vecinas más próximas en el tiempo — una repetición real comparte
# miles de hashes con la ventana correspondiente, así que aunque cada
# hash individual solo "vote" entre vecinos cercanos, sobran votos de
# sobra para llegar al umbral entre las ventanas que de verdad coinciden;
# lo único que se pierde son pares de ventanas SIN ninguna relación real,
# que no interesan.
VOTE_FAN_OUT = 6


def _build_edges(
    n: int, fingerprints: list[dict[int, int]], timestamps: list[datetime.datetime], window_seconds: float
) -> list[tuple[int, int, tuple[float, float], int, tuple[float, float]]]:
    """Para cada par de ventanas que coinciden lo suficiente, calcula
    (fuerza, i, rango_en_i, j, rango_en_j). Si ambas tienen información de
    tiempo por hash, el rango es el tramo exacto compartido (ver
    `fingerprint.align`); si no, el rango es la ventana entera (como en la
    versión anterior, sin precisión por sub-tramo).

    IMPORTANTE: exige que `timestamps` (y por tanto `fingerprints`, que
    van emparejados índice a índice) vengan ya ordenados cronológicamente
    — así los índices dentro de cada bucket de hash salen ya en orden de
    tiempo sin tener que ordenar cada bucket por separado (con cientos de
    miles de buckets distintos en 24h de audio real, ese `sorted()` por
    bucket llegó a costar varios segundos él solo)."""
    ts_epoch = [t.timestamp() for t in timestamps]

    inverted: dict[int, list[int]] = {}
    for i, fp in enumerate(fingerprints):
        for h in fp:
            inverted.setdefault(h, []).append(i)

    votes: dict[tuple[int, int], int] = {}
    for idxs in inverted.values():
        n_idx = len(idxs)
        if n_idx < 2 or n_idx > MAX_SEGMENTS_PER_HASH:
            continue
        # `idxs` ya está en orden cronológico (ver docstring): cada
        # aparición del hash solo se empareja con sus pocas vecinas más
        # próximas en el tiempo, no con TODAS las demás — evita el coste
        # O(k²) por bucket sin perder votos reales (ver VOTE_FAN_OUT).
        for a in range(n_idx):
            i = idxs[a]
            ts_i = ts_epoch[i]
            for b in range(a + 1, min(a + 1 + VOTE_FAN_OUT, n_idx)):
                j = idxs[b]
                if abs(ts_i - ts_epoch[j]) < window_seconds:
                    continue  # comparten audio fuente por construcción, no es una repetición
                pair = (i, j)
                votes[pair] = votes.get(pair, 0) + 1

    edges: list[tuple[int, int, tuple[float, float], int, tuple[float, float]]] = []
    for (i, j), v in votes.items():
        if v < MIN_VOTOS_PRECHECK:
            continue
        if has_timing(fingerprints[i]) and has_timing(fingerprints[j]):
            result = align(fingerprints[i], fingerprints[j])
            if result is None:
                continue
            strength, range_i, range_j = result
        else:
            strength = shared_hash_count(fingerprints[i], fingerprints[j])
            range_i = (0.0, window_seconds)
            range_j = (0.0, window_seconds)

        if strength >= MIN_SHARED_HASHES:
            edges.append((strength, i, range_i, j, range_j))

    edges.sort(key=lambda e: -e[0])
    return edges


def _group_by_region(
    n: int, fingerprints: list[dict[int, int]], timestamps: list[datetime.datetime], window_seconds: float
) -> list[list[tuple[int, float, float]]]:
    """Agrupa por sub-tramos: cada ventana puede aportar a varios grupos
    distintos si distintas partes suyas coinciden con patrones distintos.
    Procesa los emparejamientos de más a menos fuertes, así el tramo
    "canónico" de cada ventana lo fija su mejor evidencia."""
    edges = _build_edges(n, fingerprints, timestamps, window_seconds)

    # idx -> lista de {start, end, group}
    regions: list[list[dict]] = [[] for _ in range(n)]
    group_members: dict[int, list[tuple[int, float, float]]] = {}
    next_group_id = 0

    def find_region(idx: int, rng: tuple[float, float]) -> dict | None:
        for r in regions[idx]:
            if _ranges_overlap((r["start"], r["end"]), rng):
                return r
        return None

    for _strength, i, range_i, j, range_j in edges:
        ri = find_region(i, range_i)
        rj = find_region(j, range_j)

        if ri is None and rj is None:
            gid = next_group_id
            next_group_id += 1
            regions[i].append({"start": range_i[0], "end": range_i[1], "group": gid})
            regions[j].append({"start": range_j[0], "end": range_j[1], "group": gid})
            group_members[gid] = [(i, *range_i), (j, *range_j)]
        elif ri is not None and rj is None:
            gid = ri["group"]
            regions[j].append({"start": range_j[0], "end": range_j[1], "group": gid})
            group_members[gid].append((j, *range_j))
        elif ri is None and rj is not None:
            gid = rj["group"]
            regions[i].append({"start": range_i[0], "end": range_i[1], "group": gid})
            group_members[gid].append((i, *range_i))
        else:
            if ri["group"] != rj["group"]:
                gid_keep, gid_drop = ri["group"], rj["group"]
                for idx in range(n):
                    for r in regions[idx]:
                        if r["group"] == gid_drop:
                            r["group"] = gid_keep
                group_members[gid_keep].extend(group_members.pop(gid_drop))
            # ya son el mismo grupo: esta comparación solo lo refuerza

    group_members = _merge_similar_groups(group_members, fingerprints, timestamps, window_seconds)
    return [members for members in group_members.values() if len(members) > 1]


def _members_match(
    idx_a: int, range_a: tuple[float, float],
    idx_b: int, range_b: tuple[float, float],
    fingerprints: list[dict[int, int]],
    timestamps: list[datetime.datetime],
    window_seconds: float,
) -> bool:
    """Igual que en la primera pasada: se descartan pares de ventanas que
    se solapan en el tiempo fuente (comparten audio real por
    construcción, no es una repetición — el mismo criterio de
    `_build_edges`) y, si ambos lados tienen tiempo por hash, se exige que
    la alineación real caiga DENTRO del tramo que cada miembro
    representa — así una ventana puente (con dos anuncios distintos) no
    puede colar una coincidencia de "su otro tramo" y fundir dos grupos
    que en realidad son anuncios diferentes. Si no hay tiempo (formato
    viejo), se compara la ventana entera como antes."""
    if _overlaps(timestamps[idx_a], timestamps[idx_b], window_seconds):
        return False
    fp_a, fp_b = fingerprints[idx_a], fingerprints[idx_b]
    if has_timing(fp_a) and has_timing(fp_b):
        result = align(fp_a, fp_b)
        if result is None:
            return False
        strength, aligned_a, aligned_b = result
        if strength < MIN_SHARED_HASHES_CONSOLIDACION:
            return False
        return _ranges_overlap(aligned_a, range_a) and _ranges_overlap(aligned_b, range_b)
    return shared_hash_count(fp_a, fp_b) >= MIN_SHARED_HASHES_CONSOLIDACION


def _merge_similar_groups(
    group_members: dict[int, list[tuple[int, float, float]]],
    fingerprints: list[dict[int, int]],
    timestamps: list[datetime.datetime],
    window_seconds: float,
) -> dict[int, list[tuple[int, float, float]]]:
    """Dentro de una misma aparición real, las ventanas que se solapan en
    el tiempo nunca se comparan entre sí (ver `_overlaps`) — así que si
    dos apariciones distintas de UN MISMO anuncio solo llegaron a
    compararse a través de ventanas parciales distintas cada vez, podrían
    quedar en grupos separados aunque sea literalmente el mismo audio.
    Aquí se comparan los miembros de grupos distintos entre sí (con un
    umbral algo más laxo, justificado porque ambos grupos ya pasaron el
    filtro fuerte por separado) y se fusionan los que de verdad coinciden
    — respetando siempre el tramo exacto de cada miembro, nunca la
    ventana entera, para no reintroducir el problema de las ventanas
    puente."""
    gids = list(group_members.keys())
    parent = {gid: gid for gid in gids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for a in range(len(gids)):
        for b in range(a + 1, len(gids)):
            ga, gb = gids[a], gids[b]
            if find(ga) == find(gb):
                continue
            matched = False
            for idx_a, sa, ea in group_members[ga]:
                if matched:
                    break
                for idx_b, sb, eb in group_members[gb]:
                    if _members_match(idx_a, (sa, ea), idx_b, (sb, eb), fingerprints, timestamps, window_seconds):
                        matched = True
                        break
            if matched:
                union(ga, gb)

    merged: dict[int, list[tuple[int, float, float]]] = {}
    for gid in gids:
        merged.setdefault(find(gid), []).extend(group_members[gid])
    return merged


def _estimate_group_boundary(
    representative_idx: int,
    members: list[tuple[int, float, float]],
    fingerprints: list[dict[int, int]],
    timestamps: list[datetime.datetime],
    window_seconds: float,
) -> tuple[float | None, float | None, int]:
    """Compara la ventana representativa DIRECTAMENTE contra cada otra
    aparición del grupo (no basta con mirar las entradas que ya haya en
    `members`: el union-find solo guarda una región por ventana la
    primera vez que se establece, así que la representativa casi siempre
    aparece una única vez ahí aunque el grupo tenga muchas apariciones).
    La mediana de todas esas comparaciones es lo que de verdad mejora con
    cada aparición nueva."""
    other_idx = {idx for idx, _s, _e in members if idx != representative_idx}
    rep_fp = fingerprints[representative_idx]
    starts, ends = [], []
    for idx in other_idx:
        if _overlaps(timestamps[representative_idx], timestamps[idx], window_seconds):
            continue
        result = align(rep_fp, fingerprints[idx])
        if result is not None:
            _strength, range_rep, _range_other = result
            starts.append(range_rep[0])
            ends.append(range_rep[1])
    if not starts:
        return None, None, 0
    return median(starts), median(ends), len(starts)


def regroup(radio: str) -> None:
    with _lock:
        conn = get_conn()
        try:
            cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=REGROUP_LOOKBACK_HOURS)).isoformat()
            rows = conn.execute(
                "SELECT id, timestamp, duracion, fingerprint, archivo_audio FROM segmentos"
                " WHERE radio = ? AND fingerprint IS NOT NULL AND timestamp >= ?"
                " ORDER BY timestamp",
                (radio, cutoff),
            ).fetchall()
            if len(rows) < 6:
                return

            fingerprints = [bytes_to_fingerprint(r["fingerprint"]) for r in rows]
            timestamps = [datetime.datetime.fromisoformat(r["timestamp"]) for r in rows]
            window_seconds = rows[0]["duracion"]
            groups = _group_by_region(len(rows), fingerprints, timestamps, window_seconds)

            conn.execute("UPDATE segmentos SET grupo_id = NULL WHERE radio = ?", (radio,))
            conn.execute("DELETE FROM grupos WHERE radio = ?", (radio,))

            for members in groups:
                # Marca de tiempo real de cada aparición: inicio de la
                # ventana + inicio del tramo dentro de ella. Con esto, dos
                # anuncios distintos que compartan ventana puente quedan
                # con marcas de tiempo propias y no se cuentan como la
                # misma aparición el uno del otro.
                member_real_ts = [
                    timestamps[idx] + datetime.timedelta(seconds=s) for idx, s, _e in members
                ]
                duracion = rows[members[0][0]]["duracion"]
                n_apariciones = _count_apariciones(member_real_ts, duracion)
                if n_apariciones < MIN_APARICIONES:
                    continue

                member_idx_set = {idx for idx, _s, _e in members}

                pattern_fp: dict[int, int] = {}
                for idx in member_idx_set:
                    pattern_fp.update(fingerprints[idx])

                # Representante: preferir uno con información de tiempo
                # (formato nuevo) — si no, nunca se podría estimar el
                # tramo exacto por mucho que lleguen más apariciones,
                # porque la alineación necesita tiempo en ambos lados.
                candidates = [idx for idx in member_idx_set if rows[idx]["archivo_audio"]]
                timed_candidates = [idx for idx in candidates if has_timing(fingerprints[idx])]
                rep_idx = (timed_candidates or candidates or [None])[0]
                representative_id = rows[rep_idx]["id"] if rep_idx is not None else None

                inicio_estimado = fin_estimado = None
                n_estimaciones = 0
                if rep_idx is not None:
                    inicio_estimado, fin_estimado, n_estimaciones = _estimate_group_boundary(
                        rep_idx, members, fingerprints, timestamps, window_seconds
                    )

                cur = conn.execute(
                    "INSERT INTO grupos (radio, fingerprint, n_segmentos, n_apariciones, primera_vez, ultima_vez,"
                    " representative_segment_id, inicio_estimado, fin_estimado, n_estimaciones)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        radio,
                        fingerprint_to_bytes(pattern_fp),
                        len(member_idx_set),
                        n_apariciones,
                        min(member_real_ts).isoformat(),
                        max(member_real_ts).isoformat(),
                        representative_id,
                        inicio_estimado,
                        fin_estimado,
                        n_estimaciones,
                    ),
                )
                grupo_id = cur.lastrowid
                ids = [rows[idx]["id"] for idx in member_idx_set]
                conn.executemany(
                    "UPDATE segmentos SET grupo_id = ? WHERE id = ?",
                    [(grupo_id, seg_id) for seg_id in ids],
                )

            conn.commit()
        finally:
            conn.close()


# worker.py llama a regroup_async() cada vez que llega un segmento nuevo
# (cada ~20s por radio). Con pocas horas acumuladas regroup() es rápido y
# eso no importa, pero con muchas horas de audio real acumuladas puede
# tardar bastante más que 20s en completarse — y sin ningún control, cada
# llamada lanza un hilo NUEVO que se queda esperando a `_lock` aunque el
# anterior siga corriendo. Una noche real esto apiló 54 hilos en cola,
# saturó la CPU y acabó bloqueando incluso la lectura del propio stream
# de audio (sin ningún error visible en los logs: ffmpeg simplemente se
# quedó esperando a que alguien vaciara la tubería). Este guard evita
# apilar hilos (nunca hay más de uno corriendo ni esperando por radio) y
# además evita relanzar más a menudo de lo necesario — perder unos
# segundos de margen en cuándo se ve reflejado el último segmento es
# preferible a arriesgarse otra vez a un apilamiento como ese.
MIN_REGROUP_INTERVAL_SECONDS = 45

_regroup_state_lock = threading.Lock()
_regroup_running: set[str] = set()
_last_regroup_start: dict[str, float] = {}


def regroup_async(radio: str) -> None:
    now = time.monotonic()
    with _regroup_state_lock:
        if radio in _regroup_running:
            return
        if now - _last_regroup_start.get(radio, 0.0) < MIN_REGROUP_INTERVAL_SECONDS:
            return
        _regroup_running.add(radio)
        _last_regroup_start[radio] = now
    threading.Thread(target=_regroup_then_clear, args=(radio,), daemon=True).start()


def _regroup_then_clear(radio: str) -> None:
    try:
        regroup(radio)
    finally:
        with _regroup_state_lock:
            _regroup_running.discard(radio)
