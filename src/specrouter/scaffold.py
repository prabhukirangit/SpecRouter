"""Generate a ``custom`` adapter rules file from a sample doc page using an LLM.

The LLM is used **once**, to author the CSS-selector rules — runtime scraping stays the
deterministic, free bs4 path. Reuses the enrichment LLM config (``SPECROUTER_ENRICH_*``).
"""

from __future__ import annotations

from typing import Any

import yaml

from .adapters import custom
from .config import ConfigError, Settings
from .models import EndpointRecord

# Compact schema embedded in the prompt so the model emits a valid rules file.
_RULES_SCHEMA = """\
base_url: "<API host execute_tool calls, optional>"
pages:
  index_url: "<listing/nav page URL, optional>"
  link_selector: "<CSS selector for anchors linking to per-resource doc pages, optional>"
  link_attr: "href"
  # OR instead of index_url/link_selector:
  # urls: ["<explicit doc page URL>", ...]
operation:
  block_selector: "<CSS selector repeated once per operation; omit if one op per page>"
  method_selector: "<CSS selector whose text/attr holds GET/POST/PUT/PATCH/DELETE>"
  method_attr: null
  path_selector: "<CSS selector whose text is the path template, e.g. /things/{id}>"
  summary_selector: null
  description_selector: null
  tags_selector: null
params:
  scope: "operation"   # or "page" if one shared table serves all ops on a page
  row_selector: "<CSS selector for each parameter table row>"
  name_selector: "<within a row: parameter name>"
  type_selector: "<within a row: data type>"
  required_selector: "<within a row: required flag, optional>"
  required_match: null   # text meaning required, e.g. "yes" (optional)
  in_selector: null      # path|query|header|body, optional (else inferred)
  description_selector: "<within a row: description>"
body:
  wrapper_key: null   # set if request bodies are wrapped, e.g. {"thing": {...}}
"""

_PROMPT = """\
You configure SpecRouter's "custom" HTML documentation adapter. Given one API documentation
page, produce a rules file of CSS selectors that extracts its operations and parameters.

Output ONLY a YAML document matching this schema (omit optional keys you can't determine):

{schema}

Rules:
- Use real CSS selectors that exist in the HTML below (inspect class names / structure).
- method_selector/path_selector must locate the HTTP verb and the endpoint path template.
- If a single parameter table is shared by all operations on the page, set params.scope: page.
- If request bodies are wrapped under a key, set body.wrapper_key to that key.
- No prose, no code fences — just the YAML.

{index_block}HTML of the documentation page:
---
{html}
---
"""

_MAX_HTML_CHARS = 40000


def _clean_html(html: str) -> str:
    """Strip scripts/styles/comments so class names + structure fit the token budget."""
    BeautifulSoup = custom._bs4()
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "svg", "noscript", "link", "meta"]):
        tag.decompose()
    try:
        from bs4 import Comment

        for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
            c.extract()
    except Exception:
        pass
    text = soup.prettify()
    return text[:_MAX_HTML_CHARS]


def _extract_yaml(raw: str) -> dict[str, Any]:
    """Parse the model output into a rules dict, tolerating ``` fences / stray prose."""
    text = raw.strip()
    if "```" in text:
        # Keep the content of the first fenced block.
        parts = text.split("```")
        block = parts[1] if len(parts) > 1 else text
        if block.lstrip().lower().startswith("yaml"):
            block = block.split("\n", 1)[1] if "\n" in block else ""
        text = block
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"The model did not return valid YAML rules: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("The model did not return a YAML mapping of rules.")
    return data


def generate_rules(
    sample_url: str, settings: Settings, *, index_url: str | None = None
) -> tuple[dict[str, Any], list[EndpointRecord]]:
    """Return ``(rules, preview_records)`` for a sample documentation page."""
    if not settings.enrich_provider or not settings.enrich_model:
        raise ConfigError(
            "init-rules needs an LLM: set SPECROUTER_ENRICH_PROVIDER and SPECROUTER_ENRICH_MODEL "
            "(the same model settings used for enrichment), plus the provider's API key."
        )

    from . import enrichment  # reuse the LLM builder + message-text extractor

    sample_html = custom._fetch_one(sample_url, settings)
    cleaned = _clean_html(sample_html)

    index_block = ""
    if index_url:
        try:
            index_cleaned = _clean_html(custom._fetch_one(index_url, settings))[:_MAX_HTML_CHARS // 2]
            index_block = (
                "HTML of the INDEX page (use it to infer pages.index_url + pages.link_selector):\n"
                f"---\n{index_cleaned}\n---\n\n"
            )
        except Exception:
            index_block = ""

    prompt = _PROMPT.format(schema=_RULES_SCHEMA, index_block=index_block, html=cleaned)

    model = enrichment._build_model(settings)
    rules = _extract_yaml(enrichment._extract_text(model.invoke(prompt), collapse_whitespace=False))
    if index_url and "pages" in rules and isinstance(rules["pages"], dict):
        rules["pages"].setdefault("index_url", index_url)

    preview = custom._parse_page(sample_html, sample_url, rules)
    return rules, preview
