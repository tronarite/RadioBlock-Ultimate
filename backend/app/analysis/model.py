"""Modelo de clustering por emisora (HDBSCAN).

Cada emisora tiene su propio `RadioModel`, cacheado en memoria y
respaldado en la tabla `clusters`. El reentrenamiento recalcula el
clustering completo sobre todos los segmentos con features conocidos de
esa emisora; se ejecuta en background (ver `worker/worker.py`) para no
bloquear el proxy, tal y como pide la especificación.

HDBSCAN no soporta actualización incremental real, así que "incremental"
aquí significa: se dispara automáticamente tras cada etiqueta nueva,
recalculando sobre el conjunto acumulado, no en un batch programado.
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

_lock = threading.Lock()
_cache: dict[int, dict] = {}  # radio_id -> {scaler, clusterer, label_to_cluster, cluster_labels}


def _majority_label(label_usuarios: list[str | None]) -> str | None:
    votes = [l for l in label_usuarios if l and l != "ignorado"]
    if not votes:
        return None
    return max(set(votes), key=votes.count)


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

        # Limpia clusters existentes de la emisora.
        old_clusters = db.query(Cluster).filter(Cluster.radio_id == radio_id).all()
        for c in old_clusters:
            for s in c.segmentos:
                s.cluster_id = None
        for c in old_clusters:
            db.delete(c)
        db.flush()

        label_to_cluster: dict[int, int] = {}
        cluster_labels: dict[int, str | None] = {}

        for internal_label in sorted(set(labels)):
            member_idx = [i for i, l in enumerate(labels) if l == internal_label]
            member_segs = [segmentos[i] for i in member_idx]

            if internal_label == -1:
                for s in member_segs:
                    s.cluster_id = None
                    s.label = "desconocido"
                    s.confidence = 0.0
                continue

            centroid = X[member_idx].mean(axis=0)
            user_label = _majority_label([s.label_usuario for s in member_segs])

            cluster = Cluster(
                radio_id=radio_id,
                label=user_label,
                centroid=centroid.astype(np.float64).tobytes(),
                n_segmentos=len(member_segs),
            )
            db.add(cluster)
            db.flush()

            # Segmento representativo: el más cercano al centroide (en espacio escalado).
            centroid_scaled = Xs[member_idx].mean(axis=0)
            dists = np.linalg.norm(Xs[member_idx] - centroid_scaled, axis=1)
            cluster.representative_segment_id = member_segs[int(np.argmin(dists))].id

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

    Devuelve (label, confidence) con label en {"anuncio", "musica", "desconocido"}.
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
