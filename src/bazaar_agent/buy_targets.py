"""Buy targets: a human's buy approval of an off-page card (an epic or a legendary) is a card the agents pursue.

A buy approval (`approvals.py`, HA1; `bazaar approve` or the MCP `approve`, HA2) only lifts `human_approval_above`,
and the strategy only chases missing PAGE cards, so a buy approval of an epic lifted a cap nobody reached. With
`buy_targets_enabled`, each active buy approval of an off-page card we do not hold is a TARGET:
- the maker keeps one public bid for it on the board (cash for any copy: nothing of ours is given), on a ladder
  from `buy_target_start_share` × the ceiling up to the ceiling in `buy_target_steps` steps, one step every
  `buy_target_step_ticks` ticks after the approval was granted; at the ceiling it holds;
- the taker takes a standing ask for it (on any venue it trades on, an ask addressed to us included) whose cost,
  fee included, is at or under the ceiling, inside the team's accept quota.
The ceiling is the least of the approved max, `max_price_<rarity>` and the official value of one more copy
(`GET /api/me/value`) minus `off_page_min_surplus` (`Guardrails.value_margin_for`, at least 1): always strictly
below our value. `guardrails.check()` enforces the same bound on every send, before any approval is read, so no
approval lifts it; the cash floor, the hourly spend cap, `block_buying_held_cards`, `no_buyback_ticks`, the
breakers, the accept quota and the kill switch still bind every bid and accept. A target ends when we hold the card,
its approval expires (`until_tick`) or is revoked: the maker then cancels its bid. No negotiation logic lives in the
MCP: a human approves the card, the max and the ttl, the agents do the rest.

Read once per tick per process from the shared Postgres (`TargetBoard`), on its own query, so the guardrails'
approval read (`approvals.ApprovalBoard`) is never touched. Fails CLOSED: an unreadable table is no target. The
ladder starts at the tick of the card's latest `approval_granted` row (both approve paths write one, so a
re-approve starts the ladder again), else at the tick this process first saw the target.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from bazaar_agent.breakers import CONNECT_TIMEOUT_S, TickBoard
from bazaar_agent.guardrails import OFF_PAGE_RARITIES, Guardrails
from bazaar_agent.intel import card_rarities

READ = (
    "select a.card, a.max_price, a.until_tick, coalesce(a.by, ''), (select max(d.tick) from decisions d "
    "where d.agent = 'guard' and d.kind = 'approval_granted' and d.candidates->>'card' = a.card "
    "and d.candidates->>'side' = 'buy' and d.tick <= %s) "
    "from human_approvals a where a.side = 'buy' and a.max_price is not null and a.until_tick > %s"
)


@dataclass(frozen=True)
class TargetRow:
    """One active buy approval as stored (any card: the off-page filter needs the catalog)."""

    card: str
    max_price: int
    until_tick: int
    by: str = ""
    granted_tick: int | None = None


@dataclass(frozen=True)
class BuyTarget:
    """An off-page card a human ordered bought, up to `max_price`, until `until_tick`."""

    card: str
    rarity: str
    max_price: int
    until_tick: int
    start_tick: int  # the ladder's first step
    by: str = ""


class TargetBoard(TickBoard["tuple[TargetRow, ...] | None"]):
    """The active buy approvals, read once per tick; fail CLOSED (a failed read is None: no target)."""

    name = "buy_targets"
    fallback_note = "no buy target this tick (fail closed: the maker cancels a target's bid)"

    def fallback(self) -> tuple[TargetRow, ...] | None:
        return None

    def no_table(self) -> tuple[TargetRow, ...] | None:
        return ()  # nobody ever approved anything on this database

    def query(self, conn: psycopg.Connection, tick: int) -> tuple[TargetRow, ...] | None:
        rows = conn.execute(READ, (tick, tick)).fetchall()
        return tuple(
            TargetRow(str(r[0]), int(r[1]), int(r[2]), str(r[3]), None if r[4] is None else int(r[4])) for r in rows
        )


_BOARD: dict[str, TargetBoard] = {}


def _default_connect() -> psycopg.Connection:
    from bazaar_agent import pgconn

    return pgconn.connect(app="bazaar-buy-targets", connect_timeout_s=CONNECT_TIMEOUT_S)


def board(timeout_s: float = 1.0) -> TargetBoard:
    """This process's board (one per process, DATABASE_URL). Tests install their own with `install`."""
    if "board" not in _BOARD:
        _BOARD["board"] = TargetBoard(_default_connect, timeout_s)
    return _BOARD["board"]


def install(b: TargetBoard) -> TargetBoard | None:
    """Replace this process's board; returns the old one (for a test to put back)."""
    old = _BOARD.get("board")
    _BOARD["board"] = b
    return old


def off_page(rarity: str | None) -> bool:
    return str(rarity or "").strip().lower() in OFF_PAGE_RARITIES


class TargetBook:
    """The buy targets in force this tick for one agent; remembers when it first saw each (the ladder's start
    when no `approval_granted` row is found)."""

    def __init__(self) -> None:
        self._first: dict[tuple[str, int], int] = {}

    def active(self, rules: Guardrails, tick: int, catalog: dict[str, Any], held: Mapping[str, int]) -> list[BuyTarget]:
        """Off-page cards with an active buy approval that we do not hold. Off, or unreadable: none."""
        if not rules.buy_targets_enabled:
            return []
        rows = board(rules.breaker_read_timeout_s).read(tick)
        if not rows:
            return []
        rarities = card_rarities(catalog)
        out = []
        for r in sorted(rows, key=lambda r: r.card):
            rarity = rarities.get(r.card)
            if not off_page(rarity) or r.until_tick <= tick or held.get(r.card, 0) > 0 or r.max_price < 1:
                continue  # a page card stays the strategy's: its approval only lifts `human_approval_above`
            first = self._first.setdefault((r.card, r.until_tick), tick)
            start = r.granted_tick if r.granted_tick is not None else first
            out.append(BuyTarget(r.card, str(rarity), r.max_price, r.until_tick, start, r.by))
        return out


def ceiling(t: BuyTarget, official: float | None, rules: Guardrails) -> int | None:
    """The most this target may cost us, fee included: the approved max, `max_price_<rarity>` and the official
    value minus `off_page_min_surplus`, whichever is least. None: no official value read, no cap for the rarity
    (buying it is not allowed) or nothing left under the value."""
    cap = rules.max_price_for(t.rarity)
    if cap is None or official is None:
        return None
    top = min(t.max_price, cap, math.floor(official - rules.value_margin_for(t.rarity) + 1e-9))
    return top if top >= 1 else None


def first_bid(top: int, rules: Guardrails) -> int:
    return max(1, min(top, math.ceil(top * rules.buy_target_start_share - 1e-9)))


def ladder_step(t: BuyTarget, tick: int, rules: Guardrails) -> int:
    """How many steps up the ladder is at `tick` (0 = the first bid, `buy_target_steps` = the ceiling)."""
    return min(rules.buy_target_steps, max(0, tick - t.start_tick) // rules.buy_target_step_ticks)


def ladder_price(t: BuyTarget, top: int, tick: int, rules: Guardrails) -> int:
    """Our bid for this target at `tick`: from `first_bid` up to `top` in even steps, never above `top`."""
    start = first_bid(top, rules)
    return start + (top - start) * ladder_step(t, tick, rules) // rules.buy_target_steps


def describe(t: BuyTarget, top: int, tick: int, rules: Guardrails) -> str:
    step = ladder_step(t, tick, rules)
    return (
        f"buy target {t.card} ({t.rarity}) approved by {t.by or 'a human'} until tick {t.until_tick}: "
        f"ladder step {step}/{rules.buy_target_steps} from {first_bid(top, rules)} to the ceiling {top}"
    )
