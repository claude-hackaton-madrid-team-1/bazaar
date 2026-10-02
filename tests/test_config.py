import pytest

from bazaar_agent.config import ConfigError, load_settings, read_env_file


def test_env_file_parsing_and_env_precedence(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# c\nBAZAAR_KEY='tk-file'\nexport TYPESAFE_API_KEY=ts-1\n\n")
    assert read_env_file(env) == {"BAZAAR_KEY": "tk-file", "TYPESAFE_API_KEY": "ts-1"}
    monkeypatch.setenv("BAZAAR_KEY", "tk-env")
    s = load_settings(env)
    assert s.require_team_key() == "tk-env"
    assert "tk-env" not in repr(s)


def test_missing_team_key_names_the_variable_not_a_value(tmp_path, monkeypatch):
    monkeypatch.delenv("BAZAAR_KEY", raising=False)
    with pytest.raises(ConfigError, match="BAZAAR_KEY"):
        load_settings(tmp_path / "none.env").require_team_key()
