"""SpecRouter command-line interface.

  specrouter index     prebuild + persist the search index, then exit
  specrouter serve     start the MCP server (stdio); builds on demand if needed
  specrouter refresh   force a re-fetch + rebuild + persist, out of band
"""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_settings
from .engines import levenshtein
from .index import ensure_index


def _cmd_index(_args: argparse.Namespace) -> int:
    settings = load_settings()
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
    bundle = ensure_index(settings, force=True)
    print(f"Refreshed: {bundle.endpoint_count()} endpoints (hash {bundle.source_hash}).")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    # Imported lazily so `index`/`refresh` don't require the mcp dependency tree.
    from .server import run

    run(transport=args.transport, host=args.host, port=args.port)
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
