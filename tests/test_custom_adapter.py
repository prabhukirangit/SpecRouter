"""Custom (config-driven HTML) adapter: parsing, end-to-end load, wrapped execution."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from specrouter import executor
from specrouter.adapters import custom
from specrouter.adapters.custom import CustomHtmlAdapter
from specrouter.config import Settings
from specrouter.engines import bm25f
from specrouter.models import IndexBundle

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_docs.html"

RULES = {
    "base_url": "https://api.example.com",
    "pages": {"urls": ["https://docs.example.com/widgets.html"]},
    "operation": {
        "block_selector": "section.operation",
        "method_selector": ".http-method",
        "path_selector": ".endpoint-path",
        "summary_selector": ".operation-summary",
        "description_selector": ".operation-description",
    },
    "params": {
        "row_selector": "table.params tbody tr",
        "name_selector": ".param-name",
        "type_selector": ".param-type",
        "required_selector": ".param-required",
        "required_match": "yes",
        "in_selector": ".param-in",
        "description_selector": ".param-desc",
    },
    "body": {"wrapper_key": "widget"},
}


def _records():
    html = FIXTURE.read_text(encoding="utf-8")
    return custom._parse_page(html, "https://docs.example.com/widgets.html", RULES)


def test_parse_operations_and_params():
    recs = {r.tool_name: r for r in _records()}
    assert set(recs) == {"get_widgets_id", "post_widgets"}

    get = recs["get_widgets_id"]
    assert get.method == "GET"
    params = {p.name: p for p in get.parameters}
    assert params["id"].location == "path" and params["id"].required
    assert params["verbose"].location == "query" and params["verbose"].schema_type == "boolean"


def test_body_wrapper_collapses_into_object_param():
    post = next(r for r in _records() if r.tool_name == "post_widgets")
    body = [p for p in post.parameters if p.location == "body"]
    assert len(body) == 1
    assert body[0].name == "widget" and body[0].schema_type == "object"
    # field hints preserved in the description
    assert "name" in body[0].description and "color" in body[0].description


def test_load_end_to_end_with_mocked_http(tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(json.dumps(RULES), encoding="utf-8")  # JSON is valid YAML
    settings = Settings(
        spec_url="adapter:custom", source_adapter="custom",
        custom_rules=str(rules_file), cache_dir=tmp_path,
    )

    html = FIXTURE.read_text(encoding="utf-8")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=html))
    real_client = httpx.Client
    monkeypatch.setattr(
        custom.httpx, "Client",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )

    result = CustomHtmlAdapter().load(settings)
    assert {r.tool_name for r in result.records} == {"get_widgets_id", "post_widgets"}
    assert result.base_url == "https://api.example.com"
    assert result.source_hash  # deterministic content hash present


def test_wrapped_body_executes_through_existing_executor(tmp_path, monkeypatch):
    post = next(r for r in _records() if r.tool_name == "post_widgets")
    bundle = IndexBundle(records=[post], base_url="https://api.example.com", source_hash="t",
                         built_at=datetime.now(timezone.utc).isoformat())
    bundle.bm25_stats = bm25f.prime(bundle.records)

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(201, json={"ok": True})

    transport = httpx.MockTransport(handler)
    real_client = executor._CredScopedClient
    monkeypatch.setattr(
        executor, "_CredScopedClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )

    settings = Settings(spec_url="x")
    executor.execute("/widgets", "POST", {"widget": {"name": "w1", "color": "red"}}, bundle, settings)
    assert seen["url"] == "https://api.example.com/widgets"
    assert json.loads(seen["body"]) == {"widget": {"name": "w1", "color": "red"}}
