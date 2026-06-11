import logging
import sys
from logging.handlers import RotatingFileHandler

import httpx
import pytest

from specrouter import executor, logging_setup
from specrouter.config import Settings
from specrouter.discovery import discover


def _reset_logging():
    logger = logging.getLogger("specrouter")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    logging_setup._configured = False


@pytest.fixture
def reset_logging():
    _reset_logging()
    yield
    _reset_logging()


def _read_log(tmp_path):
    for h in logging.getLogger("specrouter").handlers:
        h.flush()
    p = tmp_path / "specrouter.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_configure_creates_file_and_is_idempotent(tmp_path, reset_logging):
    s = Settings(spec_url="x", log_dir=tmp_path)
    logging_setup.configure_logging(s)
    logging_setup.configure_logging(s)  # second call must not add a duplicate handler

    file_handlers = [
        h for h in logging.getLogger("specrouter").handlers
        if isinstance(h, RotatingFileHandler)
    ]
    assert len(file_handlers) == 1

    logging.getLogger("specrouter.unit").info("hello-world")
    assert "hello-world" in _read_log(tmp_path)


def test_never_logs_to_stdout(tmp_path, reset_logging):
    # Even with console logging enabled, stdout must stay clean (stdio MCP protocol).
    s = Settings(spec_url="x", log_dir=tmp_path, log_to_console=True)
    logging_setup.configure_logging(s)
    for h in logging.getLogger("specrouter").handlers:
        assert getattr(h, "stream", None) is not sys.stdout


def test_execution_logged_without_secrets(tmp_path, reset_logging, bundle, monkeypatch):
    s = Settings(
        spec_url="x", log_dir=tmp_path, auth_mode="bearer",
        bearer_token="super-secret-token", base_url="https://api.example.com",
    )
    logging_setup.configure_logging(s)

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    real = executor._CredScopedClient
    monkeypatch.setattr(
        executor, "_CredScopedClient",
        lambda *a, **k: real(*a, **{**k, "transport": transport}),
    )

    executor.execute("/v1/system/alerts", "GET", {}, bundle, s)
    text = _read_log(tmp_path)
    assert "EXEC GET https://api.example.com/v1/system/alerts" in text
    assert "-> 200" in text
    assert "super-secret-token" not in text  # auth headers are never logged


def test_log_args_can_be_disabled(tmp_path, reset_logging, bundle, monkeypatch):
    s = Settings(
        spec_url="x", log_dir=tmp_path, base_url="https://api.example.com", log_args=False,
    )
    logging_setup.configure_logging(s)

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    real = executor._CredScopedClient
    monkeypatch.setattr(
        executor, "_CredScopedClient",
        lambda *a, **k: real(*a, **{**k, "transport": transport}),
    )

    # 'limit' is a query arg — with log_args off it must not appear in the line.
    executor.execute("/v1/users/{id}/billing", "GET", {"id": "acct-42", "limit": 99}, bundle, s)
    text = _read_log(tmp_path)
    assert "EXEC GET" in text
    assert "args=" not in text
    assert "limit" not in text  # the args dict (incl. query params) is omitted


def test_discovery_logged(tmp_path, reset_logging, bundle):
    s = Settings(spec_url="x", log_dir=tmp_path)
    logging_setup.configure_logging(s)
    result = discover("system alerts", bundle, s)
    text = _read_log(tmp_path)
    assert "discover q=" in text
    if result["tools"]:
        assert result["tools"][0]["name"] in text
