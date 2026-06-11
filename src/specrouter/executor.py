"""execute_tool: perform the live, authenticated HTTP call (usecase.md section 4, Tool 2).

Locates the selected endpoint in the index, splits the LLM-supplied ``arguments`` into
path substitutions / query params / headers / JSON body according to the endpoint's
parameter spec, applies auth, executes the request, and returns a wrapped JSON result.
"""

from __future__ import annotations

import json
import logging
from typing import Any, NamedTuple

import httpx

from .auth import apply_auth
from .config import Settings
from .models import EndpointRecord, IndexBundle, PATH_PARAM_RE as _PATH_PARAM

logger = logging.getLogger(__name__)
_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class ExecutionError(Exception):
    """Raised for client-side problems (unknown route, missing required params)."""


def _same_origin(a: httpx.URL, b: httpx.URL) -> bool:
    return (a.scheme, a.host, a.port) == (b.scheme, b.host, b.port)


class _CredScopedMixin:
    """Mixin for httpx clients that drops SpecRouter-injected credential headers when a
    response redirects to a *different origin*.

    httpx already strips ``Authorization``/``Cookie`` across origins, but it does not
    know about custom secret headers — the configured api-key header or user-supplied
    ``extra_headers``. Without this, a downstream that 3xx-redirects off-origin would
    leak those secrets to the new host. Same-origin redirects keep the headers so
    legitimate trailing-slash / http→https hops still work.

    ``_redirect_headers`` lives on ``httpx.BaseClient`` (shared by ``Client`` and
    ``AsyncClient``), so this single override serves both the sync and async clients.
    """

    def __init__(
        self, *args: Any, sensitive_headers: set[str] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._sensitive = {h.lower() for h in (sensitive_headers or set())}

    def _redirect_headers(self, request: httpx.Request, url: httpx.URL, method: str) -> Any:
        headers = super()._redirect_headers(request, url, method)
        if self._sensitive and not _same_origin(url, request.url):
            for name in [h for h in headers if h.lower() in self._sensitive]:
                del headers[name]
        return headers


class _CredScopedClient(_CredScopedMixin, httpx.Client):
    """Synchronous credential-scoped client (CLI / direct ``execute`` callers)."""


class _CredScopedAsyncClient(_CredScopedMixin, httpx.AsyncClient):
    """Async credential-scoped client used by the ``execute_tool`` request path so a
    slow upstream never blocks the FastMCP event loop (which runs sync tools inline)."""


def _find_record(bundle: IndexBundle, path: str, method: str) -> EndpointRecord | None:
    method = method.upper()
    for rec in bundle.records:
        if rec.method == method and rec.path == path:
            return rec
    return None


def _classify_args(
    rec: EndpointRecord, path: str, method: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], dict[str, Any]]:
    """Split arguments into (path, query, header, body) buckets."""
    declared = {p.name: p for p in rec.parameters}
    path_param_names = set(_PATH_PARAM.findall(path))

    path_args: dict[str, Any] = {}
    query_args: dict[str, Any] = {}
    header_args: dict[str, str] = {}
    body_args: dict[str, Any] = {}

    for name, value in arguments.items():
        param = declared.get(name)
        if name in path_param_names:
            path_args[name] = value
        elif param is None:
            # Undeclared: default to body for write verbs, query otherwise.
            if method in _BODY_METHODS:
                body_args[name] = value
            else:
                query_args[name] = value
        elif param.location == "path":
            path_args[name] = value
        elif param.location == "query":
            query_args[name] = value
        elif param.location == "header":
            header_args[name] = str(value)
        elif param.location == "body":
            body_args[name] = value
        else:  # cookie or anything else -> query as a safe default
            query_args[name] = value

    return path_args, query_args, header_args, body_args


def _check_required(rec: EndpointRecord, arguments: dict[str, Any]) -> None:
    missing = [p.name for p in rec.parameters if p.required and p.name not in arguments]
    if missing:
        raise ExecutionError(f"Missing required parameter(s): {', '.join(missing)}")


def _build_url(base_url: str | None, path: str, path_args: dict[str, Any]) -> str:
    filled = path
    for name in _PATH_PARAM.findall(path):
        if name not in path_args:
            raise ExecutionError(f"Missing path parameter: {name}")
        filled = filled.replace("{" + name + "}", str(path_args[name]))
    if base_url:
        return base_url.rstrip("/") + "/" + filled.lstrip("/")
    if filled.startswith("http://") or filled.startswith("https://"):
        return filled
    raise ExecutionError(
        "No base URL configured. Set SPECROUTER_BASE_URL or include servers[] in the spec."
    )


class _Prepared(NamedTuple):
    """The pure, I/O-free product of request preparation, shared by sync + async paths."""

    method: str
    url: str
    request_kwargs: dict[str, Any]
    auth: Any
    sensitive: set[str]
    arguments: dict[str, Any]


def _prepare(
    path: str,
    method: str,
    arguments: dict[str, Any] | None,
    bundle: IndexBundle,
    settings: Settings,
) -> _Prepared:
    """Resolve the endpoint, classify args, build the URL + request kwargs + auth.

    Pure (no network); shared verbatim by the sync and async execute paths so the two
    can never drift in how they map arguments or apply auth.
    """
    arguments = arguments or {}
    method = method.upper()

    rec = _find_record(bundle, path, method)
    if rec is None:
        raise ExecutionError(
            f"No endpoint matches {method} {path}. Call discover_tools first."
        )

    _check_required(rec, arguments)
    path_args, query_args, header_args, body_args = _classify_args(
        rec, path, method, arguments
    )
    # Settings override wins at execution time so SPECROUTER_BASE_URL takes effect
    # immediately, without rebuilding a cached index that froze an old base URL.
    base_url = settings.base_url or bundle.base_url
    url = _build_url(base_url, path, path_args)

    request_kwargs: dict[str, Any] = {
        "method": method,
        "url": url,
        "params": query_args or None,
        "headers": header_args or {},
    }
    if body_args:
        request_kwargs["json"] = body_args

    request_kwargs = apply_auth(request_kwargs, settings)
    auth = request_kwargs.pop("auth", None)

    # Secret headers httpx won't strip on its own (it only handles Authorization/Cookie):
    # the api-key header and any user extra_headers. Drop them on cross-origin redirects.
    sensitive: set[str] = set(settings.extra_headers)
    if settings.auth_mode == "api_key":
        sensitive.add(settings.api_key_header)

    return _Prepared(method, url, request_kwargs, auth, sensitive, arguments)


def _finalize(resp: httpx.Response, prep: _Prepared, settings: Settings) -> dict[str, Any]:
    """Emit the audit log line and wrap the response as ``{status, contentType, content}``."""
    if logger.isEnabledFor(logging.INFO):
        args_part = f" args={prep.arguments}" if settings.log_args else ""
        logger.info("EXEC %s %s%s -> %s", prep.method, prep.url, args_part, resp.status_code)
    return {
        "status": resp.status_code,
        "contentType": resp.headers.get("content-type", ""),
        "content": resp.text,
    }


def execute(
    path: str,
    method: str,
    arguments: dict[str, Any] | None,
    bundle: IndexBundle,
    settings: Settings,
) -> dict[str, Any]:
    """Execute the HTTP call synchronously and return ``{status, contentType, content}``."""
    prep = _prepare(path, method, arguments, bundle, settings)
    with _CredScopedClient(
        timeout=settings.request_timeouts(),
        follow_redirects=True,
        sensitive_headers=prep.sensitive,
    ) as client:
        resp = client.request(auth=prep.auth, **prep.request_kwargs)
    return _finalize(resp, prep, settings)


async def execute_async(
    path: str,
    method: str,
    arguments: dict[str, Any] | None,
    bundle: IndexBundle,
    settings: Settings,
) -> dict[str, Any]:
    """Async twin of :func:`execute`: awaits the upstream call so a slow API never
    blocks the FastMCP event loop (and therefore other connected clients)."""
    prep = _prepare(path, method, arguments, bundle, settings)
    async with _CredScopedAsyncClient(
        timeout=settings.request_timeouts(),
        follow_redirects=True,
        sensitive_headers=prep.sensitive,
    ) as client:
        resp = await client.request(auth=prep.auth, **prep.request_kwargs)
    return _finalize(resp, prep, settings)


def _error_response(status: int, message: str) -> str:
    """A structured failure the client LLM can read directly.

    ``error`` is surfaced at the top level (its presence unambiguously signals failure),
    while ``content`` mirrors the success shape (always a string) so a single parser
    handles both. ``status`` carries an HTTP-style code the model already understands.
    """
    return json.dumps({
        "status": status,
        "contentType": "application/json",
        "content": json.dumps({"error": message}),
        "error": message,
    })


def _map_exc(exc: Exception, method: str, path: str, settings: Settings) -> str:
    """Map an execute() exception to a structured ``{status, error, content}`` JSON string.

    Shared by the sync and async wrappers so their failure contract can't drift:
      - **400** invalid request (unknown route, missing required/path params, no base URL)
      - **504** upstream timeout    - **502** other upstream/transport failure
      - **500** unexpected internal error (bad URL, auth-plugin failure, …)
    """
    if isinstance(exc, ExecutionError):
        logger.warning("EXEC %s %s failed (client): %s", method, path, exc)
        return _error_response(400, str(exc))
    if isinstance(exc, httpx.TimeoutException):
        logger.warning("EXEC %s %s timed out: %s", method, path, exc)
        # ConnectTimeout fires at connect_timeout; all other phases use request_timeout.
        timeout_val = settings.connect_timeout if isinstance(exc, httpx.ConnectTimeout) else settings.request_timeout
        return _error_response(504, f"Upstream request timed out after {timeout_val}s: {exc}")
    if isinstance(exc, httpx.HTTPError):
        logger.warning("EXEC %s %s failed (upstream): %s", method, path, exc)
        return _error_response(502, f"Upstream request failed: {exc}")
    # InvalidURL, auth-plugin errors, anything unexpected.
    logger.exception("EXEC %s %s failed (unexpected)", method, path)
    return _error_response(500, f"Internal error executing the request: {exc}")


def execute_to_json(
    path: str,
    method: str,
    arguments: dict[str, Any] | None,
    bundle: IndexBundle,
    settings: Settings,
) -> str:
    """Synchronous: return a JSON string for ``execute_tool``; failures are wrapped, never
    raised, so the client LLM always gets a comprehensible ``{status, error, content}``
    object instead of an opaque tool crash. Genuine upstream 4xx/5xx responses are passed
    through untouched with their real status."""
    try:
        return json.dumps(execute(path, method, arguments, bundle, settings))
    except Exception as exc:
        return _map_exc(exc, method, path, settings)


async def execute_to_json_async(
    path: str,
    method: str,
    arguments: dict[str, Any] | None,
    bundle: IndexBundle,
    settings: Settings,
) -> str:
    """Async twin of :func:`execute_to_json` used by the ``execute_tool`` MCP tool, so the
    event loop stays free during the upstream call. Same wrapped failure contract."""
    try:
        return json.dumps(await execute_async(path, method, arguments, bundle, settings))
    except Exception as exc:
        return _map_exc(exc, method, path, settings)
