"""Index build, persistence, and hash-keyed staleness control.

``ensure_index`` is the single shared entry point used by the CLI prebuild
(``specrouter index``), the server startup fallback, and the ``refresh_index``
admin tool — so the build/persist/refresh logic lives in exactly one place.
"""

from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime, timezone

from .adapters import SourceResult, resolve_adapter
from .config import Settings
from .engines import bm25f
from .models import IndexBundle

logger = logging.getLogger(__name__)

# Bump when the pickled IndexBundle layout changes, to invalidate stale caches.
INDEX_FORMAT_VERSION = 4


def build_index(settings: Settings, result: SourceResult) -> IndexBundle:
    """Assemble a primed, ready-to-serve IndexBundle from an adapter's SourceResult."""
    records = result.records
    logger.info(
        "Building index from %d records (enrichment=%s)", len(records), settings.enrich
    )
    if settings.enrich:
        # Build-time only: rewrites descriptions, then priming indexes the new text.
        from . import enrichment

        enrichment.enrich_records(records, settings)
    bundle = IndexBundle(
        records=records,
        base_url=result.base_url,
        source_hash=result.source_hash,
        etag=result.etag,
        last_modified=result.last_modified,
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
    logger.info(
        "Saved index -> %s (%d endpoints, hash %s)",
        settings.index_path(), bundle.endpoint_count(), bundle.source_hash[:12],
    )


def load_index(settings: Settings) -> IndexBundle | None:
    """Load a cached bundle if present and version-compatible, else None.

    Security note: the cache is a pickle written by this process into
    ``SPECROUTER_CACHE_DIR`` (``~/.specrouter`` by default). Unpickling executes
    arbitrary code, so the cache directory must be trusted/owner-writable — do not
    point ``SPECROUTER_CACHE_DIR`` at a world-writable or shared location.
    """
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


def ensure_index(settings: Settings, *, force: bool = False) -> IndexBundle:
    """Return a ready index, rebuilding + persisting only when the source changed.

    Works through the selected source adapter (``SPECROUTER_SOURCE_ADAPTER``):
    - ``force=True`` always re-loads and rebuilds (used by ``refresh_index``).
    - Otherwise: load cache; if the adapter's cheap freshness check passes, reuse it;
      else re-load the source and rebuild only if the content hash differs.
    """
    adapter = resolve_adapter(settings)
    logger.info("ensure_index: adapter=%s force=%s", adapter.name, force)
    cached = None if force else load_index(settings)

    if cached is not None and adapter.is_fresh(settings, cached):
        logger.info("Index is fresh (cache hit, %d endpoints) — reusing.", cached.endpoint_count())
        return cached

    logger.info("Loading source via '%s' adapter...", adapter.name)
    result = adapter.load(settings)

    if cached is not None and not force and result.source_hash == cached.source_hash:
        # Source unchanged after all (cheap check lacked validators) — refresh validators only.
        logger.info("Source unchanged (hash match) - keeping cached index.")
        cached.etag = result.etag or cached.etag
        cached.last_modified = result.last_modified or cached.last_modified
        save_index(settings, cached)
        return cached

    logger.info("Source changed or rebuild forced - rebuilding index.")
    bundle = build_index(settings, result)
    save_index(settings, bundle)
    return bundle
