"""Sunday runs must validate every database target before libpq can connect."""

import importlib.util
import os
from pathlib import Path
from unittest.mock import MagicMock, Mock

import psycopg
import pytest

_spec = importlib.util.spec_from_file_location(
    "sim_sunday", Path(__file__).resolve().parents[1] / "scripts" / "sim_sunday.py"
)
assert _spec is not None and _spec.loader is not None
sunday = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sunday)


@pytest.fixture(autouse=True)
def no_libpq_environment(monkeypatch):
    for key in os.environ:
        if key.startswith("PG"):
            monkeypatch.delenv(key)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user@remote.example/postgres",
        "postgresql://user@127.0.0.1/railway",
        "postgresql://user@127.0.0.1/bazaar",
        "postgresql://user@127.0.0.1/postgres?hostaddr=203.0.113.1",
        "postgresql://user@127.0.0.1/postgres?service=shared",
        "host=127.0.0.1 dbname=postgres service=shared",
        "host=127.0.0.1,remote.example dbname=postgres",
        "host=127.0.0.1 dbname=postgres options='-c search_path=public'",
        "not a database URL",
    ],
)
def test_unsafe_admin_is_rejected_before_connect(monkeypatch, url):
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setenv("SIM_SUNDAY_PG_ADMIN_URL", url)
    with pytest.raises(SystemExit):
        sunday.database(8985, create=True)
    connect.assert_not_called()


def test_libpq_environment_cannot_override_admin(monkeypatch):
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setenv("PGSERVICE", "shared")
    with pytest.raises(SystemExit):
        sunday.database(8985, create=True)
    connect.assert_not_called()


def test_local_admin_only_creates_the_port_scoped_sim_database(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setenv("SIM_SUNDAY_PG_ADMIN_URL", sunday.LOCAL_ADMIN)
    url = sunday.database(8985, create=True)
    info = psycopg.conninfo.conninfo_to_dict(url)
    assert info["host"] == "127.0.0.1"
    assert info["dbname"] == "bazaar_sim_sunday_8985"
    statements = connect.return_value.__enter__.return_value.execute.call_args_list
    assert [call.args[0] for call in statements] == [
        'drop database if exists "bazaar_sim_sunday_8985" with (force)',
        'create database "bazaar_sim_sunday_8985"',
    ]


@pytest.mark.parametrize("name", ["railway", "RAILWAY", "%72ailway", "bazaar", "postgres", "bazaar_sim_railway"])
def test_agent_and_report_refuse_real_databases(monkeypatch, tmp_path, name):
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(psycopg, "connect", connect)
    url = f"postgresql://user@127.0.0.1/{name}"
    with pytest.raises(SystemExit):
        sunday.agent_env(8985, url, tmp_path, tmp_path / "empty.env")
    with pytest.raises(SystemExit):
        sunday.decision_rows(url)
    connect.assert_not_called()


def test_every_child_has_isolated_settings(monkeypatch, tmp_path):
    from bazaar_agent.config import load_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://user@remote.example/railway")
    monkeypatch.setenv("BAZAAR_SIM_DATABASE_URL", "postgresql://user@localhost/bazaar")
    monkeypatch.setenv("BAZAAR_ENV_FILE", "/must-not-read")
    empty = tmp_path / "empty.env"
    empty.write_text("")
    for env in (
        sunday.base_env(empty),
        sunday.agent_env(8985, sunday.NOWHERE_DB, tmp_path, empty),
        sunday.agent_env(8985, "postgresql://user@localhost/bazaar_sim_test", tmp_path, empty),
    ):
        assert "DATABASE_URL" not in env
        assert env["BAZAAR_SIM_DATABASE_URL"]
        assert env["SIM_DATABASE_URL"] == "memory"
        with monkeypatch.context() as child:
            for key in os.environ:
                child.delenv(key)
            for key, value in env.items():
                child.setenv(key, value)
            settings = load_settings()
            info = psycopg.conninfo.conninfo_to_dict(settings.require_database_url())
            assert info["host"] == "127.0.0.1"
            assert info["dbname"] in {"none", "bazaar_sim_test"}
