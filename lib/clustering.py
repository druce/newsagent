"""UMAP + Optuna-tuned HDBSCAN clustering.

Math ported from ~/projects/OpenAIAgentsSDK/do_cluster.py with verbosity dropped.
Pretrained UMAP reducer expected at ./umap_reducer.pkl (3072-dim -> ~690-dim).
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any, Optional

import hdbscan
import numpy as np
import optuna
from sklearn.metrics import silhouette_score

# Suppress Optuna's INFO logging — we don't need its spam
optuna.logging.set_verbosity(optuna.logging.WARNING)
logging.getLogger("hdbscan").setLevel(logging.WARNING)

_RANDOM_STATE = 42


def load_umap_reducer(path: str = "umap_reducer.pkl") -> Any:
    """Load the pickled UMAP reducer. Raises FileNotFoundError if missing."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"UMAP reducer not found at {path}")
    with p.open("rb") as f:
        return pickle.load(f)


def apply_umap(embeddings: list[list[float]], reducer: Any) -> np.ndarray:
    """Apply the reducer to a list of full-dimension embeddings, return reduced 2D array."""
    M = np.asarray(embeddings, dtype=np.float32)
    return reducer.transform(M).astype(np.float64)


def normalize_l2(embeddings: list[list[float]]) -> np.ndarray:
    """L2-normalize embeddings so euclidean distance ranks neighbors like cosine."""
    M = np.asarray(embeddings, dtype=np.float64)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return M / norms


def cluster_quality_metrics(embeddings: np.ndarray, labels: np.ndarray) -> dict:
    """Compute silhouette, n_clusters, noise_ratio, etc."""
    n = len(labels)
    unique = set(labels.tolist())
    noise_count = int(np.sum(labels == -1))
    n_clusters = len(unique) - (1 if -1 in unique else 0)
    noise_ratio = noise_count / max(n, 1)

    silhouette = float("nan")
    if n_clusters >= 2 and noise_ratio < 1.0:
        mask = labels != -1
        if mask.sum() >= 2 and len(set(labels[mask].tolist())) >= 2:
            try:
                silhouette = float(silhouette_score(embeddings[mask], labels[mask]))
            except Exception:
                silhouette = float("nan")

    return {
        "n_clusters": n_clusters,
        "noise_ratio": noise_ratio,
        "silhouette": silhouette,
    }


def optimize_hdbscan(
    reduced: np.ndarray,
    n_trials: int = 30,
    timeout: Optional[int] = None,
) -> tuple[np.ndarray, dict]:
    """Run Optuna over HDBSCAN hyperparameters. Return (cluster_labels, metrics_dict)."""
    if len(reduced) < 3:
        # Not enough points; return all-noise.
        return np.full(len(reduced), -1), {
            "n_clusters": 0,
            "noise_ratio": 1.0,
            "silhouette": float("nan"),
        }

    def objective(trial: optuna.Trial) -> float:
        min_cluster_size = trial.suggest_int("min_cluster_size", 2, max(3, len(reduced) // 4))
        min_samples = trial.suggest_int("min_samples", 1, min_cluster_size)
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            metric="euclidean",
            cluster_selection_method="eom",
        )
        labels = clusterer.fit_predict(reduced)
        metrics = cluster_quality_metrics(reduced, labels)
        n_clusters = metrics["n_clusters"]
        if n_clusters < 2 or metrics["noise_ratio"] > 0.8:
            # Sentinel worse than any valid score (we're minimizing).
            return float("inf")
        # Composite: more clusters good (up to ~10), low noise good, high silhouette good
        sil = metrics["silhouette"] if not np.isnan(metrics["silhouette"]) else 0.0
        score = (
            0.4 * sil
            + 0.3 * (1.0 - metrics["noise_ratio"])
            + 0.3 * min(n_clusters / 10.0, 1.0)
        )
        return -score

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=_RANDOM_STATE),
    )
    study.optimize(objective, n_trials=n_trials, timeout=timeout)

    # Only trust the study's best params if at least one trial produced a
    # valid clustering (finite score). Otherwise fall back to a permissive
    # default — works for both UMAP-reduced and high-dim normalized inputs.
    found_valid = (
        study.best_trial is not None and np.isfinite(study.best_value)
    )
    best_params = (
        study.best_params
        if found_valid
        else {"min_cluster_size": 3, "min_samples": 1}
    )
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=best_params["min_cluster_size"],
        min_samples=best_params["min_samples"],
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(reduced)
    metrics = cluster_quality_metrics(reduced, labels)
    metrics["best_params"] = best_params
    return labels, metrics
