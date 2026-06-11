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
    real_client = executor._CredScopedClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(executor, "_CredScopedClient", factory)
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


def _patch_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = executor._CredScopedClient
    monkeypatch.setattr(
        executor, "_CredScopedClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )


def test_to_json_client_error_is_400(bundle, settings):
    out = json.loads(executor.execute_to_json("/nope", "GET", {}, bundle, settings))
    assert out["status"] == 400
    assert "discover_tools" in out["error"]          # actionable hint for the LLM
    assert json.loads(out["content"])["error"] == out["error"]  # shape mirrors content


def test_to_json_missing_required_is_400(bundle, settings):
    out = json.loads(executor.execute_to_json("/v1/users/{id}/billing", "GET", {}, bundle, settings))
    assert out["status"] == 400
    assert "Missing required parameter" in out["error"]


def test_to_json_upstream_failure_is_502(bundle, settings, monkeypatch):
    _patch_transport(monkeypatch, lambda req: (_ for _ in ()).throw(
        httpx.ConnectError("refused", request=req)))
    out = json.loads(executor.execute_to_json("/v1/system/alerts", "GET", {}, bundle, settings))
    assert out["status"] == 502
    assert "Upstream request failed" in out["error"]


def test_to_json_timeout_is_504(bundle, settings, monkeypatch):
    _patch_transport(monkeypatch, lambda req: (_ for _ in ()).throw(
        httpx.ReadTimeout("slow", request=req)))
    out = json.loads(executor.execute_to_json("/v1/system/alerts", "GET", {}, bundle, settings))
    assert out["status"] == 504
    assert "timed out" in out["error"]


def test_to_json_unexpected_error_is_500(bundle):
    # A broken auth plugin raises ImportError (not an httpx error) -> wrapped as 500,
    # never an opaque tool crash the LLM can't read.
    s = Settings(spec_url="x", auth_mode="plugin", auth_plugin="no_such_module:sign",
                 base_url="https://api.example.com")
    out = json.loads(executor.execute_to_json("/v1/system/alerts", "GET", {}, bundle, s))
    assert out["status"] == 500
    assert "error" in out


def test_upstream_4xx_passed_through(bundle, settings, monkeypatch):
    # A genuine API error response is NOT wrapped — the real status + body reach the LLM.
    _patch_transport(monkeypatch, lambda req: httpx.Response(404, json={"detail": "missing"}))
    out = json.loads(executor.execute_to_json("/v1/system/alerts", "GET", {}, bundle, settings))
    assert out["status"] == 404
    assert "error" not in out                         # success-shaped pass-through
    assert "missing" in out["content"]


# --- async execute path (used by the execute_tool MCP tool) ------------------

@pytest.fixture
def captured_async(monkeypatch):
    """Patch executor's async client to a MockTransport; records request + ctor kwargs."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    real_client = executor._CredScopedAsyncClient

    def factory(*args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(executor, "_CredScopedAsyncClient", factory)
    return seen


async def test_async_path_query_split(bundle, settings, captured_async):
    result = await executor.execute_async(
        "/v1/users/{id}/billing", "GET", {"id": "acct-42", "limit": 10}, bundle, settings
    )
    req = captured_async["request"]
    assert str(req.url) == "https://api.example.com/v1/users/acct-42/billing?limit=10"
    assert result["status"] == 200


async def test_async_applies_granular_timeout(bundle, settings, captured_async):
    # The client must receive an httpx.Timeout (connect vs read/write/pool), not a bare float.
    await executor.execute_async("/v1/system/alerts", "GET", {}, bundle, settings)
    timeout = captured_async["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == settings.connect_timeout
    assert timeout.read == settings.request_timeout


def _patch_async_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = executor._CredScopedAsyncClient
    monkeypatch.setattr(
        executor, "_CredScopedAsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": transport}),
    )


async def test_async_to_json_client_error_is_400(bundle, settings):
    out = json.loads(await executor.execute_to_json_async("/nope", "GET", {}, bundle, settings))
    assert out["status"] == 400
    assert "discover_tools" in out["error"]


async def test_async_to_json_timeout_is_504(bundle, settings, monkeypatch):
    _patch_async_transport(monkeypatch, lambda req: (_ for _ in ()).throw(
        httpx.ReadTimeout("slow", request=req)))
    out = json.loads(
        await executor.execute_to_json_async("/v1/system/alerts", "GET", {}, bundle, settings))
    assert out["status"] == 504
    assert "timed out" in out["error"]


async def test_async_to_json_upstream_failure_is_502(bundle, settings, monkeypatch):
    _patch_async_transport(monkeypatch, lambda req: (_ for _ in ()).throw(
        httpx.ConnectError("refused", request=req)))
    out = json.loads(
        await executor.execute_to_json_async("/v1/system/alerts", "GET", {}, bundle, settings))
    assert out["status"] == 502
    assert "Upstream request failed" in out["error"]


async def test_async_upstream_4xx_passed_through(bundle, settings, monkeypatch):
    _patch_async_transport(monkeypatch, lambda req: httpx.Response(404, json={"detail": "missing"}))
    out = json.loads(
        await executor.execute_to_json_async("/v1/system/alerts", "GET", {}, bundle, settings))
    assert out["status"] == 404
    assert "error" not in out
    assert "missing" in out["content"]


async def test_async_secret_headers_dropped_on_cross_origin_redirect(bundle, monkeypatch):
    """The async client must strip secret headers on off-origin redirects too."""
    s = Settings(
        spec_url="x", auth_mode="api_key", api_key="K123", api_key_header="X-API-Key",
        extra_headers={"X-Tenant": "acme"}, base_url="https://api.example.com",
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.example.com":
            return httpx.Response(
                302, headers={"location": "https://evil.example.net/v1/system/alerts"}
            )
        return httpx.Response(200, json={"ok": True})

    _patch_async_transport(monkeypatch, handler)
    await executor.execute_async("/v1/system/alerts", "GET", {}, bundle, s)

    assert len(seen) == 2
    assert seen[0].headers.get("x-api-key") == "K123"
    assert seen[0].headers.get("x-tenant") == "acme"
    assert "x-api-key" not in seen[1].headers
    assert "x-tenant" not in seen[1].headers


def test_secret_headers_dropped_on_cross_origin_redirect(bundle, monkeypatch):
    """A downstream that redirects off-origin must not receive the api-key / extra headers."""
    s = Settings(
        spec_url="x", auth_mode="api_key", api_key="K123", api_key_header="X-API-Key",
        extra_headers={"X-Tenant": "acme"}, base_url="https://api.example.com",
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.example.com":
            return httpx.Response(
                302, headers={"location": "https://evil.example.net/v1/system/alerts"}
            )
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    real_client = executor._CredScopedClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(executor, "_CredScopedClient", factory)
    execute("/v1/system/alerts", "GET", {}, bundle, s)

    assert len(seen) == 2
    # First hop (same origin) carries the secrets…
    assert seen[0].headers.get("x-api-key") == "K123"
    assert seen[0].headers.get("x-tenant") == "acme"
    # …the cross-origin redirect hop must not.
    assert "x-api-key" not in seen[1].headers
    assert "x-tenant" not in seen[1].headers
