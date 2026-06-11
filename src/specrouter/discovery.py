"""discover_tools: map a natural-language query to top-k MCP tool definitions.

Runs the three engines over the in-memory index, fuses their ranks with RRF, and
emits MCP-shaped tool schemas for the winning endpoints (usecase.md section 4, Tool 1).
"""

from __future__ import annotations

import logging
from typing import Any

from .config import Settings
from .engines import bm25f, jaccard, levenshtein, rrf
from .models import EndpointRecord, IndexBundle
from .tokenizer import tokenize

logger = logging.getLogger(__name__)

_JSON_TYPES = {"integer", "number", "string", "boolean", "array", "object"}


def discover(query: str, bundle: IndexBundle, settings: Settings) -> dict[str, Any]:
    """Return ``{"tools": [...]}`` for the endpoints best matching ``query``."""
    records = bundle.records
    if not records:
        return {"tools": []}

    query_tokens = tokenize(query)
    query_text = " ".join(query_tokens) if query_tokens else (query or "").strip().lower()

    bm25_scores = bm25f.score(
        query_tokens, records, bundle.bm25_stats,
        field_weights=settings.field_weights, k1=settings.k1,
        fuzzy_expand=settings.fuzzy_expand,
        fuzzy_max_ratio=settings.fuzzy_max_ratio,
        fuzzy_min_token_len=settings.fuzzy_min_token_len,
        fuzzy_weight_power=settings.fuzzy_weight_power,
    )
    jaccard_scores = jaccard.score(query_tokens, records)
    lev_scores = levenshtein.score(query_text, records)

    ranked = rrf.top_k(
        [bm25_scores, jaccard_scores, lev_scores],
        k=settings.rrf_k, top=settings.top_k,
    )

    tools = [_to_tool_schema(records[idx], settings) for idx, _score in ranked]
    if logger.isEnabledFor(logging.INFO):
        logger.info(
            "discover q=%r -> %s (%d/%d)",
            query, [t["name"] for t in tools], len(tools), len(records),
        )
    return {"tools": tools}


def _to_tool_schema(rec: EndpointRecord, settings: Settings) -> dict[str, Any]:
    """Render one endpoint as a native-looking MCP tool definition."""
    tag_part = f"[TAGS: {', '.join(rec.tags)}] " if rec.tags else ""
    if rec.ai_enriched:
        # The enriched description is a complete, concise docstring; show it as-is.
        body = rec.description or rec.summary or rec.operation_id
    else:
        body = rec.summary or rec.description[:120] or rec.operation_id
    description = f"{tag_part}{body} Path: {rec.method} {rec.path}"

    properties: dict[str, Any] = {}
    required: list[str] = []
    for p in rec.parameters:
        json_type = p.schema_type if p.schema_type in _JSON_TYPES else "string"
        desc = p.description or f"{p.location} parameter"
        properties[p.name] = {
            "type": json_type,
            "description": f"{desc} (in: {p.location})",
        }
        if p.required:
            required.append(p.name)

    tool: dict[str, Any] = {
        "name": rec.tool_name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
        # Routing hints the LLM passes back to execute_tool.
        "_route": {"path": rec.path, "method": rec.method},
    }
    # Separate, MCP-native outputSchema for the primary 2xx response body.
    if settings.include_output_schema and rec.output_schema:
        tool["outputSchema"] = rec.output_schema
    return tool
