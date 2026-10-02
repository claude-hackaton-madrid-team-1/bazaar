"""What the evals read, all from Postgres: the feed, the duels, dealer curves, /me snapshots, decisions.

No game API call happens here, so `bazaar evals run` adds nothing to the team key's 5 req/s budget.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from bazaar_agent.evals.dealers import CurveRow
from bazaar_agent.evals.duels import Closure
from bazaar_agent.evals.trades import Settlement, Valuation
from bazaar_agent.feed import Event
from bazaar_agent.identity import valid_team_id

DEALER_EVENT_TYPES = ("thread.opened", "thread.message", "settlement")
DECISION_LINK_TICKS = 3  # a settlement lands at most this many ticks after the accept that caused it


def _rows(conn: psycopg.Connection, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    return conn.execute(query, params).fetchall()  # type: ignore[arg-type]  # constant SQL in this module


def team_from_snapshots(conn: psycopg.Connection) -> str | None:
    """Our team id from the newest /me snapshot (`score.team`): no key and no API call needed."""
    rows = _rows(conn, "select score->>'team' from snapshots where score ? 'team' order by tick desc limit 1")
    return valid_team_id(rows[0][0]) if rows else None


def day_openings(conn: psycopg.Connection) -> list[tuple[int, str]]:
    rows = _rows(conn, "select tick, payload->>'day' from feed_events where type = 'day.opened' order by tick")
    return [(int(tick), str(day)) for tick, day in rows if tick is not None and day]


def dealer_events(conn: psycopg.Connection) -> list[Event]:
    rows = _rows(
        conn,
        "select id, tick, type, actor, payload from feed_events where type = any(%s) order by id",
        (list(DEALER_EVENT_TYPES),),
    )
    return [{"id": int(i), "tick": t, "type": k, "actor": a, "payload": p or {}} for i, t, k, a, p in rows]


def curve_rows(conn: psycopg.Connection) -> list[CurveRow]:
    rows = _rows(conn, "select dealer, item, opening_ask, fill_price from dealer_curves")
    return [CurveRow(str(d), str(i), o, f) for d, i, o, f in rows if d and i]


def dealer_levels(conn: psycopg.Connection) -> dict[str, int]:
    rows = _rows(conn, "select id, level from traders where kind = 'dealer' and level is not null")
    return {str(dealer): int(level) for dealer, level in rows}


def duels(conn: psycopg.Connection, since_tick: int | None) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        "select payload from duels where payload is not null and (%s::int is null or tick >= %s::int) order by duel",
        (since_tick, since_tick),
    )
    return [dict(p) for (p,) in rows if isinstance(p, Mapping)]


def duel_closures(conn: psycopg.Connection) -> dict[int, Closure]:
    rows = _rows(
        conn,
        "select (payload->>'duel')::int, payload->>'status', tick from feed_events "
        "where type = 'duel.closed' and payload->>'duel' ~ '^[0-9]+$' order by id",
    )
    return {int(did): Closure(str(status), tick) for did, status, tick in rows if status}


def our_settlements(conn: psycopg.Connection, ours: str, since_tick: int | None) -> list[Settlement]:
    """Our trades with other teams: settlements with us as a party and no dealer (`persona` null)."""
    rows = _rows(
        conn,
        "select id, tick, payload from feed_events where type = 'settlement' and payload->'parties' ? %s "
        "and payload->>'persona' is null and (%s::int is null or tick >= %s::int) order by id",
        (ours, since_tick, since_tick),
    )
    out = []
    for event_id, tick, p in rows:
        items = [i for i in p.get("items") or [] if isinstance(i, Mapping)]
        if not items:
            continue
        first = items[0]
        out.append(
            Settlement(
                settlement=int(p.get("settlement") or event_id),
                tick=int(p.get("tick") or tick or 0),
                venue=p.get("venue"),
                buyer=str(first.get("to")),
                seller=str(first.get("frm")),
                ref=str(first.get("ref")),
                asset_ids=tuple(int(i["id"]) for i in items if isinstance(i.get("id"), int)),
                price=int(p.get("price") or 0),
                fee=int(p.get("fee") or 0),
            )
        )
    return out


def _asset_value(assets: object, asset_ids: tuple[int, ...]) -> float | None:
    """The summed `your_value` of the traded assets in one snapshot, None when it holds none of them."""
    held = assets if isinstance(assets, list) else []
    values = [
        float(a["your_value"])
        for a in held
        if isinstance(a, Mapping) and a.get("id") in asset_ids and isinstance(a.get("your_value"), int | float)
    ]
    return sum(values) if values else None


def valuation(conn: psycopg.Connection, s: Settlement, ours: str) -> Valuation:
    """The snapshots just before and after the settlement: the card's value and our cash change."""
    before = _rows(
        conn, "select tick, cash, assets from snapshots where tick < %s order by tick desc limit 1", (s.tick,)
    )
    after = _rows(conn, "select tick, cash, assets from snapshots where tick >= %s order by tick limit 1", (s.tick,))
    b, a = (before[0] if before else None), (after[0] if after else None)
    held = a if s.buyer == ours else b
    value = _asset_value(held[2], s.asset_ids) if held else None
    cash = None
    if b and a and b[1] is not None and a[1] is not None:
        others = _rows(
            conn,
            "select count(*) from feed_events where type = 'settlement' and payload->'parties' ? %s "
            "and tick > %s and tick <= %s and coalesce((payload->>'settlement')::bigint, id) <> %s",
            (ours, b[0], a[0], s.settlement),
        )
        change = int(a[1]) - int(b[1])
        cash = change if others and others[0][0] == 0 and change in _trade_cash(s, ours) else None
    return Valuation(value, cash, b[0] if b else None, a[0] if a else None)


def _trade_cash(s: Settlement, ours: str) -> tuple[int, ...]:
    """The cash changes this trade alone can explain (whoever paid the venue fee). Anything else between
    the two snapshots (a venue bond, a pack, a fee earned on our venue) means the change is not the trade's."""
    return (-s.price, -(s.price + s.fee)) if s.buyer == ours else (s.price, s.price - s.fee)


@dataclass(frozen=True)
class LinkedDecision:
    id: int
    agent: str | None
    tick: int | None  # its trace in Phoenix is `<agent> tick <tick>`
    jev: dict[str, Any] | None


def linked_decision(conn: psycopg.Connection, s: Settlement, ours: str) -> LinkedDecision | None:
    """The live decision that caused the trade: the taker's accept of that card, or the maker's ask
    for that asset, settled shortly before."""
    if s.buyer == ours:
        rows = _rows(
            conn,
            "select id, agent, tick, jev from decisions where kind = 'accept_ask' and status = 'done' "
            "and not coalesce(dry_run, false) and candidates->>'ref' = %s and tick between %s and %s "
            "order by tick desc, id desc limit 1",
            (s.ref, s.tick - DECISION_LINK_TICKS, s.tick),
        )
    else:
        rows = _rows(
            conn,
            "select id, agent, tick, jev from decisions where kind = 'post_ask' and status = 'done' "
            "and not coalesce(dry_run, false) and (candidates->>'asset_id')::bigint = any(%s) and tick <= %s "
            "order by tick desc, id desc limit 1",
            (list(s.asset_ids), s.tick),
        )
    if not rows:
        return None
    decision_id, agent, tick, jev = rows[0]
    return LinkedDecision(int(decision_id), agent, tick, dict(jev) if isinstance(jev, Mapping) else None)


def latest_score(conn: psycopg.Connection) -> tuple[int, dict[str, Any]] | None:
    rows = _rows(conn, "select tick, score from snapshots where score is not null order by tick desc limit 1")
    return (int(rows[0][0]), dict(rows[0][1])) if rows and isinstance(rows[0][1], Mapping) else None


def jev_calls(conn: psycopg.Connection) -> dict[str, tuple[int, int]]:
    """Per Jev question: (calls, decided) from the agents' decisions."""
    rows = _rows(
        conn,
        "select coalesce(jev->>'question', 'offer_is_worth_accepting'), count(*), "
        "count(*) filter (where jev->>'verdict' in ('yes', 'no')) from decisions where jev is not null group by 1",
    )
    return {str(q): (int(n), int(d)) for q, n, d in rows}
