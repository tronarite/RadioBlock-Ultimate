"""Entrenamiento del detector v2 a partir de tramos etiquetados."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GridSearchCV, GroupKFold, StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import store


MIN_TRAMOS = 20
CS = (0.01, 0.1, 1.0, 10.0, 100.0)


@dataclass
class Dataset:
    """Ventanas propagadas desde los tramos, con metadatos para evaluar."""

    X: np.ndarray
    y: np.ndarray
    groups: np.ndarray
    novel: np.ndarray
    segment_ids: np.ndarray
    times: np.ndarray

    def __iter__(self):
        # Mantiene una interfaz sencilla para quien sólo necesita el dataset base.
        yield self.X
        yield self.y
        yield self.groups
        yield self.novel


def _bounds(row):
    return (
        float(row["t_start_user"] if row["t_start_user"] is not None else row["t_start"]),
        float(row["t_end_user"] if row["t_end_user"] is not None else row["t_end"]),
    )


def load_dataset(c, backend: str) -> Dataset:
    """Propaga cada etiqueta de tramo a ventanas con al menos 50 % de solape."""
    rows = c.execute(
        """
        SELECT s.*, w.id window_id, w.t_start window_start, w.emb
        FROM segments s
        JOIN windows w ON w.radio=s.radio AND w.backend=?
          AND w.t_start < COALESCE(s.t_end_user, s.t_end)
          AND w.t_start + ? > COALESCE(s.t_start_user, s.t_start)
        WHERE s.label IS NOT NULL
        ORDER BY s.id, w.t_start
        """,
        (backend, store.WINDOW_S),
    ).fetchall()

    # Una ventana puede rozar dos tramos; conserva el de mayor solape para no
    # entrenar dos veces con posibles etiquetas contradictorias.
    selected = {}
    for row in rows:
        start, end = _bounds(row)
        overlap = max(0.0, min(row["window_start"] + store.WINDOW_S, end) - max(row["window_start"], start))
        if overlap < store.WINDOW_S * 0.5:
            continue
        old = selected.get(row["window_id"])
        if old is None or overlap > old[0] or (overlap == old[0] and row["id"] < old[1]["id"]):
            selected[row["window_id"]] = (overlap, row)

    chosen = [item[1] for item in selected.values()]
    chosen.sort(key=lambda r: (r["window_start"], r["window_id"]))
    if not chosen:
        return Dataset(np.empty((0, 0), np.float32), np.empty(0, int), np.empty(0, str),
                       np.empty(0, bool), np.empty(0, int), np.empty(0, float))
    vectors = [store.unpack(r["emb"]).ravel() for r in chosen]
    sizes = {v.size for v in vectors}
    if len(sizes) != 1:
        raise ValueError("Hay embeddings de tamaños incompatibles para este backend.")
    return Dataset(
        np.vstack(vectors).astype(np.float32),
        np.asarray([r["label"] for r in chosen], dtype=int),
        np.asarray([f"{r['radio']}::{int(r['window_start'] // 86400)}" for r in chosen]),
        np.asarray([r["label"] == 1 and r["source"] == "model" and not (r["n_repeats"] or 0) for r in chosen]),
        np.asarray([r["id"] for r in chosen], dtype=int),
        np.asarray([r["window_start"] for r in chosen], dtype=float),
    )


def _model(c: float = 1.0) -> Pipeline:
    return Pipeline([
        ("scaler", StandardScaler()),
        ("classifier", LogisticRegression(C=c, class_weight="balanced", max_iter=3000, random_state=42)),
    ])


def _fit(X, y, groups=None):
    """Ajusta C por validación interna, respetando los grupos cuando existen."""
    if len(np.unique(y)) < 2:
        raise ValueError("Falta una de las dos clases.")
    if groups is not None and len(np.unique(groups)) >= 2:
        cv = GroupKFold(n_splits=min(4, len(np.unique(groups))))
        search = GridSearchCV(_model(), {"classifier__C": CS}, scoring="f1", cv=cv, n_jobs=1)
        search.fit(X, y, groups=groups)
        return search.best_estimator_
    counts = np.bincount(y)
    if counts.min() >= 2:
        cv = StratifiedKFold(n_splits=min(4, int(counts.min())), shuffle=True, random_state=42)
        search = GridSearchCV(_model(), {"classifier__C": CS}, scoring="f1", cv=cv, n_jobs=1)
        search.fit(X, y)
        return search.best_estimator_
    return _model().fit(X, y)


def _valid_dataset(data: Dataset) -> bool:
    n_segments = len(np.unique(data.segment_ids))
    if n_segments < MIN_TRAMOS:
        print(f"Muy pocos tramos etiquetados ({n_segments}); se necesitan al menos {MIN_TRAMOS}.")
        return False
    if len(np.unique(data.y)) < 2:
        print("No se puede entrenar: sólo hay una clase en las ventanas etiquetadas.")
        return False
    return True


def _metrics(y, scores):
    if not len(y):
        return {k: float("nan") for k in ("precision", "recall", "f1", "auc", "pr_auc", "precision_at_recall_0.8", "recall_at_precision_0.95")}
    pred = scores >= 0.5
    precision, recall, _ = precision_recall_curve(y, scores)
    p80 = precision[recall >= 0.8]
    r95 = recall[precision >= 0.95]
    out = {
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "auc": float(roc_auc_score(y, scores)) if len(np.unique(y)) == 2 else float("nan"),
        "pr_auc": float(average_precision_score(y, scores)) if len(np.unique(y)) == 2 else float("nan"),
        "precision_at_recall_0.8": float(np.max(p80)) if p80.size else 0.0,
        "recall_at_precision_0.95": float(np.max(r95)) if r95.size else 0.0,
    }
    return out


def _segment_scores(data: Dataset, scores):
    ids = np.unique(data.segment_ids)
    y = np.empty(len(ids), dtype=int)
    averaged = np.empty(len(ids), dtype=float)
    novel = np.empty(len(ids), dtype=bool)
    for i, ident in enumerate(ids):
        mask = data.segment_ids == ident
        y[i] = data.y[mask][0]
        averaged[i] = scores[mask].mean()
        novel[i] = data.novel[mask].any()
    return y, averaged, novel


def _folds(data: Dataset):
    groups = data.groups.copy()
    if len(np.unique(groups)) < 2:
        # Sin dos días disponibles, separamos bloques temporales contiguos.
        order = np.argsort(data.times)
        n_blocks = min(3, max(2, len(data) // 8))
        groups = np.empty(len(data), dtype=object)
        for block, indexes in enumerate(np.array_split(order, n_blocks)):
            groups[indexes] = f"bloque-{block}"
        print("Sólo hay un día: validación en bloques temporales contiguos.")
    return GroupKFold(n_splits=min(5, len(np.unique(groups)))).split(data.X, data.y, groups), groups


def _print_metrics(title, metrics):
    print(title + ": " + ", ".join(f"{key}={value:.3f}" for key, value in metrics.items()))


def _learning_curve(data: Dataset, folds, groups):
    result = {}
    unique_segments = np.unique(data.segment_ids)
    for fraction in (0.10, 0.25, 0.50, 1.00):
        scores = np.full(len(data.y), np.nan)
        for train, test in folds:
            train_segments = np.unique(data.segment_ids[train])
            n = max(2, int(np.ceil(len(train_segments) * fraction)))
            chosen = set(np.sort(train_segments)[:n])
            subset = train[np.isin(data.segment_ids[train], list(chosen))]
            if len(np.unique(data.y[subset])) < 2:
                continue
            model = _fit(data.X[subset], data.y[subset], groups[subset])
            scores[test] = model.predict_proba(data.X[test])[:, 1]
        valid = ~np.isnan(scores)
        if valid.any():
            sy, ss, _ = _segment_scores(Dataset(data.X[valid], data.y[valid], groups[valid], data.novel[valid], data.segment_ids[valid], data.times[valid]), scores[valid])
            result[fraction] = _metrics(sy, ss)["f1"]
        else:
            result[fraction] = float("nan")
    print("Curva de aprendizaje (F1 por tramo): " + ", ".join(f"{int(k * 100)}%={v:.3f}" for k, v in result.items()))
    return result


def evaluar(c, backend: str):
    data = load_dataset(c, backend)
    if not _valid_dataset(data):
        return None
    fold_list, groups = _folds(data)
    folds = list(fold_list)
    scores = np.zeros(len(data.y), dtype=float)
    for train, test in folds:
        if len(np.unique(data.y[train])) < 2:
            print("No se puede validar: una partición de entrenamiento tiene una sola clase.")
            return None
        model = _fit(data.X[train], data.y[train], groups[train])
        scores[test] = model.predict_proba(data.X[test])[:, 1]
    window_metrics = _metrics(data.y, scores)
    sy, ss, snovel = _segment_scores(data, scores)
    segment_metrics = _metrics(sy, ss)
    novel_metrics = _metrics(sy[snovel], ss[snovel])
    _print_metrics("Ventanas", window_metrics)
    _print_metrics("Tramos", segment_metrics)
    _print_metrics("Tramos novel", novel_metrics)
    curve = _learning_curve(data, folds, groups)
    return {"windows": window_metrics, "segments": segment_metrics, "novel": novel_metrics, "learning_curve": curve}


def _model_path(backend: str) -> Path:
    return store.DATA_DIR / f"modelo2_{backend}.joblib"


def fit(c, backend: str):
    data = load_dataset(c, backend)
    if not _valid_dataset(data):
        return None
    evaluar(c, backend)
    model = _fit(data.X, data.y, data.groups)
    package = {"model": model, "backend": backend, "n_features": data.X.shape[1]}
    path = _model_path(backend)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(package, path)
    print(f"Modelo guardado en {path} con {len(np.unique(data.segment_ids))} tramos.")
    return package


def predict_all(c, backend: str) -> int:
    path = _model_path(backend)
    if not path.exists():
        print("No existe un modelo entrenado; ejecuta train2.py fit primero.")
        return 0
    package = joblib.load(path)
    rows = c.execute("SELECT id, emb FROM windows WHERE backend=? ORDER BY id", (backend,)).fetchall()
    if not rows:
        print("No hay ventanas para predecir.")
        return 0
    X = np.vstack([store.unpack(row["emb"]).ravel() for row in rows]).astype(np.float32)
    if X.shape[1] != package["n_features"]:
        raise RuntimeError("El tamaño de los embeddings no coincide con el modelo guardado.")
    scores = package["model"].predict_proba(X)[:, 1]
    c.executemany("UPDATE windows SET score=? WHERE id=?", zip(map(float, scores), (r["id"] for r in rows)))
    c.commit()
    print(f"Predicciones actualizadas para {len(rows)} ventanas.")
    return len(rows)


def smooth_segments(c, radio: str, backend: str, thr: float = 0.8, min_len: float = 8, max_gap: float = 4):
    """Convierte scores por ventana en candidatos de tramo, sin tocar etiquetas."""
    rows = c.execute("SELECT t_start, score FROM windows WHERE radio=? AND backend=? AND score IS NOT NULL ORDER BY t_start", (radio, backend)).fetchall()
    if not rows:
        print("No hay scores para suavizar.")
        return 0
    starts = np.asarray([r["t_start"] for r in rows], float)
    raw = np.asarray([r["score"] for r in rows], float)
    smooth = np.convolve(raw, np.ones(7) / 7, mode="same")
    active = np.zeros(len(smooth), dtype=bool)
    for i, value in enumerate(smooth):
        active[i] = value >= thr or (i and active[i - 1] and value >= thr - 0.1)
    candidates = []
    start = None
    end = None
    values = []
    for t, value, on in zip(starts, smooth, active):
        if on and (start is None or t - end <= max_gap):
            start = t if start is None else start
            end = t + store.WINDOW_S
            values.append(value)
        elif on:
            if end - start >= min_len:
                candidates.append((start, end, float(np.mean(values))))
            start, end, values = t, t + store.WINDOW_S, [value]
        elif start is not None and t - end > max_gap:
            if end - start >= min_len:
                candidates.append((start, end, float(np.mean(values))))
            start = end = None
            values = []
    if start is not None and end - start >= min_len:
        candidates.append((start, end, float(np.mean(values))))
    inserted = 0
    for start, end, score in candidates:
        labeled = c.execute(
            """SELECT 1 FROM segments
               WHERE radio=? AND label IS NOT NULL
                 AND COALESCE(t_start_user, t_start) < ?
                 AND COALESCE(t_end_user, t_end) > ? LIMIT 1""",
            (radio, end, start),
        ).fetchone()
        if labeled:
            continue
        c.execute("INSERT OR IGNORE INTO segments (radio, t_start, t_end, source, score) VALUES (?, ?, ?, 'model', ?)", (radio, start, end, score))
        inserted += c.execute("SELECT changes()").fetchone()[0]
    c.commit()
    print(f"Candidatos de modelo insertados: {inserted}.")
    return inserted


def main():
    parser = argparse.ArgumentParser(description="Entrena el detector de anuncios por ventanas.")
    parser.add_argument("command", choices=("fit", "eval", "predict", "segments"))
    parser.add_argument("--backend", required=True)
    parser.add_argument("--radio", help="obligatoria para el comando segments")
    parser.add_argument("--thr", type=float, default=0.8)
    parser.add_argument("--min-len", type=float, default=8)
    parser.add_argument("--max-gap", type=float, default=4)
    args = parser.parse_args()
    if args.command == "segments" and not args.radio:
        parser.error("segments requiere --radio")
    with store.conn2() as c:
        if args.command == "fit":
            fit(c, args.backend)
        elif args.command == "eval":
            evaluar(c, args.backend)
        elif args.command == "predict":
            predict_all(c, args.backend)
        else:
            smooth_segments(c, args.radio, args.backend, args.thr, args.min_len, args.max_gap)


if __name__ == "__main__":
    main()
