"""Shared pytest fixtures: build an in-memory index from the bundled sample spec."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from specrouter.config import Settings
from specrouter.engines import bm25f
from specrouter.models import IndexBundle
from specrouter.parser import parse_spec

SAMPLE = Path(__file__).resolve().parents[1] / "sample" / "petstore.json"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(spec_url="file://" + str(SAMPLE), cache_dir=tmp_path)


@pytest.fixture
def bundle(settings) -> IndexBundle:
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    records, base_url = parse_spec(spec, base_url_override=settings.base_url)
    b = IndexBundle(
        records=records, base_url=base_url, source_hash="test",
        built_at=datetime.now(timezone.utc).isoformat(), spec_url=settings.spec_url,
    )
    b.bm25_stats = bm25f.prime(records)
    return b
