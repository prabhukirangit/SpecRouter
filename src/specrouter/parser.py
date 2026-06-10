"""OpenAPI/Swagger spec -> list[EndpointRecord].

Walks ``paths -> {verb}``, resolves local ``$ref`` pointers, extracts parameters
(path/query/header + request-body properties), and primes the per-field token bags
that the retrieval engines consume.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from .models import EndpointRecord, FIELD_NAMES, Parameter
from .tokenizer import tokenize

_HTTP_VERBS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
_LEVENSHTEIN_FIELDS = ("operation_id", "path")
_NAME_SANITIZE = re.compile(r"[^a-zA-Z0-9]+")


def parse_spec(
    spec: dict[str, Any],
    *,
    base_url_override: str | None = None,
    output_schema_max_depth: int = 4,
    spec_url: str | None = None,
) -> tuple[list[EndpointRecord], str | None]:
    """Parse a loaded OpenAPI document into records and a resolved base URL."""
    records: list[EndpointRecord] = []
    paths = spec.get("paths") or {}

    for raw_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        # Parameters declared at the path level apply to every operation under it.
        shared_params = path_item.get("parameters", [])

        for verb in _HTTP_VERBS:
            operation = path_item.get(verb)
            if not isinstance(operation, dict):
                continue
            records.append(
                _build_record(
                    spec, raw_path, verb.upper(), operation, shared_params,
                    output_schema_max_depth=output_schema_max_depth,
                )
            )

    base_url = base_url_override or _resolve_base_url(spec, spec_url)
    return records, base_url


def _build_record(
    spec: dict[str, Any],
    path: str,
    method: str,
    operation: dict[str, Any],
    shared_params: list[Any],
    *,
    output_schema_max_depth: int = 4,
) -> EndpointRecord:
    operation_id = operation.get("operationId") or _synth_operation_id(method, path)
    summary = operation.get("summary") or ""
    description = operation.get("description") or ""
    tags = [str(t) for t in operation.get("tags", []) if t]

    parameters = _extract_parameters(spec, operation, shared_params)
    output_schema = _extract_output_schema(spec, operation, max_depth=output_schema_max_depth)

    record = EndpointRecord(
        method=method,
        path=path,
        operation_id=operation_id,
        summary=summary,
        description=description,
        tags=tags,
        parameters=parameters,
        tool_name=_synth_tool_name(method, path),
        output_schema=output_schema,
    )
    _prime_fields(record)
    return record


def _extract_parameters(
    spec: dict[str, Any], operation: dict[str, Any], shared_params: list[Any]
) -> list[Parameter]:
    params: list[Parameter] = []
    seen: set[tuple[str, str]] = set()

    for raw in list(shared_params) + list(operation.get("parameters", [])):
        resolved = _deref(spec, raw)
        if not isinstance(resolved, dict):
            continue
        name = resolved.get("name")
        location = resolved.get("in")
        if not name or not location:
            continue
        key = (str(name), str(location))
        if key in seen:
            continue
        seen.add(key)
        schema = _deref(spec, resolved.get("schema", {})) or {}
        params.append(
            Parameter(
                name=str(name),
                location=str(location),
                required=bool(resolved.get("required", location == "path")),
                schema_type=str(schema.get("type", "string")),
                description=str(resolved.get("description", "")),
            )
        )

    params.extend(_extract_body_params(spec, operation))
    return params


def _extract_body_params(spec: dict[str, Any], operation: dict[str, Any]) -> list[Parameter]:
    """Flatten a JSON request body's top-level properties into body parameters."""
    request_body = _deref(spec, operation.get("requestBody", {}))
    if not isinstance(request_body, dict):
        return []
    content = request_body.get("content", {})
    media = content.get("application/json") or next(
        (v for v in content.values() if isinstance(v, dict)), None
    )
    if not isinstance(media, dict):
        return []
    schema = _deref(spec, media.get("schema", {})) or {}
    properties = schema.get("properties", {})
    required_names = set(schema.get("required", []))

    out: list[Parameter] = []
    for prop_name, prop_schema in properties.items():
        prop_schema = _deref(spec, prop_schema) or {}
        out.append(
            Parameter(
                name=str(prop_name),
                location="body",
                required=prop_name in required_names,
                schema_type=str(prop_schema.get("type", "string")),
                description=str(prop_schema.get("description", "")),
            )
        )
    return out


def _extract_output_schema(
    spec: dict[str, Any], operation: dict[str, Any], *, max_depth: int
) -> dict[str, Any] | None:
    """Compact JSON-Schema of the primary success (2xx) JSON response, or None."""
    responses = operation.get("responses")
    if not isinstance(responses, dict) or not responses:
        return None

    # Prefer 200, then 201, then the first other 2xx, then "default".
    status = None
    if "200" in responses:
        status = "200"
    elif "201" in responses:
        status = "201"
    else:
        status = next(
            (s for s in responses if str(s).startswith("2")),
            "default" if "default" in responses else None,
        )
    if status is None:
        return None

    response = _deref(spec, responses.get(status))
    if not isinstance(response, dict):
        return None
    content = response.get("content")
    if not isinstance(content, dict):
        return None
    media = content.get("application/json") or next(
        (v for v in content.values() if isinstance(v, dict)), None
    )
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    if not isinstance(schema, dict):
        return None
    return _inline_schema(spec, schema, max_depth, set())


def _inline_schema(
    spec: dict[str, Any], node: Any, max_depth: int, seen: set[str], _depth: int = 0
) -> Any:
    """Resolve ``$ref``s and recurse, capping depth and guarding reference cycles."""
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str):
        if ref in seen:  # cycle -> collapse
            return {"type": "object"}
        resolved = _deref(spec, node)
        return _inline_schema(spec, resolved, max_depth, seen | {ref}, _depth)

    if _depth >= max_depth:
        # Too deep: keep just the type hint to avoid context bloat.
        return {"type": node.get("type", "object")}

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            out["properties"] = {
                k: _inline_schema(spec, v, max_depth, seen, _depth + 1)
                for k, v in value.items()
            }
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            out[key] = _inline_schema(spec, value, max_depth, seen, _depth + 1)
        elif key in ("allOf", "oneOf", "anyOf") and isinstance(value, list):
            out[key] = [
                _inline_schema(spec, v, max_depth, seen, _depth + 1) for v in value
            ]
        else:
            out[key] = value
    return out


def _prime_fields(record: EndpointRecord) -> None:
    """Populate ``field_tokens`` (all fields) and ``field_text`` (Levenshtein fields)."""
    param_text = " ".join(p.name for p in record.parameters)
    raw_fields = {
        "operation_id": record.operation_id,
        "summary": record.summary,
        "tags": " ".join(record.tags),
        "path": record.path,
        "parameters": param_text,
        "description": record.description,
    }
    for name in FIELD_NAMES:
        record.field_tokens[name] = tokenize(raw_fields.get(name, ""))

    for name in _LEVENSHTEIN_FIELDS:
        record.field_text[name] = " ".join(record.field_tokens.get(name, []))


def _deref(spec: dict[str, Any], node: Any, _depth: int = 0) -> Any:
    """Resolve a local ``$ref`` (``#/components/...``). External refs are left as-is."""
    if _depth > 50 or not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if not ref or not isinstance(ref, str) or not ref.startswith("#/"):
        return node
    target: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(target, dict) and part in target:
            target = target[part]
        else:
            return {}
    return _deref(spec, target, _depth + 1)


def _resolve_base_url(spec: dict[str, Any], spec_url: str | None = None) -> str | None:
    # OpenAPI 3.x: servers[0].url ; Swagger 2.0: schemes + host + basePath.
    servers = spec.get("servers")
    if isinstance(servers, list) and servers:
        url = servers[0].get("url") if isinstance(servers[0], dict) else None
        if url:
            url = str(url)
            # A relative server URL (e.g. "/api/v3") is only usable once joined to an
            # absolute origin. If the spec came from an http(s) URL, resolve against it.
            if not urlsplit(url).scheme and spec_url and urlsplit(spec_url).scheme in ("http", "https"):
                url = urljoin(spec_url, url)
            return url.rstrip("/")
    host = spec.get("host")
    if host:
        scheme = (spec.get("schemes") or ["https"])[0]
        base_path = spec.get("basePath", "")
        return f"{scheme}://{host}{base_path}".rstrip("/")
    return None


def _synth_operation_id(method: str, path: str) -> str:
    tokens = [t for t in _NAME_SANITIZE.split(path) if t]
    return method.lower() + "".join(t.capitalize() for t in tokens)


def _synth_tool_name(method: str, path: str) -> str:
    # e.g. GET /v1/users/{id}/billing -> get_v1_users_id_billing
    cleaned = _NAME_SANITIZE.sub("_", path).strip("_").lower()
    return f"{method.lower()}_{cleaned}" if cleaned else method.lower()
