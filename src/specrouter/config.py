"""Environment-driven configuration for SpecRouter.

All runtime behaviour is configured through ``SPECROUTER_*`` environment variables
so the server can be dropped into MCP client configs without code changes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# --- BM25F field weights (W_c) and length-normalisation (b_c) -----------------
# Defaults taken directly from usecase.md section 3.1. Each entry is (W_c, b_c).
DEFAULT_FIELD_WEIGHTS: dict[str, tuple[float, float]] = {
    "operation_id": (4.0, 0.1),
    "summary": (3.5, 0.5),
    "tags": (3.0, 0.2),
    "path": (2.5, 0.3),
    "parameters": (2.0, 0.4),  # parameter_names & schema_properties
    "description": (1.0, 0.9),
}

# BM25 term-frequency saturation parameter.
DEFAULT_K1 = 1.5

# Fields that Levenshtein scans for character-level typo correction.
LEVENSHTEIN_FIELDS = ("operation_id", "path")

VALID_AUTH_MODES = ("none", "bearer", "api_key", "basic", "custom", "plugin")

# LangChain model_provider identifiers supported for description enrichment.
VALID_ENRICH_PROVIDERS = ("openai", "anthropic", "google_genai", "ollama")


class ConfigError(ValueError):
    """Raised when the environment configuration is invalid or incomplete."""


@dataclass
class Settings:
    """Resolved runtime settings for a SpecRouter instance."""

    spec_url: str
    base_url: str | None = None
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".specrouter")
    top_k: int = 5
    rrf_k: int = 60
    k1: float = DEFAULT_K1
    field_weights: dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_FIELD_WEIGHTS)
    )

    # --- fuzzy term expansion (BM25F typo tolerance) ------------------------
    # When enabled, each query token is expanded to near-neighbour vocabulary
    # terms (Levenshtein), so typos like "biling" contribute real BM25F signal
    # in the high-weight structural fields. Fuzzy hits are down-weighted by
    # similarity so exact matches always dominate.
    fuzzy_expand: bool = True
    fuzzy_max_ratio: float = 0.34  # max edits allowed ~= ratio * token length
    fuzzy_min_token_len: int = 4   # don't expand very short tokens
    fuzzy_weight_power: float = 2.0  # contribution multiplier = similarity ** power

    # --- output schema ------------------------------------------------------
    # Emit a separate MCP-native outputSchema (primary 2xx JSON response body).
    include_output_schema: bool = True
    output_schema_max_depth: int = 4  # cap $ref inlining depth to avoid bloat

    # --- auth ---------------------------------------------------------------
    auth_mode: str = "none"
    bearer_token: str | None = None
    api_key: str | None = None
    api_key_header: str = "X-API-Key"
    basic_user: str | None = None
    basic_pass: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    auth_plugin: str | None = None  # "module:callable"

    # --- AI description enrichment (opt-in, build-time only) ----------------
    enrich: bool = False
    enrich_provider: str | None = None  # openai | anthropic | google_genai | ollama
    enrich_model: str | None = None
    enrich_base_url: str | None = None  # Ollama / OpenAI-compatible local servers
    enrich_api_key: str | None = None   # else provider-standard env var
    enrich_concurrency: int = 5
    enrich_max_sentences: int = 4

    # --- networking ---------------------------------------------------------
    request_timeout: float = 30.0

    def cache_key(self) -> str:
        """Stable filesystem-safe key identifying this spec source."""
        import hashlib

        return hashlib.sha256(self.spec_url.encode("utf-8")).hexdigest()[:16]

    def index_path(self) -> Path:
        return self.cache_dir / f"{self.cache_key()}.index.pkl"

    def meta_path(self) -> Path:
        return self.cache_dir / f"{self.cache_key()}.meta.json"

    def enrich_cache_path(self) -> Path:
        return self.cache_dir / f"{self.cache_key()}.enrich.json"


def _get_bool(env: dict[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _parse_dotenv(text: str) -> dict[str, str]:
    """Minimal ``.env`` parser: ``KEY=VALUE`` lines, ``#`` comments, optional quotes."""
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _apply_dotenv(env: dict[str, str]) -> dict[str, str]:
    """Overlay values from a ``.env`` file as defaults; real env vars win.

    Path is ``SPECROUTER_ENV_FILE`` if set, else ``./.env``. Missing file is a no-op.
    """
    path = Path(env.get("SPECROUTER_ENV_FILE", ".env"))
    if not path.is_file():
        return env
    # utf-8-sig strips a BOM if an editor (or PowerShell) added one.
    file_values = _parse_dotenv(path.read_text(encoding="utf-8-sig"))
    merged = dict(file_values)
    merged.update(env)  # process environment takes precedence over the file
    return merged


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from the process environment (or an override dict).

    A ``.env`` file (``./.env`` or ``$SPECROUTER_ENV_FILE``) is loaded automatically;
    real environment variables override file values.
    """
    env = dict(os.environ if env is None else env)
    env = _apply_dotenv(env)

    spec_url = env.get("SPECROUTER_SPEC_URL")
    if not spec_url:
        raise ConfigError(
            "SPECROUTER_SPEC_URL is required (URL or file:// path to the OpenAPI spec)."
        )

    auth_mode = env.get("SPECROUTER_AUTH_MODE", "none").strip().lower()
    if auth_mode not in VALID_AUTH_MODES:
        raise ConfigError(
            f"SPECROUTER_AUTH_MODE='{auth_mode}' invalid; expected one of {VALID_AUTH_MODES}."
        )

    extra_headers: dict[str, str] = {}
    raw_headers = env.get("SPECROUTER_EXTRA_HEADERS")
    if raw_headers:
        try:
            parsed = json.loads(raw_headers)
            if not isinstance(parsed, dict):
                raise ValueError("must be a JSON object")
            extra_headers = {str(k): str(v) for k, v in parsed.items()}
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"SPECROUTER_EXTRA_HEADERS must be a JSON object: {exc}") from exc

    cache_dir = Path(env.get("SPECROUTER_CACHE_DIR", str(Path.home() / ".specrouter")))

    settings = Settings(
        spec_url=spec_url,
        base_url=env.get("SPECROUTER_BASE_URL") or None,
        cache_dir=cache_dir,
        top_k=int(env.get("SPECROUTER_TOP_K", "5")),
        rrf_k=int(env.get("SPECROUTER_RRF_K", "60")),
        k1=float(env.get("SPECROUTER_K1", str(DEFAULT_K1))),
        fuzzy_expand=_get_bool(env, "SPECROUTER_FUZZY_EXPAND", True),
        fuzzy_max_ratio=float(env.get("SPECROUTER_FUZZY_MAX_RATIO", "0.34")),
        fuzzy_min_token_len=int(env.get("SPECROUTER_FUZZY_MIN_TOKEN_LEN", "4")),
        fuzzy_weight_power=float(env.get("SPECROUTER_FUZZY_WEIGHT_POWER", "2.0")),
        include_output_schema=_get_bool(env, "SPECROUTER_INCLUDE_OUTPUT_SCHEMA", True),
        output_schema_max_depth=int(env.get("SPECROUTER_OUTPUT_SCHEMA_MAX_DEPTH", "4")),
        auth_mode=auth_mode,
        bearer_token=env.get("SPECROUTER_BEARER_TOKEN") or None,
        api_key=env.get("SPECROUTER_API_KEY") or None,
        api_key_header=env.get("SPECROUTER_API_KEY_HEADER", "X-API-Key"),
        basic_user=env.get("SPECROUTER_BASIC_USER") or None,
        basic_pass=env.get("SPECROUTER_BASIC_PASS") or None,
        extra_headers=extra_headers,
        auth_plugin=env.get("SPECROUTER_AUTH_PLUGIN") or None,
        request_timeout=float(env.get("SPECROUTER_REQUEST_TIMEOUT", "30")),
        enrich=_get_bool(env, "SPECROUTER_ENRICH", False),
        enrich_provider=(env.get("SPECROUTER_ENRICH_PROVIDER") or "").strip().lower() or None,
        enrich_model=env.get("SPECROUTER_ENRICH_MODEL") or None,
        enrich_base_url=env.get("SPECROUTER_ENRICH_BASE_URL") or None,
        enrich_api_key=env.get("SPECROUTER_ENRICH_API_KEY") or None,
        enrich_concurrency=int(env.get("SPECROUTER_ENRICH_CONCURRENCY", "5")),
        enrich_max_sentences=int(env.get("SPECROUTER_ENRICH_MAX_SENTENCES", "4")),
    )

    _validate_auth(settings)
    _validate_enrich(settings)
    return settings


def _validate_auth(s: Settings) -> None:
    """Fail fast when an auth mode is selected without its required credentials."""
    if s.auth_mode == "bearer" and not s.bearer_token:
        raise ConfigError("auth_mode=bearer requires SPECROUTER_BEARER_TOKEN.")
    if s.auth_mode == "api_key" and not s.api_key:
        raise ConfigError("auth_mode=api_key requires SPECROUTER_API_KEY.")
    if s.auth_mode == "basic" and not (s.basic_user and s.basic_pass):
        raise ConfigError(
            "auth_mode=basic requires SPECROUTER_BASIC_USER and SPECROUTER_BASIC_PASS."
        )
    if s.auth_mode == "plugin" and not s.auth_plugin:
        raise ConfigError("auth_mode=plugin requires SPECROUTER_AUTH_PLUGIN ('module:callable').")


def _validate_enrich(s: Settings) -> None:
    """Fail fast when enrichment is enabled but misconfigured."""
    if not s.enrich:
        return
    if s.enrich_provider not in VALID_ENRICH_PROVIDERS:
        raise ConfigError(
            f"SPECROUTER_ENRICH=true requires SPECROUTER_ENRICH_PROVIDER in {VALID_ENRICH_PROVIDERS}."
        )
    if not s.enrich_model:
        raise ConfigError("SPECROUTER_ENRICH=true requires SPECROUTER_ENRICH_MODEL.")
