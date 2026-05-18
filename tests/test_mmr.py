import numpy as np
import pytest
from lib.mmr import mmr_select


def test_mmr_returns_k_indices():
    """Returns exactly k indices when k < n."""
    embeddings = np.eye(5)  # 5 orthogonal unit vectors
    relevance = [0.9, 0.8, 0.7, 0.6, 0.5]
    result = mmr_select(embeddings, relevance, k=3)
    assert len(result) == 3
    assert len(set(result)) == 3  # unique
    assert all(0 <= i < 5 for i in result)


def test_mmr_returns_all_when_k_ge_n():
    """Returns all n indices when k >= n, without error."""
    embeddings = np.eye(4)
    relevance = [0.5, 0.6, 0.7, 0.8]
    result = mmr_select(embeddings, relevance, k=10)
    assert sorted(result) == [0, 1, 2, 3]


def test_mmr_lambda_one_mirrors_relevance_order():
    """With lambda_=1.0, selection is purely by relevance (descending)."""
    np.random.seed(42)
    n = 6
    # Distinct orthogonal embeddings so cosine sim doesn't matter
    embeddings = np.eye(n)
    # Monotonically decreasing relevance with no ties
    relevance = [0.95, 0.85, 0.75, 0.65, 0.55, 0.45]
    result = mmr_select(embeddings, relevance, k=n, lambda_=1.0)
    # Selected relevance should be non-increasing
    selected_relevance = [relevance[i] for i in result]
    for prev, curr in zip(selected_relevance, selected_relevance[1:]):
        assert prev >= curr, f"Relevance not non-increasing: {selected_relevance}"


def test_mmr_lambda_zero_prefers_diverse():
    """With lambda_=0.0, selection avoids similar items.

    Two nearly identical vectors and one orthogonal vector.
    With k=2 and lambda_=0, the orthogonal one should be selected
    alongside one of the duplicates (not both duplicates).
    """
    # v0 and v1 are nearly identical; v2 is orthogonal to both
    v0 = np.array([1.0, 0.0, 0.0])
    v1 = np.array([0.999, 0.001, 0.0])  # very close to v0
    v2 = np.array([0.0, 1.0, 0.0])      # orthogonal

    embeddings = np.array([v0, v1, v2])
    # All equal relevance so only diversity drives selection
    relevance = [1.0, 1.0, 1.0]

    result = mmr_select(embeddings, relevance, k=2, lambda_=0.0)
    # v2 (index 2) must be in the selection because it's the most diverse
    assert 2 in result, f"Expected index 2 (orthogonal vector) in result, got {result}"
    # The two selected items should NOT be both v0 and v1 (near-duplicates)
    assert not ({0, 1}.issubset(set(result))), f"Should not select both near-duplicates: {result}"


def test_mmr_empty_input_returns_empty():
    """Empty embeddings returns empty list."""
    result = mmr_select([], [], k=5)
    assert result == []
