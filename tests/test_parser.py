import json
from pathlib import Path

from specrouter.parser import parse_spec

SAMPLE = Path(__file__).resolve().parents[1] / "sample" / "petstore.json"


def _records():
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    records, base_url = parse_spec(spec)
    return records, base_url


def test_flattens_all_operations():
    records, base_url = _records()
    names = {r.tool_name for r in records}
    assert "get_v1_users_id_billing" in names
    assert "post_v1_users" in names
    assert base_url == "https://api.example.com"


def test_path_parameter_required():
    records, _ = _records()
    rec = next(r for r in records if r.operation_id == "getUserBillingHistory")
    id_param = next(p for p in rec.parameters if p.name == "id")
    assert id_param.location == "path" and id_param.required


def test_body_properties_become_params():
    records, _ = _records()
    rec = next(r for r in records if r.operation_id == "createUser")
    body = {p.name: p for p in rec.parameters if p.location == "body"}
    assert "email" in body and body["email"].required
    assert "name" in body and not body["name"].required


def test_field_tokens_primed():
    records, _ = _records()
    rec = next(r for r in records if r.operation_id == "fetchSystemAlerts")
    assert rec.field_tokens["operation_id"] == ["fetch", "system", "alerts"]
    assert rec.field_text["path"]  # Levenshtein text populated


def test_ref_resolution():
    spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://x.test"}],
        "components": {
            "parameters": {
                "IdParam": {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
            }
        },
        "paths": {
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"$ref": "#/components/parameters/IdParam"}],
                }
            }
        },
    }
    records, _ = parse_spec(spec)
    rec = records[0]
    assert any(p.name == "id" and p.location == "path" for p in rec.parameters)
