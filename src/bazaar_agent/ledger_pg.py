"""The guardrail ledger in Postgres: one accepts-per-tick count and one spend cap for every machine.

The taker and maker on Railway, `bazaar-duels`, and the CLI on a laptop all write the same `ledger`
table, so `max_accepts_per_tick` and `max_spend_per_game_hour` hold for the whole team, not per
process. `reserve_accept` counts and inserts under one advisory lock: two processes can never both
take the last accept of a tick. The connection is reopened after a drop (a Postgres restart, a lost
proxy); while it is down every call raises `LedgerUnavailable` and the caller sends nothing.

`open_ledger` is the one place a process picks its ledger. Live against the real game (a process that
sends game writes) it must be the SHARED one, a non-local DATABASE_URL, or the process refuses to start;
while that host is unreachable the live process fails closed and keeps retrying. A dry run (or a live run
against a simulator) may use this machine's JSONL file while Postgres is down at start, and moves back to
Postgres when it answers.
"""

from __future__ import annotations

import ipaddress
import re
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict

from bazaar_agent.guardrails import HANDS_OFF, Ledger, LedgerStore, hands_off_id, is_pack
from bazaar_agent.pgconn import RETRY_EVERY_S, DatabaseUrlError, Reconnector, Target, describe

ACCEPT_LOCK = "bazaar_agent.ledger.accept"
STATEMENT_TIMEOUT_MS = 3000  # a stuck query must not eat the tick
IDLE_IN_TRANSACTION_MS = 5000  # a writer that dies inside reserve_accept frees the advisory lock in 5 s
CONNECT_TIMEOUT_S = 5  # one open to a dead host costs at most this, once per RETRY_EVERY_S: never a whole tick
T = TypeVar("T")


class LedgerUnavailable(RuntimeError):
    """The shared ledger cannot be read or written now. Callers fail closed: no write this tick."""


class LedgerNotShared(RuntimeError):
    """A live process has no shared ledger to count against: it must not send a single game write."""


class PgLedger:
    """`LedgerStore` on the Postgres `ledger` table (created by `bazaar db init`).

    `connect` opens a connection; it is called lazily, and again after a drop, at most once per
    `pgconn.RETRY_EVERY_S` while Postgres is down (see `Reconnector`)."""

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection],
        source: str = "",
        *,
        log: Callable[[str], None] = lambda message: None,
        where: str = "postgres ledger table",
        name: str = "ledger: Postgres",
        down_note: str = "no game write until it is back (fail closed)",
        retry_every_s: float = RETRY_EVERY_S,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        def opened() -> psycopg.Connection:
            conn = connect()
            try:
                conn.autocommit = True  # each call is its own transaction; reserve_accept opens one explicitly
                conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
                conn.execute(f"set idle_in_transaction_session_timeout = {IDLE_IN_TRANSACTION_MS}")
            except BaseException:
                conn.close()
                raise
            return conn

        self._pg = Reconnector(opened, log, name=name, fallback=down_note, retry_every_s=retry_every_s, now=now)
        self._source = source
        self._failed = False  # the last call failed: down until one succeeds
        self.where = where

    def _run(self, what: str, query: Callable[[psycopg.Connection], T]) -> T:
        """`query` on the live connection. Down, or a query that fails: `LedgerUnavailable`, and the
        connection is dropped so a later call reconnects."""
        conn = self._pg.get()
        if conn is None:
            self._failed = True
            raise LedgerUnavailable(f"ledger {what} failed (Postgres unreachable)")
        try:
            result = query(conn)
        except psycopg.Error as e:
            self._failed = True
            if conn.closed or conn.broken or isinstance(e, psycopg.OperationalError | psycopg.InterfaceError):
                self._pg.drop()  # a dead or timed-out connection: the next call opens a fresh one
            raise LedgerUnavailable(f"ledger {what} failed ({type(e).__name__})") from None
        self._failed = False
        return result

    def ping(self) -> None:
        """A round trip to Postgres, or `LedgerUnavailable`: a live tick asks this before any game write."""
        self._run("check", lambda conn: conn.execute("select 1").fetchone())

    @property
    def down(self) -> bool:
        """The last call (or open) failed: no write until one succeeds. Reads flags only, never the network."""
        return self._failed or self._pg.down

    @property
    def reachable(self) -> bool:
        """Postgres answers now (opens a connection if none is open, within the retry throttle)."""
        return self._pg.get() is not None

    def _one(self, sql: str, args: tuple[object, ...]) -> int:
        row = self._run("read", lambda conn: conn.execute(sql, args).fetchone())  # type: ignore[arg-type]  # fixed SQL
        return int(row[0] or 0) if row else 0

    def record(self, kind: str, tick: int, t_hours: float, price: int = 0, item: str = "") -> None:
        self._run(
            "write",
            lambda conn: conn.execute(
                "insert into ledger (kind, tick, t_hours, price, item, source) values (%s, %s, %s, %s, %s, %s)",
                (kind, tick, t_hours, int(price), item, self._source),
            ),
        )

    def spent_since(self, t_hours: float, prefix: str = "") -> int:
        if not prefix:
            return self._one("select sum(price) from ledger where kind = 'spend' and t_hours > %s", (t_hours,))
        return self._one(
            "select sum(price) from ledger where kind = 'spend' and t_hours > %s and starts_with(item, %s)",
            (t_hours, prefix),
        )

    def packs_since(self, t_hours: float) -> Counter[str]:
        rows = self._run(
            "read",
            lambda conn: conn.execute(
                "select item, count(*) from ledger where kind = 'spend' and t_hours > %s and price > 0 group by item",
                (t_hours,),
            ).fetchall(),
        )
        return Counter({str(item): int(n) for item, n in rows if is_pack(str(item or ""))})

    def accepts_in_tick(self, tick: int) -> int:
        return self.count_in_tick("accept", tick)

    def accept_items(self, tick: int) -> list[str]:
        rows = self._run(
            "read",
            lambda conn: conn.execute(
                "select item from ledger where kind = 'accept' and tick = %s order by id", (tick,)
            ).fetchall(),
        )
        return [str(item or "") for (item,) in rows]

    def accept_rows(self, tick: int) -> list[tuple[str, int]]:
        rows = self._run(
            "read",
            lambda conn: conn.execute(
                "select item, price from ledger where kind = 'accept' and tick = %s order by id", (tick,)
            ).fetchall(),
        )
        return [(str(item or ""), int(price or 0)) for item, price in rows]

    def hands_off_ids(self) -> set[int]:
        """Offer ids a person posted by hand (`guardrails.HANDS_OFF` listing rows), from every machine."""
        rows = self._run(
            "read",
            lambda conn: conn.execute(
                "select item from ledger where kind = 'listing' and item like %s", (HANDS_OFF + "%",)
            ).fetchall(),
        )
        ids = (hands_off_id(str(item or "")) for (item,) in rows)
        return {i for i in ids if i is not None}

    def count_in_tick(self, kind: str, tick: int) -> int:
        return self._one("select count(*) from ledger where kind = %s and tick = %s", (kind, tick))

    def count_since(self, kind: str, t_hours: float, prefix: str = "") -> int:
        return self._one(
            "select count(*) from ledger where kind = %s and t_hours > %s and starts_with(item, %s)",
            (kind, t_hours, prefix),
        )

    def reserve_accept(self, tick: int, t_hours: float, price: int, item: str, limit: int) -> bool:
        """True when this process got one of the tick's accepts (and it is recorded); False when spent.

        Atomic twice over: an advisory lock serializes the count-and-insert, and the unique index on
        (tick, slot) refuses a second row for the same slot even from a writer that skipped the lock."""

        def reserve(conn: psycopg.Connection) -> bool:
            try:
                with conn.transaction():
                    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (ACCEPT_LOCK,))
                    taken = conn.execute(
                        "select count(*) from ledger where kind = 'accept' and tick = %s", (tick,)
                    ).fetchone()
                    slot = int(taken[0]) + 1 if taken is not None else 1
                    if slot > limit:
                        return False
                    conn.execute(
                        "insert into ledger (kind, tick, t_hours, price, item, source, slot) "
                        "values ('accept', %s, %s, %s, %s, %s, %s)",
                        (tick, t_hours, int(price), item, self._source, slot),
                    )
            except psycopg.errors.UniqueViolation:
                return False
            return True

        return self._run("accept reservation", reserve)

    def release_accept(self, tick: int, item: str) -> None:
        """Give back a reserved accept the game refused (a refused request costs nothing, RULES.md): the
        newest reservation of `item` in `tick` is deleted, so its slot can be taken again."""
        self._run(
            "accept release",
            lambda conn: conn.execute(
                "delete from ledger where id = (select id from ledger where kind = 'accept' and tick = %s "
                "and item = %s order by id desc limit 1)",
                (tick, item),
            ),
        )

    def close(self) -> None:
        self._pg.drop()


class FallbackLedger:
    """Dry run (or a simulator) only: the Postgres ledger while it answers, this machine's JSONL file while not.

    Postgres is retried (throttled by `PgLedger`) on every call, so the run moves back to it once it
    answers. Never used live against the real game: there a down ledger means no write, not a local count."""

    def __init__(self, pg: PgLedger, file: Ledger) -> None:
        self._pg, self._file = pg, file

    @property
    def on_file(self) -> bool:
        """The last call counted on the file (Postgres down). Reads flags only, never the network."""
        return self._pg.down

    def _use(self, call: Callable[[LedgerStore], T]) -> T:
        try:
            return call(self._pg)
        except LedgerUnavailable:
            return call(self._file)

    @property
    def where(self) -> str:
        return self._pg.where if self._pg.reachable else f"{self._file.where} (this machine only)"

    def record(self, kind: str, tick: int, t_hours: float, price: int = 0, item: str = "") -> None:
        self._use(lambda ledger: ledger.record(kind, tick, t_hours, price, item))

    def spent_since(self, t_hours: float, prefix: str = "") -> int:
        return self._use(lambda ledger: ledger.spent_since(t_hours, prefix))

    def packs_since(self, t_hours: float) -> Counter[str]:
        return self._use(lambda ledger: ledger.packs_since(t_hours))

    def accepts_in_tick(self, tick: int) -> int:
        return self._use(lambda ledger: ledger.accepts_in_tick(tick))

    def count_in_tick(self, kind: str, tick: int) -> int:
        return self._use(lambda ledger: ledger.count_in_tick(kind, tick))

    def count_since(self, kind: str, t_hours: float, prefix: str = "") -> int:
        return self._use(lambda ledger: ledger.count_since(kind, t_hours, prefix))

    def accept_items(self, tick: int) -> list[str]:
        return self._use(lambda ledger: ledger.accept_items(tick))

    def accept_rows(self, tick: int) -> list[tuple[str, int]]:
        return self._use(lambda ledger: ledger.accept_rows(tick))

    def hands_off_ids(self) -> set[int]:
        return self._use(lambda ledger: ledger.hands_off_ids())

    def reserve_accept(self, tick: int, t_hours: float, price: int, item: str, limit: int) -> bool:
        return self._use(lambda ledger: ledger.reserve_accept(tick, t_hours, price, item, limit))

    def release_accept(self, tick: int, item: str) -> None:
        self._use(lambda ledger: ledger.release_accept(tick, item))


LOCAL_HOSTS = ("localhost", "host.docker.internal", "gateway.docker.internal")  # this machine, seen from docker
# 127.1, 2130706433, 0x7f000001, 0x7f.1, 0177.1: inet_aton forms that libpq dials as IPv4
NUMERIC_HOST = re.compile(r"(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+)){0,3}")


def is_shared(target: Target) -> bool:
    """A database other machines reach too: not the docker default, not this machine or its LAN."""
    host = target.host.strip("[]").rstrip(".").lower()
    if target.is_local_default or not host or host.startswith("/"):  # "/..." is a Unix socket
        return False
    if host in LOCAL_HOSTS or host.endswith((".localhost", ".local")):  # this machine, or mDNS on its LAN
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:  # a host name (Railway's proxy or private network), not one label (a compose service)
        return "." in host and NUMERIC_HOST.fullmatch(host) is None
    return not (address.is_loopback or address.is_private or address.is_link_local or address.is_unspecified)


PLAIN_HOST = re.compile(r"/[\w./-]*|\[?[0-9A-Za-z.:-]+\]?")  # a name, an IP literal, or a Unix socket directory


def _target(database_url: str) -> tuple[Target | None, str]:
    """Where the URL points, as a log-safe label (host and port only, never a credential). A host or port
    libpq read out of a mangled URL (a raw "@" or "/" in the password) may hold part of the password: such
    a URL is never labelled and never shared. So is a host list or a `hostaddr` (it may dial this machine)."""
    unsafe = None, "an unparseable DATABASE_URL (percent-encode the password)"
    try:
        target = describe(database_url)
        extra = conninfo_to_dict(database_url)
    except (DatabaseUrlError, psycopg.Error):
        return unsafe
    if not PLAIN_HOST.fullmatch(target.host) or not target.port.isdigit() or "hostaddr" in extra:
        return unsafe
    if any("@" in str(v) for k, v in extra.items() if k not in ("user", "password")):
        return unsafe  # the real "@" landed past a password fragment that libpq took for the host
    return target, f"{target.host}:{target.port}"


def official_game(game_url: str) -> bool:
    """BAZAAR_URL is the real game, not a simulator: only there a live write needs the shared ledger."""
    from bazaar_agent.config import DEFAULT_URL

    return (urlsplit(game_url).hostname or "").rstrip(".") == urlsplit(DEFAULT_URL).hostname  # hostname is lowercase


def _opener(app: str) -> Callable[[], psycopg.Connection]:
    """The first open applies the schema (`connect_ready`); a reconnect is a plain open, so a Postgres that
    comes back never makes a tick wait on the schema's lock. Both wait at most `CONNECT_TIMEOUT_S`."""
    from bazaar_agent import db, pgconn

    ready = False

    def open_conn() -> psycopg.Connection:
        nonlocal ready
        if ready:
            return pgconn.connect(app=app, connect_timeout_s=CONNECT_TIMEOUT_S)
        conn = db.connect_ready(app=app, connect_timeout_s=CONNECT_TIMEOUT_S)
        ready = True
        return conn

    return open_conn


def ensure_writable(ledger: LedgerStore) -> None:
    """Before a tick's first game write: the shared Postgres ledger answers, or `LedgerUnavailable` (send
    nothing this tick). A file ledger (dry run, simulator) is always writable."""
    if isinstance(ledger, PgLedger):
        ledger.ping()


def ledger_health(ledger: LedgerStore) -> str:
    """For /health (public, so no host): shared | down (no game write until it answers) | local file."""
    if isinstance(ledger, PgLedger):
        return "down" if ledger.down else "shared"
    if isinstance(ledger, FallbackLedger):
        return "local file" if ledger.on_file else "shared"
    return "local file"


def open_ledger(
    data_dir: Path,
    *,
    source: str,
    live: bool = False,
    database_url: str | None = None,
    game_url: str | None = None,
    connect: Callable[[], psycopg.Connection] | None = None,
    log: Callable[[str], None] = lambda message: None,
) -> LedgerStore:
    """The ledger this process counts against, chosen once and logged (host only, never a password).

    - live against the real game: the shared Postgres ledger (a non-local DATABASE_URL), reconnecting after
      a drop and failing closed while it is down; no shared DATABASE_URL raises `LedgerNotShared`.
    - dry run, or live against a simulator: Postgres when it answers at start (a `PgLedger` that fails
      closed if it drops later), else this machine's JSONL file until Postgres answers.
    """
    from bazaar_agent.config import load_settings

    if database_url is None or game_url is None:
        settings = load_settings()
        database_url = settings.database_url.get_secret_value() if database_url is None else database_url
        game_url = settings.bazaar_url if game_url is None else game_url
    target, label = _target(database_url)
    shared = target is not None and is_shared(target)
    strict = live and official_game(game_url)
    if strict and not shared:
        raise LedgerNotShared(
            f"live trading needs the team's shared ledger, and DATABASE_URL points at {label} (this machine "
            "only): set DATABASE_URL to the shared Railway Postgres (see `uv run bazaar db check`) or run dry"
        )
    opener = connect or _opener(f"bazaar-{source}")
    scope = "shared, counted across every machine" if shared else "this machine only"
    where = f"postgres ledger table on {label} ({scope})"
    file = Ledger(data_dir / "ledger.jsonl")
    down_note = (
        "no game write until it answers (fail closed)"
        if strict
        else f"using {file.where} (this machine only) until it answers"
    )
    pg = PgLedger(opener, source, log=log, where=where, name=f"ledger: Postgres on {label}", down_note=down_note)
    up = pg.reachable  # down: the reconnector has already said so, with the error's type
    if up:
        log(f"ledger: {where}")
    if live and not strict:
        log(f"ledger: {urlsplit(game_url).hostname} is a simulator, so a live run may count on {file.where}")
    return pg if up or strict else FallbackLedger(pg, file)
