"""Postgres access: plain SQL through psycopg 3, no ORM."""

from __future__ import annotations

import json
from collections.abc import Iterable
from importlib.resources import files
from typing import Any

import psycopg
from psycopg import sql as pgsql

from bazaar_agent.intel import Print, dealer_threads, set_of, tape

Event = dict[str, Any]


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, connect_timeout=5)


def init_schema(conn: psycopg.Connection) -> None:
    sql = files("bazaar_agent").joinpath("sql/schema.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)  # type: ignore[arg-type]  # trusted file shipped in the package
    conn.commit()


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
    events = list(events)
    prints: list[Print] = tape(events)
    with conn.cursor() as cur:
        cur.executemany(
            "insert into feed_events (id, tick, type, actor, payload) values (%s, %s, %s, %s, %s) "
            "on conflict (id) do nothing",
            [(e["id"], e.get("tick"), e.get("type"), e.get("actor"), json.dumps(e.get("payload"))) for e in events],
        )
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
    conn.commit()
    return {"feed_events": len(events), "tape": len(prints)}


def load_curves(conn: psycopg.Connection, events: Iterable[Event]) -> int:
    """Rebuild dealer curves. Needs the FULL history: a partial window would overwrite good rows."""
    curves = dealer_threads(events)
    with conn.cursor() as cur:
        cur.executemany(
            "insert into dealer_curves (thread_id, dealer, team, item, opening_ask, asks, bids, final_ask, "
            "outcome, fill_price, steps, ticks) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (thread_id) do update set asks = excluded.asks, bids = excluded.bids, "
            "final_ask = excluded.final_ask, outcome = excluded.outcome, fill_price = excluded.fill_price, "
            "steps = excluded.steps, ticks = excluded.ticks",
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
                )
                for t in curves
            ],
        )
    conn.commit()
    return len(curves)


def load_feed(conn: psycopg.Connection, events: Iterable[Event]) -> dict[str, int]:
    """Upsert raw events, the tape and the rebuilt dealer curves from the full history. Idempotent."""
    events = list(events)
    counts = load_events(conn, events)
    return {**counts, "dealer_curves": load_curves(conn, events)}


def upsert_traders(conn: psycopg.Connection, snapshots: Iterable[Any], tick: int) -> int:
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
        for t in snapshots
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "insert into traders (id, kind, name, level, traits, menu, unlock, first_seen_tick, last_seen_tick, "
            "status, updated_tick) values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s) "
            "on conflict (id) do update set name = excluded.name, level = excluded.level, traits = excluded.traits, "
            "menu = excluded.menu, unlock = excluded.unlock, last_seen_tick = excluded.last_seen_tick, "
            "status = excluded.status, updated_tick = excluded.updated_tick",
            rows,
        )
    conn.commit()
    return len(rows)


def insert_alerts(conn: psycopg.Connection, alerts: Iterable[Any]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "insert into alerts (tick, kind, subject, detail) values (%s, %s, %s, %s)",
            [(a.tick, a.kind, a.subject, a.detail) for a in alerts],
        )
    conn.commit()


def save_snapshot(conn: psycopg.Connection, tick: int, me: dict[str, Any]) -> None:
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


def save_competitors(conn: psycopg.Connection, flows: Iterable[Any], tick: int) -> int:
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
        for f in flows
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "insert into competitor_profiles (team, updated_tick, set_interest, avg_pack_price, listings, fills, "
            "notes) values (%s, %s, %s, %s, %s, %s, %s) on conflict (team) do update set "
            "updated_tick = excluded.updated_tick, "
            "set_interest = excluded.set_interest, avg_pack_price = excluded.avg_pack_price, "
            "listings = excluded.listings, fills = excluded.fills, notes = excluded.notes",
            rows,
        )
    conn.commit()
    return len(rows)
