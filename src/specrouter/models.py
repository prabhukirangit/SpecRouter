"""Core data structures shared across the retrieval and execution pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Field names used by BM25F. Must align with config.DEFAULT_FIELD_WEIGHTS keys.
FIELD_NAMES = ("operation_id", "summary", "tags", "path", "parameters", "description")

# Shared regex for OpenAPI path-parameter templates like {id} or {user_id}.
PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")


@dataclass
class Parameter:
    """A single endpoint parameter (path / query / header / cookie / body property)."""

    name: str
    location: str  # "path" | "query" | "header" | "cookie" | "body"
    required: bool = False
    schema_type: str = "string"
    description: str = ""


@dataclass
class EndpointRecord:
    """One OpenAPI operation, flattened with pre-tokenized fields for the engines."""

    method: str  # uppercase HTTP verb
    path: str  # raw template path, e.g. /v1/users/{id}/billing
    operation_id: str
    summary: str
    description: str
    tags: list[str] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    tool_name: str = ""

    # Compact JSON-Schema of the primary 2xx response body, or None.
    output_schema: dict[str, Any] | None = None
    # True once an LLM-generated description has replaced the original.
    ai_enriched: bool = False

    # Per-field token bags consumed by BM25F / Jaccard / Levenshtein.
    field_tokens: dict[str, list[str]] = field(default_factory=dict)
    # Raw joined text per Levenshtein field, used for character-distance scoring.
    field_text: dict[str, str] = field(default_factory=dict)

    @property
    def all_tokens(self) -> set[str]:
        """Union of every field's tokens (used by the Jaccard engine)."""
        out: set[str] = set()
        for toks in self.field_tokens.values():
            out.update(toks)
        return out


@dataclass
class IndexBundle:
    """A fully-built, ready-to-serve search index plus provenance metadata."""

    records: list[EndpointRecord]
    base_url: str | None
    source_hash: str
    etag: str | None = None
    last_modified: str | None = None
    built_at: str = ""
    spec_url: str = ""

    # Precomputed BM25F statistics (populated by engines.bm25f.prime).
    bm25_stats: dict[str, Any] = field(default_factory=dict)

    def endpoint_count(self) -> int:
        return len(self.records)
