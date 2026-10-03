"""The teammates' read-only login: SELECT works, every write is refused.

The integration tests create a throwaway role and schema, and run only against the local docker
Postgres (the default DATABASE_URL): roles are cluster-wide, so they never touch the shared Railway DB.
"""

import secrets

import psycopg
import pytest
from psycopg import errors, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_agent import readonly_user as ro
from bazaar_agent.config import DEFAULT_DATABASE_URL
from bazaar_agent.pgconn import describe

# ---------------------------------------------------------------- pure


def test_generated_passwords_are_long_unique_and_url_safe():
    a, b = ro.generate_password(), ro.generate_password()
    assert a != b and len(a) >= 40
    assert ro.check_password(a) == a
    assert ro.connection_url(describe("postgresql://u:p@h:1/railway"), a).count(a) == 1  # no escaping needed


@pytest.mark.parametrize("bad", ["Qz7", "a" * 20 + " b", "a" * 20 + "\n"], ids=["short", "space", "newline"])
def test_unusable_passwords_are_refused_without_echoing_them(bad):
    with pytest.raises(ro.PasswordError) as e:
        ro.check_password(bad)
    assert bad.strip() not in str(e.value)


def test_the_url_uses_the_admin_host_port_and_database_never_its_user():
    target = describe("postgresql://postgres:admin-secret@iriguchi.proxy.rlwy.net:28880/railway")
    url = ro.connection_url(target, "p@ss/word:with%chars-0123")
    assert url == (
        "postgresql://bazaar_team_ro:p%40ss%2Fword%3Awith%25chars-0123"
        "@iriguchi.proxy.rlwy.net:28880/railway?sslmode=require"
    )
    assert "postgres@" not in url and "admin-secret" not in url
    assert conninfo_to_dict(url)["password"] == "p@ss/word:with%chars-0123"


def test_the_script_renders_quoted_identifiers_and_has_no_write_grant():
    rendered = ro.script().as_string(None)
    assert '"bazaar_team_ro"' in rendered and "'bazaar_team_ro'" in rendered and '"public"' in rendered
    grants = [line for line in rendered.lower().splitlines() if line.strip().startswith("grant")]
    assert grants and all(g.startswith(("grant select", "grant usage", "grant connect")) for g in grants)
    assert "{" not in rendered


def test_cli_refuses_a_short_stdin_password_before_connecting(monkeypatch):
    monkeypatch.setattr(ro, "apply", lambda *a, **k: pytest.fail("must not connect"))
    result = CliRunner().invoke(cli.app, ["db", "readonly-user", "--password-stdin"], input="tiny\n")
    assert result.exit_code == 1 and "too short" in result.output and "tiny" not in result.output


# ---------------------------------------------------------------- local Postgres


@pytest.fixture(scope="module")
def admin_url():
    try:
        psycopg.connect(DEFAULT_DATABASE_URL, connect_timeout=3).close()
    except psycopg.OperationalError:
        pytest.skip("local Postgres not reachable (uv run bazaar db up)")
    return DEFAULT_DATABASE_URL


@pytest.fixture
def setup(admin_url):
    """A role + schema with one table; yields (role, password, schema). Both dropped afterwards."""
    suffix = secrets.token_hex(4)
    role, schema, password = f"bazaar_pytest_ro_{suffix}", f"bazaar_pytest_ro_{suffix}", ro.generate_password()
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        admin.execute(
            sql.SQL("create table {}.cards (id serial primary key, name text)").format(sql.Identifier(schema))
        )
        admin.execute(sql.SQL("insert into {}.cards (name) values ('LAV-03')").format(sql.Identifier(schema)))
    with psycopg.connect(admin_url) as admin:
        ro.apply(admin, password, role=role, schema=schema)
    yield role, password, schema
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("drop owned by {}").format(sql.Identifier(role)))
        admin.execute(sql.SQL("drop role {}").format(sql.Identifier(role)))


def login(admin_url, role, password, schema):
    params = {**conninfo_to_dict(admin_url), "user": role, "password": password, "options": f"-c search_path={schema}"}
    return psycopg.connect(make_conninfo(**params), autocommit=True)


@pytest.mark.integration
def test_role_can_select_and_its_sessions_are_read_only_with_a_timeout(admin_url, setup):
    with login(admin_url, *setup) as conn:
        assert conn.execute("select name from cards").fetchall() == [("LAV-03",)]
        assert conn.execute("show default_transaction_read_only").fetchone() == ("on",)
        assert conn.execute("show statement_timeout").fetchone() == ("30s",)
        with pytest.raises(errors.ReadOnlySqlTransaction):
            conn.execute("insert into cards (name) values ('x')")


@pytest.mark.integration
@pytest.mark.parametrize(
    "statement",
    [
        "insert into cards (name) values ('x')",
        "update cards set name = 'x'",
        "delete from cards",
        "truncate cards",
        "create table evil (id int)",
        "select nextval('cards_id_seq')",
    ],
    ids=["insert", "update", "delete", "truncate", "create", "nextval"],
)
def test_privileges_refuse_every_write_even_when_the_session_turns_read_write(admin_url, setup, statement):
    with login(admin_url, *setup) as conn:
        conn.execute("set default_transaction_read_only = off")  # a teammate can flip it; privileges still hold
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(statement)


@pytest.mark.integration
def test_tables_created_later_by_the_admin_are_readable(admin_url, setup):
    role, password, schema = setup
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("create table {}.later (v int)").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("insert into {}.later values (7)").format(sql.Identifier(schema)))
    with login(admin_url, *setup) as conn:
        assert conn.execute("select v from later").fetchall() == [(7,)]
        conn.execute("set default_transaction_read_only = off")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("insert into later values (8)")


@pytest.mark.integration
def test_reapplying_is_idempotent_and_rotates_the_password(admin_url, setup):
    role, old, schema = setup
    new = ro.generate_password()
    with psycopg.connect(admin_url) as admin:
        ro.apply(admin, new, role=role, schema=schema)
        row = admin.execute(
            "select rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolconnlimit,"
            " rolpassword like 'SCRAM-SHA-256$%%' from pg_authid where rolname = %s",
            (role,),
        ).fetchone()
    assert row == (True, False, False, False, 10, True)
    with login(admin_url, role, new, schema) as conn:
        assert conn.execute("select count(*) from cards").fetchone() == (1,)
    with pytest.raises(psycopg.OperationalError):
        login(admin_url, role, old, schema).close()
