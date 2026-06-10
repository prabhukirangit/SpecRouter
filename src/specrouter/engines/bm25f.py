"""BM25F: fielded, in-memory lexical indexing (usecase.md section 3.1).

Composite term frequency across weighted fields:

    f~(q, D) = sum_c  W_c * f_c(q, D) / (1 + b_c * (L_c / L_avg,c - 1))

Score:

    Score(D, Q) = sum_{q in Q}  IDF(q) * f~(q, D) / (k1 + f~(q, D))

Statistics (document frequencies + average field lengths) are precomputed once at
index-build time via :func:`prime`, then reused on every query.
"""

from __future__ import annotations

import math
from typing import Any

from ..models import EndpointRecord, FIELD_NAMES
from .levenshtein import _distance


def prime(records: list[EndpointRecord]) -> dict[str, Any]:
    """Precompute corpus statistics needed for BM25F scoring."""
    n_docs = len(records)
    # Average length per field.
    field_len_sum: dict[str, int] = {f: 0 for f in FIELD_NAMES}
    # Document frequency: number of docs whose *combined* token set contains the term.
    doc_freq: dict[str, int] = {}

    for rec in records:
        for f in FIELD_NAMES:
            field_len_sum[f] += len(rec.field_tokens.get(f, []))
        for term in rec.all_tokens:
            doc_freq[term] = doc_freq.get(term, 0) + 1

    avg_field_len = {
        f: (field_len_sum[f] / n_docs if n_docs else 0.0) for f in FIELD_NAMES
    }
    # Bucket the vocabulary by token length so fuzzy expansion only scans the
    # relevant length band instead of the whole vocabulary on every query.
    vocab_by_len: dict[int, list[str]] = {}
    for term in doc_freq:
        vocab_by_len.setdefault(len(term), []).append(term)
    return {
        "n_docs": n_docs,
        "avg_field_len": avg_field_len,
        "doc_freq": doc_freq,
        "vocab_by_len": vocab_by_len,
    }


def _idf(term: str, stats: dict[str, Any]) -> float:
    n = stats["n_docs"]
    df = stats["doc_freq"].get(term, 0)
    # BM25 probabilistic IDF with +1 smoothing to stay non-negative.
    return math.log(1 + (n - df + 0.5) / (df + 0.5))


def _expand_terms(
    query_tokens: list[str],
    vocab_by_len: dict[int, list[str]],
    *,
    fuzzy_expand: bool,
    fuzzy_max_ratio: float,
    fuzzy_min_token_len: int,
    fuzzy_weight_power: float,
) -> dict[str, float]:
    """Map query tokens (+ fuzzy neighbours) to BM25F contribution weights.

    Exact tokens get weight 1.0. Fuzzy neighbours within the edit-distance budget
    get ``similarity ** power`` (< 1.0), so a corrected typo never outranks an exact
    hit. Done once per query; only vocabulary buckets within ``±max_dist`` of the
    token length are scanned (``|Δlen| > max_dist`` implies distance > max_dist).
    """
    weights: dict[str, float] = {}
    for q in dict.fromkeys(query_tokens):  # unique, order-stable
        weights[q] = 1.0  # exact term always full weight (tf may be 0, harmless)
        if not fuzzy_expand or len(q) < fuzzy_min_token_len:
            continue
        max_dist = max(1, int(len(q) * fuzzy_max_ratio))
        for length in range(len(q) - max_dist, len(q) + max_dist + 1):
            for vt in vocab_by_len.get(length, ()):
                if vt == q:
                    continue
                dist = _distance(q, vt)
                if dist > max_dist:
                    continue
                sim = 1.0 - dist / max(len(q), len(vt))
                w = sim ** fuzzy_weight_power
                if w > weights.get(vt, 0.0):
                    weights[vt] = w
    return weights


def score(
    query_tokens: list[str],
    records: list[EndpointRecord],
    stats: dict[str, Any],
    *,
    field_weights: dict[str, tuple[float, float]],
    k1: float,
    fuzzy_expand: bool = False,
    fuzzy_max_ratio: float = 0.34,
    fuzzy_min_token_len: int = 4,
    fuzzy_weight_power: float = 2.0,
) -> list[float]:
    """Return a BM25F score per record, aligned with ``records`` order."""
    if not query_tokens or not records:
        return [0.0] * len(records)

    avg_len = stats["avg_field_len"]
    term_weights = _expand_terms(
        query_tokens, stats.get("vocab_by_len", {}),
        fuzzy_expand=fuzzy_expand, fuzzy_max_ratio=fuzzy_max_ratio,
        fuzzy_min_token_len=fuzzy_min_token_len, fuzzy_weight_power=fuzzy_weight_power,
    )
    idf_cache = {t: _idf(t, stats) for t in term_weights}

    scores: list[float] = []
    for rec in records:
        # Per-field term-frequency maps + normalization denominators.
        field_tf: dict[str, dict[str, int]] = {}
        field_norm: dict[str, float] = {}
        for f in FIELD_NAMES:
            toks = rec.field_tokens.get(f, [])
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            field_tf[f] = tf
            w_c, b_c = field_weights.get(f, (1.0, 0.75))
            avg = avg_len.get(f, 0.0) or 1.0
            field_norm[f] = 1.0 + b_c * (len(toks) / avg - 1.0)

        doc_score = 0.0
        for term, term_weight in term_weights.items():
            f_tilde = 0.0
            for f in FIELD_NAMES:
                fc = field_tf[f].get(term, 0)
                if not fc:
                    continue
                w_c, _b_c = field_weights.get(f, (1.0, 0.75))
                denom = field_norm[f] or 1.0
                f_tilde += w_c * (fc / denom)
            if f_tilde > 0.0:
                doc_score += term_weight * idf_cache[term] * (f_tilde / (k1 + f_tilde))
        scores.append(doc_score)
    return scores
