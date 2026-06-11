"""Source adapters: pluggable ingesters that turn an API documentation source into
``EndpointRecord``s. Selected via ``SPECROUTER_SOURCE_ADAPTER`` (``openapi`` | ``custom``
| ``module:callable``). The retrieval/execution pipeline is adapter-agnostic.
"""

from .base import SourceAdapter, SourceResult, resolve_adapter

__all__ = ["SourceAdapter", "SourceResult", "resolve_adapter"]
