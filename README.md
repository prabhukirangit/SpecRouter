<p align="center">
  <img src="src/specrouter/specroutermcp-banner.png" alt="SpecRouterMCP" width="100%"/>
</p>

# SpecRouterMCP 🧭

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Server](https://img.shields.io/badge/Protocol-MCP-orange.svg)](https://modelcontextprotocol.io)

**SpecRouterMCP** is a high-performance Model Context Protocol (MCP) server that dynamically converts massive OpenAPI/Swagger specifications into executable LLM tools on-the-fly. Lexical, embedding-free discovery engine that turns any OpenAPI/Swagger spec into MCP tools on demand — an in-memory engine exposing two generic tools (discover + execute) that use BM25F + fuzzy matching to surface only the top-k relevant endpoints, keeping the LLM's context lean.

A lightning-fast, local CPU-bound search matrix, SpecRouterMCP achieves sub-millisecond route discovery while eliminating context-window bloat.

## 🚀 Two-Tool Lazy Loading

Instead of hardcoding hundreds of API endpoints into the LLM context, SpecRouterMCP exposes exactly two tools:

1. **`discover_tools(query)`** — a local, multi-field lexical + set-based + character-fuzzy search engine
   maps natural-language intent to the best-matching endpoints and returns a top-k array of dynamic tool
   definitions, each with a separate **`inputSchema`** and **`outputSchema`**.
2. **`execute_tool(path, method, arguments)`** — takes the selected route and arguments, maps parameters,
   and executes the authenticated live HTTP transaction, returning the raw JSON payload.

A third admin tool, **`refresh_index()`**, re-fetches the spec and rebuilds the index on demand.

Two optional enhancements layer on top: per-tool **output schemas** (derived from each operation's 2xx
response) and opt-in, build-time **AI-enriched descriptions** that sharpen tool selection without adding any
runtime LLM cost. See [Tool schemas](#tool-schemas-input-and-output) and
[AI-enriched descriptions](#ai-enriched-descriptions-optional-build-time) below.

## 🧠 Retrieval Stack (Zero Vectors)

| Engine | Role |
| --- | --- |
| **BM25F** (fielded BM25) | Weights structural fields (`operationId`, `summary`, `tags`, `path`, params) above verbose `description` text. |
| **Token-Set Jaccard** | Intersection-over-union of unique tokens, immune to phrase-length distortion. |
| **Levenshtein** | Character-level edit distance for typo self-correction (`"usrs biling"` → `users billing`). |
| **Reciprocal Rank Fusion** | Fuses the three engines by rank position (`k=60`), no lossy score normalization. |

BM25F additionally applies **fuzzy term expansion**: each query token is matched against the spec
vocabulary so typos contribute real signal in the high-weight structural fields (so `"usrs biling"` ranks
the billing endpoint *first*, not just within top-k). Fuzzy hits are down-weighted by similarity, scan only
the relevant token-length band, and can be disabled via `SPECROUTER_FUZZY_EXPAND=false`.

All engines are pure stdlib. See **Performance** below for the optional `[fast]` accelerator.

## Quickstart (clone & run)

SpecRouter is a standard Python package — use **uv** (recommended) or plain pip/venv.

### Option A — uv (fastest)

```bash
git clone <repo-url> && cd SpecRouter

uv venv                       # create .venv
uv pip install -e ".[fast]"   # install with the rapidfuzz accelerator

cp .env.example .env          # then edit .env and set SPECROUTER_SPEC_URL
uv run specrouter index       # (optional) prebuild + persist the index
uv run specrouter serve       # start the MCP server (stdio)
```

`uv run` auto-activates the project venv, so you don't have to source it manually.

### Option B — pip + venv

```bash
git clone <repo-url> && cd SpecRouter

python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -e ".[fast]"      # or ".[dev,fast]" to also run the tests

cp .env.example .env          # edit .env and set SPECROUTER_SPEC_URL
specrouter serve              # start the MCP server (stdio)
```

### Install extras

| Target | Includes |
| --- | --- |
| `pip install -e .` | pure-stdlib, infrastructure-free default |
| `pip install -e ".[fast]"` | + `rapidfuzz` accelerator for the fuzzy stage |
| `pip install -e ".[dev]"` | + `pytest` for the test suite |

## Configuration via `.env`

SpecRouter automatically loads a `.env` file from the working directory (or the path in
`SPECROUTER_ENV_FILE`). **Real environment variables override `.env` values.** Copy the template and edit:

```bash
cp .env.example .env
```

At minimum set `SPECROUTER_SPEC_URL`. See the full variable table below.

## Configuration (environment variables)

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
| `SPECROUTER_REQUEST_TIMEOUT` | `30` | HTTP timeout (seconds). |

### Auth (applied to `execute_tool` calls)

| `SPECROUTER_AUTH_MODE` | Required vars |
| --- | --- |
| `none` *(default)* | — |
| `bearer` | `SPECROUTER_BEARER_TOKEN` |
| `api_key` | `SPECROUTER_API_KEY` (+ `SPECROUTER_API_KEY_HEADER`, default `X-API-Key`) |
| `basic` | `SPECROUTER_BASIC_USER`, `SPECROUTER_BASIC_PASS` |
| `custom` | `SPECROUTER_EXTRA_HEADERS` (JSON object of static headers) |
| `plugin` | `SPECROUTER_AUTH_PLUGIN` = `module:callable` — a hook `(request_kwargs, settings) -> request_kwargs` for OAuth/mTLS/request signing. |

`SPECROUTER_EXTRA_HEADERS` is merged in under every mode, so you can combine static headers with any scheme.

### Base URL resolution

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

## CLI

```bash
specrouter index     # prebuild + persist the index, then exit (the upfront build script)
specrouter serve     # start the MCP server over stdio (builds on demand if no fresh cache)
specrouter refresh   # force a re-fetch + rebuild + persist, out of band

# Host it as a network service for remote clients:
specrouter serve --transport streamable-http --host 0.0.0.0 --port 8000
```

## Indexing, persistence & refresh

- **Build upfront *or* at startup.** Run `specrouter index` to pre-warm the on-disk index, or just
  `specrouter serve` and it builds in-memory on first boot. Both go through one shared `ensure_index` path.
- **Persisted & hash-keyed.** The index is pickled to `SPECROUTER_CACHE_DIR`, keyed by the spec URL, with a
  JSON `meta` sidecar (`source_hash`, `etag`, `last_modified`, `built_at`, `endpoint_count`).
- **Auto-refresh on change.** On startup a cheap HTTP `HEAD` probe compares `ETag`/`Last-Modified`; if the
  server offers no validators, the body is fetched and compared by SHA-256 content hash. The index is rebuilt
  **only when the spec actually changed.**
- **Manual refresh.** Call the `refresh_index` MCP tool or run `specrouter refresh` to force a rebuild.

## Performance: the optional `[fast]` accelerator

Of the three engines, only **Levenshtein** is genuinely `O(N·m·n)` and becomes the bottleneck as endpoint
counts climb toward the thousands. BM25F, Jaccard, and RRF are already trivially fast in pure Python at this
scale, so they stay stdlib regardless.

Installing the `[fast]` extra (`rapidfuzz`) transparently routes the fuzzy stage through a compiled
batch distance implementation. **Results are identical** to the stdlib path — a parity test enforces this.
The default install stays pure-stdlib so the "infrastructure-free, offline, no binary dependencies" promise
holds out of the box.

### Measured `discover_tools` latency

Added cost of **fuzzy term expansion** over the exact-match baseline (single CPU core):

| Spec size | Vocab | stdlib (added) | rapidfuzz `[fast]` (added) |
| --- | --- | --- | --- |
| ~50 endpoints | ~300 | ~2–3 ms | <1 ms |
| ~500 endpoints | ~2.5K | ~15–20 ms | ~2–3 ms |
| ~2000 endpoints | ~10K | ~50–85 ms | ~1–10 ms |

For small/medium specs, fuzzy expansion is cheap on pure stdlib. For large specs (thousands of endpoints)
install `[fast]` to keep it in single-digit milliseconds, or disable expansion with
`SPECROUTER_FUZZY_EXPAND=false`. Even the worst case stays well under typical LLM round-trip latency.

## Tool schemas: input **and** output

Each tool returned by `discover_tools` carries a separate, MCP-native `inputSchema` and `outputSchema`.
The `outputSchema` is derived from the operation's primary success (2xx) JSON response, with `$ref`s inlined
up to `SPECROUTER_OUTPUT_SCHEMA_MAX_DEPTH` (default 4) so the client LLM knows what `execute_tool` returns
without re-introducing context bloat. Disable with `SPECROUTER_INCLUDE_OUTPUT_SCHEMA=false`.

```jsonc
{
  "name": "get_v1_users_id_billing",
  "description": "[TAGS: Finance] … Path: GET /v1/users/{id}/billing",
  "inputSchema":  { "type": "object", "properties": { "id": { … } }, "required": ["id"] },
  "outputSchema": { "type": "object", "properties": { "invoices": { "type": "array", … } } }
}
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

## Run as an MCP server

An MCP server over stdio is **not a long-running daemon you visit in a browser** — the MCP client
(Claude Desktop, Claude Code, etc.) launches it as a subprocess and talks to it over stdin/stdout. So you
normally don't run `serve` by hand; you point your client's config at the `specrouter serve` command and the
client starts/stops it for you. Running `specrouter serve` in a terminal directly is only useful for manual
testing (it will sit waiting for MCP protocol messages on stdin).

### 1. Verify it works standalone (optional)

```bash
specrouter index     # parses the spec and prints the endpoint count + cache path
```

Or inspect the tools live with the [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
(needs Node/`npx`). Set `SPECROUTER_SPEC_URL` first (env or `.env`):

```bash
# Recommended — runs the real CLI entrypoint (full init), connects the Inspector over stdio:
npx @modelcontextprotocol/inspector specrouter serve

# Or via the MCP dev tool (needs the CLI extras: pip install -e ".[dev]"). Point it at the
# bundled launcher shim, NOT a command string:
mcp dev dev_server.py
```

> Two gotchas with `mcp dev`: it expects a Python **file**, not a command (`mcp dev "specrouter serve"`
> fails), and it imports that file standalone — which breaks package-relative imports. The repo ships a tiny
> `dev_server.py` shim that absolute-imports the server so `mcp dev dev_server.py` works.

### 2. Register with an MCP client (local, stdio)

**Claude Code:**

```bash
claude mcp add specrouter -- specrouter serve
```

(set the env vars in your shell or a `.env` first, or pass `-e KEY=value` flags).

**Claude Desktop / generic `mcpServers` config** — installed console script:

```json
{
  "mcpServers": {
    "specrouter": {
      "command": "specrouter",
      "args": ["serve"],
      "env": {
        "SPECROUTER_SPEC_URL": "https://api.example.com/openapi.json",
        "SPECROUTER_AUTH_MODE": "bearer",
        "SPECROUTER_BEARER_TOKEN": "..."
      }
    }
  }
}
```

**Using uv from a cloned checkout** (no global install needed):

```json
{
  "mcpServers": {
    "specrouter": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/SpecRouter", "specrouter", "serve"],
      "env": {
        "SPECROUTER_SPEC_URL": "https://api.example.com/openapi.json"
      }
    }
  }
}
```

> The `env` block in client config and a local `.env` file both work; the client's `env` values take
> precedence. If you omit `env` entirely, SpecRouter falls back to the `.env` in its working directory.

## Remote hosting (one shared server, many clients)

Instead of each user launching a local subprocess, you can run SpecRouter once on a host/VM/container and
have clients connect over HTTP. Use the **streamable-http** transport (recommended) or **sse**.

### On the server

```bash
# Install, configure, and start (set SPECROUTER_* via env or a .env in the working dir)
export SPECROUTER_SPEC_URL="https://api.example.com/openapi.json"
export SPECROUTER_AUTH_MODE="bearer"
export SPECROUTER_BEARER_TOKEN="..."

specrouter index    # optional: prebuild the cache
specrouter serve --transport streamable-http --host 0.0.0.0 --port 8000
```

The MCP endpoint is then `http://<host>:8000/mcp` (streamable-http) or `http://<host>:8000/sse` (sse).
Run it under a process manager (systemd, Docker, supervisor) and, for anything beyond localhost, **front it
with HTTPS + auth** (see the security note below).

Example `Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[fast]"
ENV SPECROUTER_SPEC_URL=""
EXPOSE 8000
CMD ["specrouter", "serve", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
```

### On each client — `mcp.json` / `mcpServers`

Clients that support remote MCP (HTTP) connect by **URL**, no local install:

```json
{
  "mcpServers": {
    "specrouter": {
      "type": "http",
      "url": "https://specrouter.your-domain.com/mcp"
    }
  }
}
```

If you put an auth proxy in front, pass a header:

```json
{
  "mcpServers": {
    "specrouter": {
      "type": "http",
      "url": "https://specrouter.your-domain.com/mcp",
      "headers": { "Authorization": "Bearer <client-token>" }
    }
  }
}
```

Add it to Claude Code with:

```bash
claude mcp add --transport http specrouter https://specrouter.your-domain.com/mcp
```

**Stdio-only clients** (some older clients can't speak HTTP directly) can bridge via
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote):

```json
{
  "mcpServers": {
    "specrouter": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://specrouter.your-domain.com/mcp"]
    }
  }
}
```

### ⚠️ Security note for hosted deployments

`execute_tool` makes **live, authenticated calls** to your downstream API using the server's configured
credentials. Anyone who can reach the MCP endpoint can invoke those calls. When hosting beyond localhost:

- Terminate **HTTPS** and require authentication at a reverse proxy (the `headers` block above), or restrict
  network access (VPC / firewall / private network).
- Treat the host's `SPECROUTER_*` auth credentials as secrets (env/secret manager, not committed).
- Consider a least-privilege downstream token, since every connected client shares the server's identity.

## Tests

```bash
pytest
```

Covers tokenizer normalization, each engine, parser `$ref` resolution, end-to-end discovery (including the
typo case), executor parameter routing, all auth modes (httpx mocked), output-schema extraction/inlining, and
description enrichment (with a fake LLM — no network or provider deps). The stdlib/rapidfuzz parity test is
skipped automatically when `rapidfuzz` is not installed.
