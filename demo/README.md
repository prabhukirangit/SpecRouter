# SpecRouter live demo — Acme Product Catalog (~100 endpoints)

A self-contained local API you can stand up in one command to **record SpecRouter in action**:
a FastAPI "product catalog" that exposes **~100 distinct endpoints** across 10 categories and serves
its own OpenAPI spec at `/openapi.json`. Point SpecRouter at it, attach SpecRouter to Claude Code or
VS Code, and watch `discover_tools` narrow 100 endpoints to the right few, then `execute_tool` return
live product data.

The catalog is deterministic (fixed seed) — the same product ids/details come back every run, so your
recording is reproducible.

---

## 1. Install

From the repo root:

```bash
pip install -e ".[demo]"      # installs the catalog's only extra deps: fastapi + uvicorn
```

(The core `specrouter` package stays dependency-light; FastAPI/uvicorn are demo-only.)

## 2. Run the catalog API

```bash
uvicorn demo.catalog_api.app:app --host 127.0.0.1 --port 8000
# or:  python -m demo.catalog_api.app      # honours $CATALOG_PORT (default 8000)
```

Verify:

- Spec:            <http://localhost:8000/openapi.json>  (≈102 operations, tagged by category)
- Interactive docs: <http://localhost:8000/docs>
- Sample data:     <http://localhost:8000/catalog/electronics/products>

> **Port note:** the spec advertises `servers: [{ url: http://localhost:<port> }]`. If you change the
> port, either set `CATALOG_PORT` (so the spec matches) **or** set `SPECROUTER_BASE_URL` (step 3) to
> the real URL — `SPECROUTER_BASE_URL` always wins.

## 3. Point SpecRouter at it

No auth — frictionless for a demo. The two env vars SpecRouter needs:

```bash
SPECROUTER_SPEC_URL=http://localhost:8000/openapi.json
SPECROUTER_BASE_URL=http://localhost:8000
```

Quick smoke test from the repo root (builds the index from the live spec, then lists endpoint count):

```bash
SPECROUTER_SPEC_URL=http://localhost:8000/openapi.json \
SPECROUTER_BASE_URL=http://localhost:8000 \
specrouter index
# → logs/specrouter.log shows "Saved index … (102 endpoints …)"
```

## 4. Attach SpecRouter to your client

### Claude Code (CLI)

```bash
claude mcp add specrouter \
  --env SPECROUTER_SPEC_URL=http://localhost:8000/openapi.json \
  --env SPECROUTER_BASE_URL=http://localhost:8000 \
  -- specrouter serve
```

> **Source checkout? Use the venv's `specrouter` (scoped install).** The bare `specrouter` above only
> works if it's on your **global** PATH (a system-wide `pip install`). If you installed into the
> project venv (`pip install -e ".[demo]"`), `specrouter` is **not** on the global PATH, so the
> command `claude` spawns won't find it — point at the venv's console script by absolute path instead.
> Replace the last line (`-- specrouter serve`) with:
>
> - **Linux / macOS:** `-- /abs/path/to/SpecRouter/.venv/bin/specrouter serve`
> - **Windows:** `-- C:/abs/path/to/SpecRouter/.venv/Scripts/specrouter.exe serve`
>   *(forward slashes are fine; the trailing `.exe` is the Windows console script)*
>
> Alternatively, let `uv` resolve the venv for you (no absolute path needed):
> `-- uv run --directory /abs/path/to/SpecRouter specrouter serve`.

### Claude Desktop / VS Code — `mcpServers` JSON

Add to your client's MCP config (Claude Desktop config, or `.vscode/mcp.json` for VS Code):

```json
{
  "mcpServers": {
    "specrouter": {
      "command": "specrouter",
      "args": ["serve"],
      "env": {
        "SPECROUTER_SPEC_URL": "http://localhost:8000/openapi.json",
        "SPECROUTER_BASE_URL": "http://localhost:8000"
      }
    }
  }
}
```

The `"command": "specrouter"` above assumes a global install. **From a source checkout (venv install),
point `command` at the venv's console script** so the client can find it:

```jsonc
// Linux / macOS
"command": "/abs/path/to/SpecRouter/.venv/bin/specrouter", "args": ["serve"]

// Windows
"command": "C:/abs/path/to/SpecRouter/.venv/Scripts/specrouter.exe", "args": ["serve"]

// Or let uv resolve the venv (any OS, uv on PATH):
"command": "uv", "args": ["run", "--directory", "/abs/path/to/SpecRouter", "specrouter", "serve"]
```

(keep the same `"env"` block in all cases.)

Restart the client so it picks up the new server. SpecRouter exposes three tools: `discover_tools`,
`execute_tool`, `refresh_index`.

## 5. Demo script (prompts to record)

With both servers running and SpecRouter attached, try prompts like:

- *"Find endpoints for the technical specs of an electronics product."*
  → `discover_tools` returns the `…/electronics/products/{product_id}/specs` tool at the top.
- *"Get the specs for product `elec-001`."*
  → `execute_tool` → `200` `{ "color": "...", "weight_kg": "...", "warranty_months": "..." }`.
- *"List the current beauty deals and discounts."* → beauty `deals` tool → live list of discounts.
- *"Show me the bestselling toys."* / *"Get full details for product `book-002`."*
- *"Add 2 of `toy-001` to the cart."* → `POST …/cart` → a cart line with the computed total.
- *"Get details for product `nope-999` in books."* → a clean `404` surfaced through `execute_tool`
  (shows the structured error pass-through).

## What's where

| File | Purpose |
| --- | --- |
| `demo/catalog_api/data.py` | 10 categories, Pydantic response models, deterministic seeded products. |
| `demo/catalog_api/app.py`  | Builds the FastAPI app; registers ~10 ops per category (≈100 total) + `/categories`, `/health`. |

## Endpoints per category (×10 categories)

`GET /catalog/{cat}/products` · `/featured` · `/bestsellers` · `/deals` · `/inventory` ·
`GET /catalog/{cat}/products/{id}` · `/specs` · `/reviews` · `/pricing` ·
`POST /catalog/{cat}/products/{id}/cart`

Categories: electronics, books, clothing, home_kitchen, toys, sports, beauty, grocery, automotive,
garden.
