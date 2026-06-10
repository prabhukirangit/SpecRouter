"""Pluggable authentication for downstream ``execute_tool`` HTTP calls.

Resolves the configured :class:`~specrouter.config.Settings` auth mode into the
keyword arguments handed to ``httpx.request`` (``headers`` and/or ``auth``), plus an
optional plugin hook that can mutate the full request kwargs (for OAuth signing,
request signing, mTLS setup, etc.).
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

import httpx

from .config import Settings

# A plugin is ``callable(request_kwargs: dict, settings: Settings) -> dict``.
AuthPlugin = Callable[[dict[str, Any], Settings], dict[str, Any]]


def _load_plugin(spec: str) -> AuthPlugin:
    if ":" not in spec:
        raise ValueError(f"SPECROUTER_AUTH_PLUGIN must be 'module:callable', got '{spec}'.")
    module_name, attr = spec.split(":", 1)
    module = importlib.import_module(module_name)
    fn = getattr(module, attr, None)
    if not callable(fn):
        raise ValueError(f"Auth plugin '{spec}' is not callable.")
    return fn


def base_headers(settings: Settings) -> dict[str, str]:
    """Static headers applied to every request (auth + user-supplied extras)."""
    headers: dict[str, str] = dict(settings.extra_headers)
    mode = settings.auth_mode
    if mode == "bearer" and settings.bearer_token:
        headers["Authorization"] = f"Bearer {settings.bearer_token}"
    elif mode == "api_key" and settings.api_key:
        headers[settings.api_key_header] = settings.api_key
    return headers


def apply_auth(request_kwargs: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Augment ``httpx.request`` kwargs with the configured auth strategy."""
    headers = dict(request_kwargs.get("headers") or {})
    headers.update(base_headers(settings))
    request_kwargs["headers"] = headers

    if settings.auth_mode == "basic" and settings.basic_user is not None:
        request_kwargs["auth"] = httpx.BasicAuth(
            settings.basic_user, settings.basic_pass or ""
        )

    if settings.auth_mode == "plugin" and settings.auth_plugin:
        plugin = _load_plugin(settings.auth_plugin)
        request_kwargs = plugin(request_kwargs, settings)

    return request_kwargs
