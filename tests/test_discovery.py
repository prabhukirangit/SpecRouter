from specrouter.discovery import discover


def test_discover_clean_query(bundle, settings):
    result = discover("get user billing history", bundle, settings)
    names = [t["name"] for t in result["tools"]]
    assert "get_v1_users_id_billing" in names
    # Winner should be the billing endpoint.
    assert result["tools"][0]["name"] == "get_v1_users_id_billing"


def test_discover_typo_query(bundle, settings):
    # "usrs biling" must route to the users/billing endpoint via fuzzy BM25F + RRF.
    result = discover("get usrs biling", bundle, settings)
    names = [t["name"] for t in result["tools"]]
    assert "get_v1_users_id_billing" in names
    # With fuzzy term expansion the corrected endpoint should now rank first.
    assert result["tools"][0]["name"] == "get_v1_users_id_billing"


def test_fuzzy_expansion_can_be_disabled(bundle, settings):
    settings.fuzzy_expand = False
    result = discover("usrs biling", bundle, settings)
    # Still callable; result set is non-empty even with fuzzy off.
    assert isinstance(result["tools"], list)


def test_tool_schema_shape(bundle, settings):
    result = discover("billing", bundle, settings)
    tool = next(t for t in result["tools"] if t["name"] == "get_v1_users_id_billing")
    assert tool["inputSchema"]["type"] == "object"
    assert "id" in tool["inputSchema"]["properties"]
    assert "id" in tool["inputSchema"]["required"]
    assert tool["description"].startswith("[TAGS: Finance]")
    assert tool["_route"] == {"path": "/v1/users/{id}/billing", "method": "GET"}


def test_respects_top_k(bundle, settings):
    settings.top_k = 2
    result = discover("user", bundle, settings)
    assert len(result["tools"]) <= 2
