"""Enrichment tests with a fake LLM — no network, no provider deps required."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from specrouter import enrichment
from specrouter.config import Settings
from specrouter.engines import bm25f
from specrouter.models import IndexBundle
from specrouter.parser import parse_spec

SAMPLE = Path(__file__).resolve().parents[1] / "sample" / "petstore.json"
_OP_RE = re.compile(r"operationId: (\S+)")

# A token that appears NOWHERE in the original spec — proof that retrieval ranks
# on the enriched text once we inject it for one specific endpoint.
MARKER = "zylophone"


class FakeMsg:
    def __init__(self, content):
        self.content = content


class FakeModel:
    """Records call count; injects MARKER for billing, fails for alerts."""

    def __init__(self, counter):
        self.counter = counter

    def batch(self, prompts, config=None, return_exceptions=False):
        out = []
        for p in prompts:
            self.counter[0] += 1
            op = _OP_RE.search(p).group(1)
            if op == "fetchSystemAlerts":
                out.append(RuntimeError("simulated provider error"))
            elif op == "getUserBillingHistory":
                out.append(FakeMsg(f"Retrieves account {MARKER} invoices for billing review."))
            else:
                out.append(FakeMsg(f"Concise synthetic docstring for {op}."))
        return out


@pytest.fixture
def enrich_settings(tmp_path) -> Settings:
    return Settings(
        spec_url="file://" + str(SAMPLE), cache_dir=tmp_path,
        enrich=True, enrich_provider="openai", enrich_model="fake-model",
    )


def _records():
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return parse_spec(spec, output_schema_max_depth=4)[0]


def _bundle(records) -> IndexBundle:
    b = IndexBundle(records=records, base_url=None, source_hash="t",
                    built_at=datetime.now(timezone.utc).isoformat())
    b.bm25_stats = bm25f.prime(records)
    return b


def test_description_replaced_and_reprimed(enrich_settings, monkeypatch):
    counter = [0]
    monkeypatch.setattr(enrichment, "_build_model", lambda s: FakeModel(counter))
    records = _records()
    enrichment.enrich_records(records, enrich_settings)

    rec = next(r for r in records if r.operation_id == "getUserBillingHistory")
    assert rec.ai_enriched
    assert MARKER in rec.description
    # Re-primed: the enriched text is now in the description field tokens.
    assert MARKER in rec.field_tokens["description"]


def test_retrieval_ranks_on_enriched_text(enrich_settings, monkeypatch):
    monkeypatch.setattr(enrichment, "_build_model", lambda s: FakeModel([0]))
    records = _records()
    enrichment.enrich_records(records, enrich_settings)
    bundle = _bundle(records)

    # MARKER exists only in the enriched billing description, so the BM25F engine
    # (which indexes the description field) must score that endpoint uniquely highest.
    scores = bm25f.score(
        [MARKER], records, bundle.bm25_stats,
        field_weights=enrich_settings.field_weights, k1=enrich_settings.k1,
    )
    best = max(range(len(scores)), key=lambda i: scores[i])
    assert records[best].operation_id == "getUserBillingHistory"
    assert scores[best] > 0 and sum(1 for s in scores if s > 0) == 1


def test_failure_falls_back_to_original(enrich_settings, monkeypatch):
    monkeypatch.setattr(enrichment, "_build_model", lambda s: FakeModel([0]))
    records = _records()
    original = next(r for r in records if r.operation_id == "fetchSystemAlerts").description
    enrichment.enrich_records(records, enrich_settings)

    rec = next(r for r in records if r.operation_id == "fetchSystemAlerts")
    assert not rec.ai_enriched
    assert rec.description == original


def test_cache_prevents_second_call(enrich_settings, monkeypatch):
    counter = [0]
    monkeypatch.setattr(enrichment, "_build_model", lambda s: FakeModel(counter))

    enrichment.enrich_records(_records(), enrich_settings)
    first = counter[0]
    assert first > 0
    assert enrich_settings.enrich_cache_path().is_file()

    # Second run on identical endpoints: cache hits, billing not re-called.
    records2 = _records()
    enrichment.enrich_records(records2, enrich_settings)
    # Only the always-failing alerts endpoint (never cached) is retried.
    assert counter[0] - first <= 1
    rec = next(r for r in records2 if r.operation_id == "getUserBillingHistory")
    assert rec.ai_enriched and MARKER in rec.description


def test_missing_langchain_raises_configerror(enrich_settings, monkeypatch):
    # Simulate the [ai] extra not being installed.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "langchain.chat_models" or name.startswith("langchain"):
            raise ImportError("no langchain")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from specrouter.config import ConfigError

    with pytest.raises(ConfigError):
        enrichment.enrich_records(_records(), enrich_settings)
