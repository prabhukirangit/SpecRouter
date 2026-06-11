"""The default ``openapi`` source adapter: fetch an OpenAPI/Swagger spec and parse it.

This wraps the existing fetcher + parser so the standard spec path is just one adapter
among others. Behavior is identical to the pre-adapter pipeline.
"""

from __future__ import annotations

from .. import fetcher
from ..config import Settings
from ..models import IndexBundle
from ..parser import parse_spec
from .base import SourceResult


class OpenApiAdapter:
    name = "openapi"

    def load(self, settings: Settings) -> SourceResult:
        fetched = fetcher.fetch_spec(settings.spec_url, timeout=settings.request_timeouts())
        records, base_url = parse_spec(
            fetched.spec,
            base_url_override=settings.base_url,
            output_schema_max_depth=settings.output_schema_max_depth,
            spec_url=settings.spec_url,
        )
        return SourceResult(
            records=records,
            base_url=base_url,
            source_hash=fetched.content_hash,
            etag=fetched.etag,
            last_modified=fetched.last_modified,
        )

    def is_fresh(self, settings: Settings, cached: IndexBundle) -> bool:
        """Cheap HEAD probe comparing ETag / Last-Modified (no body download)."""
        meta = fetcher.head_meta(settings.spec_url, timeout=settings.request_timeouts())
        if meta is None:
            return False
        if meta.etag and cached.etag:
            return meta.etag == cached.etag
        if meta.last_modified and cached.last_modified:
            return meta.last_modified == cached.last_modified
        return False
