"""Index build, persistence, and hash-keyed staleness control.

``ensure_index`` is the single shared entry point used by the CLI prebuild
(``specrouter index``), the server startup fallback, and the ``refresh_index``
admin tool — so the build/persist/refresh logic lives in exactly one place.
"""

from __future__ import annotations

import json
import pickle
from datetime import datetime, timezone

from . import fetcher
from .config import Settings
from .engines import bm25f
from .models import IndexBundle
from .parser import parse_spec

# Bump when the pickled IndexBundle layout changes, to invalidate stale caches.
INDEX_FORMAT_VERSION = 3


def build_index(settings: Settings, fetched: fetcher.FetchResult) -> IndexBundle:
    """Parse a freshly fetched spec into a primed, ready-to-serve IndexBundle."""
    records, base_url = parse_spec(
        fetched.spec,
        base_url_override=settings.base_url,
        output_schema_max_depth=settings.output_schema_max_depth,
        spec_url=settings.spec_url,
    )
    if settings.enrich:
        # Build-time only: rewrites descriptions, then priming indexes the new text.
        from . import enrichment

        enrichment.enrich_records(records, settings)
    bundle = IndexBundle(
        records=records,
        base_url=base_url,
        source_hash=fetched.content_hash,
        etag=fetched.etag,
        last_modified=fetched.last_modified,
        built_at=datetime.now(timezone.utc).isoformat(),
        spec_url=settings.spec_url,
    )
    bundle.bm25_stats = bm25f.prime(records)
    return bundle


def save_index(settings: Settings, bundle: IndexBundle) -> None:
    """Persist the bundle (pickle) plus a human-readable meta sidecar (JSON)."""
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    with settings.index_path().open("wb") as fh:
        pickle.dump({"version": INDEX_FORMAT_VERSION, "bundle": bundle}, fh)
    meta = {
        "version": INDEX_FORMAT_VERSION,
        "spec_url": bundle.spec_url,
        "source_hash": bundle.source_hash,
        "etag": bundle.etag,
        "last_modified": bundle.last_modified,
        "built_at": bundle.built_at,
        "endpoint_count": bundle.endpoint_count(),
    }
    settings.meta_path().write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_index(settings: Settings) -> IndexBundle | None:
    """Load a cached bundle if present and version-compatible, else None."""
    path = settings.index_path()
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            payload = pickle.load(fh)
    except (pickle.UnpicklingError, EOFError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != INDEX_FORMAT_VERSION:
        return None
    bundle = payload.get("bundle")
    return bundle if isinstance(bundle, IndexBundle) else None


def _is_fresh(settings: Settings, cached: IndexBundle) -> bool:
    """Decide whether the cached index still matches the remote spec.

    Cheap path: a HEAD probe comparing ETag / Last-Modified. If the server gives
    us neither, fall through and report stale so the caller re-fetches the body
    and compares content hashes.
    """
    meta = fetcher.head_meta(settings.spec_url, timeout=settings.request_timeout)
    if meta is None:
        return False
    if meta.etag and cached.etag:
        return meta.etag == cached.etag
    if meta.last_modified and cached.last_modified:
        return meta.last_modified == cached.last_modified
    return False


def ensure_index(settings: Settings, *, force: bool = False) -> IndexBundle:
    """Return a ready index, rebuilding + persisting only when the spec changed.

    - ``force=True`` always re-fetches and rebuilds (used by ``refresh_index``).
    - Otherwise: load cache; if a cheap HEAD probe proves it fresh, reuse it;
      else fetch the full body and rebuild only if the content hash differs.
    """
    cached = None if force else load_index(settings)

    if cached is not None and _is_fresh(settings, cached):
        return cached

    fetched = fetcher.fetch_spec(settings.spec_url, timeout=settings.request_timeout)

    if cached is not None and not force and fetched.content_hash == cached.source_hash:
        # Body unchanged after all (HEAD lacked validators) — refresh validators only.
        cached.etag = fetched.etag or cached.etag
        cached.last_modified = fetched.last_modified or cached.last_modified
        save_index(settings, cached)
        return cached

    bundle = build_index(settings, fetched)
    save_index(settings, bundle)
    return bundle
