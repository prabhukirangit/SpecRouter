"""init-rules scaffold: LLM-drafted rules + dry-run preview (fake LLM, no network)."""

from pathlib import Path

import pytest

from specrouter import enrichment, scaffold
from specrouter.adapters import custom
from specrouter.config import ConfigError, Settings

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_docs.html"

# Rules the fake model "generates" — matches the widgets fixture.
_RULES_YAML = """
base_url: "https://api.example.com"
pages:
  urls: ["https://docs.example.com/widgets.html"]
operation:
  block_selector: "section.operation"
  method_selector: ".http-method"
  path_selector: ".endpoint-path"
  summary_selector: ".operation-summary"
params:
  row_selector: "table.params tbody tr"
  name_selector: ".param-name"
  type_selector: ".param-type"
  required_selector: ".param-required"
  required_match: "yes"
  in_selector: ".param-in"
  description_selector: ".param-desc"
body:
  wrapper_key: "widget"
"""


class FakeMsg:
    def __init__(self, content):
        self.content = content


class FakeModel:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, prompt):
        return FakeMsg(self.payload)


@pytest.fixture
def scaffold_settings():
    return Settings(spec_url="x", enrich_provider="openai", enrich_model="fake-model")


def test_generate_rules_and_preview(scaffold_settings, monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr(custom, "_fetch_one", lambda url, settings: html)
    monkeypatch.setattr(enrichment, "_build_model", lambda s: FakeModel(_RULES_YAML))

    rules, preview = scaffold.generate_rules("https://docs.example.com/widgets.html", scaffold_settings)

    assert rules["operation"]["block_selector"] == "section.operation"
    assert rules["body"]["wrapper_key"] == "widget"
    assert {r.tool_name for r in preview} == {"get_widgets_id", "post_widgets"}


def test_generate_rules_strips_code_fences(scaffold_settings, monkeypatch):
    html = FIXTURE.read_text(encoding="utf-8")
    fenced = "```yaml\n" + _RULES_YAML.strip() + "\n```"
    monkeypatch.setattr(custom, "_fetch_one", lambda url, settings: html)
    monkeypatch.setattr(enrichment, "_build_model", lambda s: FakeModel(fenced))

    rules, preview = scaffold.generate_rules("https://docs.example.com/widgets.html", scaffold_settings)
    assert rules["base_url"] == "https://api.example.com"
    assert len(preview) == 2


def test_extract_yaml_plain_and_fenced():
    assert scaffold._extract_yaml("base_url: x")["base_url"] == "x"
    assert scaffold._extract_yaml("```yaml\nbase_url: y\n```")["base_url"] == "y"
    with pytest.raises(ConfigError):
        scaffold._extract_yaml("just some prose, not yaml mapping: : :")


def test_missing_llm_provider_raises(monkeypatch):
    s = Settings(spec_url="x")  # no enrich_provider/model
    monkeypatch.setattr(custom, "_fetch_one", lambda url, settings: "<html></html>")
    with pytest.raises(ConfigError):
        scaffold.generate_rules("https://docs.example.com/x.html", s)
