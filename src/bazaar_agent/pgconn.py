"""Postgres connections: the one place every process opens one, from DATABASE_URL alone.

On Railway every teammate pastes the same URL (the public TCP proxy, `<name>.proxy.rlwy.net:<port>`)
into `.env`; locally it defaults to the docker compose Postgres. What the URL sets always wins
(`sslmode`, `connect_timeout`, ...): we only fill in what it leaves out, and never disable SSL.
The password never leaves this module: callers print `describe()` and `redact()`, never the URL.
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from bazaar_agent.config import DEFAULT_DATABASE_URL, load_settings

# Defaults only: a key the URL already sets is never overridden.
CONNECT_DEFAULTS = {
    "connect_timeout": "10",  # a laptop on venue Wi-Fi to the Railway proxy: generous, never a hang
    "keepalives": "1",  # a dropped proxy or Wi-Fi surfaces as an error the monitor recovers from
    "keepalives_idle": "30",
    "keepalives_interval": "10",
    "keepalives_count": "3",
}
DEFAULT_APP = "bazaar-cli"
MASK = "***"
_URL_PASSWORD = re.compile(r"(postgres(?:ql)?://[^:/@\s]*:)([^@\s]*)@")
_KEYWORD_PASSWORD = re.compile(r"(password\s*=\s*)('[^']*'|\S+)", re.IGNORECASE)


class DatabaseUrlError(ValueError):
    """DATABASE_URL cannot be parsed. The message is redacted: it never carries the password."""


def _passwords(url: str) -> set[str]:
    found = set()
    if m := _URL_PASSWORD.match(url):
        found.add(m.group(2))  # as typed, maybe percent-encoded
    with contextlib.suppress(psycopg.Error):
        found.add(str(conninfo_to_dict(url).get("password") or ""))  # decoded
    return {p for p in found if p}


def redact(text: str, url: str | None = None) -> str:
    """`text` without passwords: the one in `url` (raw and decoded), and any URL's or `password=`'s."""
    for secret in sorted(_passwords(url) if url else set(), key=len, reverse=True):
        text = text.replace(secret, MASK)
    text = _URL_PASSWORD.sub(rf"\g<1>{MASK}@", text)
    return _KEYWORD_PASSWORD.sub(rf"\g<1>{MASK}", text)


def _parse(url: str) -> dict[str, str]:
    try:
        return {k: str(v) for k, v in conninfo_to_dict(url).items() if v is not None}
    except psycopg.Error as e:
        raise DatabaseUrlError(f"DATABASE_URL is not a valid Postgres URL: {redact(str(e).strip(), url)}") from None


@dataclass(frozen=True)
class Target:
    """Where a URL points, safe to print: no password field exists."""

    user: str
    host: str
    port: str
    dbname: str
    sslmode: str
    is_local_default: bool

    def __str__(self) -> str:
        return f"{self.user}@{self.host}:{self.port}/{self.dbname}"


def _where(params: dict[str, str]) -> tuple[str, str, str, str]:
    user = params.get("user", "")
    return user, params.get("host", "localhost"), params.get("port", "5432"), params.get("dbname", user)


def describe(url: str) -> Target:
    params = _parse(url)
    where = _where(params)
    return Target(*where, params.get("sslmode", "prefer"), where == _where(_parse(DEFAULT_DATABASE_URL)))


def connection_params(url: str, app: str = DEFAULT_APP) -> dict[str, str]:
    """libpq keywords for `url`: our defaults and `application_name`, then everything the URL sets."""
    return {**CONNECT_DEFAULTS, "application_name": app, **_parse(url)}


def connect(database_url: str | None = None, *, app: str = DEFAULT_APP) -> psycopg.Connection:
    """Open Postgres at `database_url`, or DATABASE_URL (env, then `.env`, then the local default)."""
    url = load_settings().database_url.get_secret_value() if database_url is None else database_url
    return psycopg.connect(make_conninfo(**connection_params(url, app)))


RETRY_EVERY_S = 15.0  # >= connect_timeout: a black-holed host stalls at most one connect per window


class Reconnector:
    """One connection for a long-running process (the monitor, the ledger), opened lazily and reopened after a drop.

    `get()` returns None while Postgres is unreachable, so the caller keeps its fallback going and tries
    again later: after a failed open, the next attempt waits `retry_every_s` (a connect to a dead host can
    take `connect_timeout`, 10 s), so an outage never stalls every call. Call `drop()` after any failed
    write: the next `get()` starts fresh at once.
    """

    def __init__(
        self,
        open_conn: Callable[[], psycopg.Connection],
        notify: Callable[[str], None],
        *,
        name: str = "Postgres",
        fallback: str = "JSONL only until it is back",
        retry_every_s: float = RETRY_EVERY_S,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._open = open_conn
        self._notify = notify
        self._name, self._fallback = name, fallback
        self._retry_every_s, self._now = retry_every_s, now
        self._conn: psycopg.Connection | None = None
        self._down = False
        self._failed_at: float | None = None

    def get(self) -> psycopg.Connection | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        self._conn = None
        if self._failed_at is not None and self._now() - self._failed_at < self._retry_every_s:
            return None
        try:
            self._conn = self._open()
        except Exception as e:  # any failure: Postgres down, bad URL, schema lock timeout
            if not self._down:
                self._notify(f"{self._name} unavailable ({type(e).__name__}); {self._fallback}")
            self._down, self._failed_at = True, self._now()
            return None
        if self._down:
            self._notify(f"{self._name} reconnected")
        self._down, self._failed_at = False, None
        return self._conn

    @property
    def down(self) -> bool:
        """True after a failed open, until an open succeeds."""
        return self._down

    def drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
