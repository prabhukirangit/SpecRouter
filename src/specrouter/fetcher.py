"""Fetch an OpenAPI spec from a remote URL (or ``file://`` path).

Returns both the parsed document and provenance metadata (content hash, ETag,
Last-Modified) used by the index layer to detect when a spec has changed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml


@dataclass
class FetchResult:
    spec: dict[str, Any]
    content_hash: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass
class RemoteMeta:
    """Lightweight metadata obtained without downloading the full body when possible."""

    etag: str | None = None
    last_modified: str | None = None


def _parse_bytes(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = yaml.safe_load(text)  # YAML is a JSON superset; handles both
    if not isinstance(data, dict):
        raise ValueError("Spec did not parse into a JSON/YAML object.")
    return data


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_file_url(url: str) -> bool:
    return url.startswith("file://") or urlsplit(url).scheme in ("", "file")


def _read_file(url: str) -> bytes:
    if url.startswith("file://"):
        path = url[len("file://") :]
        # Windows file URLs look like file:///C:/path -> strip a leading slash.
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
    else:
        path = url
    return Path(path).read_bytes()


def fetch_spec(url: str, *, timeout: float | httpx.Timeout = 30.0) -> FetchResult:
    """Download and parse the spec, capturing change-detection metadata."""
    if _is_file_url(url):
        raw = _read_file(url)
        return FetchResult(spec=_parse_bytes(raw), content_hash=_hash(raw))

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        raw = resp.content
        return FetchResult(
            spec=_parse_bytes(raw),
            content_hash=_hash(raw),
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
        )


def head_meta(url: str, *, timeout: float | httpx.Timeout = 30.0) -> RemoteMeta | None:
    """Cheap HEAD probe for ETag/Last-Modified. Returns None when unavailable."""
    if _is_file_url(url):
        return None
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.head(url)
            if resp.status_code >= 400:
                return None
            return RemoteMeta(
                etag=resp.headers.get("etag"),
                last_modified=resp.headers.get("last-modified"),
            )
    except httpx.HTTPError:
        return None
