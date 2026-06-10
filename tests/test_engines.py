import pytest

from specrouter.config import DEFAULT_FIELD_WEIGHTS, DEFAULT_K1
from specrouter.engines import bm25f, jaccard, levenshtein, rrf


def test_bm25f_prefers_operationid_match(bundle):
    stats = bundle.bm25_stats
    scores = bm25f.score(
        ["billing"], bundle.records, stats,
        field_weights=DEFAULT_FIELD_WEIGHTS, k1=DEFAULT_K1,
    )
    best = max(range(len(scores)), key=lambda i: scores[i])
    assert bundle.records[best].operation_id == "getUserBillingHistory"


def test_jaccard_overlap(bundle):
    scores = jaccard.score(["system", "alerts"], bundle.records)
    best = max(range(len(scores)), key=lambda i: scores[i])
    assert bundle.records[best].operation_id == "fetchSystemAlerts"


def test_levenshtein_typo_correction(bundle):
    # "usrs biling" should be closest to the users/billing endpoint text.
    scores = levenshtein.score("users biling", bundle.records)
    best = max(range(len(scores)), key=lambda i: scores[i])
    assert "billing" in bundle.records[best].path or "users" in bundle.records[best].path


def test_levenshtein_stdlib_distance():
    assert levenshtein._distance_stdlib("kitten", "sitting") == 3
    assert levenshtein._distance_stdlib("", "abc") == 3
    assert levenshtein._distance_stdlib("abc", "abc") == 0


def test_rrf_fusion_orders_consistent_winner():
    # doc 0 ranks first in all three engines -> must win.
    s1 = [0.9, 0.1, 0.2]
    s2 = [0.8, 0.3, 0.1]
    s3 = [0.7, 0.2, 0.4]
    fused = rrf.fuse([s1, s2, s3], k=60)
    assert max(range(len(fused)), key=lambda i: fused[i]) == 0


def test_rrf_topk_shape():
    ranked = rrf.top_k([[0.1, 0.9], [0.2, 0.8]], k=60, top=1)
    assert len(ranked) == 1
    assert ranked[0][0] == 1


@pytest.mark.skipif(not levenshtein.using_rapidfuzz(), reason="rapidfuzz not installed")
def test_levenshtein_parity_with_stdlib():
    # The [fast] accelerator must produce identical distances to the stdlib path.
    for a, b in [("usrs biling", "users billing"), ("kitten", "sitting"), ("", "abc")]:
        assert levenshtein._distance(a, b) == levenshtein._distance_stdlib(a, b)
