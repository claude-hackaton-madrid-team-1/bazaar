"""Our holdings in Postgres: `/api/me` as our processes read it, shared so we never decide on a stale view.

Album first (AGENTS.md): every agent reads `/api/me` before it buys, sells, lists or negotiates, and again
after every deal. The taker, the maker, the MCP server and the CLI share one key's 5 req/s, so the first
process that needs `/me` in a tick reads it and upserts `me_snapshots`; the others answer from that row
while it is provably current. A stored snapshot is a decision input only when ALL of these hold:

1. tick  — it was read in the reader's current game tick (the server's `tick` in /me);
2. epoch — no state-changing send of ours, from any process, started or finished since it was read
           (`holdings_state.epoch`, bumped before AND after every such send by `sdk.TrackedBazaar`);
3. calm  — no thread message of ours went out this tick. Friday's feed shows a dealer's answer, and the
           settlement of a deal, at the tick boundary (27 of 27), so this is the conservative case: an
           answer inside the tick would settle at once;
4. age   — it is younger than `holdings_max_age_s` (GUARDRAILS.md): the backstop for what we cannot
           see coming.

The row must also match itself (its payload names our team, its tick and its digest), and it belongs to
one world ("real" or "sim:<host>"): a simulator never answers for the game. Anything unknown is a live
read: no Postgres yet, no clock, a tick about to end, no team id yet, a row that does not match, a send of
ours whose bump was lost, a lock wait that timed out, or `holdings_from_db = false`. One process at a
time reads `/me` for the team (`pg_advisory_xact_lock`), so two agents that start a tick together make one
call, not two; every live read is upserted, so the table holds the newest view any of our processes saw.

Nothing here may hold up a send or a tick: every Postgres call runs on a worker thread per connection
(`SharedDb.call`), the write tracker has its own connection and waits at most `HOOK_TIMEOUT_S` for a bump,
and a read waits at most `READ_DEADLINE_S` before it reads `/me` live, whatever the network does.
"""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import logging
import queue
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import psycopg
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bazaar_agent.catalog_db import CatalogSync, save_catalog
from bazaar_agent.config import Settings
from bazaar_agent.guardrails import STARTER_STALL_MARKER, Guardrails
from bazaar_agent.identity import valid_team_id
from bazaar_agent.sdk import TEAM_RETRIES, TEAM_TIMEOUT_S
from bazaar_agent.ticks import Clock

LOCK_TIMEOUT_MS = 3000  # wait this long for another process's /me read, then read it ourselves
STATEMENT_TIMEOUT_MS = 5000
IDLE_IN_TX_TIMEOUT_MS = 20000  # a reader that hangs mid-read never holds the team's lock longer
TICK_SLACK_S = 2.0  # a thread message this close before our clock's tick start still counts as this tick
TICK_END_MARGIN_S = 1.0  # a clock this close to its tick's end is not trusted to name the current tick
CONNECT_TIMEOUT_S = 3  # a laptop off the network answers live in 3 s, not the default 10
RETRY_AFTER_S = 30.0  # Postgres unreachable: live reads only, for this long, before trying it again
HOOK_TIMEOUT_S = 0.2  # the write tracker never holds a send longer than this, whatever Postgres does
READ_DEADLINE_S = 5.0  # a database read (lock wait 3 s + the leader's /me) past this: read /me live
STUCK_AFTER_S = 5.0  # a worker busy this long is stuck: reads skip the database until it is free
WRITER_RETRY_AFTER_S = 5.0  # the writer reconnects sooner: a lost bump is a lost invalidation
# The SDK's own budget for one /me (every attempt of `sdk.TEAM_TIMEOUT_S`, plus backoff): a read already asked.
ME_BUDGET_S = (TEAM_RETRIES + 1) * TEAM_TIMEOUT_S + 5.0
EXIT_CATCH_UP_S = 2.0  # a one-shot command waits this long, at exit, to record a bump it lost
NO_HOLDINGS_EFFECT = ("/api/duels", "/api/flags")  # sends that move no card and no cash

Source = Literal["db", "live"]
WriteKind = Literal["thread", "trade"]
log = logging.getLogger(__name__)


def write_kind(method: str, path: str) -> WriteKind | None:
    """What a send can do to our holdings: None (a read, a duel move, a flag); "thread" (a message or a new
    thread: a counterparty may answer and settle at once); "trade" (everything else, unknown routes too)."""
    if method.upper() == "GET":
        return None
    bare = path.split("?", 1)[0].rstrip("/")
    if bare.startswith(NO_HOLDINGS_EFFECT):
        return None
    if bare == "/api/threads" or (bare.startswith("/api/threads/") and bare.endswith("/messages")):
        return "thread"
    return "trade"


# ---------------------------------------------------------------- the /me payload (validated: external input)


class MeAsset(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    kind: str = Field(max_length=20)
    ref: str = Field(max_length=40)
    rarity: str | None = None
    set: str | None = None
    serial: int | None = None
    your_value: float | None = None


class MePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(pattern=r"^t\d{1,3}$")
    tick: int | None = Field(default=None, ge=0)
    cash: float
    level: int | None = None
    assets: list[MeAsset] = Field(default_factory=list)
    album: dict[str, Any] = Field(default_factory=dict)
    affinity: dict[str, float] = Field(default_factory=dict)
    score: dict[str, Any] | None = None


SECRET_FIELD = re.compile(r"key|token|secret|password", re.IGNORECASE)
# Values shaped like a team key, a broker key or an API key, wherever they sit (`runtime.tools.KEY_SHAPES`).
SECRET_VALUE = re.compile(
    r"\b(?:tk-[A-Za-z0-9_-]{6,}|bk_[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{16,}|[a-z][a-z0-9]{1,20}_(?:ak|bk)_[A-Za-z0-9_-]{8,})"
)
REDACTED = "[redacted]"


def without_secrets(value: Any) -> Any:
    """`/api/me` carries `starter_broker_key` once our free stall exists: no field named like a key, token,
    secret or password, and no value shaped like a key, is ever stored or answered (read the broker key
    from a live `team.me()` when you need it)."""
    if isinstance(value, dict):
        kept = {k: without_secrets(v) for k, v in value.items() if not SECRET_FIELD.search(str(k))}
        if value.get("starter_broker_key"):  # the key goes; that we have the free stall stays (runs_venue)
            kept[STARTER_STALL_MARKER] = True
        return kept
    if isinstance(value, list):
        return [without_secrets(v) for v in value]
    if isinstance(value, str) and SECRET_VALUE.search(value):
        return REDACTED
    return value


def parse_me(raw: Any) -> MePayload | None:
    try:
        return MePayload.model_validate(raw)
    except ValidationError:
        return None


def digest(me: MePayload) -> str:
    """The etag of what we hold: cash, level, every asset and the album counts. Same holdings, same digest."""
    pages = sorted((str(p.get("set")), str(p.get("have"))) for p in me.album.get("pages") or [] if isinstance(p, dict))
    basis = {
        "cash": me.cash,
        "level": me.level,
        "assets": sorted((a.id, a.kind, a.ref) for a in me.assets),
        "pages": pages,
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()[:16]


def summary(me: MePayload) -> dict[str, Any]:
    """Our cards with asset ids, duplicates (ref -> asset ids), sealed packs and album pages."""
    cards = sorted((a for a in me.assets if a.kind == "card"), key=lambda a: (a.ref, a.id))
    by_ref: dict[str, list[int]] = {}
    for a in cards:
        by_ref.setdefault(a.ref, []).append(a.id)
    return {
        "cards": [
            {
                "asset": a.id,
                "ref": a.ref,
                "set": a.set,
                "rarity": a.rarity,
                "serial": a.serial,
                "your_value": a.your_value,
            }
            for a in cards
        ],
        "duplicates": {ref: ids for ref, ids in by_ref.items() if len(ids) > 1},
        "packs": [{"asset": a.id, "pack": a.ref} for a in sorted(me.assets, key=lambda a: a.id) if a.kind != "card"],
        "pages": [p for p in me.album.get("pages") or [] if isinstance(p, dict)],
    }


@dataclass(frozen=True)
class MeRead:
    """One answer to "what do we hold": the /me payload and where it came from."""

    me: dict[str, Any]
    source: Source
    tick: int | None
    age_s: float
    epoch: int | None  # None: read while Postgres was unavailable (not stored)
    digest: str
    read_by: str
    why: str  # "fresh" for a stored snapshot; else why it was read live

    def meta(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "tick": self.tick,
            "age_s": round(self.age_s, 2),
            "epoch": self.epoch,
            "digest": self.digest,
            "read_by": self.read_by,
            "why": self.why,
        }

    def line(self) -> str:
        if self.source == "db":
            return f"/me from db (tick {self.tick}, {self.age_s:.1f} s old, epoch {self.epoch}, read by {self.read_by})"
        return f"/me live ({self.why})"


# ---------------------------------------------------------------- which game, and what a process may write


@dataclass(frozen=True)
class Scope:
    """`world` keys the snapshots and the epoch ("real", or "sim:<host:port>"). `shared_tables`: whether this
    process may write the tables that have no world (the evals' `snapshots`, `cards`): the real game always,
    a simulator only in a database of its own (BAZAAR_SIM_DATABASE_URL)."""

    world: str = "real"
    shared_tables: bool = True


REAL = Scope()


def scope_of(settings: Settings) -> Scope:
    if not settings.simulator:
        return REAL
    return Scope(f"sim:{urlsplit(settings.bazaar_url).netloc or '?'}", settings.sim_database)


# ---------------------------------------------------------------- SQL (plain, no ORM)


@dataclass(frozen=True)
class Stored:
    tick: int
    epoch: int
    digest: str
    read_by: str
    me: dict[str, Any]
    age_s: float
    epoch_now: int
    messaged: bool


def stored(conn: psycopg.Connection, world: str, team: str, tick: int, into_tick_s: float) -> Stored | None:
    """The snapshot of `team` at exactly `tick` in `world`, with the state it is judged against. Exactly: a row
    from a higher tick is a simulator world that was reset, or a reader whose clock lags (it reads live)."""
    row = conn.execute(
        "select s.tick, s.epoch, s.digest, s.read_by, s.me, "
        "extract(epoch from clock_timestamp() - s.read_at)::float8, coalesce(h.epoch, 0), "
        "coalesce(h.thread_message_at > clock_timestamp() - make_interval(secs => %(into)s), false) "
        "from me_snapshots s left join holdings_state h on h.scope = s.world "
        "where s.world = %(world)s and s.team = %(team)s and s.tick = %(tick)s",
        {"into": into_tick_s, "world": world, "team": team, "tick": tick},
    ).fetchone()
    return Stored(*row) if row else None


def verdict(row: Stored | None, team: str, max_age_s: float) -> str:
    """'fresh', or why the stored snapshot is not a decision input (rules 2-4, then the row's own integrity:
    its payload names our team, its tick and its digest; rule 1 is the query's)."""
    if row is None:
        return "no snapshot this tick"
    if row.epoch != row.epoch_now:
        return "a write of ours since it was read"
    if row.messaged:
        return "a thread message of ours this tick"
    if row.age_s > max_age_s:
        return f"older than {max_age_s:g} s"
    me = parse_me(row.me)
    if me is None or me.id != team or (me.tick is not None and me.tick != row.tick) or digest(me) != row.digest:
        return "stored row does not match itself"
    return "fresh"


def current_epoch(conn: psycopg.Connection, world: str) -> int:
    return epoch_and_time(conn, world)[0]


def epoch_and_time(conn: psycopg.Connection, world: str) -> tuple[int, Any]:
    """The epoch and the server's clock, read BEFORE `/me`: the row's `read_at` is when the read began, so a
    store that lands late (a stalled link) makes the row look old, never new."""
    row = conn.execute(
        "select coalesce((select epoch from holdings_state where scope = %s), 0), clock_timestamp()", (world,)
    ).fetchone()
    assert row is not None
    return int(row[0]), row[1]


def bump(conn: psycopg.Connection, world: str, kind: WriteKind, what: str, writer: str) -> None:
    """A send of ours: every snapshot read before now is stale; a thread message also clouds this tick."""
    conn.execute(
        "insert into holdings_state (scope, epoch, written_at, thread_message_at, last_write, last_writer) "
        "values (%(scope)s, 1, clock_timestamp(), case when %(thread)s then clock_timestamp() end, %(what)s, "
        "%(writer)s) on conflict (scope) do update set epoch = holdings_state.epoch + 1, "
        "written_at = excluded.written_at, "
        "thread_message_at = coalesce(excluded.thread_message_at, holdings_state.thread_message_at), "
        "last_write = excluded.last_write, last_writer = excluded.last_writer",
        {"scope": world, "thread": kind == "thread", "what": what[:120], "writer": writer[:40]},
    )


def save(conn: psycopg.Connection, scope: Scope, me: MePayload, raw: dict[str, Any], tick: int, epoch: int,
         read_by: str, read_at: Any) -> bool:  # fmt: skip
    """Upsert the snapshot: a newer epoch, or the same epoch read later, wins; True when this row won. Only
    then, and only when the scope may, the evals' `snapshots` row of the tick follows it."""
    s = summary(me)
    written = conn.execute(
        "insert into me_snapshots (world, team, tick, epoch, digest, read_at, read_by, cash, level, cards, "
        "duplicates, packs, pages, affinity, score, me) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s, %s, %s) "
        "on conflict (world, team, tick) do update set epoch = excluded.epoch, digest = excluded.digest, "
        "read_at = excluded.read_at, read_by = excluded.read_by, cash = excluded.cash, level = excluded.level, "
        "cards = excluded.cards, duplicates = excluded.duplicates, packs = excluded.packs, pages = excluded.pages, "
        "affinity = excluded.affinity, score = excluded.score, me = excluded.me "
        "where excluded.epoch > me_snapshots.epoch "
        "or (excluded.epoch = me_snapshots.epoch and excluded.read_at >= me_snapshots.read_at)",
        (
            scope.world, me.id, tick, epoch, digest(me), read_at, read_by, round(me.cash), me.level,
            json.dumps(s["cards"]), json.dumps(s["duplicates"]), json.dumps(s["packs"]), json.dumps(s["pages"]),
            json.dumps(me.affinity), json.dumps(me.score), json.dumps(raw),
        ),
    ).rowcount == 1  # fmt: skip
    if written and scope.shared_tables:
        conn.execute(
            "insert into snapshots (tick, cash, level, assets, album, score) values (%s, %s, %s, %s, %s, %s) "
            "on conflict (tick) do update set cash = excluded.cash, level = excluded.level, "
            "assets = excluded.assets, album = excluded.album, score = excluded.score",
            (tick, round(me.cash), me.level, json.dumps(raw.get("assets")), json.dumps(raw.get("album")),
             json.dumps(me.score)),
        )  # fmt: skip
    return written


# ---------------------------------------------------------------- connections that never hold up a send


class SharedDb:
    """One autocommit connection, reopened after a failure, used by ONE worker thread.

    `call(fn, timeout_s)` runs `fn(conn)` on the worker and waits at most `timeout_s`: Postgres I/O that hangs
    (a black-holed proxy, a network change) costs the caller its deadline, never more. A call that overruns
    marks the worker stuck; later calls answer at once (not ok) until it is free, except `queue_if_stuck`
    ones (a bump must still land, late). `conn` is None while Postgres is down. `session()` is the direct,
    synchronous access the worker (and tests) use; nothing else touches the connection."""

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None,
        now: Callable[[], float] = time.monotonic,
        *,
        retry_after_s: float = RETRY_AFTER_S,
        name: str = "holdings-db",
    ) -> None:
        self._connect, self._now, self.retry_after_s, self.name = connect, now, retry_after_s, name
        self._conn: psycopg.Connection | None = None
        self._down_until = 0.0
        self.lock = threading.RLock()
        self._jobs: queue.Queue[tuple[Callable[[psycopg.Connection | None], Any], dict[str, Any], threading.Event]]
        self._jobs = queue.Queue()
        self._worker: threading.Thread | None = None
        self._starting = threading.Lock()  # never `lock`: a hung worker holds that one
        self._busy_since: float | None = None
        self.on_connect: Callable[[psycopg.Connection], None] | None = None  # runs on the worker, once per open

    @contextmanager
    def session(self, timeout_s: float | None = None) -> Iterator[psycopg.Connection | None]:
        if not self.lock.acquire(timeout=-1 if timeout_s is None else timeout_s):
            yield None
            return
        try:
            yield self._get()
        finally:
            self.lock.release()

    def call(
        self,
        fn: Callable[[psycopg.Connection | None], Any],
        timeout_s: float,
        *,
        queue_if_stuck: bool = False,
    ) -> tuple[bool, Any]:
        """(True, fn's answer) when the worker finished in time; (False, None) when it did not. An exception
        `fn` raised (a refused `/me`) is raised here. Without a database, `fn(None)` runs in the caller."""
        if self._connect is None:
            return True, fn(None)
        if self.stuck() and not queue_if_stuck:
            return False, None
        box: dict[str, Any] = {}
        done = threading.Event()
        self._start()
        self._jobs.put((fn, box, done))
        if not done.wait(timeout_s):
            return False, None
        if "error" in box:
            raise box["error"]
        return True, box.get("value")

    def warm(self) -> None:
        """Open the connection on the worker now; nobody waits for it."""
        if self._connect is not None:
            self.call(lambda conn: None, 0.0, queue_if_stuck=True)

    def stuck(self) -> bool:
        started = self._busy_since
        return started is not None and self._now() - started > STUCK_AFTER_S

    def _start(self) -> None:
        with self._starting:
            if self._worker is None:
                self._worker = threading.Thread(target=self._work, name=self.name, daemon=True)
                self._worker.start()

    def _work(self) -> None:
        while True:
            fn, box, done = self._jobs.get()
            self._busy_since = self._now()
            try:
                with self.session() as conn:
                    box["value"] = fn(conn)
            except BaseException as e:  # handed to the caller, if it still waits
                box["error"] = e
            finally:
                self._busy_since = None
                done.set()

    def _get(self) -> psycopg.Connection | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        if self._connect is None or self._now() < self._down_until:
            return None
        try:
            conn = self._connect()
            conn.autocommit = True
            conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS}")
            conn.execute(f"set idle_in_transaction_session_timeout = {IDLE_IN_TX_TIMEOUT_MS}")
        except Exception as e:  # unreachable, bad URL, schema lock timeout: live reads until it is back
            log.warning("holdings (%s): Postgres unavailable (%s); retrying later", self.name, type(e).__name__)
            self._down_until = self._now() + self.retry_after_s
            return None
        self._conn = conn
        if self.on_connect is not None:
            try:
                self.on_connect(conn)
            except Exception as e:  # e.g. a catch-up bump that failed: the next send retries it
                log.warning("holdings: on-connect step failed (%s)", type(e).__name__)
        return self._conn  # None when the on-connect step found the connection broken

    def failed(self, error: BaseException) -> None:
        log.warning("holdings: Postgres error (%s); reconnecting later", type(error).__name__)
        conn, self._conn = self._conn, None
        self._down_until = self._now() + self.retry_after_s
        if conn is not None:
            with contextlib.suppress(Exception):  # already gone
                conn.close()


class WriteTracker:
    """`on_write` for `sdk.TrackedBazaar`: bumps the epoch before a send goes and after it returns, on the
    writer connection's worker, waiting at most HOOK_TIMEOUT_S. A bump that did not land in time sets
    `missed`: this process's reader reads live until a later bump (or the connection opening, or the
    process exiting) catches up with one more bump. A late bump still lands when the worker gets to it."""

    def __init__(self, shared: SharedDb, writer: str, world: str = REAL.world) -> None:
        self.shared, self.writer, self.world = shared, writer, world
        self.bumps = 0
        self.failures = 0
        self.missed = False

    def __call__(self, method: str, path: str, phase: str) -> None:
        kind = write_kind(method, path)
        if kind is None:
            return
        what = f"{method.upper()} {path} ({phase})"
        if self.shared.stuck():  # known lost: no wait, no queue growth; the reconnect catches up
            self.failures, self.missed = self.failures + 1, True
            return
        ok, landed = self.shared.call(lambda conn: self._bump(conn, kind, what), HOOK_TIMEOUT_S, queue_if_stuck=True)
        if not (ok and landed):
            self.failures, self.missed = self.failures + 1, True

    def catch_up(self, timeout_s: float = HOOK_TIMEOUT_S) -> None:
        """One bump now if one was lost (the connection just opened, or the process exits)."""
        if self.missed:
            self.shared.call(lambda conn: self._bump(conn, None, "catch-up"), timeout_s, queue_if_stuck=True)

    def _bump(self, conn: psycopg.Connection | None, kind: WriteKind | None, what: str) -> bool:
        """On the worker. True when the bump (and a pending catch-up) landed."""
        if conn is None:
            return False
        try:
            if self.missed:
                bump(conn, self.world, "trade", "catch-up: a bump of this process was lost", self.writer)
                self.missed = False
            if kind is not None:
                bump(conn, self.world, kind, what, self.writer)
        except psycopg.Error as e:
            self.missed = True
            self.shared.failed(e)
            return False
        self.bumps += 1
        return True


# The per-process registry: one reader connection (schema applied once, `db.connect_ready`) and one writer
# connection (plain `db.connect`) for the write tracker, each with its own worker thread.
_PROCESS: dict[str, Any] = {"name": "bazaar"}


def name_process(name: str) -> None:
    """What `holdings_state.last_writer` and `me_snapshots.read_by` call this process (taker, maker, mcp)."""
    _PROCESS["name"] = name
    tracker = _PROCESS.get("tracker")
    if tracker is not None:
        tracker.writer = name


def process_db() -> SharedDb:
    shared = _PROCESS.get("db")
    if shared is None:
        from bazaar_agent import db

        connect = lambda: db.connect_ready(f"bazaar-holdings-{_PROCESS['name']}", CONNECT_TIMEOUT_S)  # noqa: E731
        shared = SharedDb(connect, name="holdings-reads")
        _PROCESS["db"] = shared
        shared.warm()
    return shared  # type: ignore[no-any-return]


def process_tracker(settings: Settings) -> WriteTracker:
    tracker = _PROCESS.get("tracker")
    if tracker is None:
        shared = _PROCESS.get("writer_db")
        if shared is None:
            from bazaar_agent import db

            app = f"bazaar-writes-{_PROCESS['name']}"
            connect = lambda: db.connect(app=app, connect_timeout_s=CONNECT_TIMEOUT_S)  # noqa: E731
            shared = SharedDb(connect, retry_after_s=WRITER_RETRY_AFTER_S, name="holdings-writes")
            _PROCESS["writer_db"] = shared
        tracker = WriteTracker(shared, str(_PROCESS["name"]), scope_of(settings).world)
        shared.on_connect = lambda conn: tracker._bump(conn, None, "catch-up") if tracker.missed else None
        _PROCESS["tracker"] = tracker
        shared.warm()  # open now, while the process reads: its first send finds the connection ready
        atexit.register(tracker.catch_up, EXIT_CATCH_UP_S)  # a one-shot command that lost a bump says so
    return tracker  # type: ignore[no-any-return]


def for_process(
    read_me: Callable[[], dict[str, Any]],
    rules: Guardrails,
    settings: Settings,
    *,
    team: str | None = None,
    on_team: Callable[[str], None] | None = None,
) -> Holdings:
    """The reader for this process: its own connection and worker, the write tracker's `missed` flag, the
    catalog writer when the scope may write `cards`.
    `team`: our id when known (BAZAAR_TEAM_ID or `.local/team_id`); `on_team` caches one a live read learns."""
    scope = scope_of(settings)
    shared = process_db()
    return Holdings(
        read_me,
        shared,
        reader=str(_PROCESS["name"]),
        rules=rules,
        team=team,
        catalog=catalog_sync(shared) if scope.shared_tables else None,
        on_team=on_team,
        scope=scope,
        tracker=process_tracker(settings),
    )


def catalog_sync(shared: SharedDb) -> CatalogSync:
    """`cards` written from a catalog this process already read; Postgres down or slow = retried later."""

    def save_now(conn: psycopg.Connection | None, catalog: dict[str, Any], tick: int) -> int | None:
        if conn is None:
            return None
        try:
            return save_catalog(conn, catalog, tick)
        except psycopg.Error as e:
            shared.failed(e)
            return None

    def write(catalog: dict[str, Any], tick: int) -> int:
        ok, written = shared.call(lambda conn: save_now(conn, catalog, tick), READ_DEADLINE_S)
        if not ok or written is None:
            raise ConnectionError("postgres busy, slow or not connected")
        return int(written)

    return CatalogSync(write)


# ---------------------------------------------------------------- the reader every process uses


def into_tick_s(clock: Clock, elapsed_s: float = 0.0) -> float:
    """Seconds since our clock's tick began (plus slack): a thread message newer than that is this tick's."""
    into = min(max(clock.tick_seconds - clock.next_tick_in, 0.0), clock.tick_seconds)
    return into + max(elapsed_s, 0.0) + TICK_SLACK_S


class Ticket:
    """One read's progress, shared by its caller and its job on the worker. Under `lock`: either the caller
    gives up first (the job then never asks the game) or the job asks first (the caller then waits for the
    game's answer, which the job hands over before it stores it)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.asked = False
        self.cancelled = False
        self.answered = threading.Event()
        self.ready = threading.Event()  # the game answered, or the job ended: the caller stops waiting
        self.read: MeRead | None = None
        self.error: BaseException | None = None
        self.note: str | None = None  # why the job gave no read (e.g. "postgres error")

    def ask(self) -> bool:
        """The job, before `/me`: False when the caller already gave up."""
        with self.lock:
            if self.cancelled:
                return False
            self.asked = True
            return True

    def give_up(self) -> bool:
        """The caller, past its deadline: True when the job has already asked the game (wait for it)."""
        with self.lock:
            self.cancelled = True
            return self.asked

    def answer(self, read: MeRead | None, error: BaseException | None) -> None:
        self.read, self.error = read, error
        self.answered.set()
        self.ready.set()


class Holdings:
    """`me(clock)`: the stored snapshot when it is provably current, else a live `/api/me` (stored)."""

    def __init__(
        self,
        read_me: Callable[[], dict[str, Any]],
        shared: SharedDb,
        *,
        reader: str,
        rules: Guardrails,
        team: str | None = None,
        catalog: CatalogSync | None = None,
        on_team: Callable[[str], None] | None = None,
        scope: Scope = REAL,
        tracker: WriteTracker | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read_me, self.shared, self.reader, self.rules, self._now = read_me, shared, reader, rules, now
        self.team = valid_team_id(team)
        self.catalog = catalog
        self.on_team = on_team  # called when a live read names our team (first time, or a corrected id)
        self.scope, self.tracker = scope, tracker
        self.counts: Counter[str] = Counter()  # "db" (calls saved), "live" (calls made), "live:<why>"

    def me(self, clock: Clock | None, *, clock_read_at: float | None = None, live_because: str | None = None) -> MeRead:
        """Album first. `live_because` forces a live read (after a deal); the answer is stored either way.
        `clock_read_at`: this object's `now()` when `clock` was read (default: just now)."""
        read_at = clock_read_at if clock_read_at is not None else self._now()
        reason = live_because or self._skip_reason(clock, self._now() - read_at)
        if reason is not None or clock is None:
            return self._live(clock, reason or "no clock")
        ok, got, note = self._on_worker(lambda c, t: self._from_db(c, clock, read_at, t))
        if got is not None:
            return got
        return self._plain(clock, note or ("postgres busy or not connected" if ok else "postgres too slow"))

    def _on_worker(
        self, work: Callable[[psycopg.Connection, Ticket], MeRead | None]
    ) -> tuple[bool, MeRead | None, str | None]:
        """Run a read on the worker; the caller wakes as soon as the game has answered (the store goes on
        without it) or the job ended, and gives up after READ_DEADLINE_S unless the job has already asked
        the game: then it waits for that answer, at most ME_BUDGET_S (the SDK's own budget for one /me). A
        job whose caller gave up never asks the game. (ok, read, note): ok False = the deadline passed."""
        ticket = Ticket()

        def job(conn: psycopg.Connection | None) -> None:
            try:
                if conn is not None and ticket.read is None:
                    found = work(conn, ticket)
                    ticket.read = ticket.read or found
            except BaseException as e:  # handed to the caller (a refused /me)
                ticket.error = ticket.error or e
            finally:
                ticket.ready.set()

        if self.shared.stuck():
            return False, None, None
        self.shared.call(job, 0.0, queue_if_stuck=True)  # enqueue (runs inline without a database)
        if ticket.ready.wait(READ_DEADLINE_S) or (ticket.give_up() and ticket.answered.wait(ME_BUDGET_S)):
            if ticket.error is not None:
                raise ticket.error
            return True, ticket.read, ticket.note
        return False, None, None

    def after_deal(self, clock: Clock | None, what: str) -> MeRead:
        """A deal of ours (sent, or seen settled): bump the epoch, so no process trusts an older snapshot,
        then re-read /me and store it (album first: re-read after every deal)."""

        def mark(conn: psycopg.Connection | None) -> None:
            if conn is None:
                return
            try:
                bump(conn, self.scope.world, "trade", f"deal: {what}", self.reader)
            except psycopg.Error as e:
                self.shared.failed(e)

        self.shared.call(mark, HOOK_TIMEOUT_S, queue_if_stuck=True)
        return self.me(clock, live_because=f"after {what}")

    def observe_catalog(self, tick: int, catalog: dict[str, Any]) -> None:
        """Store a catalog this process already read: first time, on a set release, every few ticks."""
        if self.catalog is None:
            return
        try:
            self.catalog.observe(tick, catalog)
        except Exception as e:  # reference data: a missed write is retried at the next read
            log.warning("holdings: catalog not stored (%s)", type(e).__name__)

    def _skip_reason(self, clock: Clock | None, elapsed: float) -> str | None:
        if not self.rules.holdings_from_db:
            return "holdings_from_db = false"
        if clock is None:
            return "no clock"
        if clock.next_tick_in - elapsed < TICK_END_MARGIN_S:
            return "the tick is about to end"
        if self.team is None:
            return "team id not known yet"
        if self.tracker is not None and self.tracker.missed:
            return "a send of ours was not recorded"
        return None

    def _from_db(self, conn: psycopg.Connection, clock: Clock, read_at: float, ticket: Ticket) -> MeRead | None:
        """On the worker. None (the caller reads `/me` itself) when Postgres fails before the game was asked."""
        assert self.team is not None
        if ticket.cancelled:  # the caller left: no database work, no game call
            return None
        try:
            found, why = self._fresh(conn, clock, read_at)
            if found is not None:
                return found
            with conn.transaction():  # one reader per team: the others wait, then find its row
                conn.execute(f"set local lock_timeout = {LOCK_TIMEOUT_MS}")
                conn.execute(  # per schema and world: a test schema never contends with the live tables
                    "select pg_advisory_xact_lock(hashtext(current_schema() || %s))",
                    (f":bazaar_agent.holdings:{self.scope.world}:{self.team}",),
                )
                found, why = self._fresh(conn, clock, read_at)  # after the wait: the tick may be ending
                if found is not None:
                    return found
                epoch, began = epoch_and_time(conn, self.scope.world)
                return self._read_and_store(conn, clock, epoch, began, why, ticket)
        except psycopg.errors.LockNotAvailable:
            return self._live_unlocked(conn, clock, "waited too long for another reader", ticket)
        except psycopg.Error as e:
            self.shared.failed(e)
            ticket.note = "postgres error"
            return ticket.read  # the game's answer when it came before the failure, else None

    def _fresh(self, conn: psycopg.Connection, clock: Clock, read_at: float) -> tuple[MeRead | None, str]:
        """The stored snapshot when every rule holds, else None and the first rule it breaks. Judged NOW:
        after a lock wait, the clock read at `read_at` may be about to leave its tick."""
        assert self.team is not None
        since = self._now() - read_at
        if clock.next_tick_in - since < TICK_END_MARGIN_S:
            return None, "the tick is about to end"
        row = stored(conn, self.scope.world, self.team, clock.tick, into_tick_s(clock, since))
        why = verdict(row, self.team, self.rules.holdings_max_age_s)
        if row is None or why != "fresh":
            return None, why
        self.counts["db"] += 1
        me = without_secrets(row.me)
        return MeRead(me, "db", row.tick, row.age_s, row.epoch, row.digest, row.read_by, why), why

    def _read_and_store(
        self, conn: psycopg.Connection, clock: Clock | None, epoch: int, began: Any, why: str, ticket: Ticket
    ) -> MeRead | None:
        """/me from the game (unless the caller gave up), handed to the caller at once, then the upsert in a
        savepoint: a failed or hung write never loses or delays the read."""
        if not ticket.ask():
            return None
        try:  # from ask() to answer(): whatever happens, the caller is told
            raw = without_secrets(self._read_me())
            self.counts["live"] += 1
            read = self._as_read(raw, clock, epoch, why)
        except BaseException as e:
            ticket.answer(None, e)
            raise
        ticket.answer(read, None)
        me = parse_me(raw)
        if me is not None and read.tick is not None:
            try:
                with conn.transaction():
                    save(conn, self.scope, me, raw, read.tick, epoch, self.reader, began)
            except psycopg.Error as e:
                log.warning("holdings: snapshot not stored (%s)", type(e).__name__)
        return read

    def _live(self, clock: Clock | None, why: str) -> MeRead:
        ok, got, note = self._on_worker(lambda c, t: self._live_unlocked(c, clock, why, t))
        if got is not None:
            return got
        return self._plain(clock, why if ok else f"{why}; postgres too slow")

    def _live_unlocked(self, conn: psycopg.Connection, clock: Clock | None, why: str, ticket: Ticket) -> MeRead | None:
        """A live read stored under the epoch read BEFORE it (a send in between makes it stale at once)."""
        if ticket.cancelled:
            return None
        try:
            epoch, began = epoch_and_time(conn, self.scope.world)
        except psycopg.Error as e:
            self.shared.failed(e)
            return None  # the caller reads /me itself
        return self._read_and_store(conn, clock, epoch, began, why, ticket)

    def _plain(self, clock: Clock | None, why: str) -> MeRead:
        raw = without_secrets(self._read_me())
        self.counts["live"] += 1
        return self._as_read(raw, clock, None, why)

    def _as_read(self, raw: dict[str, Any], clock: Clock | None, epoch: int | None, why: str) -> MeRead:
        me = parse_me(raw)
        if me is not None and me.id != self.team:  # /api/me is the authority on which team our key is
            self.team = me.id
            if self.on_team is not None:
                try:
                    self.on_team(me.id)
                except Exception as e:  # a read-only data dir: the id is still known in this process
                    log.warning("holdings: team id not cached (%s)", type(e).__name__)
        tick = me.tick if me is not None and me.tick is not None else (clock.tick if clock is not None else None)
        self.counts[f"live:{why}"] += 1
        return MeRead(raw, "live", tick, 0.0, epoch, digest(me) if me is not None else "?", self.reader, why)
