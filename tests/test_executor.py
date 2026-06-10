import json

import httpx
import pytest

from specrouter import executor
from specrouter.config import Settings
from specrouter.executor import ExecutionError, execute


@pytest.fixture
def captured(monkeypatch):
    """Patch executor's httpx.Client to a MockTransport that records the request."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(executor.httpx, "Client", factory)
    return seen


def test_path_query_split(bundle, settings, captured):
    result = execute(
        "/v1/users/{id}/billing", "GET", {"id": "acct-42", "limit": 10}, bundle, settings
    )
    req = captured["request"]
    assert str(req.url) == "https://api.example.com/v1/users/acct-42/billing?limit=10"
    assert result["status"] == 200


def test_body_params_for_post(bundle, settings, captured):
    execute("/v1/users", "POST", {"email": "a@b.com", "name": "Ada"}, bundle, settings)
    req = captured["request"]
    assert json.loads(req.content) == {"email": "a@b.com", "name": "Ada"}


def test_missing_required_raises(bundle, settings, captured):
    with pytest.raises(ExecutionError):
        execute("/v1/users/{id}/billing", "GET", {"limit": 5}, bundle, settings)


def test_unknown_route_raises(bundle, settings, captured):
    with pytest.raises(ExecutionError):
        execute("/nope", "GET", {}, bundle, settings)


def test_bearer_auth_header(bundle, captured):
    s = Settings(spec_url="x", auth_mode="bearer", bearer_token="secret-token")
    execute("/v1/system/alerts", "GET", {}, bundle, s)
    assert captured["request"].headers["authorization"] == "Bearer secret-token"


def test_api_key_header(bundle, captured):
    s = Settings(spec_url="x", auth_mode="api_key", api_key="K123", api_key_header="X-API-Key")
    execute("/v1/system/alerts", "GET", {}, bundle, s)
    assert captured["request"].headers["x-api-key"] == "K123"


def test_basic_auth(bundle, captured):
    s = Settings(spec_url="x", auth_mode="basic", basic_user="u", basic_pass="p")
    execute("/v1/system/alerts", "GET", {}, bundle, s)
    assert captured["request"].headers["authorization"].startswith("Basic ")


def test_custom_extra_headers(bundle, captured):
    s = Settings(spec_url="x", auth_mode="custom", extra_headers={"X-Tenant": "acme"})
    execute("/v1/system/alerts", "GET", {}, bundle, s)
    assert captured["request"].headers["x-tenant"] == "acme"
