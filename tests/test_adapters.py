"""Source-adapter framework: toggle, plugin resolution, end-to-end build."""

import pytest

from specrouter import index
from specrouter.adapters import resolve_adapter
from specrouter.adapters.openapi import OpenApiAdapter
from specrouter.config import ConfigError, Settings
from specrouter.discovery import discover


def test_default_is_openapi():
    s = Settings(spec_url="x")
    assert isinstance(resolve_adapter(s), OpenApiAdapter)
    assert resolve_adapter(s).name == "openapi"


def test_custom_resolves():
    s = Settings(spec_url="x", source_adapter="custom", custom_rules="rules.yaml")
    assert resolve_adapter(s).name == "custom"


def test_unknown_adapter_raises():
    with pytest.raises(ConfigError):
        resolve_adapter(Settings(spec_url="x", source_adapter="bogus"))


def test_plugin_must_be_module_callable():
    with pytest.raises(ConfigError):
        resolve_adapter(Settings(spec_url="x", source_adapter="onlymodule"))


def test_module_callable_plugin_resolves_and_builds(tmp_path):
    s = Settings(spec_url="x", source_adapter="_fake_adapter:make", cache_dir=tmp_path)
    adapter = resolve_adapter(s)
    assert adapter.name == "fake"

    # ensure_index drives the whole build through the plugin adapter.
    bundle = index.ensure_index(s, force=True)
    assert bundle.endpoint_count() == 1
    assert bundle.base_url == "https://api.example.com"

    result = discover("get widget", bundle, s)
    assert result["tools"][0]["name"] == "get_widgets_id"


def test_bad_plugin_target_raises(tmp_path):
    with pytest.raises(ConfigError):
        resolve_adapter(Settings(spec_url="x", source_adapter="_fake_adapter:not_here"))
