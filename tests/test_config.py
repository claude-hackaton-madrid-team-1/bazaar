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


# ---------------------------------------------------------------- real game vs simulator (BAZAAR_SIM)


REAL_URL = "https://bazaar.causaprima.ai"
SIM_URL = "https://bazaar-sim-production-1d48.up.railway.app"
REAL_DB = "postgresql://postgres:pw@iriguchi.proxy.rlwy.net:28880/railway?sslmode=require"
SIM_DB = "postgresql://postgres:pw@iriguchi.proxy.rlwy.net:28880/bazaar_sim?sslmode=require"
NAMES = (
    "BAZAAR_URL",
    "BAZAAR_SIM",
    "BAZAAR_SIM_KEY",
    "BAZAAR_KEY",
    "DATABASE_URL",
    "BAZAAR_SIM_DATABASE_URL",
    "BAZAAR_SIM_PORT",
)


def settings_for(tmp_path, monkeypatch, **env):
    for name in (*NAMES, "BAZAAR_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return load_settings(tmp_path / "none.env")


def test_unset_or_zero_is_the_real_game_with_the_team_key_exactly_as_before(tmp_path, monkeypatch):
    from bazaar_agent.config import REPO_ROOT

    for flag in (None, "0", "false", "off", ""):
        env = {"BAZAAR_KEY": "tk-real-0042"} | ({"BAZAAR_SIM": flag} if flag is not None else {})
        s = settings_for(tmp_path, monkeypatch, **env)
        assert not s.simulator and s.bazaar_url == REAL_URL
        assert s.require_team_key() == "tk-real-0042"
        assert s.data_dir == REPO_ROOT / ".local"
        assert s.target == {"mode": "real", "url": REAL_URL} and "real game" in s.target_line()


def test_bazaar_sim_1_is_the_hardcoded_simulator_with_a_sim_key(tmp_path, monkeypatch):
    for flag in ("1", "true", "yes", "on", "TRUE"):
        s = settings_for(tmp_path, monkeypatch, BAZAAR_SIM=flag, BAZAAR_KEY="tk-real-0042")
        assert s.simulator and s.bazaar_url == SIM_URL
        assert s.require_team_key() == "sim-team1"  # the default sim key; the real key is never loaded
        assert s.target == {"mode": "simulator", "url": SIM_URL} and "SIMULATOR" in s.target_line()
        assert "tk-real-0042" not in repr(s) + s.target_line()
    s = settings_for(tmp_path, monkeypatch, BAZAAR_SIM="1", BAZAAR_SIM_KEY="sim-team3")
    assert s.require_team_key() == "sim-team3"


def test_bazaar_url_is_gone_and_fails_fast(tmp_path, monkeypatch):
    for url in (REAL_URL, SIM_URL, "http://127.0.0.1:8765"):
        with pytest.raises(ConfigError, match="BAZAAR_URL is no longer read"):
            settings_for(tmp_path, monkeypatch, BAZAAR_URL=url)


def test_a_bad_flag_value_fails_fast(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="BAZAAR_SIM must be 1"):
        settings_for(tmp_path, monkeypatch, BAZAAR_SIM="maybe")


def test_the_real_key_can_never_reach_the_simulator(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch, BAZAAR_SIM="1", BAZAAR_SIM_KEY="tk-real-0042")
    with pytest.raises(ConfigError, match="only a simulator key") as e:
        s.require_team_key()
    assert "tk-real-0042" not in str(e.value)
    with pytest.raises(ConfigError):
        s.team_key()


def test_a_sim_key_can_never_reach_the_real_host(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch, BAZAAR_KEY="sim-team1")
    with pytest.raises(ConfigError, match="simulator key"):
        s.require_team_key()


def test_any_host_but_the_official_one_still_takes_only_a_sim_key(tmp_path, monkeypatch):
    from bazaar_agent.config import Settings

    with pytest.raises(ConfigError, match="only a simulator key"):
        Settings(bazaar_url="http://127.0.0.1:8765", bazaar_key="tk-real-0042").require_team_key()
    with pytest.raises(ConfigError, match="https"):
        Settings(bazaar_url="http://bazaar.causaprima.ai", bazaar_key="tk-real-0042").require_team_key()
    assert Settings(bazaar_url="http://127.0.0.1:8765", bazaar_key="sim-team1").require_team_key() == "sim-team1"


def test_simulated_play_never_writes_to_the_real_railway_database(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch, BAZAAR_SIM="1", DATABASE_URL=REAL_DB)
    with pytest.raises(ConfigError, match="BAZAAR_SIM_DATABASE_URL") as e:
        s.require_database_url()
    assert "pw" not in str(e.value)
    sneaky = "postgresql://postgres:pw@h:5432/bazaar_sim?dbname=railway"
    with pytest.raises(ConfigError):
        settings_for(tmp_path, monkeypatch, BAZAAR_SIM="1", DATABASE_URL=sneaky).require_database_url()
    with pytest.raises(ConfigError):
        settings_for(tmp_path, monkeypatch, BAZAAR_SIM="1").require_database_url(REAL_DB)  # explicit URL too


def test_the_sim_database_url_wins_against_the_simulator_only(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch, BAZAAR_SIM="1", DATABASE_URL=REAL_DB, BAZAAR_SIM_DATABASE_URL=SIM_DB)
    assert s.require_database_url() == SIM_DB
    real = settings_for(tmp_path, monkeypatch, DATABASE_URL=REAL_DB, BAZAAR_SIM_DATABASE_URL=SIM_DB)
    assert real.require_database_url() == REAL_DB  # the real game keeps its own memory


def test_db_check_and_pgconn_refuse_instead_of_connecting(tmp_path, monkeypatch):
    from bazaar_agent import db, pgconn

    settings_for(tmp_path, monkeypatch, BAZAAR_SIM="1", DATABASE_URL=REAL_DB)
    monkeypatch.setattr("bazaar_agent.config.REPO_ROOT", tmp_path)
    ok, lines = db.run_check()
    assert not ok and "railway" in lines[0] and "pw" not in " ".join(lines)
    ok, lines = db.run_check(REAL_DB)
    assert not ok and "railway" in lines[0]
    with pytest.raises(ConfigError):
        pgconn.connect()
    with pytest.raises(ConfigError):
        pgconn.connect(REAL_DB)


def test_the_monitor_stream_gets_the_same_guard(tmp_path, monkeypatch):
    from bazaar_agent import cli

    s = settings_for(tmp_path, monkeypatch, BAZAAR_SIM="1", BAZAAR_SIM_KEY="tk-real-0042")
    with pytest.raises(ConfigError):
        cli.open_stream(s, lambda _: None)


def test_simulated_play_keeps_its_files_out_of_the_real_data_dir(tmp_path, monkeypatch):
    from bazaar_agent.config import REPO_ROOT, SIM_DATA_DIR

    sim = settings_for(tmp_path, monkeypatch, BAZAAR_SIM="1")
    assert sim.data_dir == SIM_DATA_DIR and sim.feed_dir != REPO_ROOT / ".local" / "feed"
    monkeypatch.setenv("BAZAAR_DATA_DIR", str(tmp_path / "mine"))
    assert load_settings(tmp_path / "none.env").data_dir == tmp_path / "mine"


def test_the_flag_can_come_from_dotenv_and_the_environment_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("BAZAAR_SIM=1\nBAZAAR_KEY=tk-real-0042\n")
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    assert load_settings(env).simulator
    monkeypatch.setenv("BAZAAR_SIM", "0")
    assert not load_settings(env).simulator


def test_bazaar_sim_local_is_the_hardcoded_laptop_simulator(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch, BAZAAR_SIM="local", BAZAAR_KEY="tk-real-0042")
    assert s.simulator and s.bazaar_url == "http://127.0.0.1:8765" and s.require_team_key() == "sim-team1"


def test_bazaar_sim_port_moves_only_the_laptop_simulator_and_stays_on_loopback(tmp_path, monkeypatch):
    s = settings_for(tmp_path, monkeypatch, BAZAAR_SIM="local", BAZAAR_SIM_PORT="8817")
    assert s.bazaar_url == "http://127.0.0.1:8817" and s.require_team_key() == "sim-team1"
    real = settings_for(tmp_path, monkeypatch, BAZAAR_SIM_PORT="8817", BAZAAR_KEY="tk-real-0042")
    assert real.bazaar_url == "https://bazaar.causaprima.ai"  # the real game ignores it
    stray = settings_for(tmp_path, monkeypatch, BAZAAR_SIM_PORT="not-a-port", BAZAAR_KEY="tk-real-0042")
    assert stray.bazaar_url == "https://bazaar.causaprima.ai"  # never even parsed: it cannot stop the real game
    for bad in ("80", "70000", "8817/evil", "host:1"):
        with pytest.raises(ConfigError, match="BAZAAR_SIM_PORT"):
            settings_for(tmp_path, monkeypatch, BAZAAR_SIM="local", BAZAAR_SIM_PORT=bad)


def test_bazaar_env_file_replaces_the_repo_dotenv(tmp_path, monkeypatch):
    for name in (*NAMES, "TYPESAFE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    real = tmp_path / "real.env"
    real.write_text("TYPESAFE_API_KEY=ts-secret\nANTHROPIC_API_KEY=sk-secret\n")
    monkeypatch.setattr("bazaar_agent.config.REPO_ROOT", tmp_path)
    (tmp_path / ".env").write_text(real.read_text())
    assert load_settings().typesafe_api_key is not None
    empty = tmp_path / "empty.env"
    empty.write_text("")
    monkeypatch.setenv("BAZAAR_ENV_FILE", str(empty))
    s = load_settings()
    assert s.typesafe_api_key is None and s.anthropic_api_key is None


def test_a_missing_or_relative_env_file_fails_fast_instead_of_dropping_dotenv(tmp_path, monkeypatch):
    for bad in (str(tmp_path / "typo.env"), "relative.env"):
        monkeypatch.setenv("BAZAAR_ENV_FILE", bad)
        with pytest.raises(ConfigError, match="BAZAAR_ENV_FILE"):
            load_settings()
