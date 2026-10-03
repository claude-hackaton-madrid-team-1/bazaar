"""Postgres access: plain SQL through psycopg 3, no ORM.

Several writers may share one database (a monitor on each laptop, all on the team's Railway
Postgres), so every write is idempotent and never moves a row backwards: rows are written in key
order (no deadlock between writers), stale ticks do not overwrite newer ones, alerts have a natural
key, and only the writer holding the oldest history rebuilds the history-derived tables.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import psycopg
from psycopg import sql as pgsql

from bazaar_agent.config import ConfigError, load_settings
from bazaar_agent.intel import Print, dealer_threads, set_of, tape, team_flows
from bazaar_agent.pgconn import DatabaseUrlError, Target, describe, redact
from bazaar_agent.pgconn import connect as connect

Event = dict[str, Any]
log = logging.getLogger(__name__)


def init_schema(conn: psycopg.Connection) -> bool:
    """Apply the schema (idempotent, safe beside other sessions). True when pgvector is on."""
    sql = files("bazaar_agent").joinpath("sql/schema.sql").read_text(encoding="utf-8")
    conn.add_notice_handler(_log_schema_warning)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)  # type: ignore[arg-type]  # trusted file shipped in the package
        conn.commit()
    finally:
        conn.remove_notice_handler(_log_schema_warning)
    return pgvector_version(conn) is not None


def _log_schema_warning(diag: psycopg.errors.Diagnostic) -> None:
    """A schema step that chose to go on (a view left as it was) says so in a WARNING; psycopg drops notices."""
    if diag.severity_nonlocalized == "WARNING":
        log.warning("schema: %s", diag.message_primary)


def connect_ready(app: str, connect_timeout_s: int | None = None) -> psycopg.Connection:
    """A connection to DATABASE_URL with the schema applied: what a long-running writer opens."""
    conn = connect(app=app, connect_timeout_s=connect_timeout_s)
    try:
        init_schema(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def pgvector_version(conn: psycopg.Connection) -> str | None:
    row = conn.execute("select extversion from pg_extension where extname = 'vector'").fetchone()
    conn.commit()
    return str(row[0]) if row else None


def table_counts(conn: psycopg.Connection) -> list[tuple[str, int]]:
    with conn.cursor() as cur:
        cur.execute("select tablename from pg_tables where schemaname = current_schema() order by tablename")
        names = [r[0] for r in cur.fetchall()]
        out = []
        for name in names:
            cur.execute(pgsql.SQL("select count(*) from {}").format(pgsql.Identifier(name)))
            row = cur.fetchone()
            out.append((name, int(row[0]) if row else 0))
    return out


def load_events(conn: psycopg.Connection, events: Iterable[Event]) -> dict[str, int]:
    """Insert raw events and their settlements (tape). Safe on any subset: conflicts are ignored."""
    with conn.cursor() as cur:
        counts = insert_events(cur, events)
    conn.commit()
    return counts


def jsonb_safe(value: Any) -> Any:
    """Strings Postgres `jsonb` accepts: no NUL, no lone surrogate. One such string in a feed payload would
    otherwise fail the whole batch, tick after tick, until the event left the window."""
    if isinstance(value, str):
        return value.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")
    if isinstance(value, dict):
        return {jsonb_safe(str(k)): jsonb_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonb_safe(v) for v in value]
    return value


INT4 = 2**31 - 1
INT8 = 2**63 - 1


def _event_row(e: Event) -> tuple[Any, ...]:
    """One feed_events row; raises (ValueError, TypeError, ...) for an event Postgres would refuse."""
    tick = e.get("tick")
    if not isinstance(e["id"], int) or not 0 <= e["id"] <= INT8:
        raise ValueError(f"event {e.get('id')!r}: the id must be a bigint")
    if tick is not None and (not isinstance(tick, int) or not -INT4 <= tick <= INT4):
        raise ValueError(f"event {e['id']}: the tick must be an int")
    if not all(v is None or isinstance(v, str) for v in (e.get("type"), e.get("actor"))):
        raise ValueError(f"event {e['id']}: type and actor must be text")
    payload = json.dumps(jsonb_safe(e.get("payload")), allow_nan=False)  # NaN / Infinity: jsonb refuses them
    return (e["id"], tick, jsonb_safe(e.get("type")), jsonb_safe(e.get("actor")), payload)


def _storable(p: Print) -> Print:
    """A tape print Postgres accepts: ints in range, no NUL in its text (raises ValueError otherwise)."""
    if any(not -INT4 <= value <= INT4 for value in (p.tick, p.price, p.fee)):
        raise ValueError("a tape number out of range")
    if not 0 <= p.settlement <= INT8:
        raise ValueError("a settlement id out of range")
    for text in (p.venue, p.persona, p.buyer, p.seller, p.ref):
        if text is not None and not isinstance(text, str):
            raise ValueError("a tape text field that is not text")
        if text is not None and "\x00" in text:
            raise ValueError("NUL in a tape field")
        if text is not None:
            text.encode("utf-8")  # a lone surrogate: UnicodeEncodeError, a ValueError
    return p


def _usable(events: Iterable[Event]) -> tuple[list[tuple[Any, ...]], list[Print]]:
    """The rows and tape prints of every event that can be stored; a bad event is skipped, never the batch."""
    rows: list[tuple[Any, ...]] = []
    prints: list[Print] = []
    for e in events:
        try:
            row = _event_row(e)
            printed = [_storable(p) for p in tape([e])]
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        rows.append(row)
        prints += printed
    return sorted(rows, key=lambda r: r[0]), sorted(prints, key=lambda p: p.settlement)


def insert_events(cur: psycopg.Cursor[Any], events: Iterable[Event]) -> dict[str, int]:
    """`load_events` without the commit: the caller owns the transaction (the taker's feed archive)."""
    rows, prints = _usable(events)
    if rows:
        cur.executemany(
            "insert into feed_events (id, tick, type, actor, payload) values (%s, %s, %s, %s, %s) "
            "on conflict (id) do nothing",
            rows,
        )
    if prints:
        cur.executemany(
            "insert into tape (settlement_id, tick, venue, persona, buyer, seller, items, card_id, price, "
            "fee) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (settlement_id) do nothing",
            [
                (
                    p.settlement,
                    p.tick,
                    p.venue,
                    p.persona,
                    p.buyer,
                    p.seller,
                    json.dumps({"ref": p.ref, "n": p.items}),
                    p.ref if set_of(p.ref) else None,
                    p.price,
                    p.fee,
                )
                for p in prints
            ],
        )
    return {"feed_events": len(rows), "tape": len(prints)}


def load_curves(conn: psycopg.Connection, events: Iterable[Event], ours: str | None = None) -> int:
    """Rebuild dealer curves from a FULL history (see `covers_history`). A curve only grows: a writer
    that is behind (fewer steps, a shorter span, no fill yet) never overwrites a fresher row.
    `ours` (our team id) tags our own threads; a writer that does not know it leaves the tag alone."""
    curves = dealer_threads(events, ours)  # sorted by thread id
    with conn.cursor() as cur:
        cur.executemany(
            "insert into dealer_curves (thread_id, dealer, team, item, opening_ask, asks, bids, final_ask, "
            "outcome, fill_price, steps, ticks, ours) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (thread_id) do update set asks = excluded.asks, bids = excluded.bids, "
            "final_ask = excluded.final_ask, outcome = excluded.outcome, fill_price = excluded.fill_price, "
            "steps = excluded.steps, ticks = excluded.ticks, ours = coalesce(excluded.ours, dealer_curves.ours) "
            "where excluded.steps >= coalesce(dealer_curves.steps, 0) "
            "and excluded.ticks >= coalesce(dealer_curves.ticks, 0) "
            "and (excluded.fill_price is not null or dealer_curves.fill_price is null)",
            [
                (
                    t.thread,
                    t.dealer,
                    t.team,
                    t.item,
                    t.opening_ask,
                    t.dealer_prices,
                    t.team_prices,
                    t.final_price,
                    "deal" if t.fill_price is not None else "open_or_walked",
                    t.fill_price,
                    t.steps,
                    t.last_tick - t.opened_tick,
                    t.ours if ours else None,
                )
                for t in curves
            ],
        )
    conn.commit()
    return len(curves)


def covers_history(conn: psycopg.Connection, events: list[Event]) -> bool:
    """True when `events` reach back as far as the stored feed (call it after `load_events`).

    dealer_curves and competitor_profiles are rebuilt from a writer's whole capture. A teammate whose
    capture started later holds only the recent window, and its rebuild would replace counts built
    from more history, so the writer with the oldest history owns those tables.
    """
    if not events:
        return False
    row = conn.execute("select min(id) from feed_events").fetchone()
    conn.commit()
    oldest = row[0] if row else None
    return oldest is None or min(e["id"] for e in events) <= oldest


def load_feed(conn: psycopg.Connection, events: Iterable[Event], ours: str | None = None) -> dict[str, int]:
    """Upsert raw events and the tape; rebuild dealer curves when this history is complete. Idempotent."""
    events = list(events)
    counts = load_events(conn, events)
    curves = load_curves(conn, events, ours) if covers_history(conn, events) else 0
    return {**counts, "dealer_curves": curves}


def load_history(conn: psycopg.Connection, events: Iterable[Event], tick: int, ours: str | None = None) -> bool:
    """The monitor's periodic rebuild. False when the derived tables were left to a fuller history."""
    events = list(events)
    load_events(conn, events)
    if not covers_history(conn, events):
        return False
    load_curves(conn, events, ours)
    save_competitors(conn, team_flows(events), tick, ours)
    return True


def upsert_traders(conn: psycopg.Connection, snapshots: Iterable[Any], tick: int) -> int:
    """Upsert every trader seen at `tick`. A writer at an older tick never overwrites a newer row,
    and a snapshot without a level (most feed events carry none) keeps the known one."""
    rows = [
        (
            t.trader_id,
            t.kind,
            t.name,
            t.level,
            json.dumps(t.traits),
            t.menu,
            json.dumps(t.unlock),
            tick,
            tick,
            t.status,
            tick,
        )
        for t in sorted(snapshots, key=lambda s: s.trader_id)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "insert into traders (id, kind, name, level, traits, menu, unlock, first_seen_tick, last_seen_tick, "
            "status, updated_tick) values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s) "
            "on conflict (id) do update set name = excluded.name, "
            "level = coalesce(excluded.level, traders.level), traits = excluded.traits, "
            "menu = excluded.menu, unlock = excluded.unlock, "
            "first_seen_tick = least(traders.first_seen_tick, excluded.first_seen_tick), "
            "last_seen_tick = greatest(traders.last_seen_tick, excluded.last_seen_tick), "
            "status = excluded.status, updated_tick = excluded.updated_tick "
            "where traders.updated_tick is null or excluded.updated_tick >= traders.updated_tick",
            rows,
        )
    conn.commit()
    return len(rows)


TRADER_COLUMNS = ("id", "kind", "name", "status", "level", "first_seen_tick", "last_seen_tick")


def trader_rows(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Every trader the monitor has seen (dealers and teams), ordered by kind and id."""
    rows = conn.execute(
        "select id, kind, name, status, level, first_seen_tick, last_seen_tick from traders order by kind, id"
    ).fetchall()
    return [dict(zip(TRADER_COLUMNS, row, strict=True)) for row in rows]


def insert_alerts(conn: psycopg.Connection, alerts: Iterable[Any]) -> int:
    """Insert alerts; one another monitor already stored (same tick, kind, subject, detail) is skipped.
    Returns how many were new."""
    rows = sorted({(a.tick, a.kind, a.subject, a.detail) for a in alerts})
    with conn.cursor() as cur:
        cur.executemany(
            "insert into alerts (tick, kind, subject, detail) values (%s, %s, %s, %s) on conflict do nothing",
            rows,
        )
        added = max(cur.rowcount, 0)
    conn.commit()
    return added


def save_snapshot(conn: psycopg.Connection, tick: int, me: dict[str, Any]) -> None:
    """Our /api/me at `tick`. Two monitors in the same tick store the same team's state: last one wins."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into snapshots (tick, cash, level, assets, album, score) values (%s, %s, %s, %s, %s, %s) "
            "on conflict (tick) do update set cash = excluded.cash, level = excluded.level, "
            "assets = excluded.assets, album = excluded.album, score = excluded.score",
            (
                tick,
                me.get("cash"),
                me.get("level"),
                json.dumps(me.get("assets")),
                json.dumps(me.get("album")),
                json.dumps(me.get("score")),
            ),
        )
    conn.commit()


def save_competitors(conn: psycopg.Connection, flows: Iterable[Any], tick: int, ours: str | None = None) -> int:
    """Upsert competitor profiles. Our own team is not a competitor: it is skipped, and a row an older
    version wrote for it is removed (`bazaar teams` shows our flow apart, from the feed)."""
    rows = [
        (
            f.team,
            tick,
            json.dumps(dict(f.set_interest)),
            f.avg_pack_price,
            json.dumps({"listings": f.listings}),
            json.dumps({"buys": f.buys, "sells": f.sells, "spent": f.spent, "earned": f.earned}),
            json.dumps({"top_set": f.top_set, "dealer_threads": f.dealer_threads, "bids": f.bids}),
        )
        for f in sorted(flows, key=lambda f: f.team)
        if f.team != ours
    ]
    with conn.cursor() as cur:
        if ours:
            cur.execute("delete from competitor_profiles where team = %s", (ours,))
        cur.executemany(
            "insert into competitor_profiles (team, updated_tick, set_interest, avg_pack_price, listings, fills, "
            "notes) values (%s, %s, %s, %s, %s, %s, %s) on conflict (team) do update set "
            "updated_tick = excluded.updated_tick, "
            "set_interest = excluded.set_interest, avg_pack_price = excluded.avg_pack_price, "
            "listings = excluded.listings, fills = excluded.fills, notes = excluded.notes "
            "where competitor_profiles.updated_tick is null "
            "or excluded.updated_tick >= competitor_profiles.updated_tick",
            rows,
        )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------- `bazaar db check`


@dataclass(frozen=True)
class CheckReport:
    server_version: str
    round_trips_ms: tuple[float, ...]
    ssl: bool
    pgvector: str | None  # installed version
    pgvector_shipped: bool  # the server has the extension files: `bazaar db init` can turn it on
    tables: list[tuple[str, int]]


def check(database_url: str, round_trips: int = 3) -> CheckReport:
    with connect(database_url, app="bazaar-check") as conn:
        timings = []
        for _ in range(round_trips):
            start = time.perf_counter()
            conn.execute("select 1").fetchone()
            timings.append((time.perf_counter() - start) * 1000)
        version = conn.execute("show server_version").fetchone()
        ssl = conn.execute("select ssl from pg_stat_ssl where pid = pg_backend_pid()").fetchone()
        shipped = conn.execute("select 1 from pg_available_extensions where name = 'vector'").fetchone()
        return CheckReport(
            server_version=str(version[0]) if version else "?",
            round_trips_ms=tuple(timings),
            ssl=bool(ssl and ssl[0]),
            pgvector=pgvector_version(conn),
            pgvector_shipped=shipped is not None,
            tables=table_counts(conn),
        )


def _target_line(target: Target) -> str:
    where = "local default URL: yes (docker compose)" if target.is_local_default else "local default URL: no"
    return f"target    {target} · {where}"


def report_lines(report: CheckReport, target: Target) -> list[str]:
    pgvector = (
        f"on ({report.pgvector})"
        if report.pgvector
        else (
            "off: shipped, run `bazaar db init`"
            if report.pgvector_shipped
            else "off: this server has no pgvector (embedding columns skipped)"
        )
    )
    width = max((len(name) for name, _ in report.tables), default=0)
    tables = [f"  {name:<{width}}  {n:>7}" for name, n in report.tables]
    return [
        f"server    PostgreSQL {report.server_version}",
        f"ssl       {'on' if report.ssl else 'off'} (sslmode {target.sslmode})",
        f"latency   {' / '.join(f'{ms:.1f}' for ms in report.round_trips_ms)} ms "
        f"({len(report.round_trips_ms)} round trips)",
        f"pgvector  {pgvector}",
        f"tables    {len(report.tables) or 'none: run `bazaar db init`'}",
        *tables,
    ]


def run_check(database_url: str | None = None) -> tuple[bool, list[str]]:
    """`bazaar db check`: (reachable, lines to print). No line ever carries the password."""
    try:
        url = load_settings().require_database_url(database_url)
    except ConfigError as e:
        return False, [str(e)]
    try:
        target = describe(url)
    except DatabaseUrlError as e:
        return False, [str(e)]
    lines = [_target_line(target)]
    try:
        return True, lines + report_lines(check(url), target)
    except psycopg.Error as e:
        detail = (redact(str(e), url).strip().splitlines() or ["?"])[0]  # first attempt; the rest repeats it
        hint = "start it with `uv run bazaar db up`" if target.is_local_default else "check DATABASE_URL in .env"
        return False, [*lines, f"unreachable: {detail}", f"hint      {hint}"]
