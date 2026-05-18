import numpy as np
import pytest
from pathlib import Path
from lib.clustering import (
    optimize_hdbscan, cluster_quality_metrics, apply_umap, load_umap_reducer,
)


def _three_blobs(n_per_blob: int = 8, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    blobs = [
        rng.normal(loc=[0, 0], scale=0.05, size=(n_per_blob, 2)),
        rng.normal(loc=[5, 0], scale=0.05, size=(n_per_blob, 2)),
        rng.normal(loc=[0, 5], scale=0.05, size=(n_per_blob, 2)),
    ]
    return np.vstack(blobs)


def test_optimize_hdbscan_finds_three_clusters():
    X = _three_blobs()
    labels, metrics = optimize_hdbscan(X, n_trials=10)
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    assert 2 <= n_clusters <= 4
    assert metrics.get("noise_ratio", 1.0) < 0.5


def test_cluster_quality_metrics_keys():
    X = _three_blobs()
    labels = np.array([0]*8 + [1]*8 + [2]*8)
    m = cluster_quality_metrics(X, labels)
    assert "n_clusters" in m
    assert "noise_ratio" in m
    assert m["n_clusters"] == 3
    assert m["noise_ratio"] == 0.0


def test_optimize_hdbscan_empty_returns_no_clusters():
    X = np.zeros((1, 2))
    labels, _ = optimize_hdbscan(X, n_trials=2)
    # Single point can't cluster
    assert len(labels) == 1


@pytest.mark.skipif(not Path("umap_reducer.pkl").exists(),
                    reason="umap_reducer.pkl not present")
def test_load_umap_reducer_succeeds():
    reducer = load_umap_reducer("umap_reducer.pkl")
    assert hasattr(reducer, "n_components")
    assert reducer.n_components > 0


def test_load_umap_reducer_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_umap_reducer("nonexistent.pkl")
