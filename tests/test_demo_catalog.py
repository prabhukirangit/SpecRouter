"""Guard test for the local demo catalog (demo/README.md).

Skipped unless FastAPI is installed (`pip install -e .[demo]`) — the demo is not part of the
core package. Keeps the demo from silently rotting: it must serve a valid spec with ~100
category-tagged operations and return live data.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from demo.catalog_api.app import app  # noqa: E402
from demo.catalog_api.data import CATEGORIES  # noqa: E402

client = TestClient(app)


def _operations(spec: dict) -> list[tuple[str, str]]:
    methods = {"get", "post", "put", "patch", "delete"}
    return [(m, p) for p, item in spec["paths"].items() for m in item if m in methods]


def test_spec_has_about_100_category_operations():
    spec = client.get("/openapi.json").json()
    ops = _operations(spec)
    assert len(ops) >= 100, f"expected ~100 operations, got {len(ops)}"
    # Absolute servers[] so SpecRouter can resolve the base URL straight from the spec.
    assert spec["servers"][0]["url"].startswith("http")
    tags = {t for item in spec["paths"].values() for op in item.values() for t in op.get("tags", [])}
    assert set(CATEGORIES).issubset(tags)


def test_endpoints_return_live_data_and_404():
    listing = client.get("/catalog/electronics/products")
    assert listing.status_code == 200
    pid = listing.json()[0]["id"]

    assert client.get(f"/catalog/electronics/products/{pid}").status_code == 200
    assert client.get(f"/catalog/electronics/products/{pid}/specs").status_code == 200
    assert client.post(f"/catalog/toys/products/toy-001/cart", json={"quantity": 2}).json()[
        "quantity"
    ] == 2
    # Unknown product id surfaces a clean 404 (demoed through execute_tool's pass-through).
    assert client.get("/catalog/books/products/nope-999").status_code == 404
