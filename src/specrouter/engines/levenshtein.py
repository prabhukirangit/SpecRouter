"""Levenshtein character-fuzzy logic (usecase.md section 3.4).

    Similarity_Lev(Q, D_field) = 1 / (1.0 + Distance_Lev(Query, Field))

The query is compared against each record's high-precision fields (``operation_id``,
``path`` token text); the best (smallest-distance) field wins. This self-corrects
typos such as ``"usrs biling"`` -> ``users billing`` before tool orchestration fails.

Optional accelerator: if the ``[fast]`` extra (``rapidfuzz``) is installed it is used
for the edit-distance computation; otherwise a stdlib dynamic-programming fallback is
used. **Both paths return identical distances.**
"""

from __future__ import annotations

from ..config import LEVENSHTEIN_FIELDS
from ..models import EndpointRecord

try:  # optional [fast] accelerator
    from rapidfuzz.distance import Levenshtein as _rf_lev  # type: ignore

    _HAVE_RAPIDFUZZ = True
except ImportError:  # pragma: no cover - depends on optional install
    _rf_lev = None
    _HAVE_RAPIDFUZZ = False


def using_rapidfuzz() -> bool:
    """True when the rapidfuzz fast path is active (for diagnostics/tests)."""
    return _HAVE_RAPIDFUZZ


def _distance_stdlib(a: str, b: str) -> int:
    """Iterative two-row dynamic-programming Levenshtein edit distance."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(
                prev[j] + 1,       # deletion
                cur[j - 1] + 1,    # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev = cur
    return prev[-1]


def _distance(a: str, b: str) -> int:
    if _HAVE_RAPIDFUZZ:
        return int(_rf_lev.distance(a, b))
    return _distance_stdlib(a, b)


def score(query_text: str, records: list[EndpointRecord]) -> list[float]:
    """Return a best-field Levenshtein similarity per record."""
    query = (query_text or "").strip().lower()
    if not query:
        return [0.0] * len(records)
    out: list[float] = []
    for rec in records:
        best = 0.0
        for f in LEVENSHTEIN_FIELDS:
            target = rec.field_text.get(f, "")
            if not target:
                continue
            dist = _distance(query, target)
            sim = 1.0 / (1.0 + dist)
            if sim > best:
                best = sim
        out.append(best)
    return out
