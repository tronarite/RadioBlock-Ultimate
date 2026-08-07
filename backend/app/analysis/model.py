"""Motor de detección de patrones repetidos por emisora.

Un patrón (`Cluster`) es un grupo de segmentos que comparten la misma
huella acústica (ver `analysis/fingerprint.py`): literalmente el mismo
audio sonando varias veces (una cuña, un anuncio), no solo "algo que suena
parecido". La primera versión de este motor usaba clustering por MFCC medio
+ HDBSCAN, pero esa señal solo capta timbre general — agrupaba tramos de
una misma tertulia larga como si fueran repeticiones, porque cualquier
locutor suena parecido a sí mismo de un minuto a otro. La huella acústica
no tiene ese problema: exige coincidencia real de contenido (mismos picos
de espectrograma en las mismas posiciones relativas).

El reentrenamiento agrupa segmentos por solape de huella (unión-búsqueda
sobre un índice invertido hash -> segmentos, para no comparar todos los
pares) y solo propone como patrón los grupos que aparecen MIN_APARICIONES
veces separadas en el tiempo — así una racha larga de segmentos
consecutivos del mismo audio (p.ej. una cuña de 40s partida en dos
segmentos de 20s) cuenta como una sola aparición, no dos.

Se dispara periódicamente desde el worker conforme llega audio nuevo (ver
`retrain_async` y `worker/worker.py`), no solo cuando el usuario etiqueta
algo — así el sistema puede proponer patrones nuevos sin ninguna acción
manual previa. Se ejecuta en background para no bloquear el proxy.
"""

import threading

from sqlalchemy.orm import Session

from app.analysis.fingerprint import (
    bytes_to_fingerprint,
    fingerprint_to_bytes,
    similarity,
)
from app.db.models import Cluster, Segmento

MIN_SEGMENTS_TO_CLUSTER = 6

# Cuántas veces tiene que APARECER un patrón, en momentos distintos y
# separados en el tiempo, antes de proponerlo para revisión. Un tramo
# continuo (p.ej. una cuña de 40s trozeada en segmentos de 20s) es UNA sola
# aparición aunque genere varios segmentos con huella coincidente entre sí
# — eso no es repetición, es continuidad, y no debe contar como patrón.
MIN_APARICIONES = 3

# Solape mínimo de huella para considerar que dos segmentos son el mismo
# audio repetido (1.0 = uno contiene al otro por completo). Con huella real
# (picos de espectrograma), el mismo clip da un solape muy alto (típicamente
# >0.6); contenido distinto da prácticamente 0 — no hace falta un umbral fino.
SIMILARITY_THRESHOLD = 0.4

# Ignora hashes que aparecen en demasiados segmentos: no identifican un
# clip concreto (silencio, ruido de fondo genérico), solo generan
# comparaciones caras sin aportar señal.
MAX_SEGMENTS_PER_HASH = 30

_lock = threading.Lock()
# radio_id -> {"clusters": [(cluster_id, label|None, fingerprint set), ...]}
_cache: dict[int, dict] = {}


def _majority_label(label_usuarios: list[str | None]) -> str | None:
    votes = [l for l in label_usuarios if l]
    if not votes:
        return None
    return max(set(votes), key=votes.count)


def _count_apariciones(member_segs: list) -> int:
    """Cuenta apariciones distintas de un patrón, colapsando en una sola
    aparición las rachas de segmentos consecutivos (mismo tramo continuo de
    audio). Dos miembros se consideran parte de la MISMA aparición si están
    separados por poco más que la duración de un segmento; si hay un hueco
    mayor, es que el patrón desapareció y volvió a sonar más tarde: eso sí
    es una repetición real."""
    ordered = sorted(member_segs, key=lambda s: s.timestamp)
    apariciones = 1
    for prev, cur in zip(ordered, ordered[1:]):
        gap = (cur.timestamp - prev.timestamp).total_seconds()
        contiguity_window = 1.5 * (prev.duracion or 10)
        if gap > contiguity_window:
            apariciones += 1
    return apariciones


def _group_by_fingerprint(n: int, fingerprints: list[set[int]]) -> list[list[int]]:
    """Agrupa índices de segmentos cuya huella coincide, vía unión-búsqueda
    sobre un índice invertido hash -> segmentos (evita comparar todos los
    pares posibles). Descarta grupos de tamaño 1 (huella que no coincide
    con ningún otro segmento: ruido, sin patrón)."""
    inverted: dict[int, list[int]] = {}
    for i, fp in enumerate(fingerprints):
        for h in fp:
            inverted.setdefault(h, []).append(i)

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    checked: set[tuple[int, int]] = set()
    for idxs in inverted.values():
        if len(idxs) < 2 or len(idxs) > MAX_SEGMENTS_PER_HASH:
            continue
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                pair = (idxs[a], idxs[b])
                if pair in checked:
                    continue
                checked.add(pair)
                if similarity(fingerprints[idxs[a]], fingerprints[idxs[b]]) >= SIMILARITY_THRESHOLD:
                    union(idxs[a], idxs[b])

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [members for members in groups.values() if len(members) > 1]


def retrain_async(radio_id: int, session_factory) -> None:
    """Lanza `retrain` en un hilo aparte para no bloquear al llamador
    (ni el bucle del worker, ni la request HTTP que etiquetó algo)."""

    def _run():
        db = session_factory()
        try:
            retrain(db, radio_id)
        finally:
            db.close()

    threading.Thread(target=_run, daemon=True).start()


def retrain(db: Session, radio_id: int) -> None:
    """Recalcula los patrones de una emisora por solape de huella acústica
    y persiste clusters/segmentos."""
    segmentos = (
        db.query(Segmento)
        .filter(Segmento.radio_id == radio_id, Segmento.fingerprint.isnot(None))
        .all()
    )

    with _lock:
        if len(segmentos) < MIN_SEGMENTS_TO_CLUSTER:
            _cache.pop(radio_id, None)
            return

        fingerprints = [bytes_to_fingerprint(s.fingerprint) for s in segmentos]

        # Antes de borrar los clusters viejos, guardamos su huella y etiqueta:
        # si un patrón ya revisado por el usuario cambia ligeramente de
        # miembros de una pasada a otra (un segmento límite entra o sale),
        # esto permite reconocerlo igualmente y no perder la revisión ya
        # hecha (que volvería a pedirse en el panel si no).
        old_clusters = db.query(Cluster).filter(Cluster.radio_id == radio_id).all()
        old_labeled = [
            (c.label, bytes_to_fingerprint(c.fingerprint)) for c in old_clusters if c.label
        ]

        for c in old_clusters:
            for s in c.segmentos:
                s.cluster_id = None
        for c in old_clusters:
            db.delete(c)
        db.flush()

        groups = _group_by_fingerprint(len(segmentos), fingerprints)
        grouped_idx = {i for g in groups for i in g}

        # Un segmento sin ninguna repetición todavía, pero que el usuario ya
        # marcó explícitamente en directo (ver `RadioWorker.mark_recent`), se
        # trata como un grupo de un solo miembro: la verificación del usuario
        # ya ha ocurrido, así que no hace falta esperar a que se repita para
        # empezar a reconocerlo (y no perder esa marca en el próximo reentreno).
        for i, s in enumerate(segmentos):
            if i not in grouped_idx and s.label_usuario:
                groups.append([i])
                grouped_idx.add(i)

        # El resto de segmentos sin ninguna coincidencia ni marca manual: sin
        # patrón, pasan sin silenciar.
        for i, s in enumerate(segmentos):
            if i not in grouped_idx:
                s.cluster_id = None
                s.label = "desconocido"
                s.confidence = 0.0

        cache_clusters: list[tuple[int, str | None, set[int]]] = []

        for member_idx in groups:
            member_segs = [segmentos[i] for i in member_idx]
            n_apariciones = _count_apariciones(member_segs)
            user_label = _majority_label([s.label_usuario for s in member_segs])

            if user_label is None and n_apariciones < MIN_APARICIONES:
                # Nadie lo ha revisado todavía, y ha sonado menos veces de
                # las necesarias para proponerlo como patrón.
                for s in member_segs:
                    s.cluster_id = None
                    s.label = "desconocido"
                    s.confidence = 0.0
                continue

            pattern_fp: set[int] = set()
            for i in member_idx:
                pattern_fp |= fingerprints[i]

            if user_label is None:
                for old_label, old_fp in old_labeled:
                    if similarity(pattern_fp, old_fp) >= SIMILARITY_THRESHOLD:
                        user_label = old_label
                        break

            cluster = Cluster(
                radio_id=radio_id,
                label=user_label,
                fingerprint=fingerprint_to_bytes(pattern_fp),
                n_segmentos=len(member_segs),
                n_apariciones=n_apariciones,
            )
            db.add(cluster)
            db.flush()

            # Segmento representativo: cualquiera del grupo con audio guardado
            # en disco vale — todos son (casi) el mismo clip. Solo se guarda
            # el audio de los segmentos "desconocido" al ingerirlos, ver
            # worker.py.
            candidates = [s for s in member_segs if s.archivo_audio]
            if candidates:
                cluster.representative_segment_id = candidates[0].id

            resolved_label = user_label or "desconocido"
            for i, s in zip(member_idx, member_segs):
                s.cluster_id = cluster.id
                s.confidence = similarity(fingerprints[i], pattern_fp)
                s.label = resolved_label

            cache_clusters.append((cluster.id, user_label, pattern_fp))

        db.commit()
        _cache[radio_id] = {"clusters": cache_clusters}


def predict(radio_id: int, seg_fingerprint: set[int], threshold: float) -> tuple[str, float]:
    """Compara la huella de un segmento nuevo contra los patrones ya
    detectados para esa emisora.

    Devuelve (label, confidence). label es "desconocido" si no coincide con
    ningún patrón ya revisado, o la etiqueta que el usuario le dio a ese
    patrón ("anuncio", "contenido", "ignorado", ...).
    """
    with _lock:
        entry = _cache.get(radio_id)

    if entry is None or not seg_fingerprint:
        return "desconocido", 0.0

    best_label: str | None = None
    best_sim = 0.0
    for _cluster_id, label, pattern_fp in entry["clusters"]:
        if not label:
            continue  # patrón sin revisar todavía: no se aplica automáticamente
        sim = similarity(seg_fingerprint, pattern_fp)
        if sim > best_sim:
            best_sim, best_label = sim, label

    if best_label and best_sim >= threshold:
        return best_label, best_sim
    return "desconocido", best_sim


def is_ready(radio_id: int) -> bool:
    with _lock:
        return radio_id in _cache


def update_cluster_label_cache(radio_id: int, cluster_id: int, label: str | None) -> None:
    """Actualiza la etiqueta de un cluster en la caché en memoria sin esperar
    al próximo reentrenamiento (usado cuando el usuario reetiqueta desde el
    panel)."""
    with _lock:
        entry = _cache.get(radio_id)
        if entry is None:
            return
        entry["clusters"] = [
            (cid, label if cid == cluster_id else lbl, fp) for cid, lbl, fp in entry["clusters"]
        ]
