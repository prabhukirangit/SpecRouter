"""Launcher for the MCP Inspector via ``mcp dev``.

``mcp dev`` imports the target file standalone (outside any package), which breaks
the package-relative imports inside ``specrouter/server.py``. This thin shim uses an
absolute import instead, so the real package is loaded correctly:

    mcp dev dev_server.py

(Requires the package installed, e.g. ``pip install -e ".[dev]"``, and
``SPECROUTER_SPEC_URL`` set in the environment or a ``.env`` file.)
"""

from specrouter.server import mcp  # noqa: F401  -- re-exported for `mcp dev`
