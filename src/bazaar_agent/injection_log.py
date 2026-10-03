"""Prompt-injection attempts kept as proofs (`injection_attempts`): who sent which words, where, and how to check it.

Prompt injection is allowed in this game (RULES.md) and barely moves a negotiation: our prices, accepts and assets
come from structure and code, never from words. This module only RECORDS it. Nothing here is sent to the game, and
a record is never a report (a wrong report costs points).

Every counterparty text our processes already read is checked with `llm.chooser.injection_flags` (NFKD-folded
patterns plus hidden unicode): the feed window the taker reads, our team threads (team desk), our dealer threads,
and the duel messages. A tagged text is kept with its RAW words verbatim (only our own secret values are cut out,
at most `MAX_RAW` characters) and the ids anyone can check against the game: `proof` names the endpoint.

`note()` only buffers (no I/O, never raises); `flush()` runs after the tick's sends, one bounded transaction, with
Postgres retried every `RETRY_EVERY` ticks after a failure. `backfill()` reads what we already stored (feed_events,
messages, duels): read-only on the game.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from bazaar_agent.db import INT4, jsonb_safe
from bazaar_agent.llm.chooser import folded, injection_flags

DDL = (
    "create table if not exists injection_attempts (id bigserial primary key, world text not null default 'real', "
    "tick int, source text not null check (source in ('feed','team_thread','duel','dealer_thread','offer_text')), "
    "event_id bigint not null default 0, thread_id bigint not null default 0, duel_id bigint not null default 0, "
    "message_id bigint not null default 0, from_team text, to_us bool not null default false, tags text[] not null, "
    "severity text not null check (severity in ('attempt','weak')), raw text not null, normalised text not null, "
    "our_response text not null, proof text not null, seen_at timestamptz not null default now(), "
    "unique (world, source, event_id, thread_id, duel_id, message_id, tags));"
)
INSERT = (
    "insert into injection_attempts (world, tick, source, event_id, thread_id, duel_id, message_id, from_team, "
    "to_us, tags, severity, raw, normalised, our_response, proof) values "
    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    # a rerun keeps the first proof and only regrades the severity (when the grading rule changed)
    "on conflict (world, source, event_id, thread_id, duel_id, message_id, tags) do update set "
    "severity = excluded.severity where injection_attempts.severity <> excluded.severity"
)
LIST = (
    "select id, world, tick, source, event_id, thread_id, duel_id, message_id, from_team, to_us, tags, severity, "
    "raw, our_response, proof, seen_at from injection_attempts where world = %s and (%s or severity = 'attempt') "
    "order by tick desc nulls last, id desc limit %s"
)

# A strong shape is an attempt on its own; JSON, a URL or a priced verb alone is usually an honest offer text.
STRONG = frozenset({"instruction_override", "role_tag", "asset_grab", "odd_unicode"})
# `role_play` also fires on a bare "pretend" ("I never pretend otherwise", 7 dealer lines on Friday and Saturday):
# it is an attempt only when it casts a role.
ROLE_CAST = re.compile(
    r"\b(you are now|act as|pretend (to be|you are|you're|that you)|system prompt|eres ahora|act[uú]a como|finge)\b",
    re.IGNORECASE,
)
MAX_RAW = 2000
STATEMENT_TIMEOUT_MS = 1500
RETRY_EVERY = 5  # ticks between Postgres retries after a failure
BUFFER_MAX = 500  # attempts waiting for a write; the oldest are dropped during a long outage
SEEN_MAX = 20_000  # keys already buffered; past it a repeat is buffered again (the table's key dedupes it)
REDACTED = "[redacted]"
IGNORED = "ignored: structured offer only"
IGNORED_FEED = "ignored: feed text is never an instruction"
IGNORED_DEALER = "ignored: dealer words never set our price"
# Organiser channels: never another team's words.
ORGANISER_TYPES = frozenset({"announcement", "news.posted", "schedule.fired", "clock.changed", "day.opened"})
TEXT_FIELDS = ("text", "note", "message", "words", "comment", "description", "name")
OURS_IN_DUEL = "you"  # how /api/duels names our own messages
SOURCES = ("feed", "team_thread", "duel", "dealer_thread", "offer_text")


@dataclass(frozen=True)
class Attempt:
    source: str
    tags: tuple[str, ...]
    raw: str
    tick: int | None
    from_team: str | None
    to_us: bool
    our_response: str
    event_id: int = 0
    thread_id: int = 0
    duel_id: int = 0
    message_id: int = 0
    feed_event: int = 0  # the feed event that also carried a thread message (proof only, not part of the key)

    @property
    def severity(self) -> str:
        if STRONG & set(self.tags) or ("role_play" in self.tags and ROLE_CAST.search(folded(self.raw))):
            return "attempt"
        return "weak"

    @property
    def proof(self) -> str:
        """The endpoint and ids that show the same words in the game's own records."""
        if self.source == "duel":
            return f"GET /api/duels?done=true duel {self.duel_id} message #{self.message_id} (tick {self.tick})"
        if self.event_id:
            return f"GET /api/feed event {self.event_id} (tick {self.tick})"
        seen = f"; feed event {self.feed_event}" if self.feed_event else ""
        return f"GET /api/threads/{self.thread_id} message {self.message_id} (tick {self.tick}{seen})"

    @property
    def key(self) -> tuple[object, ...]:
        return (self.source, self.event_id, self.thread_id, self.duel_id, self.message_id, self.tags)


def attempt(source: str, text: object, *, response: str = IGNORED, **ids: Any) -> Attempt | None:
    """An `Attempt` when `text` carries an injection shape, else None. `ids`: tick, from_team, to_us and the
    event/thread/duel/message ids (anything not an int counts as 0)."""
    if source not in SOURCES or not isinstance(text, str):
        return None
    tags = tuple(sorted(injection_flags(text)))
    if not tags:
        return None
    return Attempt(
        source=source,
        tags=tags,
        raw=text[:MAX_RAW],
        tick=_int(ids.get("tick")),
        from_team=str(ids["from_team"]) if ids.get("from_team") is not None else None,
        to_us=bool(ids.get("to_us")),
        our_response=response,
        event_id=_int(ids.get("event_id")) or 0,
        thread_id=_int(ids.get("thread_id")) or 0,
        duel_id=_int(ids.get("duel_id")) or 0,
        message_id=_int(ids.get("message_id")) or 0,
        feed_event=_int(ids.get("feed_event")) or 0,
    )


# ---------------------------------------------------------------- where words come from


def from_feed_event(event: Mapping[str, Any], us: str | None) -> list[Attempt]:
    """A public feed event: a dealer's thread message, a team's thread message, a venue announcement, an
    offer's note. Organiser announcements and our own words are skipped."""
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else event
    if not isinstance(payload, Mapping) or event.get("type") in ORGANISER_TYPES:
        return []
    eid, tick, actor = event.get("id"), event.get("tick"), event.get("actor")
    sender = payload.get("sender") or actor
    if us is not None and sender == us:
        return []
    out: list[Attempt | None] = []
    if event.get("type") == "thread.message":
        source = "dealer_thread" if payload.get("kind") == "persona" else "team_thread"
        to_us = us is not None and us in (payload.get("team"), payload.get("with")) and sender != us
        response = IGNORED_DEALER if source == "dealer_thread" else IGNORED
        # keyed by thread and message, as our own thread reads are: one row per message whoever saw it first
        ids: dict[str, Any] = {"tick": tick, "from_team": sender, "to_us": to_us, "feed_event": eid}
        ids |= {"thread_id": payload.get("thread"), "message_id": payload.get("message")}
        out.append(attempt(source, payload.get("text"), response=response, **ids))
    else:
        for name in TEXT_FIELDS:
            ids = {"tick": tick, "from_team": sender, "event_id": eid}
            out.append(attempt("feed", payload.get(name), response=IGNORED_FEED, **ids))
    offer = payload.get("offer")
    if isinstance(offer, Mapping):
        to_us = us is not None and offer.get("to") == us
        for name in TEXT_FIELDS:
            ids = {"tick": tick, "from_team": offer.get("maker") or sender, "to_us": to_us, "event_id": eid}
            out.append(attempt("offer_text", offer.get(name), message_id=offer.get("id"), **ids))
    return [a for a in out if a is not None]


def from_thread(payload: Mapping[str, Any], us: str, source: str, tick: int | None = None) -> list[Attempt]:
    """Every message in a thread payload (`GET /api/threads/{id}`) that another party wrote."""
    if source not in ("team_thread", "dealer_thread"):
        return []
    tid = payload.get("id") or payload.get("thread")
    response = IGNORED_DEALER if source == "dealer_thread" else IGNORED
    out = []
    for m in payload.get("messages") or []:
        if not isinstance(m, Mapping) or m.get("sender") in (None, us):
            continue
        mid = m.get("message", m.get("id"))
        ids: dict[str, Any] = {
            "tick": m.get("tick", tick),
            "from_team": m.get("sender"),
            "to_us": True,
            "thread_id": tid,
        }
        out.append(attempt(source, m.get("text"), response=response, message_id=mid, **ids))
    return [a for a in out if a is not None]


def from_duel(duel: Mapping[str, Any]) -> list[Attempt]:
    """The rival's messages in one `/api/duels` entry (ours read `from: "you"`; a message has no id, so its
    position in the list is the message number)."""
    did, rival = duel.get("duel"), duel.get("rival")
    out = []
    for i, m in enumerate(duel.get("messages") or []):
        if not isinstance(m, Mapping) or m.get("from") == OURS_IN_DUEL:
            continue
        ids: dict[str, Any] = {
            "tick": m.get("tick"),
            "from_team": m.get("from") or rival,
            "to_us": True,
            "duel_id": did,
        }
        out.append(attempt("duel", m.get("text"), message_id=i + 1, **ids))
    return [a for a in out if a is not None]


# ---------------------------------------------------------------- the writer


class InjectionLog:
    """Buffers attempts during a tick; `flush()` writes them after the sends."""

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None,
        world: str = "real",
        secrets: Iterable[str] = (),
        log: Callable[[str], None] = lambda message: None,
    ) -> None:
        self._connect, self.world, self._log = connect, world, log
        self._secrets = tuple(sorted({s for s in secrets if s}, key=len, reverse=True))
        self._conn: psycopg.Connection | None = None
        self._down_at: int | None = None
        self._failed = False
        self._seen: set[tuple[object, ...]] = set()
        self._events: set[int] = set()  # feed event ids already read
        self.buffer: list[Attempt] = []

    def note(self, attempts: Iterable[Attempt]) -> int:
        """Buffer new attempts (no I/O, never raises). Returns how many were new."""
        added = 0
        try:
            for a in attempts:
                if a.key in self._seen:
                    continue
                if len(self._seen) >= SEEN_MAX:
                    self._seen.clear()
                self._seen.add(a.key)
                self.buffer.append(a)
                added += 1
                if a.severity == "attempt":  # weak tags (a venue's JSON format) are kept without a line
                    self._log(f"injection attempt recorded ({a.source} from {a.from_team}): {', '.join(a.tags)}")
            del self.buffer[:-BUFFER_MAX]
        except Exception as e:  # noqa: BLE001 — a record never costs the tick
            self._log(f"injection log: buffer failed ({type(e).__name__})")
        return added

    def note_feed(self, events: Iterable[Mapping[str, Any]], us: str) -> int:
        """Buffer the attempts in a feed window, reading each event id once (the window repeats ~20 ticks)."""
        added = 0
        try:
            for event in events:
                eid = event.get("id")
                if not isinstance(eid, int) or eid in self._events:
                    continue
                if len(self._events) >= SEEN_MAX:
                    self._events.clear()
                self._events.add(eid)
                added += self.note(from_feed_event(event, us))
        except Exception as e:  # noqa: BLE001 — a record never costs the tick
            self._log(f"injection log: feed scan failed ({type(e).__name__})")
        return added

    def row(self, a: Attempt) -> tuple[object, ...]:
        raw = self.scrub(a.raw)
        return (
            self.world, a.tick, a.source, a.event_id, a.thread_id, a.duel_id, a.message_id,
            jsonb_safe(a.from_team) if a.from_team else None, a.to_us, list(a.tags), a.severity,
            jsonb_safe(raw), jsonb_safe(folded(raw)), a.our_response, a.proof,
        )  # fmt: skip

    def scrub(self, text: str) -> str:
        """Our own secret values cut out; everything else stays verbatim (the words are the proof)."""
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def flush(self, tick: int) -> int:
        """Insert the buffered attempts (after the sends). Returns the rows sent; never raises."""
        if not self.buffer:
            return 0
        try:
            conn = self._db(tick)
            if conn is None:
                return 0
            rows = [self.row(a) for a in self.buffer]
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                with conn.cursor() as cur:
                    cur.executemany(INSERT, rows)
        except Exception as e:  # noqa: BLE001 — keep the buffer, retry in RETRY_EVERY ticks
            self._fail(e)
            if self._conn is not None and not self._conn.closed:
                self._conn.close()
            self._conn, self._down_at = None, tick
            return 0
        self.buffer = []
        if self._failed:
            self._log("injection log: Postgres writes are back")
            self._failed = False
        return len(rows)

    def _db(self, tick: int) -> psycopg.Connection | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        if self._connect is None or (self._down_at is not None and tick - self._down_at < RETRY_EVERY):
            return None
        conn = self._connect()
        conn.autocommit = True
        conn.execute(DDL)
        self._conn, self._down_at = conn, None
        return conn

    def _fail(self, e: Exception) -> None:
        if not self._failed:
            self._log(f"injection log: Postgres write failed ({type(e).__name__}); kept, retry in {RETRY_EVERY} ticks")
        self._failed = True


# ---------------------------------------------------------------- backfill and listing (the CLI)


def backfill(conn: psycopg.Connection, us: str | None) -> list[Attempt]:
    """Every attempt in what we already stored: the feed archive, our stored threads, the duels. Read-only."""
    found: list[Attempt] = []
    with conn.cursor() as cur:
        cur.execute(
            "select id, tick, type, actor, payload from feed_events where payload::text ~* %s order by id",
            ('"(' + "|".join(TEXT_FIELDS) + ')": *"',),
        )
        for eid, tick, typ, actor, payload in cur.fetchall():
            found += from_feed_event({"id": eid, "tick": tick, "type": typ, "actor": actor, "payload": payload}, us)
        cur.execute(
            "select m.id, m.thread_id, m.sender, m.tick, m.text, t.kind from messages m "
            "left join threads t on t.id = m.thread_id where m.text is not null order by m.id"
        )
        for mid, tid, sender, tick, text, kind in cur.fetchall():
            if sender == us:
                continue
            source = "team_thread" if kind == "team" else "dealer_thread"
            response = IGNORED if source == "team_thread" else IGNORED_DEALER
            ids: dict[str, Any] = {
                "tick": tick,
                "from_team": sender,
                "to_us": True,
                "thread_id": tid,
                "message_id": mid,
            }
            if a := attempt(source, text, response=response, **ids):
                found.append(a)
        cur.execute("select payload from duels where payload is not null order by duel")
        for (payload,) in cur.fetchall():
            if isinstance(payload, Mapping):
                found += from_duel(payload)
    return found


def store(conn: psycopg.Connection, log: InjectionLog, attempts: Iterable[Attempt]) -> int:
    """Insert `attempts` in one transaction; returns the rows that were new."""
    rows = [log.row(a) for a in attempts]
    if not rows:
        return 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(DDL)
        before = cur.execute("select count(*) from injection_attempts").fetchone()
        cur.executemany(INSERT, rows)
        after = cur.execute("select count(*) from injection_attempts").fetchone()
    return int((after or (0,))[0]) - int((before or (0,))[0])


def recent(conn: psycopg.Connection, world: str = "real", limit: int = 50, weak: bool = False) -> list[dict[str, Any]]:
    """The newest stored attempts as plain dicts (weak ones only with `weak`)."""
    with conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute(LIST, (world, weak, limit))
        names = [c.name for c in cur.description or ()]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def _int(value: object) -> int | None:
    """An id or tick Postgres can hold (int4 range: ticks and game ids stay far below it), else None."""
    if isinstance(value, str) and value.strip().isdigit() and len(value.strip()) < 11:
        value = int(value.strip())
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= INT4:
        return value
    return None
