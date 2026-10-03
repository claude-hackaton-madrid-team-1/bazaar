"""Our holdings in Postgres: `/api/me` as our processes read it, shared so we never decide on a stale view.

Album first (AGENTS.md): every agent reads `/api/me` before it buys, sells, lists or negotiates, and again
after every deal. The taker, the maker, the MCP server and the CLI share one key's 5 req/s, so the first
process that needs `/me` in a tick reads it and upserts `me_snapshots`; the others answer from that row
while it is provably current. A stored snapshot is a decision input only when ALL of these hold:

1. tick  — it was read in the reader's current game tick or later (the server's `tick` in /me);
2. epoch — no state-changing send of ours, from any process, started or finished since it was read
           (`holdings_state.epoch`, bumped before AND after every such send by `sdk.TrackedBazaar`);
3. calm  — no thread message of ours went out this tick: a dealer may still answer and accept it,
           and that settles at once;
4. age   — it is younger than `holdings_max_age_s` (GUARDRAILS.md): the backstop for what we cannot
           see coming.

Anything unknown is a live read: no Postgres, no clock, no team id yet, an unreadable row, a lock wait
that timed out, or `holdings_from_db = false`. One process at a time reads `/me` for the team
(`pg_advisory_xact_lock`), so two agents that start a tick together make one call, not two; every live
read is upserted, so the table always holds the newest view any of our processes has seen.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bazaar_agent.catalog_db import CatalogSync, save_catalog
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.identity import valid_team_id
from bazaar_agent.ticks import Clock

LOCK_TIMEOUT_MS = 3000  # wait this long for another process's /me read, then read it ourselves
STATEMENT_TIMEOUT_MS = 5000
IDLE_IN_TX_TIMEOUT_MS = 20000  # a reader that hangs mid-read never holds the team's lock longer
TICK_SLACK_S = 2.0  # a thread message this close before our clock's tick start still counts as this tick
CONNECT_TIMEOUT_S = 3  # a laptop off the network answers live in 3 s, not the default 10
RETRY_AFTER_S = 30.0  # Postgres unreachable: live reads only, for this long, before trying it again
SCOPE = "us"  # one epoch for every team in the database: a write may only ever invalidate MORE
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


def without_secrets(value: Any) -> Any:
    """`/api/me` carries `starter_broker_key` once our free stall exists: no field named like a key, token,
    secret or password is ever stored or answered (read it from a live `team.me()` when you need it)."""
    if isinstance(value, dict):
        return {k: without_secrets(v) for k, v in value.items() if not SECRET_FIELD.search(str(k))}
    if isinstance(value, list):
        return [without_secrets(v) for v in value]
    return value


def parse_me(raw: Any) -> MePayload | None:
    try:
        return MePayload.model_validate(raw)
    except ValidationError:
        return None


def digest(me: MePayload) -> str:
    """The etag of what we hold: cash, level, every asset and the album counts. Same holdings, same digest."""
    pages = sorted((str(p.get("set")), p.get("have")) for p in me.album.get("pages") or [] if isinstance(p, dict))
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


def stored(conn: psycopg.Connection, team: str, tick: int, into_tick_s: float) -> Stored | None:
    """The newest snapshot of `team` from tick `tick` or later, with the state it is judged against."""
    row = conn.execute(
        "select s.tick, s.epoch, s.digest, s.read_by, s.me, "
        "extract(epoch from clock_timestamp() - s.read_at)::float8, coalesce(h.epoch, 0), "
        "coalesce(h.thread_message_at > clock_timestamp() - make_interval(secs => %s), false) "
        "from me_snapshots s left join holdings_state h on h.scope = %s "
        "where s.team = %s and s.tick >= %s order by s.tick desc, s.epoch desc, s.read_at desc limit 1",
        (into_tick_s, SCOPE, team, tick),
    ).fetchone()
    return Stored(*row) if row else None


def verdict(row: Stored | None, max_age_s: float) -> str:
    """'fresh', or why the stored snapshot is not a decision input (rules 2-4; rule 1 is the query's)."""
    if row is None:
        return "no snapshot this tick"
    if row.epoch != row.epoch_now:
        return "a write of ours since it was read"
    if row.messaged:
        return "a thread message of ours this tick"
    if row.age_s > max_age_s:
        return f"older than {max_age_s:g} s"
    if parse_me(row.me) is None:
        return "stored payload unreadable"
    return "fresh"


def current_epoch(conn: psycopg.Connection) -> int:
    row = conn.execute("select epoch from holdings_state where scope = %s", (SCOPE,)).fetchone()
    return int(row[0]) if row else 0


def bump(conn: psycopg.Connection, kind: WriteKind, what: str, writer: str) -> None:
    """A send of ours: every snapshot read before now is stale; a thread message also clouds this tick."""
    conn.execute(
        "insert into holdings_state (scope, epoch, written_at, thread_message_at, last_write, last_writer) "
        "values (%(scope)s, 1, clock_timestamp(), case when %(thread)s then clock_timestamp() end, %(what)s, "
        "%(writer)s) on conflict (scope) do update set epoch = holdings_state.epoch + 1, "
        "written_at = excluded.written_at, "
        "thread_message_at = coalesce(excluded.thread_message_at, holdings_state.thread_message_at), "
        "last_write = excluded.last_write, last_writer = excluded.last_writer",
        {"scope": SCOPE, "thread": kind == "thread", "what": what[:120], "writer": writer[:40]},
    )


def save(conn: psycopg.Connection, me: MePayload, raw: dict[str, Any], tick: int, epoch: int, read_by: str,
         latency_s: float) -> None:  # fmt: skip
    """Upsert the snapshot (a newer epoch, or the same epoch read later, wins) and the evals' `snapshots` row."""
    s = summary(me)
    conn.execute(
        "insert into me_snapshots (team, tick, epoch, digest, read_at, read_by, cash, level, cards, duplicates, "
        "packs, pages, affinity, score, me) values (%s, %s, %s, %s, clock_timestamp() - make_interval(secs => %s), "
        "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (team, tick) do update set epoch = excluded.epoch, "
        "digest = excluded.digest, read_at = excluded.read_at, read_by = excluded.read_by, cash = excluded.cash, "
        "level = excluded.level, cards = excluded.cards, duplicates = excluded.duplicates, packs = excluded.packs, "
        "pages = excluded.pages, affinity = excluded.affinity, score = excluded.score, me = excluded.me "
        "where excluded.epoch > me_snapshots.epoch "
        "or (excluded.epoch = me_snapshots.epoch and excluded.read_at >= me_snapshots.read_at)",
        (
            me.id, tick, epoch, digest(me), latency_s, read_by, round(me.cash), me.level, json.dumps(s["cards"]),
            json.dumps(s["duplicates"]), json.dumps(s["packs"]), json.dumps(s["pages"]), json.dumps(me.affinity),
            json.dumps(me.score), json.dumps(raw),
        ),
    )  # fmt: skip
    conn.execute(
        "insert into snapshots (tick, cash, level, assets, album, score) values (%s, %s, %s, %s, %s, %s) "
        "on conflict (tick) do update set cash = excluded.cash, level = excluded.level, "
        "assets = excluded.assets, album = excluded.album, score = excluded.score",
        (
            tick,
            round(me.cash),
            me.level,
            json.dumps(raw.get("assets")),
            json.dumps(raw.get("album")),
            json.dumps(me.score),
        ),
    )


# ---------------------------------------------------------------- one connection per process


class SharedDb:
    """A lazy autocommit connection for the holdings, reopened after a failure; None while Postgres is down.
    One lock serialises every use: the MCP server answers tools from several threads."""

    def __init__(self, connect: Callable[[], psycopg.Connection] | None, now: Callable[[], float] = time.monotonic):
        self._connect, self._now = connect, now
        self._conn: psycopg.Connection | None = None
        self._down_until = 0.0
        self.lock = threading.RLock()

    @contextmanager
    def session(self) -> Iterator[psycopg.Connection | None]:
        with self.lock:
            yield self._get()

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
            log.warning("holdings: Postgres unavailable (%s); /api/me is read live", type(e).__name__)
            self._down_until = self._now() + RETRY_AFTER_S
            return None
        self._conn = conn
        return conn

    def failed(self, error: BaseException) -> None:
        log.warning("holdings: Postgres error (%s); reconnecting later", type(error).__name__)
        conn, self._conn = self._conn, None
        self._down_until = self._now() + RETRY_AFTER_S
        if conn is not None:
            with contextlib.suppress(Exception):  # already gone
                conn.close()


class WriteTracker:
    """`on_write` for `sdk.TrackedBazaar`: bumps the epoch before a send goes and after it returns."""

    def __init__(self, shared: SharedDb, writer: str) -> None:
        self.shared, self.writer = shared, writer
        self.bumps = 0
        self.failures = 0

    def __call__(self, method: str, path: str, phase: str) -> None:
        kind = write_kind(method, path)
        if kind is None:
            return
        with self.shared.session() as conn:
            if conn is None:
                self.failures += 1
                return
            try:
                bump(conn, kind, f"{method.upper()} {path} ({phase})", self.writer)
            except psycopg.Error as e:
                self.failures += 1
                self.shared.failed(e)
                return
        self.bumps += 1


# One holdings connection per process, shared by the write tracker and the reader. Opened lazily, with
# the schema applied once (`db.connect_ready`), so a CLI command that never writes never connects.
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

        shared = SharedDb(lambda: db.connect_ready(f"bazaar-holdings-{_PROCESS['name']}", CONNECT_TIMEOUT_S))
        _PROCESS["db"] = shared
    return shared  # type: ignore[no-any-return]


def process_tracker() -> WriteTracker:
    tracker = _PROCESS.get("tracker")
    if tracker is None:
        tracker = WriteTracker(process_db(), str(_PROCESS["name"]))
        _PROCESS["tracker"] = tracker
    return tracker  # type: ignore[no-any-return]


def for_process(read_me: Callable[[], dict[str, Any]], rules: Guardrails, team: str | None = None) -> Holdings:
    """The reader for this process, on the same connection as its write tracker, writing the catalog too."""
    shared = process_db()
    return Holdings(read_me, shared, reader=str(_PROCESS["name"]), rules=rules, team=team, catalog=catalog_sync(shared))


def catalog_sync(shared: SharedDb) -> CatalogSync:
    """`cards` written from a catalog this process already read; Postgres down = retried at the next read."""

    def write(catalog: dict[str, Any], tick: int) -> int:
        with shared.session() as conn:
            if conn is None:
                raise ConnectionError("postgres unavailable")
            try:
                return save_catalog(conn, catalog, tick)
            except psycopg.Error as e:
                shared.failed(e)
                raise

    return CatalogSync(write)


# ---------------------------------------------------------------- the reader every process uses


def into_tick_s(clock: Clock, elapsed_s: float = 0.0) -> float:
    """Seconds since our clock's tick began (plus slack): a thread message newer than that is this tick's."""
    into = min(max(clock.tick_seconds - clock.next_tick_in, 0.0), clock.tick_seconds)
    return into + max(elapsed_s, 0.0) + TICK_SLACK_S


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
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read_me, self.shared, self.reader, self.rules, self._now = read_me, shared, reader, rules, now
        self.team = valid_team_id(team)
        self.catalog = catalog
        self.counts: Counter[str] = Counter()  # "db" (calls saved), "live" (calls made), "live:<why>"

    def me(self, clock: Clock | None, *, clock_read_at: float | None = None, live_because: str | None = None) -> MeRead:
        """Album first. `live_because` forces a live read (after a deal); the answer is stored either way."""
        reason = live_because or self._skip_reason(clock)
        if reason is not None or clock is None:
            return self._live(clock, reason or "no clock")
        elapsed = self._now() - clock_read_at if clock_read_at is not None else 0.0
        with self.shared.session() as conn:
            if conn is None:
                return self._live(clock, "postgres unavailable")
            return self._from_db(conn, clock, into_tick_s(clock, elapsed))

    def after_deal(self, clock: Clock | None, what: str) -> MeRead:
        """A deal of ours (sent, or seen settled): bump the epoch, so no process trusts an older snapshot,
        then re-read /me and store it (album first: re-read after every deal)."""
        with self.shared.session() as conn:
            if conn is not None:
                try:
                    bump(conn, "trade", f"deal: {what}", self.reader)
                except psycopg.Error as e:
                    self.shared.failed(e)
        return self.me(clock, live_because=f"after {what}")

    def observe_catalog(self, tick: int, catalog: dict[str, Any]) -> None:
        """Store a catalog this process already read: first time, on a set release, every few ticks."""
        if self.catalog is None:
            return
        try:
            self.catalog.observe(tick, catalog)
        except Exception as e:  # reference data: a missed write is retried at the next read
            log.warning("holdings: catalog not stored (%s)", type(e).__name__)

    def _skip_reason(self, clock: Clock | None) -> str | None:
        if not self.rules.holdings_from_db:
            return "holdings_from_db = false"
        if clock is None:
            return "no clock"
        if self.team is None:
            return "team id not known yet"
        return None

    def _from_db(self, conn: psycopg.Connection, clock: Clock, into: float) -> MeRead:
        assert self.team is not None
        got: MeRead | None = None
        try:
            found, why = self._fresh(conn, clock, into)
            if found is not None:
                return found
            with conn.transaction():  # one reader per team: the others wait, then find its row
                conn.execute(f"set local lock_timeout = {LOCK_TIMEOUT_MS}")
                conn.execute(  # per schema: a test schema never contends with the live tables
                    "select pg_advisory_xact_lock(hashtext(current_schema() || %s))",
                    (f":bazaar_agent.holdings:{self.team}",),
                )
                found, why = self._fresh(conn, clock, into)
                if found is not None:
                    return found
                got = self._read_and_store(conn, clock, current_epoch(conn), why)
                return got
        except psycopg.errors.LockNotAvailable:
            return self._live_unlocked(conn, clock, "waited too long for another reader")
        except psycopg.Error as e:
            self.shared.failed(e)
            return got if got is not None else self._live(clock, "postgres error", store=False)

    def _fresh(self, conn: psycopg.Connection, clock: Clock, into: float) -> tuple[MeRead | None, str]:
        """The stored snapshot when every rule holds, else None and the first rule it breaks."""
        assert self.team is not None
        row = stored(conn, self.team, clock.tick, into)
        why = verdict(row, self.rules.holdings_max_age_s)
        if row is None or why != "fresh":
            return None, why
        self.counts["db"] += 1
        return MeRead(row.me, "db", row.tick, row.age_s, row.epoch, row.digest, row.read_by, why), why

    def _read_and_store(self, conn: psycopg.Connection, clock: Clock | None, epoch: int, why: str) -> MeRead:
        """/me from the game, then the upsert in a savepoint: a failed write never loses the read."""
        started = self._now()
        raw = without_secrets(self._read_me())
        latency = self._now() - started
        self.counts["live"] += 1
        read = self._as_read(raw, clock, epoch, why)
        me = parse_me(raw)
        if me is not None and read.tick is not None:
            try:
                with conn.transaction():
                    save(conn, me, raw, read.tick, epoch, self.reader, latency)
            except psycopg.Error as e:
                log.warning("holdings: snapshot not stored (%s)", type(e).__name__)
        return read

    def _live(self, clock: Clock | None, why: str, *, store: bool = True) -> MeRead:
        if not store:
            return self._plain(clock, why)
        with self.shared.session() as conn:
            if conn is None:
                return self._plain(clock, why)
            return self._live_unlocked(conn, clock, why)

    def _live_unlocked(self, conn: psycopg.Connection, clock: Clock | None, why: str) -> MeRead:
        """A live read stored under the epoch read BEFORE it (a send in between makes it stale at once)."""
        try:
            epoch = current_epoch(conn)
        except psycopg.Error as e:
            self.shared.failed(e)
            return self._plain(clock, why)
        return self._read_and_store(conn, clock, epoch, why)

    def _plain(self, clock: Clock | None, why: str) -> MeRead:
        raw = without_secrets(self._read_me())
        self.counts["live"] += 1
        return self._as_read(raw, clock, None, why)

    def _as_read(self, raw: dict[str, Any], clock: Clock | None, epoch: int | None, why: str) -> MeRead:
        me = parse_me(raw)
        if me is not None and self.team is None:
            self.team = me.id  # /api/me is the authority on which team our key is
        tick = me.tick if me is not None and me.tick is not None else (clock.tick if clock is not None else None)
        self.counts[f"live:{why}"] += 1
        return MeRead(raw, "live", tick, 0.0, epoch, digest(me) if me is not None else "?", self.reader, why)
