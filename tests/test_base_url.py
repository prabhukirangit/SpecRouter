"""Base-URL resolution: relative server URLs + runtime override precedence."""

import httpx
import pytest

from specrouter import executor
from specrouter.config import Settings
from specrouter.parser import _resolve_base_url, parse_spec

_REL_SPEC = {
    "openapi": "3.0.0",
    "servers": [{"url": "/api/v3"}],
    "paths": {
        "/store/inventory": {"get": {"operationId": "getInventory"}},
    },
}
_ABS_SPEC = {"openapi": "3.0.0", "servers": [{"url": "https://api.example.com/v2"}], "paths": {}}


def test_relative_server_url_resolved_against_spec_origin():
    base = _resolve_base_url(_REL_SPEC, "https://petstore3.swagger.io/api/v3/openapi.json")
    assert base == "https://petstore3.swagger.io/api/v3"


def test_relative_server_url_left_alone_for_file_spec():
    # No http origin to resolve against -> keep relative (best effort).
    base = _resolve_base_url(_REL_SPEC, "file:///tmp/openapi.json")
    assert base == "/api/v3"


def test_absolute_server_url_unchanged():
    assert _resolve_base_url(_ABS_SPEC, "https://elsewhere.test/openapi.json") == "https://api.example.com/v2"


def test_parse_spec_threads_spec_url():
    _records, base = parse_spec(_REL_SPEC, spec_url="https://petstore3.swagger.io/api/v3/openapi.json")
    assert base == "https://petstore3.swagger.io/api/v3"


def test_settings_base_url_overrides_frozen_bundle(bundle, monkeypatch):
    """A cached bundle with a stale/relative base_url is overridden by settings."""
    bundle.base_url = "/api/v3"  # simulate a frozen relative base from an old cache
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        executor.httpx, "Client",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )

    s = Settings(spec_url="x", base_url="https://petstore3.swagger.io/api/v3")
    executor.execute("/v1/system/alerts", "GET", {}, bundle, s)
    assert seen["url"] == "https://petstore3.swagger.io/api/v3/v1/system/alerts"
