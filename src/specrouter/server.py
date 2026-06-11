"""FastMCP server exposing the two-tool lazy-loading contract plus a refresh admin tool.

Tools:
  - discover_tools(query)             -> top-k dynamic tool schemas (usecase.md Tool 1)
  - execute_tool(path, method, ...)   -> wrapped live HTTP result   (usecase.md Tool 2)
  - refresh_index()                   -> force re-fetch + rebuild + persist
"""

from __future__ import annotations

import base64
import importlib.resources
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import executor
from .config import Settings, load_settings
from .discovery import discover
from .index import ensure_index
from .logging_setup import configure_logging
from .models import IndexBundle


class _State:
    """Holds the live settings + index bundle, swappable on refresh."""

    settings: Settings | None = None
    bundle: IndexBundle | None = None


def _server_icons() -> list | None:
    """Advertise the bundled SVG icon as a self-contained data URI (if supported)."""
    try:
        from mcp.types import Icon  # added in newer MCP SDKs

        svg = importlib.resources.files(__package__).joinpath("icon.svg").read_bytes()
        data_uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")
        return [Icon(src=data_uri, mimeType="image/svg+xml", sizes=["100x100"])]
    except Exception:
        return None  # older SDK without Icon support, or missing asset — skip gracefully


_state = _State()
_icons = _server_icons()
mcp = FastMCP("SpecRouter", **({"icons": _icons} if _icons else {}))


def _ensure_ready() -> None:
    """Lazily load settings + index on first use.

    ``run()`` pre-initializes, but tools may also be invoked by harnesses that import
    the ``mcp`` object directly (e.g. ``mcp dev src/specrouter/server.py:mcp``) without
    calling ``run()`` — so each tool self-initializes if needed.
    """
    if _state.bundle is None:
        init_state()


@mcp.tool()
def discover_tools(query: str) -> dict[str, Any]:
    """Find OpenAPI endpoints matching a natural-language intent.

    Returns up to top-k dynamic MCP tool definitions whose schemas describe the
    parameters needed to call each endpoint via ``execute_tool``.
    """
    _ensure_ready()
    return discover(query, _state.bundle, _state.settings)


@mcp.tool()
async def execute_tool(path: str, method: str, arguments: dict[str, Any] | None = None) -> str:
    """Execute a discovered endpoint against the live API.

    ``path``/``method`` come from a discover_tools result; ``arguments`` is a flat
    key-value object mapped to path substitutions, query params, headers, and body.

    Async so the up-to-``request_timeout`` upstream call is awaited off the event loop —
    FastMCP runs sync tools inline, so a blocking call here would stall every other
    connected client in HTTP hosting. A slow/unreachable upstream returns a structured
    504/502 rather than hanging.
    """
    _ensure_ready()
    return await executor.execute_to_json_async(
        path, method, arguments, _state.bundle, _state.settings
    )


@mcp.tool()
def refresh_index() -> dict[str, Any]:
    """Force a re-fetch of the spec and rebuild + persist the search index."""
    _ensure_ready()
    _state.bundle = ensure_index(_state.settings, force=True)
    return {
        "refreshed": True,
        "endpoint_count": _state.bundle.endpoint_count(),
        "source_hash": _state.bundle.source_hash,
        "built_at": _state.bundle.built_at,
    }


def init_state(settings: Settings | None = None) -> None:
    """Load settings and ensure the index is built/loaded before serving."""
    _state.settings = settings or load_settings()
    configure_logging(_state.settings)
    _state.bundle = ensure_index(_state.settings)


def run(
    transport: str = "stdio",
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Entry point used by ``specrouter serve``.

    - ``stdio`` (default): the MCP client launches this as a subprocess.
    - ``streamable-http`` / ``sse``: host it as a network service so remote clients
      connect by URL (``http://<host>:<port>/mcp`` or ``/sse``).
    """
    init_state()
    if host is not None:
        mcp.settings.host = host
    if port is not None:
        mcp.settings.port = port
    mcp.run(transport=transport)


if __name__ == "__main__":
    run()
