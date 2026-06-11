"""Rotating-file logging for SpecRouter.

Attaches a :class:`~logging.handlers.RotatingFileHandler` to the ``specrouter`` parent
logger so every module under ``specrouter.*`` (index, enrichment, discovery, executor, …)
writes to a single combined log file. See ``docs/LOGGING.md`` for what each step records.

**stdio-safety:** the default MCP transport speaks the protocol over **stdout**, so logs
must never go there. This handler writes to a file, and (optionally) to **stderr** — never
stdout. Setup is best-effort: if the log directory can't be created/written, we degrade to
a single warning instead of crashing the server.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings

_LOGGER_ROOT = "specrouter"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured = False


def configure_logging(settings: "Settings") -> None:
    """Idempotently attach the rotating file handler to the ``specrouter`` logger.

    Safe to call from every entry point (CLI commands, server startup) — the second and
    later calls are no-ops.
    """
    global _configured
    logger = logging.getLogger(_LOGGER_ROOT)
    if _configured:
        return

    level = getattr(logging, settings.log_level, logging.INFO)
    logger.setLevel(level)

    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = settings.log_dir / "specrouter.log"
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
            delay=True,  # don't open the file until the first record
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(file_handler)
    except OSError as exc:  # unwritable dir, etc. — never crash on logging setup
        logger.addHandler(logging.NullHandler())
        logger.warning("Could not open log file in %s (%s); file logging disabled.",
                       settings.log_dir, exc)

    if settings.log_to_console:
        # stderr ONLY — stdout is reserved for the MCP stdio protocol.
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(stream)

    _configured = True
    logger.info(
        "Logging initialized -> %s (level=%s, rotate=%d bytes x %d backups, console=%s)",
        settings.log_dir / "specrouter.log", settings.log_level,
        settings.log_max_bytes, settings.log_backup_count, settings.log_to_console,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``specrouter`` tree (e.g. ``specrouter.index``)."""
    return logging.getLogger(f"{_LOGGER_ROOT}.{name}")
