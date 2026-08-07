"""Modelo de clustering por emisora (HDBSCAN).

Cada emisora tiene su propio `RadioModel`, cacheado en memoria y
respaldado en la tabla `clusters`. El clustering es no supervisado: agrupa
segmentos por similitud acústica sin necesidad de ninguna etiqueta del
usuario, así que descubre patrones repetidos (cuñas, anuncios, tertulias
que se repiten) por sí solo, con solo escuchar. `HDBSCAN(min_cluster_size=
MIN_CLUSTER_SIZE)` es justo el "umbral de repetición": un grupo de audio
solo se convierte en cluster (y por tanto en candidato a revisión) cuando
aparece al menos MIN_CLUSTER_SIZE veces. Un fragmento que no se repite
nunca (habla suelta de una tertulia, noticias distintas cada vez) queda
como ruido/outlier y jamás se le pregunta nada al usuario.

El reentrenamiento se dispara periódicamente desde el worker conforme
llega audio nuevo (ver `retrain_async` y `worker/worker.py`), no solo
cuando el usuario etiqueta algo — así el sistema puede proponer patrones
nuevos sin que haga falta ninguna acción manual previa. Se ejecuta en
background para no bloquear el proxy.

HDBSCAN no soporta actualización incremental real, así que cada
reentrenamiento recalcula el clustering completo sobre el conjunto
acumulado de la emisora.
"""

import threading

import hdbscan
import numpy as np
from sklearn.preprocessing import StandardScaler
from sqlalchemy.orm import Session

from app.db.models import Cluster, Segmento
from app.analysis.features import bytes_to_features

MIN_SEGMENTS_TO_CLUSTER = 6
MIN_CLUSTER_SIZE = 3

# Cuántas veces tiene que APARECER un patrón, en momentos distintos y
# separados en el tiempo, antes de proponerlo para revisión. Un tramo
# continuo (p.ej. una tertulia de varios minutos trozeada en segmentos de
# 10s) es UNA sola aparición aunque genere muchos segmentos parecidos —
# eso no es repetición, es continuidad, y no debe contar como patrón.
MIN_APARICIONES = 3

_lock = threading.Lock()
_cache: dict[int, dict] = {}  # radio_id -> {scaler, clusterer, label_to_cluster, cluster_labels}


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
    """Recalcula el clustering completo de una emisora y persiste clusters/segmentos."""
    segmentos = (
        db.query(Segmento)
        .filter(Segmento.radio_id == radio_id, Segmento.features.isnot(None))
        .all()
    )

    with _lock:
        if len(segmentos) < MIN_SEGMENTS_TO_CLUSTER:
            _cache.pop(radio_id, None)
            return

        X = np.stack([bytes_to_features(s.features) for s in segmentos])
        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=MIN_CLUSTER_SIZE, prediction_data=True
        )
        labels = clusterer.fit_predict(Xs)
        strengths = clusterer.probabilities_

        # El reclustering es completo cada vez (HDBSCAN no soporta incremental),
        # así que un cluster ya revisado por el usuario puede perder algún
        # miembro límite entre un reentrenamiento y el siguiente. Si eso deja
        # sin voto de label_usuario a un cluster que en realidad es el mismo
        # patrón de siempre, se pierde la revisión y el panel volvería a
        # pedirla — justo lo que no queremos. Por eso, antes de borrar los
        # clusters viejos, guardamos su centroide y etiqueta para poder
        # reconocer el mismo patrón aunque sus miembros exactos hayan cambiado.
        old_clusters = db.query(Cluster).filter(Cluster.radio_id == radio_id).all()
        old_labeled_centroids = [
            (c.label, bytes_to_features(c.centroid)) for c in old_clusters if c.label
        ]

        for c in old_clusters:
            for s in c.segmentos:
                s.cluster_id = None
        for c in old_clusters:
            db.delete(c)
        db.flush()

        # A qué internal_label de ESTE reentrenamiento correspondería, según el
        # propio HDBSCAN, cada centroide ya revisado en un reentrenamiento
        # anterior. Se usa como red de seguridad cuando el voto de mayoría de
        # label_usuario no basta (ver comentario más arriba).
        inherited_label_by_internal: dict[int, str] = {}
        if old_labeled_centroids:
            old_raw = np.stack([c for _, c in old_labeled_centroids])
            old_pred, _ = hdbscan.approximate_predict(clusterer, scaler.transform(old_raw))
            for (old_label, _), pred in zip(old_labeled_centroids, old_pred):
                if pred != -1:
                    inherited_label_by_internal[int(pred)] = old_label

        label_to_cluster: dict[int, int] = {}
        cluster_labels: dict[int, str | None] = {}

        for internal_label in sorted(set(labels)):
            member_idx = [i for i, l in enumerate(labels) if l == internal_label]
            member_segs = [segmentos[i] for i in member_idx]

            n_apariciones = _count_apariciones(member_segs) if internal_label != -1 else 0

            if internal_label == -1 or n_apariciones < MIN_APARICIONES:
                # O es ruido puro (no encaja con nada), o es un tramo continuo
                # que HDBSCAN agrupó por parecerse a sí mismo pero que nunca ha
                # vuelto a sonar en un momento distinto: no es un patrón real,
                # así que no se propone para revisión. Pasa sin silenciar.
                for s in member_segs:
                    s.cluster_id = None
                    s.label = "desconocido"
                    s.confidence = 0.0
                continue

            centroid = X[member_idx].mean(axis=0)
            user_label = _majority_label(
                [s.label_usuario for s in member_segs]
            ) or inherited_label_by_internal.get(internal_label)

            cluster = Cluster(
                radio_id=radio_id,
                label=user_label,
                centroid=centroid.astype(np.float64).tobytes(),
                n_segmentos=len(member_segs),
                n_apariciones=n_apariciones,
            )
            db.add(cluster)
            db.flush()

            # Segmento representativo: el más cercano al centroide (en espacio escalado)
            # entre los que tienen audio guardado en disco (solo se guarda el audio de
            # los segmentos "desconocido" en el momento de ingestión; ver worker.py).
            centroid_scaled = Xs[member_idx].mean(axis=0)
            dists = np.linalg.norm(Xs[member_idx] - centroid_scaled, axis=1)
            candidates = [
                (d, s) for d, s in zip(dists, member_segs) if s.archivo_audio
            ]
            if candidates:
                cluster.representative_segment_id = min(candidates, key=lambda ds: ds[0])[1].id

            resolved_label = user_label or "desconocido"
            for i, s in zip(member_idx, member_segs):
                s.cluster_id = cluster.id
                s.confidence = float(strengths[i])
                s.label = resolved_label if user_label else "desconocido"

            label_to_cluster[internal_label] = cluster.id
            cluster_labels[cluster.id] = user_label

        db.commit()

        _cache[radio_id] = {
            "scaler": scaler,
            "clusterer": clusterer,
            "label_to_cluster": label_to_cluster,
            "cluster_labels": cluster_labels,
        }


def predict(radio_id: int, vector: np.ndarray, threshold: float) -> tuple[str, float]:
    """Clasifica un vector de features nuevo contra el modelo cacheado de la emisora.

    Devuelve (label, confidence). label es "desconocido" si no coincide con
    ningún patrón ya revisado, o la etiqueta que el usuario le dio a ese
    patrón ("anuncio", "contenido", "ignorado", ...).
    """
    with _lock:
        entry = _cache.get(radio_id)

    if entry is None:
        return "desconocido", 0.0

    Xs = entry["scaler"].transform(vector.reshape(1, -1))
    internal_labels, strengths = hdbscan.approximate_predict(entry["clusterer"], Xs)
    internal_label = int(internal_labels[0])
    confidence = float(strengths[0])

    if internal_label == -1 or confidence < threshold:
        return "desconocido", confidence

    cluster_id = entry["label_to_cluster"].get(internal_label)
    label = entry["cluster_labels"].get(cluster_id) if cluster_id else None
    return (label or "desconocido"), confidence


def is_ready(radio_id: int) -> bool:
    with _lock:
        return radio_id in _cache


def update_cluster_label_cache(radio_id: int, cluster_id: int, label: str | None) -> None:
    """Actualiza la etiqueta de un cluster en la caché en memoria sin esperar
    al próximo reentrenamiento (usado cuando el usuario reetiqueta desde el panel)."""
    with _lock:
        entry = _cache.get(radio_id)
        if entry is not None and cluster_id in entry["cluster_labels"]:
            entry["cluster_labels"][cluster_id] = label
