# SpecRouter — Project Guide for Claude Code

## What this is
A **Zero-ML, infrastructure-free MCP server** that converts a (potentially massive) OpenAPI/Swagger spec
into exactly two dynamic LLM tools — `discover_tools` and `execute_tool` — plus a `refresh_index` admin
tool. Discovery runs entirely in Python memory via BM25F + Token-Set Jaccard + Levenshtein, fused with
Reciprocal Rank Fusion (RRF). No vector DB, no embeddings, no GPU. Full spec lives in `usecase.md`.

## Guiding philosophy (do not break these)
- **No ML / no vector infra.** Retrieval is deterministic string algorithms only.
- **Default install is pure stdlib.** The only optional dependency is `rapidfuzz` behind the `[fast]` extra,
  used *exclusively* for the Levenshtein fuzzy stage. BM25F / Jaccard / RRF **stay pure stdlib** — never add
  a compiled lib for them; they're already fast at this scale and are the core "zero-vector" differentiator.
- **`rapidfuzz` must be optional and result-identical.** `levenshtein.py` detects it at import and falls back
  to the stdlib DP. A parity test (`tests/test_engines.py::test_levenshtein_parity_with_stdlib`) enforces
  identical distances. `bm25f._distance` reuses the same function, so fuzzy expansion gets the accelerator too.

## Output schema & AI enrichment
- **outputSchema:** `parser._extract_output_schema` picks the primary 2xx JSON response; `_inline_schema`
  resolves `$ref`s (reusing `_deref`) with a depth cap + cycle guard. `discovery._to_tool_schema` emits it as
  a separate top-level `outputSchema` when `settings.include_output_schema`. Stored on `EndpointRecord.output_schema`.
- **Enrichment (opt-in, build-time):** gated by `SPECROUTER_ENRICH`; runs in `index.build_index` *before*
  `bm25f.prime` so the rewritten description is what gets indexed. `enrichment.enrich_records` **replaces**
  `rec.description`, sets `ai_enriched`, and calls `parser._prime_fields` to re-tokenize → retrieval ranks on
  the AI text. Per-endpoint cache (`{cache_key}.enrich.json`) keyed by `_endpoint_hash` so refresh only
  re-calls changed endpoints. Best-effort: per-endpoint failures keep the original description. Providers via
  LangChain `init_chat_model` behind `[ai-*]` extras; missing extra → `ConfigError`. `_build_model` is the
  monkeypatch seam in tests (no network). Runtime stays zero-ML — never called during discovery/execution.

## Fuzzy term expansion (BM25F typo tolerance)
`bm25f._expand_terms` maps each query token to near-neighbour vocabulary terms (Levenshtein ≤ budget),
weighting fuzzy hits by `similarity ** fuzzy_weight_power` so exact matches always dominate. This makes
typo'd queries (`"usrs biling"`) rank the right endpoint *first*, not merely within top-k. Cost control:
the vocab is length-bucketed in `prime()` (`vocab_by_len`), so expansion scans only the `±max_dist` length
band. On pure stdlib this can add ~50–85 ms for very large specs (~2000 endpoints); `[fast]`/rapidfuzz keeps
it single-digit ms. Tunable via `SPECROUTER_FUZZY_*`; disable with `SPECROUTER_FUZZY_EXPAND=false`.

## Module map (`src/specrouter/`)
| File | Responsibility |
| --- | --- |
| `config.py` | `Settings` from `SPECROUTER_*` env vars; BM25F field weights/`b_c` defaults; output-schema + enrichment settings; auth/enrich validation; `.env` loader. |
| `tokenizer.py` | 4-step regex normalization (CamelCase split, separator cleanse, lowercase, stop-words). |
| `models.py` | `EndpointRecord`, `Parameter`, `IndexBundle` dataclasses. |
| `parser.py` | OpenAPI `paths -> {verb}` flatten, local `$ref` resolution, primes per-field token bags; `_extract_output_schema`/`_inline_schema` build the depth-capped `outputSchema`. |
| `enrichment.py` | Opt-in build-time LLM rewrite of descriptions (LangChain `init_chat_model`); per-endpoint hash cache; replaces `description` + re-primes so retrieval ranks on it. |
| `engines/bm25f.py` | Fielded BM25; `prime()` precomputes corpus stats + length-bucketed vocab; `score()` per query with optional fuzzy term expansion (`_expand_terms`). |
| `engines/jaccard.py` | Token-set intersection-over-union. |
| `engines/levenshtein.py` | DP edit distance (+ optional rapidfuzz); `1/(1+dist)` similarity. |
| `engines/rrf.py` | Rank fusion, `k=60`, tie-aware ranking. |
| `fetcher.py` | Fetch spec from URL/`file://`; SHA-256 hash + ETag/Last-Modified capture; HEAD probe. |
| `index.py` | `build_index`, `save_index`/`load_index` (pickle + JSON meta), and **`ensure_index`** (the one shared build/persist/staleness path). |
| `discovery.py` | `discover_tools`: run engines → RRF → emit MCP tool schemas with separate `inputSchema` + `outputSchema` (usecase.md §4 Tool 1). |
| `executor.py` | `execute_tool`: classify args into path/query/header/body, apply auth, httpx call, wrap result. Base URL = `settings.base_url or bundle.base_url` (env override wins at execution time). |
| `auth.py` | Resolve auth mode → httpx kwargs; `none/bearer/api_key/basic/custom/plugin`. |
| `server.py` | FastMCP wiring; `_state` holds the live bundle (swapped on refresh); `run()` supports `stdio` / `streamable-http` / `sse`; advertises the bundled `icon.svg` as a data-URI `Icon` (defensive — skipped on older SDKs). |
| `cli.py` | `specrouter index | serve | refresh`; `serve` takes `--transport/--host/--port` for remote hosting. |

## Where the retrieval math lives
All formulas, field weights (`W_c`/`b_c`), `k1`, RRF `k=60`, and the `"usrs biling"` typo example come
straight from `usecase.md` §3. Keep code and that doc in sync when tuning.

## Run / build / serve
```bash
pip install -e ".[dev,fast]"     # dev = pytest, fast = rapidfuzz accelerator
# optional: ".[ai-openai]" / "[ai-anthropic]" / "[ai-google]" / "[ai-ollama]" / "[ai-all]" for enrichment
specrouter index                 # prebuild + persist the index (runs enrichment if SPECROUTER_ENRICH=true)
specrouter serve                 # start MCP server over stdio
specrouter refresh               # force rebuild
```
`ensure_index` is the single entry point for CLI prebuild, server startup, and the `refresh_index` tool —
change index/persist/refresh behavior there, not in three places.

## Config quick reference
Required: `SPECROUTER_SPEC_URL`. Common: `SPECROUTER_BASE_URL`, `SPECROUTER_CACHE_DIR`, `SPECROUTER_TOP_K`,
`SPECROUTER_RRF_K`. Auth: `SPECROUTER_AUTH_MODE` ∈ `none|bearer|api_key|basic|custom|plugin` with its
matching credential vars (see README). Output schema: `SPECROUTER_INCLUDE_OUTPUT_SCHEMA`,
`SPECROUTER_OUTPUT_SCHEMA_MAX_DEPTH`. Enrichment: `SPECROUTER_ENRICH` +
`SPECROUTER_ENRICH_PROVIDER|MODEL|BASE_URL|API_KEY|CONCURRENCY|MAX_SENTENCES`. Index cache + meta sidecar +
enrich cache are keyed by a hash of the spec URL.

`config.load_settings` auto-loads a `.env` file (`./.env` or `$SPECROUTER_ENV_FILE`) via `_apply_dotenv`;
real env vars override file values. The loader reads with `utf-8-sig` to tolerate BOMs (PowerShell/editors).
`.env.example` documents every variable.

**Transports.** Default is stdio — the MCP client launches `specrouter serve` as a subprocess. For remote
hosting, `specrouter serve --transport streamable-http --host 0.0.0.0 --port 8000` exposes the endpoint at
`/mcp` (or `/sse` for the sse transport); clients connect by URL via `"type": "http"` in `mcp.json`. Security
caveat: `execute_tool` runs live authenticated calls with the server's creds, so any hosted endpoint must be
fronted by HTTPS+auth or network-restricted (documented in README "Remote hosting").

## Tests
```bash
pytest          # 47 tests; ~1s
```
`tests/conftest.py` builds an in-memory bundle from `sample/petstore.json` (no network). Executor tests mock
httpx via `MockTransport`. Output-schema tests live in `test_output_schema.py`; enrichment tests in
`test_enrichment.py` monkeypatch `enrichment._build_model` with a fake LLM (no network, no provider deps).
The rapidfuzz parity test auto-skips when the `[fast]` extra isn't installed.

## Gotchas
- The tokenizer keeps `get` (intent-bearing) but drops `api/http/v1/json` etc. Don't add HTTP verbs to the
  stop-word set — method routing is structural, not lexical.
- `EndpointRecord.field_tokens` keys must stay aligned with `config.DEFAULT_FIELD_WEIGHTS` / `FIELD_NAMES`.
- Bump `index.INDEX_FORMAT_VERSION` whenever the pickled `IndexBundle` layout changes (invalidates caches);
  currently `3` (v2 added `output_schema`/`ai_enriched`; v3 changed base-URL resolution).
- **Base URL:** `parser._resolve_base_url(spec, spec_url)` makes a relative `servers[]` URL (e.g. `/api/v3`)
  absolute by joining it to the spec's origin (only for http(s) spec sources). `executor` then uses
  `settings.base_url or bundle.base_url`, so `SPECROUTER_BASE_URL` overrides immediately without a rebuild.
  Do **not** tell users to blanket-drop `SPECROUTER_BASE_URL`: it's the API-FQDN override and is **required**
  when the API and the spec live at different origins, or when `servers[]` is relative/templated/missing.
  It's only redundant when `servers[]` already declares the correct absolute API URL (the Petstore case).
- Enrichment replaces only the `description` field; `operation_id`/`summary`/`tags`/`path` keep their
  original signal. Keep enriched text short (≤ `enrich_max_sentences`) so the damped description field
  doesn't drown structural matches.
