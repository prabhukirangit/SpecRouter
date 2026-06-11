# Logging

SpecRouter writes a single **combined, rotating log file** capturing every pipeline step. Logging is
configured automatically whenever you run a CLI command (`index` / `serve` / `refresh` / `init-rules`) or
start the server — there's nothing to turn on.

> **stdio-safety:** the default MCP transport speaks the protocol over **stdout**, so SpecRouter logs only to
> a **file** (and optionally **stderr**) — never stdout. Writing logs to stdout would corrupt the MCP stream.

## Where logs go

By default: `./logs/specrouter.log` (relative to the working directory). The file rotates at ~5 MB, keeping 5
older copies (`specrouter.log.1` … `specrouter.log.5`). All of this is configurable — see below.

## What gets logged

| Step | Logger | Level | Example line |
| --- | --- | --- | --- |
| **Indexing** | `specrouter.index` | INFO | `ensure_index: adapter=openapi force=True` · `Building index from 120 records (enrichment=False)` · `Saved index → …/<hash>.index.pkl (120 endpoints, hash a1b2c3…)` |
| **Index reuse** | `specrouter.index` | INFO | `Index is fresh (cache hit, 120 endpoints) — reusing.` · `Source unchanged (hash match) — keeping cached index.` |
| **Enrichment progress** | `specrouter.enrichment` | INFO | `Enriching 120 endpoints: 90 cached, 30 via LLM (openai/gpt-4o-mini).` · `Enrichment complete: 28/30 rewritten, 2 kept original.` |
| **Per-endpoint enrichment** | `specrouter.enrichment` | DEBUG | `Enriched get_v1_users_id_billing` |
| **Discovery** | `specrouter.discovery` | INFO | `discover q='usrs biling' -> ['get_v1_users_id_billing', …] (5/120)` |
| **Execution** | `specrouter.executor` | INFO | `EXEC GET https://api.example.com/v1/users/acct-42/billing args={'id': 'acct-42', 'limit': 10} -> 200` |
| **Execution failures** | `specrouter.executor` | WARNING | `EXEC GET /v1/... failed (upstream): …` |

The execution line records the **resolved FQDN** (the real URL called, after base-URL resolution and path
substitution), the inbound **call arguments**, and the **response status code**.

## Configuration

All via environment variables (or `.env`). Defaults shown.

| Variable | Default | Description |
| --- | --- | --- |
| `SPECROUTER_LOG_DIR` | `logs` | Directory for `specrouter.log` (created if missing). |
| `SPECROUTER_LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`. Use `DEBUG` to see per-endpoint enrichment lines. |
| `SPECROUTER_LOG_MAX_BYTES` | `5000000` | Rotate when the file reaches this size (~5 MB). |
| `SPECROUTER_LOG_BACKUP_COUNT` | `5` | Number of rotated backups to keep. |
| `SPECROUTER_LOG_CONSOLE` | `false` | Also echo logs to **stderr** (never stdout). Handy when running `serve` by hand. |
| `SPECROUTER_LOG_ARGS` | `true` | Include `execute_tool` call arguments in the EXEC line. Set `false` to omit them. |

## Security notes

- **Auth secrets are never logged.** Bearer tokens, basic credentials, the api-key header, and
  `SPECROUTER_EXTRA_HEADERS` are excluded from every log line.
- **Call arguments are logged by default** (the EXEC line). If your endpoint arguments can carry sensitive
  values (PII, tokens passed as params), set `SPECROUTER_LOG_ARGS=false`, and treat the log directory as
  sensitive (restrict read access, rotate/ship it like any audit log).
- Log files are git-ignored (`logs/`, `*.log`) so they're never committed.

## Implementation

`src/specrouter/logging_setup.py` attaches a `logging.handlers.RotatingFileHandler` to the `specrouter`
parent logger; every module logs through `logging.getLogger(__name__)` (which sits under `specrouter.*`).
Setup is idempotent and best-effort — if the log directory can't be written, SpecRouter logs a single
warning and keeps running rather than failing.
