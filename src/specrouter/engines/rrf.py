"""Reciprocal Rank Fusion (usecase.md section 3.5).

    RRF(d) = sum_{m in M}  1 / (k + r_m(d))

Each engine produces per-document scores; we convert those to ordinal ranks
(1 = best) and fuse them. RRF sidesteps the incompatible score scales of BM25F
(unbounded), Jaccard (0-1) and Levenshtein (inverse distance) without any lossy
normalization. ``k`` defaults to 60.
"""

from __future__ import annotations


def _ranks_from_scores(scores: list[float]) -> list[int]:
    """Map scores -> 1-based ranks (highest score = rank 1). Ties share a rank."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    ranks = [0] * len(scores)
    prev_score: float | None = None
    prev_rank = 0
    for position, idx in enumerate(order, start=1):
        s = scores[idx]
        if prev_score is not None and s == prev_score:
            ranks[idx] = prev_rank  # tie -> same rank as the previous item
        else:
            ranks[idx] = position
            prev_rank = position
            prev_score = s
    return ranks


def fuse(score_lists: list[list[float]], *, k: int = 60) -> list[float]:
    """Fuse several aligned score lists into one RRF score per document."""
    if not score_lists:
        return []
    n = len(score_lists[0])
    fused = [0.0] * n
    for scores in score_lists:
        if len(scores) != n:
            raise ValueError("All engine score lists must be the same length.")
        ranks = _ranks_from_scores(scores)
        for i in range(n):
            fused[i] += 1.0 / (k + ranks[i])
    return fused


def top_k(score_lists: list[list[float]], *, k: int = 60, top: int = 5) -> list[tuple[int, float]]:
    """Return ``[(doc_index, rrf_score), ...]`` for the best ``top`` documents."""
    fused = fuse(score_lists, k=k)
    ranked = sorted(range(len(fused)), key=lambda i: fused[i], reverse=True)
    return [(i, fused[i]) for i in ranked[:top]]
