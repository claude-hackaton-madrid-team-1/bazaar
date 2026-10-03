"""Connections from DATABASE_URL, password redaction and reconnects. No network: libpq only parses."""

import psycopg
import pytest
from typer.testing import CliRunner

from bazaar_agent import db, pgconn
from bazaar_agent.config import DEFAULT_DATABASE_URL

PASSWORD = "Rw7-q9Zs-unique-pw"
RAILWAY_URL = f"postgresql://postgres:{PASSWORD}@shuttle.proxy.rlwy.net:15140/railway"


def refuse_echoing_the_url(*args, **kwargs):
    """Worst case: a driver error that quotes the whole URL back."""
    raise psycopg.OperationalError(f'connection to server failed for "{RAILWAY_URL}" (password={PASSWORD})')


def test_defaults_fill_only_what_the_url_leaves_out():
    params = pgconn.connection_params(RAILWAY_URL, app="bazaar-monitor")
    assert params["host"] == "shuttle.proxy.rlwy.net" and params["port"] == "15140"
    assert params["application_name"] == "bazaar-monitor"
    assert params["connect_timeout"] == "10" and params["keepalives"] == "1"
    assert "sslmode" not in params  # libpq's default (prefer) encrypts whenever the server offers it

    custom = pgconn.connection_params(RAILWAY_URL + "?sslmode=require&connect_timeout=3&application_name=mine")
    assert (custom["sslmode"], custom["connect_timeout"], custom["application_name"]) == ("require", "3", "mine")


def test_a_bad_url_is_reported_without_its_password():
    url = "postgresql://postgres:p%zzSecretPw@host/db"  # libpq quotes the bad token back
    with pytest.raises(pgconn.DatabaseUrlError) as caught:
        pgconn.connection_params(url)
    assert "p%zzSecretPw" not in str(caught.value) and "SecretPw" not in str(caught.value)
    assert "not a valid Postgres URL" in str(caught.value)


def test_redact_removes_raw_decoded_url_and_keyword_passwords():
    encoded = "postgresql://u:a%40b-secret@h/db"  # decodes to a@b-secret
    text = "raw a%40b-secret, decoded a@b-secret, other postgres://x:hunter2@y/z, dsn password=hunter3 ok"
    clean = pgconn.redact(text, encoded)
    for secret in ("a%40b-secret", "a@b-secret", "hunter2", "hunter3"):
        assert secret not in clean
    assert "postgres://x:***@y/z" in clean and clean.endswith("ok")


def test_describe_never_carries_the_password_and_spots_the_local_default():
    target = pgconn.describe(RAILWAY_URL + "?sslmode=require")
    assert str(target) == "postgres@shuttle.proxy.rlwy.net:15140/railway"
    assert target.sslmode == "require" and not target.is_local_default
    assert PASSWORD not in repr(target)
    local = pgconn.describe(DEFAULT_DATABASE_URL)
    assert local.is_local_default and local.sslmode == "prefer"


class FakeConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_reconnector_survives_an_outage_and_reopens_after_a_drop():
    outcomes: list[object] = [OSError("down"), OSError("still down"), FakeConn(), FakeConn(), FakeConn()]
    notes: list[str] = []

    def open_conn():
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    clock = [0.0]
    pg = pgconn.Reconnector(open_conn, notes.append, now=lambda: clock[0])
    assert pg.get() is None
    clock[0] += pgconn.RETRY_EVERY_S
    assert pg.get() is None
    assert notes == ["Postgres unavailable (OSError); JSONL only until it is back"]  # warned once
    clock[0] += pgconn.RETRY_EVERY_S
    first = pg.get()
    assert isinstance(first, FakeConn) and pg.get() is first and notes[-1] == "Postgres reconnected"
    pg.drop()  # what the monitor does after a failed write
    assert first.closed
    second = pg.get()
    assert second is not None and second is not first
    second.closed = True  # the server dropped it: the next get() reopens without a drop()
    third = pg.get()
    assert third is not None and third is not second and not outcomes


def test_reconnector_tries_a_down_postgres_at_most_once_per_window():
    attempts: list[float] = []
    clock = [100.0]

    def down():
        attempts.append(clock[0])
        raise OSError("timeout")  # a black-holed host: each real attempt can take connect_timeout (10 s)

    pg = pgconn.Reconnector(down, lambda m: None, retry_every_s=15.0, now=lambda: clock[0])
    for step in range(10):  # a monitor or a ledger calling every 2 s during an outage
        clock[0] = 100.0 + 2 * step
        assert pg.get() is None
    assert attempts == [100.0, 116.0] and pg.down


def test_check_reports_unreachable_without_the_password(monkeypatch):
    monkeypatch.setattr(psycopg, "connect", refuse_echoing_the_url)
    ok, lines = db.run_check(RAILWAY_URL)
    out = "\n".join(lines)
    assert not ok and PASSWORD not in out
    assert "shuttle.proxy.rlwy.net" in out and "local default URL: no" in out
    assert "check DATABASE_URL" in out


def test_check_on_a_bad_url_fails_cleanly():
    ok, lines = db.run_check("postgresql://postgres:p%zzSecretPw@host/db")
    assert not ok and "SecretPw" not in "\n".join(lines)


def test_check_lines_when_reachable(monkeypatch):
    report = db.CheckReport("17.6", (40.0, 38.5, 39.1), True, None, False, [("alerts", 2), ("traders", 20)])
    monkeypatch.setattr(db, "check", lambda url: report)
    ok, lines = db.run_check(RAILWAY_URL + "?sslmode=require")
    out = "\n".join(lines)
    assert ok and PASSWORD not in out
    assert "40.0 / 38.5 / 39.1 ms (3 round trips)" in out
    assert "ssl       on (sslmode require)" in out
    assert "pgvector  off: this server has no pgvector" in out
    assert "traders" in out and "tables    2" in out


def test_cli_db_check_exits_nonzero_and_never_prints_the_password(monkeypatch):
    from bazaar_agent.cli import app

    monkeypatch.setenv("DATABASE_URL", RAILWAY_URL)
    monkeypatch.setattr(psycopg, "connect", refuse_echoing_the_url)
    result = CliRunner().invoke(app, ["db", "check"])
    assert result.exit_code == 1
    assert PASSWORD not in result.output and "unreachable" in result.output
