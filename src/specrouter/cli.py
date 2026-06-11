"""SpecRouter command-line interface.

  specrouter index       prebuild + persist the search index, then exit
  specrouter serve       start the MCP server (stdio); builds on demand if needed
  specrouter refresh     force a re-fetch + rebuild + persist, out of band
  specrouter init-rules  generate a custom-adapter rules file from a sample doc page (LLM)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .config import ConfigError, load_settings
from .engines import levenshtein
from .index import ensure_index
from .logging_setup import configure_logging


def _cmd_index(_args: argparse.Namespace) -> int:
    settings = load_settings()
    configure_logging(settings)
    bundle = ensure_index(settings, force=True)
    accel = "rapidfuzz" if levenshtein.using_rapidfuzz() else "stdlib"
    print(
        f"Indexed {bundle.endpoint_count()} endpoints from {settings.spec_url}\n"
        f"  cache:   {settings.index_path()}\n"
        f"  hash:    {bundle.source_hash}\n"
        f"  built:   {bundle.built_at}\n"
        f"  fuzzy:   {accel} Levenshtein"
    )
    return 0


def _cmd_refresh(_args: argparse.Namespace) -> int:
    settings = load_settings()
    configure_logging(settings)
    bundle = ensure_index(settings, force=True)
    print(f"Refreshed: {bundle.endpoint_count()} endpoints (hash {bundle.source_hash}).")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    # Imported lazily so `index`/`refresh` don't require the mcp dependency tree.
    from .server import run

    run(transport=args.transport, host=args.host, port=args.port)
    return 0


def _cmd_init_rules(args: argparse.Namespace) -> int:
    from . import scaffold

    settings = load_settings(require_spec_source=False)
    configure_logging(settings)
    print(f"Reading {args.sample_url} and asking {settings.enrich_model} to draft rules…")
    rules, preview = scaffold.generate_rules(
        args.sample_url, settings, index_url=args.index_url
    )

    print(f"\nDry run — {len(preview)} endpoint(s) extracted with the generated rules:")
    for rec in preview:
        req = sum(1 for p in rec.parameters if p.required)
        print(f"  {rec.method:6} {rec.path:40} ({len(rec.parameters)} params, {req} required)")
    if not preview:
        print("  (none — the selectors need adjustment; review/edit the rules file)")

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"\nRefusing to overwrite {out} (use --force).", file=sys.stderr)
        return 1
    header = (
        "# SpecRouter custom-adapter rules — LLM-generated draft. REVIEW before use.\n"
        f"# Source sample: {args.sample_url}\n\n"
    )
    out.write_text(header + yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")
    print(
        f"\nWrote {out}. Next:\n"
        f"  export SPECROUTER_SOURCE_ADAPTER=custom\n"
        f"  export SPECROUTER_CUSTOM_RULES={out}\n"
        f"  export SPECROUTER_BASE_URL=https://<api-host>   # where execute_tool calls go\n"
        f"  specrouter index"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="specrouter",
        description="Dynamic OpenAPI-to-MCP conversion engine (BM25F + Jaccard + Levenshtein + RRF).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("index", help="Prebuild and persist the search index, then exit.").set_defaults(
        func=_cmd_index
    )

    serve = sub.add_parser(
        "serve",
        help="Start the MCP server (stdio by default, or HTTP for remote hosting).",
    )
    serve.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "sse"),
        default="stdio",
        help="Transport: stdio (client-launched subprocess) or a network transport for hosting.",
    )
    serve.add_argument(
        "--host", default=None,
        help="Bind host for HTTP transports (default 127.0.0.1; use 0.0.0.0 to expose).",
    )
    serve.add_argument(
        "--port", type=int, default=None,
        help="Bind port for HTTP transports (default 8000).",
    )
    serve.set_defaults(func=_cmd_serve)

    sub.add_parser("refresh", help="Force re-fetch + rebuild + persist the index.").set_defaults(
        func=_cmd_refresh
    )

    init_rules = sub.add_parser(
        "init-rules",
        help="Generate a custom-adapter rules file from a sample doc page using an LLM.",
    )
    init_rules.add_argument("sample_url", help="URL of one representative API documentation page.")
    init_rules.add_argument(
        "--index-url", default=None,
        help="Optional listing/nav page URL — helps infer how to enumerate resource pages.",
    )
    init_rules.add_argument(
        "--out", default="custom_rules.yaml", help="Where to write the rules file."
    )
    init_rules.add_argument(
        "--force", action="store_true", help="Overwrite the output file if it exists."
    )
    init_rules.set_defaults(func=_cmd_init_rules)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
