"""The score impact guard's inputs (`move_impact.Facts`), read from Postgres once per tick per process.

How we got each copy we hold comes from our settlements in `feed_events` (the tape every agent stores), and k from
our `me_snapshots` score history (`neg_points`, `negotiating`). "Us" is the team of the newest snapshot. One
`TickBoard` read on a worker thread with a deadline (`breaker_read_timeout_s`): a failed or slow read answers None,
and the guard then prices every sale at the worst case (fail closed): each copy as bought from a team, k at its
fallback. `sell_state` is the same estimate for a decider's state (judge input).
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from bazaar_agent import move_impact as mi
from bazaar_agent.breakers import CONNECT_TIMEOUT_S, TickBoard

log = logging.getLogger(__name__)

WINDOW_TICKS = 480  # k is measured on the last 4 game hours of snapshots at Saturday's 30 s ticks
US = "select world, team from me_snapshots order by read_at desc limit 1"
POINTS = (
    "select tick, (score->>'neg_points')::float, (score->>'negotiating')::float from me_snapshots "
    "where world = %s and team = %s and tick > %s and score ? 'neg_points' and score ? 'negotiating' order by tick"
)
SETTLEMENTS = "select payload from feed_events where type = 'settlement' and payload->'parties' ? %s order by id"


def read_facts(conn: psycopg.Connection, tick: int) -> mi.Facts | None:
    """Our origins and score history as Postgres holds them now; None when no snapshot names us yet."""
    us = conn.execute(US).fetchone()
    if us is None:
        return None
    world, team = str(us[0]), str(us[1])
    rows = conn.execute(POINTS, (world, team, tick - WINDOW_TICKS)).fetchall()
    points = tuple(mi.ScorePoint(int(r[0]), float(r[1]), float(r[2])) for r in rows if None not in r)
    settlements = [r[0] for r in conn.execute(SETTLEMENTS, (team,)).fetchall() if isinstance(r[0], dict)]
    return mi.Facts(team, mi.origins(settlements, team), points)


class ImpactBoard(TickBoard["mi.Facts | None"]):
    """The facts, read once per tick; fail closed (a failed read is None: every sale priced at the worst case)."""

    name = "impact"
    fallback_note = "every sale is priced at the worst case (each copy as bought from a team, k at its fallback)"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("notify", log.warning)
        super().__init__(*args, **kwargs)

    def fallback(self) -> mi.Facts | None:
        return None

    def query(self, conn: psycopg.Connection, tick: int) -> mi.Facts | None:
        return read_facts(conn, tick)


_BOARD: dict[str, ImpactBoard] = {}


def _default_connect() -> psycopg.Connection:
    from bazaar_agent import pgconn

    return pgconn.connect(app="bazaar-impact", connect_timeout_s=CONNECT_TIMEOUT_S)


def board(timeout_s: float = 1.0) -> ImpactBoard:
    """This process's board (one per process, DATABASE_URL). Tests install their own with `install`."""
    if "board" not in _BOARD:
        _BOARD["board"] = ImpactBoard(_default_connect, timeout_s)
    return _BOARD["board"]


def install(b: ImpactBoard) -> ImpactBoard | None:
    """Replace this process's board; returns the old one (for a test to put back)."""
    old = _BOARD.get("board")
    _BOARD["board"] = b
    return old


def sell_state(
    me: dict[str, Any],
    ref: str,
    rarity: str | None,
    price: float,
    counterparty: str | None,
    rules: Any,
    tick: int,
    asset: int | None = None,
    value: float | None = None,
) -> dict[str, Any]:
    """The guard's estimate of one sale, for a decider's state (judge input): `Impact.as_state()` plus the bar
    (`max_score_loss_per_move`). `counterparty` None is a dealer. Reads this process's board (cached per tick)."""
    facts = board(rules.breaker_read_timeout_s).read(tick)
    impact = mi.sell_impact(
        mi.our_cards(me),
        ref,
        rarity,
        price,
        counterparty,
        facts,
        rules.score_per_neg_point_fallback,
        rules.dealer_ladder_score,
        asset,
        value,
    )
    return {
        **impact.as_state(),
        "max_score_loss_per_move": rules.max_score_loss_per_move,
        "facts_read": facts is not None,
    }
