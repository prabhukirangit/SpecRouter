from specrouter.tokenizer import tokenize


def test_camelcase_split():
    assert tokenize("fetchSystemAlerts") == ["fetch", "system", "alerts"]


def test_acronym_boundary():
    # "API" splits off "Key"; "api" dropped as stopword, "get" kept as intent.
    assert tokenize("getAPIKey") == ["get", "key"]


def test_snake_and_uri_cleansing():
    assert tokenize("/v1/users/{id}/billing_history") == ["users", "id", "billing", "history"]


def test_stopword_filtering():
    # http/api/v1/json dropped; "get" retained as intent-bearing.
    assert tokenize("get the api/v1 users.json") == ["get", "users"]


def test_empty_and_none():
    assert tokenize("") == []
    assert tokenize(None) == []
