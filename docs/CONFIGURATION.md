# SpecRouter — Configuration Reference

SpecRouter is configured entirely through environment variables (or a `.env` file).
Copy the template and edit:

```bash
cp .env.example .env
```

Real environment variables always override `.env` values. At minimum set `SPECROUTER_SPEC_URL`.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `SPECROUTER_SPEC_URL` | *(required)* | URL (or `file://` path) to the OpenAPI/Swagger spec (JSON or YAML). |
| `SPECROUTER_BASE_URL` | auto from spec `servers[]` | The API host (FQDN) `execute_tool` calls. Auto-derived from `servers[]`. **Set this to override** — required when the API lives at a different origin than the spec, or when `servers[]` is relative/templated/missing. The override wins at execution time, even over a cached index. See [Base URL resolution](#base-url-resolution). |
| `SPECROUTER_CACHE_DIR` | `~/.specrouter` | Where the persisted index + meta sidecar live. |
| `SPECROUTER_TOP_K` | `5` | Max endpoints returned by `discover_tools`. |
| `SPECROUTER_RRF_K` | `60` | RRF smoothing constant. |
| `SPECROUTER_K1` | `1.5` | BM25 term-frequency saturation. |
| `SPECROUTER_FUZZY_EXPAND` | `true` | Fuzzy BM25F term expansion (typo tolerance). Set `false` to disable. |
| `SPECROUTER_FUZZY_MAX_RATIO` | `0.34` | Max edits allowed ≈ ratio × token length (~1 edit per 3 chars). |
| `SPECROUTER_FUZZY_MIN_TOKEN_LEN` | `4` | Only expand query tokens at least this long. |
| `SPECROUTER_FUZZY_WEIGHT_POWER` | `2.0` | Fuzzy contribution = `similarity ** power`; higher = stricter. |
| `SPECROUTER_INCLUDE_OUTPUT_SCHEMA` | `true` | Emit a separate `outputSchema` (primary 2xx response) per tool. |
| `SPECROUTER_OUTPUT_SCHEMA_MAX_DEPTH` | `4` | Cap `$ref` inlining depth in `outputSchema` to avoid bloat. |
| `SPECROUTER_REQUEST_TIMEOUT` | `60` | Per-phase read/write/pool timeout (seconds) for each upstream call. |
| `SPECROUTER_CONNECT_TIMEOUT` | `10` | Timeout (seconds) to establish the connection. A slow/unreachable upstream returns a `504`/`502` instead of hanging. |
| `SPECROUTER_LOG_DIR` | `logs` | Directory for the rotating `specrouter.log` (see [Logging](LOGGING.md)). |
| `SPECROUTER_LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`. |
| `SPECROUTER_LOG_MAX_BYTES` | `5000000` | Rotate the log at ~5 MB. |
| `SPECROUTER_LOG_BACKUP_COUNT` | `5` | Rotated backups to keep. |
| `SPECROUTER_LOG_CONSOLE` | `false` | Also echo logs to stderr (never stdout). |
| `SPECROUTER_LOG_ARGS` | `true` | Include `execute_tool` call args in the audit line. |

## Auth (applied to `execute_tool` calls)

| `SPECROUTER_AUTH_MODE` | Required vars |
| --- | --- |
| `none` *(default)* | — |
| `bearer` | `SPECROUTER_BEARER_TOKEN` |
| `api_key` | `SPECROUTER_API_KEY` (+ `SPECROUTER_API_KEY_HEADER`, default `X-API-Key`) |
| `basic` | `SPECROUTER_BASIC_USER`, `SPECROUTER_BASIC_PASS` |
| `custom` | `SPECROUTER_EXTRA_HEADERS` (JSON object of static headers) |
| `plugin` | `SPECROUTER_AUTH_PLUGIN` = `module:callable` — a hook `(request_kwargs, settings) -> request_kwargs` for OAuth/mTLS/request signing. |

`SPECROUTER_EXTRA_HEADERS` is merged in under every mode, so you can combine static headers with any scheme.

## Base URL resolution

`execute_tool` needs the **API's** base URL, which is **not always the same place the spec is hosted**.
SpecRouter resolves it in this order:

1. **`SPECROUTER_BASE_URL`** (env) — always wins, at build *and* execution time.
2. The spec's `servers[0].url` (OpenAPI 3) or `host` + `schemes` + `basePath` (Swagger 2.0):
   - **Absolute** (`https://api.acme.com/v2`) → used as-is, even if the spec is hosted on a different host.
   - **Relative** (`/api/v3`) → joined to the **spec's origin** (only when the spec was fetched over
     `http(s)`). This assumes the API shares the docs' host.
   - **`{variable}` templated** or **missing** → not usable on its own.

**Set `SPECROUTER_BASE_URL` whenever your API lives at a different origin than your spec** — a common setup
where the OpenAPI doc is published on a portal / GitHub raw / internal wiki while the API runs on its own
FQDN — or when `servers[]` is relative, templated, or absent. It's safe to keep set at all times; it's only
*optional* when `servers[]` already declares the correct absolute API URL.

```bash
# Spec hosted on the docs portal, but the API runs elsewhere:
SPECROUTER_SPEC_URL=https://docs.example.com/openapi.json
SPECROUTER_BASE_URL=https://api.example.com/v2     # execute_tool calls go here
```

## AI-enriched descriptions (optional, build-time)

A terse one-line summary is often a weak signal for the client LLM choosing among tools. Enable enrichment
and SpecRouter uses an LLM **at index-build time** to rewrite each endpoint's description into a concise
(≤ 3–4 sentence) docstring. The generated text **replaces** the original and becomes the baseline for *both*
retrieval/ranking (it feeds the BM25F `description` field) and the description returned to the client.

This is **opt-in and build-time only** — discovery and execution never call an LLM, so the runtime stays
zero-ML / infrastructure-free. Results are persisted and **per-endpoint cached** (keyed by a content hash),
so a `refresh` only re-calls the LLM for endpoints that actually changed.

### Install a provider extra

```bash
pip install -e ".[ai-openai]"     # OpenAI (also covers local OpenAI-compatible servers via base_url)
pip install -e ".[ai-anthropic]"  # Anthropic (Claude)
pip install -e ".[ai-google]"     # Google Gemini
pip install -e ".[ai-ollama]"     # Local Ollama (offline, no API cost)
pip install -e ".[ai-all]"        # all of the above
```

### Configure (env)

| Variable | Example | Description |
| --- | --- | --- |
| `SPECROUTER_ENRICH` | `true` | Turn enrichment on (default `false`). |
| `SPECROUTER_ENRICH_PROVIDER` | `openai` | `openai` \| `anthropic` \| `google_genai` \| `ollama`. |
| `SPECROUTER_ENRICH_MODEL` | `gpt-4o-mini` | Model id (e.g. `claude-haiku-4-5`, `gemini-2.0-flash`, `llama3.1`). |
| `SPECROUTER_ENRICH_BASE_URL` | `http://localhost:11434/v1` | For Ollama / OpenAI-compatible local servers. |
| `SPECROUTER_ENRICH_API_KEY` | — | Override; else provider-standard `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`. |
| `SPECROUTER_ENRICH_CONCURRENCY` | `5` | Parallel LLM calls during build. |
| `SPECROUTER_ENRICH_MAX_SENTENCES` | `4` | Upper bound the prompt enforces on length. |

Enrichment runs automatically inside `specrouter index` / `refresh` / startup when enabled. Build the index
deliberately up front (`specrouter index`) so the one-time LLM cost happens before serving:

```bash
SPECROUTER_ENRICH=true SPECROUTER_ENRICH_PROVIDER=openai SPECROUTER_ENRICH_MODEL=gpt-4o-mini \
  specrouter index
```

If enrichment fails for an endpoint (no key, rate limit, network), SpecRouter logs a warning and keeps that
endpoint's original description — the build never blocks.
