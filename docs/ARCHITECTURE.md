# SpecRouter — Architecture

## Quick overview

```mermaid
flowchart LR
    USER["👤 User"]
    AGENT["🤖 LLM Agent\nClaude · GPT · Gemini …"]

    subgraph MCP["🔌 SpecRouter MCP Server"]
        DT["discover_tools"]
        ET["execute_tool"]
        RI["refresh_index"]
    end

    DISC["🔍 Discovery Engine\nBM25F + Jaccard + Levenshtein → RRF"]
    BUNDLE[("📦 Index\n_state.bundle")]
    EXEC["⚡ Executor\n_prepare → httpx → _finalize"]
    DAPI["🌐 Downstream API"]

    USER -->|query| AGENT
    AGENT -->|"discover_tools(query)"| DT
    DT --> DISC
    DISC -->|ranked tool schemas| DT
    BUNDLE --> DISC
    DT -->|ranked tool schemas| AGENT
    AGENT -->|"execute_tool(path, method, args)"| ET
    ET --> EXEC
    EXEC -->|"HTTP + auth"| DAPI
    DAPI -->|response| EXEC
    EXEC -->|JSON result| ET
    ET -->|JSON result| AGENT
    AGENT -->|answer| USER
    BUNDLE -.-> DT & ET
    RI -->|rebuild| BUNDLE
```

## Detailed flow

SpecRouter operates in two phases: an **init flow** that builds and persists the search index (once, at startup or on demand), and a **runtime flow** where the LLM agent calls `discover_tools` to find relevant endpoints and `execute_tool` to call them live.

```mermaid
flowchart TD

    subgraph INIT["⚙️ Init Flow — specrouter index / server startup"]
        direction LR
        CFG[".env / SPECROUTER_* env vars\nload_settings()"]
        EI["ensure_index()"]
        RA["resolve_adapter()\nopenapi · custom · module:callable"]
        LOAD["adapter.load()\nfetch + parse → EndpointRecords\n_prime_fields() → field_tokens"]
        ENR{"SPECROUTER_ENRICH\n= true?"}
        LLM["enrich_records()\nLangChain batch — LLM rewrites descriptions\n+ re-primes field tokens\n(per-endpoint hash cache)"]
        BUILD["build_index()\nbm25f.prime() → IndexBundle\n(BM25F corpus stats + length-bucketed vocab)"]
        SAVE["save_index()\n.index.pkl + .meta.json"]
        BUNDLE[("_state.bundle\nin-memory")]

        CFG --> EI --> RA --> LOAD --> ENR
        ENR -- yes --> LLM --> BUILD
        ENR -- no  --> BUILD
        BUILD --> SAVE --> BUNDLE
    end

    subgraph SERVER["🔌 SpecRouter MCP Server (FastMCP · stdio / http / sse)"]
        direction LR
        DT["discover_tools(query)\n— sync —"]
        ET["execute_tool(path, method, args)\n— async —"]
        RI["refresh_index()"]
    end

    subgraph DISC["🔍 Discovery Engine — discover_tools internals"]
        direction TB
        TOK["tokenize(query)\nCamelCase split · stopwords · lowercase"]
        BM25FN["BM25F\nFielded BM25 · 6 weighted fields\noperation_id ×4 · summary ×3.5 · tags ×3\npath ×2.5 · params ×2 · description ×1\n+ Levenshtein fuzzy term expansion"]
        JACCARD["Jaccard\nToken-set IoU\nacross all 6 fields"]
        LSCORE["Levenshtein\nchar edit distance\noperation_id · path"]
        RRF["Reciprocal Rank Fusion  k=60\nfuses 3 independent ranked lists"]
        TOOLS["top-k Tool Schemas\ninputSchema + outputSchema + _route hint"]

        TOK --> BM25FN & JACCARD & LSCORE
        BM25FN & JACCARD & LSCORE --> RRF --> TOOLS
    end

    subgraph EXEC["⚡ Executor — execute_tool internals"]
        direction LR
        PREP["_prepare()\nfind_record → classify_args\nbuild_url → apply_auth"]
        HTTPC["_CredScopedAsyncClient  (httpx)\ndrops secret headers on cross-origin 3xx\ngranular Timeout(request, connect)"]
        FINALIZE["_finalize() / _map_exc()\n{status · contentType · content}\nor {status · error}  (400/502/504/500)"]
    end

    DAPI["🌐 Downstream API"]
    USER["👤 User"]
    AGENT["🤖 LLM Agent\nClaude · GPT · Gemini …"]

    USER -->|natural language query| AGENT
    AGENT -->|"MCP: discover_tools(query)"| DT
    DT --> TOK
    TOOLS -->|ranked tool schemas| DT
    DT -->|ranked tool schemas| AGENT
    AGENT -->|"MCP: execute_tool(path, method, args)"| ET
    ET --> PREP --> HTTPC
    HTTPC -->|"HTTP request + auth"| DAPI
    DAPI -->|response| HTTPC
    HTTPC --> FINALIZE
    FINALIZE -->|JSON result| ET
    ET -->|JSON result| AGENT
    AGENT -->|answer| USER

    BUNDLE -.->|IndexBundle| DT & ET
    RI -->|force=True → rebuild| EI
```

## Key design notes

**Init is one-shot.** `ensure_index` is the single entry point for CLI prebuild, server startup, and `refresh_index` — nothing else touches the build path. The enrichment LLM runs here and only here; the runtime stays zero-ML.

**Three engines, one fused rank.** BM25F, Jaccard, and Levenshtein produce three independent ranked lists. RRF fuses them by rank position (not raw score), which avoids lossy score normalization and is robust to one engine returning a weak signal.

**BM25F field weights favour structure over prose.** `operation_id` (×4) and `summary` (×3.5) outweigh `description` (×1) because structural identifiers are stable and precise. Fuzzy term expansion via a length-bucketed vocab scan means typos like `"usrs biling"` rank the right endpoint first.

**Executor never blocks the event loop.** `execute_tool` is `async` — it awaits `execute_to_json_async` which uses `httpx.AsyncClient`. `discover_tools` and `refresh_index` stay sync (pure CPU). `_CredScopedAsyncClient` follows redirects but strips the api-key and extra headers on cross-origin hops (httpx only strips `Authorization`/`Cookie` by default).

**Source adapters are hot-swappable.** The adapter (`openapi`, `custom`, `module:callable`) runs only during the init flow and yields standard `EndpointRecord`s; the discovery and executor layers never know which adapter was used.
