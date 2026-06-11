# Source Adapters

`SPECROUTER_SOURCE_ADAPTER` selects **how** documentation is ingested into the in-memory index. Discovery and
execution are identical regardless of source — adapters just produce the same `EndpointRecord`s.

| Value | Ingests |
| --- | --- |
| `openapi` *(default)* | An OpenAPI 3 / Swagger 2 spec (`SPECROUTER_SPEC_URL`). |
| `custom` | A static HTML doc site, via a CSS-selector **rules file** (`SPECROUTER_CUSTOM_RULES`). |
| `module:callable` | Your own adapter — import path to a factory returning a `SourceAdapter`. |

---

## `custom` — config-driven HTML scraper

Many enterprise APIs are documented as **HTML pages, not specs** (resource pages with operation blocks +
parameter tables). The `custom` adapter turns those into discoverable + executable tools using a small rules
file of CSS selectors. **You don't write those selectors by hand** — an LLM generates them for you.

### Easy path: `init-rules` generates the rules

Point it at one representative doc page; it reads the HTML with an LLM, writes the rules file, and
**dry-runs it** so you can see the endpoints it found before using them. The LLM is used *once* here —
runtime scraping stays deterministic and free.

```bash
pip install -e ".[scrape,ai-openai]"            # bs4+lxml (scrape) + an LLM provider (any ai-* extra)

# Reuses the enrichment LLM settings:
export SPECROUTER_ENRICH_PROVIDER=openai         # openai | anthropic | google_genai | ollama
export SPECROUTER_ENRICH_MODEL=gpt-4o-mini
# export OPENAI_API_KEY=...                       # or SPECROUTER_ENRICH_API_KEY / _BASE_URL

specrouter init-rules https://docs.example.com/api/widgets \
  --index-url https://docs.example.com/api/ \
  --out custom_rules.yaml
# → prints the extracted endpoints; review custom_rules.yaml, tweak if needed

export SPECROUTER_SOURCE_ADAPTER=custom
export SPECROUTER_CUSTOM_RULES=./custom_rules.yaml
export SPECROUTER_BASE_URL=https://api.example.com      # the API host execute_tool calls
export SPECROUTER_AUTH_MODE=custom                      # + SPECROUTER_EXTRA_HEADERS / plugin as needed
specrouter index
```

The generated rules map selectors to: how to enumerate doc pages (index page + link selector, or an explicit
URL list), how to find each operation (method, path), and the parameter table
(name/type/required/in/description). An optional `body.wrapper_key` collapses body fields into a single
wrapped object (e.g. `{"widget":{…}}`) so execution works through the unchanged executor.

### Advanced: hand-edit the rules

The rules file is plain YAML — open it to fine-tune selectors the LLM got wrong, or write one from scratch
using [`examples/custom_rules.example.yaml`](../examples/custom_rules.example.yaml) as a template.

**No executor changes:** the adapter emits standard `EndpointRecord`s, so `execute_tool` runs them via
`SPECROUTER_BASE_URL` + auth exactly like OpenAPI endpoints.

**Limits:** the rules engine targets common table/section layouts of **static** HTML. The LLM-generated rules
are a strong starting point, not guaranteed perfect — that's why `init-rules` shows a preview. For irregular
layouts or **JavaScript-rendered** doc sites (which return only a nav shell to a plain fetch), write a
`module:callable` adapter (e.g. rendering with a headless browser) — that's the escape hatch.

### `custom` settings

| Variable | Default | Description |
| --- | --- | --- |
| `SPECROUTER_CUSTOM_RULES` | *(required)* | Path to the YAML/JSON rules file. |
| `SPECROUTER_CUSTOM_CONCURRENCY` | `8` | Bounded parallel page fetches. |

---

## `module:callable` — write your own adapter

For sources the `custom` rules engine can't express (irregular HTML, JS-rendered SPAs, a private metadata
API, …), implement the adapter contract and point the toggle at a factory:

```python
# myadapters.py
from specrouter.adapters.base import SourceResult

class MyAdapter:
    name = "mine"

    def load(self, settings):            # -> SourceResult(records, base_url, source_hash, etag?, last_modified?)
        ...                              # fetch + parse however you like; return EndpointRecords

    def is_fresh(self, settings, cached):  # cheap staleness check; return False to always re-load
        return False

def make():                              # SPECROUTER_SOURCE_ADAPTER=myadapters:make
    return MyAdapter()
```

```bash
export SPECROUTER_SOURCE_ADAPTER=myadapters:make
export SPECROUTER_BASE_URL=https://api.example.com
specrouter index
```

The contract is in [`src/specrouter/adapters/base.py`](../src/specrouter/adapters/base.py)
(`SourceResult` + the `SourceAdapter` protocol). Because adapters emit standard `EndpointRecord`s, the rest of
the pipeline — BM25F/fuzzy discovery, output schemas, enrichment, and `execute_tool` — works unchanged.
