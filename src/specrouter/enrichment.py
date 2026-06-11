"""Opt-in, build-time AI enrichment of endpoint descriptions.

When ``SPECROUTER_ENRICH=true``, each endpoint's description is rewritten by an LLM
(via LangChain's provider-agnostic ``init_chat_model``) into a concise docstring. The
result **replaces** ``EndpointRecord.description`` and is re-tokenized, so it becomes the
baseline for both retrieval/ranking and the returned tool description.

This runs only at index-build time and is per-endpoint cached, so discovery and
execution never call an LLM — the runtime stays zero-ML / infrastructure-free.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from .config import ConfigError, Settings
from .models import EndpointRecord
from .parser import _prime_fields

logger = logging.getLogger("specrouter.enrichment")

_PROMPT_TEMPLATE = (
    "You are documenting a REST API endpoint so an AI agent can decide when to call it.\n"
    "Write a clear, factual description in at most {max_sentences} sentences. Cover what the "
    "endpoint does, when to use it, its key inputs, and what it returns. Plain text only — no "
    "markdown, no preamble, no bullet points.\n\n"
    "Endpoint:\n{context}\n\nDescription:"
)


def _build_model(settings: Settings) -> Any:
    """Construct a LangChain chat model. Raises ConfigError if deps are missing."""
    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:  # optional [ai-*] extra not installed
        raise ConfigError(
            "AI enrichment requires LangChain. Install the provider extra, e.g. "
            f"`pip install specrouter[ai-{_extra_suffix(settings.enrich_provider)}]`."
        ) from exc

    kwargs: dict[str, Any] = {
        "model": settings.enrich_model,
        "model_provider": settings.enrich_provider,
        "temperature": 0,
    }
    if settings.enrich_base_url:
        kwargs["base_url"] = settings.enrich_base_url
    if settings.enrich_api_key:
        kwargs["api_key"] = settings.enrich_api_key
    return init_chat_model(**kwargs)


def _extra_suffix(provider: str | None) -> str:
    return {"google_genai": "google"}.get(provider or "", provider or "openai")


def _endpoint_hash(rec: EndpointRecord) -> str:
    """Stable hash of an endpoint's structural content (changes only on real change)."""
    payload = {
        "method": rec.method,
        "path": rec.path,
        "operation_id": rec.operation_id,
        "summary": rec.summary,
        "description": rec.description,
        "tags": rec.tags,
        "params": [
            {"name": p.name, "in": p.location, "required": p.required, "type": p.schema_type}
            for p in rec.parameters
        ],
        "output_schema": rec.output_schema,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _build_context(rec: EndpointRecord) -> str:
    params = ", ".join(
        f"{p.name} ({p.location}{', required' if p.required else ''}: {p.schema_type})"
        for p in rec.parameters
    ) or "none"
    out_props = ""
    if isinstance(rec.output_schema, dict):
        props = rec.output_schema.get("properties")
        if isinstance(props, dict):
            out_props = ", ".join(props.keys())
    lines = [
        f"Method: {rec.method}",
        f"Path: {rec.path}",
        f"operationId: {rec.operation_id}",
        f"Tags: {', '.join(rec.tags) or 'none'}",
        f"Summary: {rec.summary or 'none'}",
        f"Original description: {rec.description or 'none'}",
        f"Parameters: {params}",
    ]
    if out_props:
        lines.append(f"Response fields: {out_props}")
    return "\n".join(lines)


def _load_cache(settings: Settings) -> dict[str, str]:
    path = settings.enrich_cache_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _save_cache(settings: Settings, cache: dict[str, str]) -> None:
    path = settings.enrich_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_text(result: Any, *, collapse_whitespace: bool = True) -> str:
    """Pull plain text from a LangChain message / string / list response.

    ``collapse_whitespace=False`` preserves newlines (needed for YAML output).
    """
    content = getattr(result, "content", result)
    if isinstance(content, list):  # some providers return content blocks
        content = "".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        )
    text = str(content)
    return " ".join(text.split()).strip() if collapse_whitespace else text


def enrich_records(records: list[EndpointRecord], settings: Settings) -> None:
    """Replace each record's description with an LLM-generated docstring (in place).

    Best-effort: any LLM/transport failure logs a warning and leaves the original
    description intact, never blocking the index build.
    """
    if not records:
        return

    cache = _load_cache(settings)
    hashes = [_endpoint_hash(rec) for rec in records]

    # Apply cache hits immediately; collect misses for a single batched call.
    misses: list[int] = []
    for i, (rec, h) in enumerate(zip(records, hashes)):
        if h in cache:
            _apply(rec, cache[h])
        else:
            misses.append(i)

    logger.info(
        "Enriching %d endpoints: %d cached, %d via LLM (%s/%s).",
        len(records), len(records) - len(misses), len(misses),
        settings.enrich_provider, settings.enrich_model,
    )

    if misses:
        try:
            model = _build_model(settings)
        except ConfigError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Enrichment model init failed (%s); keeping original descriptions.", exc)
            _save_cache(settings, cache)
            return

        prompts = [
            _PROMPT_TEMPLATE.format(
                max_sentences=settings.enrich_max_sentences,
                context=_build_context(records[i]),
            )
            for i in misses
        ]
        try:
            results = model.batch(
                prompts, config={"max_concurrency": settings.enrich_concurrency},
                return_exceptions=True,
            )
        except Exception as exc:  # whole batch failed
            logger.warning("Enrichment batch failed (%s); keeping original descriptions.", exc)
            results = [exc] * len(misses)

        enriched = 0
        failures = 0
        for idx, result in zip(misses, results):
            if isinstance(result, Exception):
                failures += 1
                logger.warning("Enrichment failed for %s: %s", records[idx].tool_name, result)
                continue
            text = _extract_text(result)
            if not text:
                failures += 1
                continue
            cache[hashes[idx]] = text
            _apply(records[idx], text)
            enriched += 1
            logger.debug("Enriched %s", records[idx].tool_name)
        logger.info(
            "Enrichment complete: %d/%d rewritten, %d kept original.",
            enriched, len(misses), failures,
        )

    _save_cache(settings, cache)


def _apply(rec: EndpointRecord, text: str) -> None:
    """Replace the description with enriched text and re-tokenize the record."""
    rec.description = text
    rec.ai_enriched = True
    _prime_fields(rec)
