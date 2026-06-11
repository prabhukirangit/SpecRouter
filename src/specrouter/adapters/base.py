"""Source-adapter contract + resolution.

An adapter converts a documentation source (an OpenAPI spec, an HTML doc site, …) into a
``SourceResult`` (records + base URL + a content hash for staleness). ``resolve_adapter``
maps the ``SPECROUTER_SOURCE_ADAPTER`` setting to a built-in adapter or a user-provided
``module:callable`` plugin.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..config import ConfigError, Settings
from ..models import EndpointRecord, IndexBundle


@dataclass
class SourceResult:
    """Output of an adapter's ``load`` — feeds straight into the index builder."""

    records: list[EndpointRecord]
    base_url: str | None
    source_hash: str
    etag: str | None = None
    last_modified: str | None = None


@runtime_checkable
class SourceAdapter(Protocol):
    """Pluggable ingester. ``name`` is informational; ``load`` does the work."""

    name: str

    def load(self, settings: Settings) -> SourceResult: ...

    def is_fresh(self, settings: Settings, cached: IndexBundle) -> bool:
        """Cheap staleness check; default False = always re-load and hash-compare."""
        ...


def resolve_adapter(settings: Settings) -> SourceAdapter:
    """Return the adapter selected by ``settings.source_adapter``.

    - ``openapi`` (default) / ``custom`` → built-in adapters.
    - ``module:callable`` → import and call it; the callable returns a SourceAdapter.
    """
    spec = (settings.source_adapter or "openapi").strip()

    if ":" in spec:
        return _load_plugin_adapter(spec)

    if spec == "openapi":
        from .openapi import OpenApiAdapter

        return OpenApiAdapter()
    if spec == "custom":
        from .custom import CustomHtmlAdapter

        return CustomHtmlAdapter()

    # _validate_source rejects unknown names at config load time; this branch is only
    # reachable when resolve_adapter is called directly (e.g. tests, third-party code).
    raise ConfigError(
        f"SPECROUTER_SOURCE_ADAPTER='{spec}' is not a built-in adapter "
        "('openapi'|'custom') or a 'module:callable' plugin."
    )


def _load_plugin_adapter(spec: str) -> SourceAdapter:
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ConfigError(f"SPECROUTER_SOURCE_ADAPTER must be 'module:callable', got '{spec}'.")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(f"Could not import adapter module '{module_name}': {exc}") from exc
    factory = getattr(module, attr, None)
    if not callable(factory):
        raise ConfigError(f"Adapter plugin '{spec}' is not callable.")
    adapter = factory()
    if not isinstance(adapter, SourceAdapter):
        raise ConfigError(
            f"Adapter plugin '{spec}' must return an object with load()/is_fresh() (a SourceAdapter)."
        )
    return adapter
