"""Token-Set Jaccard distance (usecase.md section 3.3).

    Jaccard(Q, D) = |Q ∩ D| / |Q ∪ D|

Set semantics flatten repeated word counts, insulating the score from phrase-length
variation across verbose multi-parameter endpoints.
"""

from __future__ import annotations

from ..models import EndpointRecord


def score(query_tokens: list[str], records: list[EndpointRecord]) -> list[float]:
    """Return a Jaccard similarity per record, aligned with ``records`` order."""
    q = set(query_tokens)
    if not q:
        return [0.0] * len(records)
    out: list[float] = []
    for rec in records:
        d = rec.all_tokens
        if not d:
            out.append(0.0)
            continue
        inter = len(q & d)
        union = len(q | d)
        out.append(inter / union if union else 0.0)
    return out
