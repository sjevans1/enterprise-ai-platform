"""Maximal Marginal Relevance (MMR) reranking for diverse retrieval.

MMR balances relevance to the query against similarity to already-selected
results, promoting topical diversity in the final citation set.

Reference:
    Carbonell, J. & Goldstein, J. (1998). "The Use of MMR for
    Diverse Textual Summarization." SIGIR.

Formula:
    argmax_{d}  λ * sim(q, d) - (1 - λ) * max_{d' ∈ S} sim(d, d')

    λ = 1.0 → pure relevance (no diversity)
    λ = 0.0 → pure diversity (all selected results are maximally dissimilar)
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def _normalize(embeddings: np.ndarray) -> np.ndarray:
    """L2-normalize embeddings row-wise. Returns a copy."""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    # Avoid division by zero
    norms = np.where(norms == 0, 1.0, norms)
    return embeddings / norms


def mmr_rerank(
    query_embedding: list[float],
    candidate_embeddings: list[list[float]],
    candidate_indices: list[int],
    top_k: int,
    lambda_param: float = 0.7,
) -> list[tuple[int, float]]:
    """Rerank candidates using Maximal Marginal Relevance.

    Args:
        query_embedding: Embedding vector for the search query (1-D float list).
        candidate_embeddings: Embedding vectors for all candidates (list of 1-D float lists).
            Must be parallel to candidate_indices.
        candidate_indices: Original indices of candidates (e.g. chunk IDs in order).
        top_k: Number of results to return.
        lambda_param: Relevance/diversity trade-off in [0, 1].
            1.0 = pure relevance, 0.0 = pure diversity.

    Returns:
        List of (candidate_index, mmr_score) tuples, sorted by MMR score
        descending, length == min(top_k, len(candidates)).
    """
    if not candidate_embeddings or not candidate_indices:
        return []

    if not 0.0 <= lambda_param <= 1.0:
        raise ValueError(f"lambda_param must be in [0, 1], got {lambda_param}")

    top_k = min(top_k, len(candidate_indices))
    if top_k <= 0:
        return []

    query_vec = np.array(query_embedding, dtype=np.float64).reshape(1, -1)
    candidate_vecs = np.array(candidate_embeddings, dtype=np.float64)

    # Ensure candidate_vecs is 2-D (n_candidates, dim)
    if candidate_vecs.ndim == 1:
        candidate_vecs = candidate_vecs.reshape(1, -1)

    # Normalize for cosine similarity
    query_norm = _normalize(query_vec)[0]
    candidate_norms = _normalize(candidate_vecs)

    # Query-to-candidate cosine similarities
    query_sims = candidate_norms @ query_norm  # shape: (n_candidates,)

    # Candidate-to-candidate similarities (pre-compute)
    # For efficiency, compute on-the-fly during selection

    selected: list[int] = []
    selected_positions: list[int] = []  # positions in the candidate array
    available_positions = list(range(len(candidate_indices)))

    mmr_scores: list[tuple[int, float]] = []

    for _ in range(top_k):
        if not selected_positions:
            # First pick: highest query similarity
            best_pos = available_positions[int(np.argmax(query_sims[available_positions]))]
            best_score = float(query_sims[best_pos])
        else:
            # Compute MMR for each available candidate
            selected_matrix = candidate_norms[selected_positions]  # (n_selected, dim)
            # Similarity of each available candidate to all selected candidates
            avail_positions_arr = np.array(available_positions)
            avail_embeddings = candidate_norms[avail_positions_arr]  # (n_avail, dim)

            # sim_available_selected: (n_avail, n_selected)
            sim_available_selected = avail_embeddings @ selected_matrix.T
            # Max similarity to any selected candidate
            max_sim_selected = np.max(sim_available_selected, axis=1)  # (n_avail,)

            # MMR = lambda * query_sim - (1 - lambda) * max_sim_to_selected
            mmr = (
                lambda_param * query_sims[avail_positions_arr]
                - (1.0 - lambda_param) * max_sim_selected
            )

            best_idx_in_avail = int(np.argmax(mmr))
            best_pos = available_positions[best_idx_in_avail]
            best_score = float(mmr[best_idx_in_avail])

        selected_positions.append(best_pos)
        selected.append(candidate_indices[best_pos])
        mmr_scores.append((candidate_indices[best_pos], best_score))

        available_positions.remove(best_pos)

        if not available_positions:
            break

    logger.debug(
        f"MMR rerank: lambda={lambda_param}, selected {len(selected)} "
        f"from {len(candidate_indices)} candidates"
    )

    return mmr_scores
