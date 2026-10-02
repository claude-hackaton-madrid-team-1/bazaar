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


# ---------------------------------------------------------------- real game vs simulator


SIM_URL = "https://bazaar-sim-production.up.railway.app"
REAL_DB = "postgresql://postgres:pw@iriguchi.proxy.rlwy.net:28880/railway?sslmode=require"


def settings_for(tmp_path, monkeypatch, **env):
    for name in ("BAZAAR_URL", "BAZAAR_KEY", "DATABASE_URL", "BAZAAR_SIM_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return load_settings(tmp_path / "none.env")


def test_the_real_key_is_never_sent_to_a_host_that_is_not_the_game(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch, BAZAAR_URL=SIM_URL, BAZAAR_KEY="tk-real-0042")
    assert s.simulator
    with pytest.raises(ConfigError, match="only a simulator key") as e:
        s.require_team_key()
    assert "tk-real-0042" not in str(e.value)
    with pytest.raises(ConfigError):
        s.team_key()


def test_the_official_host_refuses_a_simulator_key(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch, BAZAAR_URL="https://bazaar.causaprima.ai", BAZAAR_KEY="sim-team1")
    assert not s.simulator
    with pytest.raises(ConfigError, match="simulator key"):
        s.require_team_key()


def test_matching_pairs_pass(tmp_path, monkeypatch):
    sim = settings_for(tmp_path, monkeypatch, BAZAAR_URL="http://127.0.0.1:8765", BAZAAR_KEY="sim-team1")
    assert sim.require_team_key() == "sim-team1" and sim.team_key() == "sim-team1"
    real = settings_for(tmp_path, monkeypatch, BAZAAR_KEY="tk-real-0042")
    assert real.bazaar_url == "https://bazaar.causaprima.ai" and real.require_team_key() == "tk-real-0042"
    assert settings_for(tmp_path, monkeypatch, BAZAAR_URL=SIM_URL).team_key() is None


def test_simulated_play_never_writes_to_the_real_railway_database(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch, BAZAAR_URL=SIM_URL, BAZAAR_KEY="sim-team1", DATABASE_URL=REAL_DB)
    with pytest.raises(ConfigError, match="BAZAAR_SIM_DATABASE_URL") as e:
        s.require_database_url()
    assert "pw" not in str(e.value)
    sneaky = "postgresql://postgres:pw@h:5432/bazaar_sim?dbname=railway"
    with pytest.raises(ConfigError):
        settings_for(tmp_path, monkeypatch, BAZAAR_URL=SIM_URL, DATABASE_URL=sneaky).require_database_url()


def test_the_sim_database_url_wins_against_a_simulator_only(tmp_path, monkeypatch):
    sim_db = "postgresql://postgres:pw@iriguchi.proxy.rlwy.net:28880/bazaar_sim?sslmode=require"
    s = settings_for(tmp_path, monkeypatch, BAZAAR_URL=SIM_URL, DATABASE_URL=REAL_DB, BAZAAR_SIM_DATABASE_URL=sim_db)
    assert s.require_database_url() == sim_db
    real = settings_for(tmp_path, monkeypatch, DATABASE_URL=REAL_DB, BAZAAR_SIM_DATABASE_URL=sim_db)
    assert real.require_database_url() == REAL_DB  # the real game keeps its own memory


def test_db_check_reports_the_refusal_instead_of_connecting(tmp_path, monkeypatch):
    from bazaar_agent import db

    settings_for(tmp_path, monkeypatch, BAZAAR_URL=SIM_URL, DATABASE_URL=REAL_DB)
    monkeypatch.setattr("bazaar_agent.config.REPO_ROOT", tmp_path)
    ok, lines = db.run_check()
    assert not ok and "railway" in lines[0] and "pw" not in " ".join(lines)


def test_pgconn_connect_refuses_the_real_database_against_a_simulator(tmp_path, monkeypatch):
    from bazaar_agent import pgconn

    settings_for(tmp_path, monkeypatch, BAZAAR_URL=SIM_URL, DATABASE_URL=REAL_DB)
    monkeypatch.setattr("bazaar_agent.config.REPO_ROOT", tmp_path)
    with pytest.raises(ConfigError):
        pgconn.connect()


def test_the_monitor_stream_gets_the_same_guard(tmp_path, monkeypatch):
    from bazaar_agent import cli

    s = settings_for(tmp_path, monkeypatch, BAZAAR_URL=SIM_URL, BAZAAR_KEY="tk-real-0042")
    with pytest.raises(ConfigError):
        cli.open_stream(s, lambda _: None)


def test_simulated_play_keeps_its_files_out_of_the_real_data_dir(tmp_path, monkeypatch):
    from bazaar_agent.config import REPO_ROOT, SIM_DATA_DIR

    monkeypatch.delenv("BAZAAR_DATA_DIR", raising=False)
    sim = settings_for(tmp_path, monkeypatch, BAZAAR_URL=SIM_URL)
    assert sim.data_dir == SIM_DATA_DIR and sim.feed_dir != REPO_ROOT / ".local" / "feed"
    assert settings_for(tmp_path, monkeypatch).data_dir == REPO_ROOT / ".local"
    monkeypatch.setenv("BAZAAR_DATA_DIR", str(tmp_path / "mine"))
    assert settings_for(tmp_path, monkeypatch, BAZAAR_URL=SIM_URL).data_dir == tmp_path / "mine"
