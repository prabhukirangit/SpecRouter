import pytest

from specrouter.config import ConfigError, _parse_dotenv, load_settings


def test_parse_dotenv_basic():
    text = '# comment\nexport SPECROUTER_TOP_K=7\nSPECROUTER_API_KEY="abc123"\n\nBAD LINE\n'
    parsed = _parse_dotenv(text)
    assert parsed["SPECROUTER_TOP_K"] == "7"
    assert parsed["SPECROUTER_API_KEY"] == "abc123"
    assert "BAD LINE" not in parsed


def test_load_settings_reads_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SPECROUTER_SPEC_URL=https://x.test/openapi.json\nSPECROUTER_TOP_K=9\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env={})  # no real env vars; values come from .env
    assert settings.spec_url == "https://x.test/openapi.json"
    assert settings.top_k == 9


def test_real_env_overrides_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SPECROUTER_SPEC_URL=https://from-file.test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env={"SPECROUTER_SPEC_URL": "https://from-env.test"})
    assert settings.spec_url == "https://from-env.test"


def test_missing_spec_url_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env present
    with pytest.raises(ConfigError):
        load_settings(env={})


def test_dotenv_with_bom(tmp_path, monkeypatch):
    # Editors / PowerShell may write a UTF-8 BOM; the first key must still parse.
    env_file = tmp_path / ".env"
    env_file.write_text("SPECROUTER_SPEC_URL=https://bom.test\n", encoding="utf-8-sig")
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env={})
    assert settings.spec_url == "https://bom.test"


def test_custom_env_file_path(tmp_path, monkeypatch):
    custom = tmp_path / "myenv"
    custom.write_text("SPECROUTER_SPEC_URL=https://custom.test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env={"SPECROUTER_ENV_FILE": str(custom)})
    assert settings.spec_url == "https://custom.test"
