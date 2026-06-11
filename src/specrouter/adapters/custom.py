"""The ``custom`` source adapter: a config-driven HTML documentation scraper.

Turns table/section-based API docs into ``EndpointRecord``s using a CSS-selector rules
file (``SPECROUTER_CUSTOM_RULES``, YAML or JSON). Static HTML only — fetched with httpx,
parsed with BeautifulSoup4 (+lxml). For irregular or JS-rendered sites, write a
``module:callable`` adapter instead.

See ``examples/custom_rules.example.yaml`` for the full schema.
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import yaml

from ..config import ConfigError, Settings
from ..models import EndpointRecord, IndexBundle, Parameter, PATH_PARAM_RE as _PATH_PARAM
from ..parser import _prime_fields, _synth_operation_id, _synth_tool_name
from .base import SourceResult

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_METHOD_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", re.IGNORECASE)


def _bs4():
    try:
        from bs4 import BeautifulSoup  # noqa: F401

        return BeautifulSoup
    except ImportError as exc:
        raise ConfigError(
            "The 'custom' source adapter needs BeautifulSoup. Install it with "
            "`pip install specrouter[scrape]`."
        ) from exc


class CustomHtmlAdapter:
    name = "custom"

    def load(self, settings: Settings) -> SourceResult:
        rules = _load_rules(settings)
        pages = _resolve_page_urls(rules, settings)

        fetched = _fetch_pages(pages, settings)
        records: list[EndpointRecord] = []
        for url in pages:
            html = fetched.get(url)
            if html:
                records.extend(_parse_page(html, url, rules))

        # Mix the rules file content into the hash so editing custom_rules.yaml
        # invalidates the cache even when the fetched HTML hasn't changed.
        rules_bytes = Path(settings.custom_rules).read_bytes() if settings.custom_rules else b""
        rules_hash = hashlib.sha256(rules_bytes).hexdigest()
        html_blob = "".join(fetched[u] for u in sorted(fetched))
        source_hash = hashlib.sha256(
            (rules_hash + html_blob).encode("utf-8", "replace")
        ).hexdigest()
        base_url = settings.base_url or rules.get("base_url")
        return SourceResult(records=records, base_url=base_url, source_hash=source_hash)

    def is_fresh(self, settings: Settings, cached: IndexBundle) -> bool:
        # No cheap validator across many pages — re-load and let the hash comparison decide.
        return False


# --- rules + page enumeration ------------------------------------------------

def _load_rules(settings: Settings) -> dict[str, Any]:
    if not settings.custom_rules:
        raise ConfigError("SPECROUTER_CUSTOM_RULES (path to a rules file) is required.")
    path = Path(settings.custom_rules)
    if not path.is_file():
        raise ConfigError(f"Custom rules file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))  # YAML is a JSON superset
    if not isinstance(data, dict):
        raise ConfigError("Custom rules file must contain a mapping/object.")
    return data


def _resolve_page_urls(rules: dict[str, Any], settings: Settings) -> list[str]:
    pages = rules.get("pages") or {}
    explicit = pages.get("urls")
    if isinstance(explicit, list) and explicit:
        return [str(u) for u in explicit]

    index_url = pages.get("index_url")
    if not index_url:
        raise ConfigError("custom rules: pages.urls or pages.index_url is required.")

    link_selector = pages.get("link_selector")
    if not link_selector:
        return [index_url]  # single-page docs

    BeautifulSoup = _bs4()
    html = _fetch_one(index_url, settings)
    soup = BeautifulSoup(html, "lxml")
    attr = pages.get("link_attr", "href")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.select(link_selector):
        href = a.get(attr)
        if not href:
            continue
        full = urljoin(index_url, href)
        if full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


# --- fetching ----------------------------------------------------------------

def _fetch_one(url: str, settings: Settings) -> str:
    with httpx.Client(timeout=settings.request_timeouts(), follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def _fetch_pages(urls: list[str], settings: Settings) -> dict[str, str]:
    out: dict[str, str] = {}
    if not urls:
        return out
    workers = max(1, min(settings.custom_concurrency, len(urls)))
    with httpx.Client(timeout=settings.request_timeouts(), follow_redirects=True) as client:
        def _get(u: str) -> tuple[str, str | None]:
            try:
                r = client.get(u)
                r.raise_for_status()
                return u, r.text
            except httpx.HTTPError:
                return u, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for url, text in pool.map(_get, urls):
                if text is not None:
                    out[url] = text
    return out


# --- per-page parsing --------------------------------------------------------

def _text(node, selector: str | None, attr: str | None = None) -> str:
    if not selector or node is None:
        return ""
    el = node.select_one(selector)
    if el is None:
        return ""
    if attr:
        return str(el.get(attr, "")).strip()
    return el.get_text(" ", strip=True)


def _parse_page(html: str, page_url: str, rules: dict[str, Any]) -> list[EndpointRecord]:
    BeautifulSoup = _bs4()
    soup = BeautifulSoup(html, "lxml")
    op_rules = rules.get("operation") or {}
    param_rules = rules.get("params") or {}
    body_rules = rules.get("body") or {}

    block_selector = op_rules.get("block_selector")
    blocks = soup.select(block_selector) if block_selector else [soup]

    # Page-scoped parameter table (shared across operations) if scope == "page".
    page_params = (
        _parse_params(soup, param_rules) if param_rules.get("scope") == "page" else None
    )

    records: list[EndpointRecord] = []
    for block in blocks:
        method = _extract_method(block, op_rules)
        path = _text(block, op_rules.get("path_selector"), op_rules.get("path_attr"))
        if not method or not path:
            continue
        path = path.strip()
        raw_params = page_params if page_params is not None else _parse_params(block, param_rules)
        summary = _text(block, op_rules.get("summary_selector"))
        description = _text(block, op_rules.get("description_selector"))
        tags = _split_tags(_text(block, op_rules.get("tags_selector")))

        records.append(
            _build_record(method, path, summary, description, tags, raw_params, body_rules)
        )
    return records


def _extract_method(block, op_rules: dict[str, Any]) -> str:
    raw = _text(block, op_rules.get("method_selector"), op_rules.get("method_attr"))
    m = _METHOD_RE.search(raw)
    return (m.group(1) if m else raw).upper().strip()


def _split_tags(text: str) -> list[str]:
    return [t.strip() for t in re.split(r"[,/]", text) if t.strip()]


def _parse_params(node, param_rules: dict[str, Any]) -> list[dict[str, str]]:
    row_selector = param_rules.get("row_selector")
    if not row_selector:
        return []
    required_match = (param_rules.get("required_match") or "").strip().lower()
    out: list[dict[str, str]] = []
    for row in node.select(row_selector):
        name = _text(row, param_rules.get("name_selector"))
        if not name:
            continue
        req_text = _text(row, param_rules.get("required_selector")).strip().lower()
        required = bool(req_text) and (required_match in req_text if required_match else True)
        out.append(
            {
                "name": name,
                "type": _text(row, param_rules.get("type_selector")) or "string",
                "required": "true" if required else "false",
                "in": _text(row, param_rules.get("in_selector")).lower(),
                "description": _text(row, param_rules.get("description_selector")),
            }
        )
    return out


def _normalize_type(t: str) -> str:
    t = (t or "").lower()
    if "int" in t:
        return "integer"
    if "bool" in t:
        return "boolean"
    if any(k in t for k in ("float", "double", "number", "decimal")):
        return "number"
    if "array" in t or "list" in t:
        return "array"
    if "object" in t or "map" in t:
        return "object"
    return "string"


def _build_record(
    method: str,
    path: str,
    summary: str,
    description: str,
    tags: list[str],
    raw_params: list[dict[str, str]],
    body_rules: dict[str, Any],
) -> EndpointRecord:
    operation_id = _synth_operation_id(method, path)
    path_names = set(_PATH_PARAM.findall(path))
    wrapper_key = body_rules.get("wrapper_key")

    parameters: list[Parameter] = []
    body_fields: list[dict[str, str]] = []

    for p in raw_params:
        name = p["name"]
        ptype = _normalize_type(p["type"])
        required = p["required"] == "true"
        loc = p.get("in") or ""
        if name in path_names or loc == "path":
            parameters.append(Parameter(name, "path", True, ptype, p["description"]))
        elif loc == "header":
            parameters.append(Parameter(name, "header", required, ptype, p["description"]))
        elif loc == "query" or (not loc and method not in _WRITE_METHODS):
            parameters.append(Parameter(name, "query", required, ptype, p["description"]))
        else:  # body
            if wrapper_key and method in _WRITE_METHODS:
                body_fields.append({**p, "type": ptype})
            else:
                parameters.append(Parameter(name, "body", required, ptype, p["description"]))

    if wrapper_key and body_fields:
        # Collapse body fields into a single wrapped object param so the existing
        # executor emits {wrapper_key: {...}} with no executor change.
        field_hints = ", ".join(
            f"{f['name']} ({f['type']}{', required' if f['required'] == 'true' else ''})"
            for f in body_fields
        )
        parameters.append(
            Parameter(
                name=str(wrapper_key),
                location="body",
                required=True,
                schema_type="object",
                description=f"Request body object. Fields: {field_hints}.",
            )
        )

    record = EndpointRecord(
        method=method,
        path=path,
        operation_id=operation_id,
        summary=summary,
        description=description,
        tags=tags,
        parameters=parameters,
        tool_name=_synth_tool_name(method, path),
    )
    _prime_fields(record)
    return record
