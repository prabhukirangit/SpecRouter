"""execute_tool: perform the live, authenticated HTTP call (usecase.md section 4, Tool 2).

Locates the selected endpoint in the index, splits the LLM-supplied ``arguments`` into
path substitutions / query params / headers / JSON body according to the endpoint's
parameter spec, applies auth, executes the request, and returns a wrapped JSON result.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .auth import apply_auth
from .config import Settings
from .models import EndpointRecord, IndexBundle

_PATH_PARAM = re.compile(r"\{([^}]+)\}")
_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class ExecutionError(Exception):
    """Raised for client-side problems (unknown route, missing required params)."""


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


def execute(
    path: str,
    method: str,
    arguments: dict[str, Any] | None,
    bundle: IndexBundle,
    settings: Settings,
) -> dict[str, Any]:
    """Execute the HTTP call and return ``{status, contentType, content}``."""
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

    with httpx.Client(timeout=settings.request_timeout, follow_redirects=True) as client:
        resp = client.request(auth=auth, **request_kwargs)

    content_type = resp.headers.get("content-type", "")
    return {
        "status": resp.status_code,
        "contentType": content_type,
        "content": resp.text,
    }


def execute_to_json(
    path: str,
    method: str,
    arguments: dict[str, Any] | None,
    bundle: IndexBundle,
    settings: Settings,
) -> str:
    """Convenience wrapper returning a JSON string (errors wrapped, never raised)."""
    try:
        return json.dumps(execute(path, method, arguments, bundle, settings))
    except ExecutionError as exc:
        return json.dumps({"status": 400, "contentType": "application/json",
                           "content": json.dumps({"error": str(exc)})})
    except httpx.HTTPError as exc:
        return json.dumps({"status": 502, "contentType": "application/json",
                           "content": json.dumps({"error": f"Upstream request failed: {exc}"})})
