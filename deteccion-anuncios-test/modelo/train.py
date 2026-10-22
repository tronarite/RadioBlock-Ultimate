"""Entrenamiento, validación temporal y predicción del detector de anuncios."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import store
from features import FEATURE_VERSION, compute_missing


MIN_ETIQUETAS = 10


def _con_repeticion(fila) -> np.ndarray:
    """Vector de características + nº de repeticiones (repeats.py), la señal más fuerte."""
    rep = float(fila["repeat_count"] or 0)
    return np.append(store.unpack(fila["features"]), [min(rep, 10.0), float(rep > 0)])


def load_labeled(conn):
    """Carga etiquetas y crea grupos por radio y día para evitar fuga temporal."""
    filas = conn.execute(
        "SELECT radio, timestamp, features, repeat_count, label FROM clips "
        "WHERE label IS NOT NULL AND features IS NOT NULL ORDER BY timestamp"
    ).fetchall()
    if not filas:
        return np.empty((0, 0), dtype=np.float32), np.empty(0, dtype=int), np.empty(0, dtype=str)

    vectores = [_con_repeticion(fila) for fila in filas]
    tamanos = {vector.size for vector in vectores}
    if len(tamanos) != 1:
        raise ValueError("Hay características con tamaños incompatibles; recalcúlalas antes de entrenar.")
    x = np.vstack(vectores).astype(np.float32)
    y = np.asarray([fila["label"] for fila in filas], dtype=int)
    grupos = np.asarray([f"{fila['radio']}::{str(fila['timestamp'])[:10]}" for fila in filas])
    return x, y, grupos


def _base_model(n_etiquetas: int):
    if n_etiquetas < 200:
        return make_pipeline(
            StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=2000, random_state=42)
        )
    return HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.08, class_weight="balanced", random_state=42
    )


def _fit_model(x, y, calibrar: bool = True):
    modelo = _base_model(len(y))
    # La calibración necesita suficientes ejemplos de ambas clases para sus tres particiones.
    if calibrar and len(y) >= 60 and np.min(np.bincount(y)) >= 10:
        modelo = CalibratedClassifierCV(modelo, method="sigmoid", cv=3)
    modelo.fit(x, y)
    return modelo


def _precision_a_recall(y, scores, objetivo: float = 0.9) -> float:
    precision, recall, _ = precision_recall_curve(y, scores)
    validas = precision[recall >= objetivo]
    return float(np.max(validas)) if validas.size else 0.0


def evaluar(x, y, grupos) -> dict | None:
    """Evalúa con GroupKFold y devuelve métricas obtenidas fuera de cada grupo."""
    if len(np.unique(grupos)) < 2:
        print("No se puede validar: hacen falta clips de al menos dos días/radios distintos.")
        return None
    n_splits = min(5, len(np.unique(grupos)))
    predicciones = np.zeros(len(y), dtype=float)
    for entrenamiento, prueba in GroupKFold(n_splits=n_splits).split(x, y, grupos):
        if len(np.unique(y[entrenamiento])) < 2:
            print("No se puede validar: alguna partición temporal de entrenamiento tiene una sola clase.")
            return None
        modelo = _fit_model(x[entrenamiento], y[entrenamiento], calibrar=False)
        predicciones[prueba] = modelo.predict_proba(x[prueba])[:, 1]

    umbral = 0.5
    metricas = {
        "precision": precision_score(y, predicciones >= umbral, zero_division=0),
        "recall": recall_score(y, predicciones >= umbral, zero_division=0),
        "f1": f1_score(y, predicciones >= umbral, zero_division=0),
        "auc": roc_auc_score(y, predicciones),
        "pr_auc": average_precision_score(y, predicciones),
        "precision_recall_0_9": _precision_a_recall(y, predicciones),
    }
    print(
        "Validación GroupKFold: "
        + ", ".join(f"{nombre}={valor:.3f}" for nombre, valor in metricas.items())
    )
    return metricas


def _validar_etiquetas(y) -> bool:
    if len(y) < MIN_ETIQUETAS:
        print(f"Muy pocas etiquetas ({len(y)}). Se necesitan al menos {MIN_ETIQUETAS} para entrenar.")
        return False
    clases, conteos = np.unique(y, return_counts=True)
    if len(clases) < 2:
        print("No se puede entrenar: faltan etiquetas de una de las dos clases (anuncio/no anuncio).")
        return False
    if np.min(conteos) < 2:
        print("Muy pocas etiquetas de una clase; se necesitan al menos dos ejemplos por clase.")
        return False
    return True


def fit(conn):
    """Calcula pendientes, entrena el modelo final y lo guarda en MODEL_PATH."""
    pendientes = compute_missing(conn)
    if pendientes:
        print(f"Características calculadas para {pendientes} clips.")
    x, y, grupos = load_labeled(conn)
    if not _validar_etiquetas(y):
        return None
    evaluar(x, y, grupos)
    modelo = _fit_model(x, y, calibrar=True)
    paquete = {"model": modelo, "feature_version": FEATURE_VERSION, "n_features": x.shape[1]}
    Path(store.MODEL_PATH).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(paquete, store.MODEL_PATH)
    print(f"Modelo guardado en {store.MODEL_PATH} con {len(y)} etiquetas.")
    return paquete


def predict_all(conn) -> int:
    """Actualiza score con P(anuncio) para todos los clips que tengan características."""
    if not Path(store.MODEL_PATH).exists():
        print("No existe un modelo entrenado; ejecuta train.py primero.")
        return 0
    paquete = joblib.load(store.MODEL_PATH)
    if paquete.get("feature_version") != FEATURE_VERSION:
        raise RuntimeError("El modelo usa otra versión de características; vuelve a entrenarlo.")
    filas = conn.execute("SELECT id, features, repeat_count FROM clips WHERE features IS NOT NULL").fetchall()
    if not filas:
        print("No hay clips con características para predecir.")
        return 0
    x = np.vstack([_con_repeticion(fila) for fila in filas]).astype(np.float32)
    if x.shape[1] != paquete["n_features"]:
        raise RuntimeError("El tamaño de las características no coincide con el modelo guardado.")
    scores = paquete["model"].predict_proba(x)[:, 1]
    conn.executemany("UPDATE clips SET score = ? WHERE id = ?", zip(map(float, scores), (f["id"] for f in filas)))
    conn.commit()
    print(f"Predicciones actualizadas para {len(filas)} clips.")
    return len(filas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Entrena el detector de anuncios de radio.")
    parser.add_argument("--eval-only", action="store_true", help="solo ejecuta la validación")
    args = parser.parse_args()
    with store.conn() as conn:
        pendientes = compute_missing(conn)
        if pendientes:
            print(f"Características calculadas para {pendientes} clips.")
        x, y, grupos = load_labeled(conn)
        if not _validar_etiquetas(y):
            sys.exit(2)
        if args.eval_only:
            evaluar(x, y, grupos)
            return
        paquete = fit(conn)
        if paquete is not None:
            predict_all(conn)


if __name__ == "__main__":
    main()
