# SpecRouter — Implementation Plan

## Context

`SpecRouter/usecase.md` specifies a new use case: a **Zero-ML, infrastructure-free MCP server** that
dynamically converts massive OpenAPI/Swagger specs into exactly two LLM tools (`discover_tools`,
`execute_tool`), avoiding context-window bloat and vector-DB infrastructure. Discovery is powered by an
in-memory, CPU-bound retrieval pipeline (BM25F + Jaccard + Levenshtein, fused via Reciprocal Rank Fusion).

The repo is currently greenfield — only `usecase.md`, `README.md`, `LICENSE`, `.gitignore` exist. This plan
builds the full Python MCP server from scratch.

**Confirmed decisions (from user):**
- **Spec source:** Remote URL (fetch live OpenAPI JSON/YAML over HTTP[S]). Local file accepted as a trivial bonus.
- **Indexing:** Both — a `specrouter index` CLI to prebuild + persist, and a startup fallback that builds in-memory if no valid cache exists.
- **Persistence/refresh:** Persist index to disk keyed by a content hash/ETag of the source spec; auto-rebuild on startup only if the spec changed; expose a manual `refresh_index` admin tool.
- **Auth (for `execute_tool` downstream calls):** Bearer/API-key via env, Basic auth, custom-headers + pluggable hook, and a no-auth mode. Configured via env vars.

## Goal

A `pip`-installable Python package exposing an MCP server with two tools, a CLI for prebuild/serve/refresh,
pure-stdlib retrieval engines (no numpy / no external search or ML libs), and pluggable auth — matching the
algorithmic spec in `usecase.md` §3–4.

## Tech choices

- **MCP:** official `mcp` Python SDK (`FastMCP`), stdio transport (default), HTTP optional later.
- **HTTP:** `httpx` (spec fetch + downstream execution).
- **YAML specs:** `PyYAML` (URL may serve YAML).
- **Retrieval engines:** pure stdlib by default — implement BM25F, Jaccard, Levenshtein (DP), RRF ourselves to honor the "infrastructure-free / zero-dependency" philosophy.
- **Optional `[fast]` accelerator:** the Levenshtein (fuzzy) stage is the only genuinely `O(N·m·n)` engine and the real bottleneck as N grows toward thousands. Provide an optional `rapidfuzz` extra (`pip install specrouter[fast]`) that `levenshtein.py` detects at import and uses for batch character-distance scoring (~10–30× on that stage), transparently falling back to the stdlib DP when absent. **Identical results either way.** BM25F/Jaccard/RRF stay stdlib regardless — no compiled lib meaningfully beats them at this input size, and they are the core "zero-vector" differentiator. Default install stays pure-stdlib so the headline "infrastructure-free / offline / no binary deps" claim holds out of the box.
- **Python:** 3.10+ (per README badge).

## Proposed layout

```
SpecRouter/
  pyproject.toml                # deps: mcp, httpx, pyyaml; extras: [fast]=rapidfuzz; console_scripts: specrouter
  CLAUDE.md                     # project guide for future Claude Code sessions
  src/specrouter/
    __init__.py
    config.py        # env-driven Settings (spec URL, auth, cache dir, top_k, RRF k, field weights/b_c)
    models.py        # dataclasses: EndpointRecord, FieldDoc, IndexBundle
    fetcher.py       # fetch spec from URL (or file://), compute content hash, capture ETag/Last-Modified
    tokenizer.py     # 4-step regex normalization (CamelCase split, snake/uri cleanse, lower, stop-words)
    parser.py        # OpenAPI /paths -> [verb] flatten into EndpointRecord list w/ fielded text
    engines/
      bm25f.py       # fielded BM25 w/ per-field W_c & b_c from spec §3.1
      jaccard.py     # token-set intersection-over-union
      levenshtein.py # DP edit distance -> 1/(1+dist) similarity
      rrf.py         # rank fusion, k=60
    index.py         # IndexBuilder: build IndexBundle; save/load to cache dir; hash-keyed staleness check
    discovery.py     # discover_tools(query) -> top-k dynamic MCP tool schemas
    executor.py      # execute_tool(path, method, arguments): param split, auth, httpx call, wrap response
    auth.py          # auth strategy resolver: none|bearer|api_key|basic|custom|plugin
    server.py        # FastMCP wiring: discover_tools, execute_tool, refresh_index
    cli.py           # `specrouter index|serve|refresh` (argparse)
  sample/petstore.json          # bundled sample spec for tests/manual runs
  tests/
    test_tokenizer.py
    test_engines.py             # bm25f, jaccard, levenshtein, rrf unit tests
    test_parser.py
    test_discovery.py           # end-to-end discovery on sample spec (incl. typo case "usrs biling")
    test_executor.py            # param mapping + auth header injection (mock httpx)
  README.md                     # add install/run/config/auth/refresh docs
```

## Implementation steps

### 1. Packaging & config
- `pyproject.toml` with deps (`mcp`, `httpx`, `pyyaml`), an optional `[fast]` extra (`rapidfuzz`), and a `specrouter` console entry point → `specrouter.cli:main`.
- `config.py` `Settings` from env:
  - `SPECROUTER_SPEC_URL` (required), `SPECROUTER_CACHE_DIR` (default `~/.specrouter`),
    `SPECROUTER_TOP_K` (default 5), `SPECROUTER_RRF_K` (default 60), `SPECROUTER_BASE_URL` (downstream API base; default derived from spec `servers[]`).
  - Auth: `SPECROUTER_AUTH_MODE` ∈ `none|bearer|api_key|basic|custom|plugin`, plus
    `SPECROUTER_BEARER_TOKEN`, `SPECROUTER_API_KEY` + `SPECROUTER_API_KEY_HEADER` (default `X-API-Key`),
    `SPECROUTER_BASIC_USER`/`SPECROUTER_BASIC_PASS`, `SPECROUTER_EXTRA_HEADERS` (JSON), `SPECROUTER_AUTH_PLUGIN` (`module:callable`).
  - Field weights/`b_c` defaulted from `usecase.md` §3.1 but overridable.

### 2. Tokenizer (`tokenizer.py`) — usecase §3.2
- `tokenize(text) -> list[str]`: CamelCase split via `(?<=[a-z])(?=[A-Z])`; replace `_ - . / { }` with space; lowercase + strip non-alphanumerics; drop API stop-words (`http`, `https`, `api`, `v1`, `v2`, `json`, plus common English fillers).

### 3. Parser (`parser.py`) + models
- `EndpointRecord`: `method`, `path`, `operation_id`, `summary`, `tags`, `description`, `parameters` (path/query/header/body w/ types & required), and per-field tokenized text bags used by the engines.
- Walk spec `paths -> {get,post,...}`; resolve `$ref`s (local component refs) for params/request bodies/schemas; synthesize a stable tool `name` (`{method}_{path}` sanitized, e.g. `get_v1_users_id_billing`).

### 4. Engines (`engines/`) — usecase §3.1, §3.3–3.5
- **bm25f.py:** precompute per-field doc frequencies, avg field lengths; score `Score(D,Q)=Σ IDF·f~/(k1+f~)` with `f~=Σ_c W_c·f_c/(1+b_c(L_c/L_avg,c −1))`. Default `k1≈1.5`; `W_c`/`b_c` per field table.
- **jaccard.py:** `|Q∩D|/|Q∪D|` over token sets (union of all endpoint field tokens).
- **levenshtein.py:** DP distance of query vs `operation_id`/`path` text → `1/(1+dist)`; take best. At import, try `import rapidfuzz` → if present use its batch distance API for this stage; else use the stdlib DP. Same scores, faster path when the `[fast]` extra is installed.
- **rrf.py:** rank each engine's outputs, `RRF(d)=Σ 1/(k+rank_m(d))`, `k=60`; return fused descending order.

### 5. Index (`index.py`) — build + persist + staleness
- `IndexBundle`: records + precomputed engine statistics + `source_hash` + `etag`/`last_modified` + `built_at`.
- `build(spec_dict)` → parses + primes engine stats.
- `save(bundle, cache_dir)` / `load(cache_dir)` via pickle + sidecar `meta.json`.
- `ensure_index(settings)`: HEAD/GET the spec URL (`fetcher`), compute hash/ETag; if cache matches → load; else fetch full, rebuild, persist. Returns ready bundle. Used by both startup and `refresh_index`.

### 6. Discovery (`discovery.py`) — Tool 1
- `discover_tools(query, top_k)`: tokenize query → run 3 engines → RRF → take top-k records → emit MCP-shaped tool defs (`name`, `description` = `[TAGS: …] Summary: … Path: …`, `inputSchema` built from path+query+body params with types/required) per `usecase.md` §4 Tool 1 response.

### 7. Executor (`executor.py`) + auth (`auth.py`) — Tool 2
- `auth.build_auth(settings)` → returns httpx `headers`/`auth` (and optional pluggable callable that mutates the request). Strategies: none, bearer, api_key (custom header), basic, custom headers, plugin (`module:callable(request_kwargs, settings) -> request_kwargs`).
- `execute_tool(path, method, arguments)`: locate `EndpointRecord`; split `arguments` into path-substitutions (fill `{id}`), query params, and JSON body per the endpoint's parameter spec; resolve base URL; apply auth; `httpx.request(...)`; return `{status, contentType, content}` wrapper per §4 Tool 2. Strict validation of `required` params with clear error payloads.

### 8. Server (`server.py`) + CLI (`cli.py`)
- `server.py`: `FastMCP("SpecRouter")`; on startup call `ensure_index`; register `discover_tools`, `execute_tool`, and admin `refresh_index` (forces re-fetch + rebuild + persist, returns new `built_at`/hash + endpoint count).
- `cli.py` (argparse):
  - `specrouter index` — prebuild + persist the index, then exit (the upfront build script).
  - `specrouter serve` — start the MCP server (stdio); uses cache if fresh, else builds.
  - `specrouter refresh` — force rebuild + persist out-of-band.

### 9. Docs & sample
- Bundle `sample/petstore.json`; expand `README.md` with install (incl. `pip install specrouter[fast]`), env config table (all auth modes), CLI usage, the prebuild-vs-startup model, the persist/refresh behavior, and the optional accelerator.

### 10. `CLAUDE.md` (project guide for future sessions)
- Create `SpecRouter/CLAUDE.md` documenting: project purpose (one-liner), the zero-ML / infrastructure-free philosophy, module map (engines, index, discovery, executor, auth, server, cli), how to run (`specrouter index|serve|refresh`), env-var config reference, the stdlib-default + optional `rapidfuzz [fast]` accelerator convention, the rule that BM25F/Jaccard/RRF stay pure-stdlib, where the retrieval math is specified (`usecase.md` §3), and how to run tests (`pytest`).

## Reuse / consistency notes
- All retrieval math comes straight from `usecase.md` §3 — field weights, `k1`, RRF `k=60`, the typo example (`"usrs biling"` → users/billing) becomes a discovery test assertion.
- `ensure_index` is the single shared code path for CLI prebuild, startup fallback, and `refresh_index` — no duplication.
- No external search/ML/vector deps, honoring the project's zero-infrastructure philosophy.

## Verification
1. **Unit tests:** `pytest` — tokenizer splits/stop-words; each engine's scoring on tiny fixtures; RRF ordering; parser `$ref` resolution; discovery returns the right endpoint for a clean query **and** the typo query; executor path/query/body split + auth header injection (httpx mocked). Add a parity test asserting the stdlib and `rapidfuzz` Levenshtein paths produce identical rankings (skipped if `rapidfuzz` not installed).
2. **Index/persist:** run `specrouter index` against `sample/petstore.json` (served via `file://` or a local `http.server`); confirm cache + `meta.json` written; rerun and confirm it loads from cache (no rebuild); change spec → confirm auto-rebuild; call `refresh_index` → confirm forced rebuild.
3. **MCP smoke test:** launch via `mcp dev` / an MCP client over stdio; call `discover_tools("get user billing")` → verify top-k schemas; call `execute_tool` against a public sample API (httpbin or petstore) → verify wrapped `{status, contentType, content}`.
4. **Auth check:** set each `SPECROUTER_AUTH_MODE` and assert the outgoing request carries the expected header/credential (via mocked httpx).
