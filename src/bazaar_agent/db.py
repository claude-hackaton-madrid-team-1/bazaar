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


def load_feed(conn: psycopg.Connection, events: Iterable[Event]) -> dict[str, int]:
    """Upsert raw events, the tape and the rebuilt dealer curves. Idempotent."""
    events = list(events)
    with conn.cursor() as cur:
        cur.executemany(
            "insert into feed_events (id, tick, type, actor, payload) values (%s, %s, %s, %s, %s) "
            "on conflict (id) do nothing",
            [(e["id"], e.get("tick"), e.get("type"), e.get("actor"), json.dumps(e.get("payload"))) for e in events],
        )
        prints: list[Print] = tape(events)
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
        curves = dealer_threads(events)
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
    return {"feed_events": len(events), "tape": len(prints), "dealer_curves": len(curves)}
