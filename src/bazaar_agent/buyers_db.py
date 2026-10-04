"""`bazaar buyers`: the buyer ranking (`buyers.rank_buyers`) for our cards, as a table, as JSON, and in Postgres.

Read-only against the game. `team_buyer_rank` keeps the latest ranking per card (our own Postgres only):
a save replaces the rows of the cards it ranks, in one transaction, and leaves every other card as it was.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import psycopg
from rich.markup import escape
from rich.table import Table

from bazaar_agent import buyers as by

COLUMNS = ("team", "rank", "willing", "interest", "missing", "expected", "rival", "blocked", "why")


def _cards(me: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [a for a in me.get("assets") or [] if isinstance(a, dict) and a.get("kind") == "card" and a.get("ref")]


def duplicates(me: Mapping[str, Any]) -> list[str]:
    """Every card we hold more than one copy of, in ref order."""
    held = Counter(str(a["ref"]) for a in _cards(me))
    return sorted(ref for ref, n in held.items() if n > 1)


def our_value(me: Mapping[str, Any], card: str) -> float:
    """The lowest `your_value` among our copies of `card` (the copy we would sell); 0 when we hold none."""
    values = [float(a.get("your_value") or 0) for a in _cards(me) if str(a["ref"]) == card]
    return min(values, default=0.0)


def rank_cards(
    cards: Iterable[str],
    *,
    me: Mapping[str, Any],
    catalog: Mapping[str, Any],
    events: Iterable[dict[str, Any]],
    board: Mapping[str, Any] | None,
    scan: Iterable[Mapping[str, Any]] = (),
) -> dict[str, list[by.BuyerRow]]:
    """card -> every other team as a buyer of it, best first."""
    events = list(events)
    holders, interest = by.market_inputs(me, catalog, events, scan)
    ranks, us = by.leaderboard_ranks(board), str(me.get("id") or "")
    return {
        card: by.rank_buyers(
            card,
            catalog=catalog,
            events=events,
            holders=holders,
            interest=interest,
            ranks=ranks,
            us=us,
            our_value=our_value(me, card),
        )
        for card in cards
    }


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return escape(str(value))  # rival and why carry team ids and feed-derived text: never rich markup


def table(card: str, rows: Iterable[by.BuyerRow]) -> Table:
    out = Table(title=f"buyers for {escape(card)}")
    for name in COLUMNS:
        out.add_column(name, overflow="fold")
    for r in rows:
        out.add_row(*(_cell(getattr(r, name)) for name in COLUMNS))
    return out


def _database_url() -> str | None:
    from bazaar_agent.config import ConfigError, load_settings

    try:
        return load_settings().require_database_url()
    except ConfigError:
        return None


def safe_error(e: BaseException) -> str:
    """The first line of a DB error, passwords removed (libpq can echo one from a bad URL)."""
    from bazaar_agent import pgconn

    text = pgconn.redact(str(e), _database_url()).strip().splitlines()
    return f"{type(e).__name__}: {text[0] if text else '?'}"


def stored_scan(open_conn: Callable[[], psycopg.Connection]) -> tuple[list[dict[str, Any]], str | None]:
    """(the stored card scan, None), or ([], a note) when Postgres cannot be read: ranking goes on without it."""
    from bazaar_agent.supply_db import load_scan

    try:
        with open_conn() as conn:
            return load_scan(conn), None
    except Exception as e:  # any failure means no scan, never a failed command
        return [], f"no scan (Postgres unavailable: {safe_error(e)}); holdings from the feed only"


def save(conn: psycopg.Connection, rows_by_card: Mapping[str, Iterable[by.BuyerRow]], tick: int) -> int:
    """Replace the stored ranking of every card in `rows_by_card` (position = 1-based order), one transaction."""
    data = [
        (r.card, r.team, i, r.rank, r.willing, r.interest, r.missing, r.expected, r.rival, r.blocked, r.why, tick)
        for rows in rows_by_card.values()
        for i, r in enumerate(rows, start=1)
    ]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("delete from team_buyer_rank where card = any(%s)", (list(rows_by_card),))
        cur.executemany(
            "insert into team_buyer_rank (card, team, position, rank, willing, interest, missing, expected, rival, "
            "blocked, why, updated_tick) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            data,
        )
    conn.commit()
    return len(data)
