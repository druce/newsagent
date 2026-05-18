"""Maximal Marginal Relevance (MMR) diversity selection.

Standard MMR algorithm (Carbonell & Goldstein, 1998):
  score(i) = lambda_ * relevance[i] - (1 - lambda_) * max_sim_to_selected(i)

Pure numpy implementation — no external retrieval libraries needed.
"""
from __future__ import annotations

import numpy as np


def mmr_select(
    embeddings: list[list[float]] | np.ndarray,
    relevance: list[float] | np.ndarray,
    k: int,
    lambda_: float = 0.7,
) -> list[int]:
    """Return indices of MMR-selected items, in selection order.

    Args:
        embeddings: (n, d) array-like of item embeddings.
        relevance:  (n,) array-like of relevance scores (higher = more relevant).
        k:          Number of items to select. If k >= n, all indices are returned.
        lambda_:    Trade-off between relevance (1.0) and diversity (0.0).

    Returns:
        List of selected indices in the order they were chosen.
    """
    if len(embeddings) == 0:
        return []

    M = np.asarray(embeddings, dtype=np.float64)
    rel = np.asarray(relevance, dtype=np.float64)
    n = M.shape[0]

    if n == 0:
        return []

    # Normalize rows to unit norm for cosine similarity via dot product.
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    # Avoid division by zero for zero vectors.
    norms = np.where(norms == 0, 1.0, norms)
    M = M / norms

    k = min(k, n)
    selected: list[int] = []
    candidates = list(range(n))

    for _ in range(k):
        if not candidates:
            break

        best_score = float("-inf")
        best_idx = candidates[0]

        for i in candidates:
            if selected:
                # Max cosine similarity to any already-selected item.
                sims = M[i] @ M[np.array(selected)].T  # shape (len(selected),)
                max_sim = float(np.max(sims))
            else:
                max_sim = 0.0

            score = lambda_ * rel[i] - (1.0 - lambda_) * max_sim
            if score > best_score:
                best_score = score
                best_idx = i

        selected.append(best_idx)
        candidates.remove(best_idx)

    return selected
