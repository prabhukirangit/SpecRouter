import json
from pathlib import Path

from specrouter.config import Settings
from specrouter.discovery import discover
from specrouter.parser import parse_spec

SAMPLE = Path(__file__).resolve().parents[1] / "sample" / "petstore.json"


def _records(max_depth=4):
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return parse_spec(spec, output_schema_max_depth=max_depth)[0]


def test_extracts_2xx_json_schema():
    rec = next(r for r in _records() if r.operation_id == "getUserBillingHistory")
    assert rec.output_schema is not None
    assert rec.output_schema["properties"]["invoices"]["type"] == "array"


def test_ref_inlined_recursively():
    rec = next(r for r in _records() if r.operation_id == "getUserBillingHistory")
    invoice = rec.output_schema["properties"]["invoices"]["items"]
    # $ref Invoice -> nested $ref Customer both inlined.
    assert "id" in invoice["properties"]
    assert invoice["properties"]["customer"]["properties"]["name"]["type"] == "string"


def test_depth_cap_collapses_deep_nesting():
    rec = next(r for r in _records(max_depth=2) if r.operation_id == "getUserBillingHistory")
    # invoices(1) -> items(2) hits the cap, so the array item is a bare type hint.
    items = rec.output_schema["properties"]["invoices"]["items"]
    assert items == {"type": "object"}


def test_no_responses_yields_none():
    rec = next(r for r in _records() if r.operation_id == "fetchSystemAlerts")
    assert rec.output_schema is None


def test_discovery_includes_output_schema(bundle, settings):
    result = discover("billing", bundle, settings)
    tool = next(t for t in result["tools"] if t["name"] == "get_v1_users_id_billing")
    assert "outputSchema" in tool
    assert tool["outputSchema"]["properties"]["invoices"]["type"] == "array"
    # input and output are kept separate.
    assert "inputSchema" in tool and tool["inputSchema"] is not tool["outputSchema"]


def test_discovery_omits_output_schema_when_disabled(bundle, settings):
    settings.include_output_schema = False
    result = discover("billing", bundle, settings)
    tool = next(t for t in result["tools"] if t["name"] == "get_v1_users_id_billing")
    assert "outputSchema" not in tool
