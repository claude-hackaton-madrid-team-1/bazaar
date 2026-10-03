"""A read-only Postgres login for teammates (DataGrip), applied with the admin DATABASE_URL.

The password is hashed here (libpq's SCRAM-SHA-256 verifier) and handed to `sql/readonly_user.sql`
as a transaction-local setting, so the plaintext never leaves this process: not on a command line,
not in a statement, not in the server's logs (a server logging every statement or error records the
verifier, which cannot log anyone in). The operator sees the password once, inside `connection_url()`.
"""

from __future__ import annotations

import re
import secrets
from importlib.resources import files
from urllib.parse import quote

import psycopg
from psycopg import sql as pgsql

from bazaar_agent.pgconn import Target
from bazaar_agent.venue import VENUE_KEYS_DDL

ROLE = "bazaar_team_ro"
SCHEMA = "public"
MIN_PASSWORD_LEN = 16
PASSWORD_SETTING = "bazaar.readonly_password"
# Tables this role must never read, created before the grants so a default privilege cannot re-grant one.
SECRET_TABLES_DDL = (VENUE_KEYS_DDL,)
_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")


class PasswordError(ValueError):
    """The operator's password is unusable. The message never carries the password."""


def generate_password() -> str:
    """43 URL-safe characters (256 bits): no escaping needed in a connection URL."""
    return secrets.token_urlsafe(32)


def check_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise PasswordError(f"password too short: at least {MIN_PASSWORD_LEN} characters")
    if any(c.isspace() or not c.isprintable() for c in password):
        raise PasswordError("password must not contain whitespace or control characters")
    return password


def script(role: str = ROLE, schema: str = SCHEMA) -> pgsql.Composed:
    """The SQL with names filled in. Plain lowercase names only: a literal sits inside `$$` bodies."""
    for name in (role, schema):
        if not _NAME.fullmatch(name):
            raise ValueError(f"not a plain Postgres name: {name!r}")
    text = files("bazaar_agent").joinpath("sql/readonly_user.sql").read_text(encoding="utf-8")
    return pgsql.SQL(text).format(  # type: ignore[arg-type]  # trusted file shipped in the package
        role=pgsql.Identifier(role), role_name=pgsql.Literal(role), schema=pgsql.Identifier(schema)
    )


def apply(conn: psycopg.Connection, password: str, *, role: str = ROLE, schema: str = SCHEMA) -> None:
    """Create or update `role` (idempotent) in one transaction; the password is sent only as a SCRAM verifier."""
    statements = script(role, schema)
    verifier = conn.pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256").decode()
    try:
        with conn.cursor() as cur:
            cur.execute(pgsql.SQL("set local search_path to {}").format(pgsql.Identifier(schema)))
            for ddl in SECRET_TABLES_DDL:
                cur.execute(ddl)  # type: ignore[arg-type]  # constant DDL
            cur.execute("select set_config(%s, %s, true)", (PASSWORD_SETTING, verifier))
            cur.execute(statements)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def connection_url(target: Target, password: str, role: str = ROLE) -> str:
    """What a teammate pastes into DataGrip: the admin URL's host, port and database, never its user."""
    host = f"[{target.host}]" if ":" in target.host else target.host
    return f"postgresql://{role}:{quote(password, safe='')}@{host}:{target.port}/{target.dbname}?sslmode=require"
