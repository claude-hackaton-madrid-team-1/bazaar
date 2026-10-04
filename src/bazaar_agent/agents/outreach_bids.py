"""Reach out to the teams that hold a card we want (Marius, Sun 4 Oct: "I want the agents to constantly interact with
other teams for cards that might have a good value for us").

For each card the maker already bids for (a missing page card the strategy prices, or a human-approved buy target),
the bid goes ADDRESSED to a team the team matrix places a spare copy with, instead of to anyone: it opens at
`START_SHARE` of our ceiling and steps up in `STEPS` equal steps, one every `STEP_TICKS`, to the ceiling (the bid the
maker would post anyway, never above it), holds there `HOLD_TICKS`, then moves on to the next team holding a spare,
round and round. At most `MAX_CARDS` cards are worked this way at once; every other bid stays public. The maker posts
each one through every buy guard (official value of one more copy, rarity caps, cash floor, hourly spend, approvals),
so a bid can never pass our value. Pure: targets and holders in, addressed bids out.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from bazaar_agent.guardrails import Guardrails
from bazaar_agent.intel import TEAM_ID

START_SHARE = 0.7  # the opening bid, as a share of our ceiling
STEPS = 4  # raises from the opening bid up to the ceiling
STEP_TICKS = 3  # ticks between two raises (45 s at Sunday's 15 s ticks)
HOLD_TICKS = 12  # ticks at the ceiling with one team before the next holder gets the bid (3 min)
MAX_CARDS = 3  # cards worked with addressed bids at once (each open bid holds its cash)


@dataclass(frozen=True)
class Want:
    """A card the maker bids for: its ceiling is the price it would post for anyone."""

    ref: str
    rarity: str
    ceiling: int
    score: float


@dataclass(frozen=True)
class Turn:
    """Which holder of a card has our bid, and since when."""

    team: str
    since: int


@dataclass(frozen=True)
class Outreach:
    team: str
    ref: str
    rarity: str
    price: int
    ceiling: int
    step: int  # raises made (0 = the opening bid; STEPS = the ceiling)
    holders: int

    @property
    def reason(self) -> str:
        return (
            f"outreach: bid {self.price} for {self.ref} addressed to {self.team}, who holds a spare "
            f"(raise {self.step}/{STEPS} toward our ceiling {self.ceiling}; {self.holders} holder(s) in turn)"
        )


def outreach_price(ceiling: int, ticks: int) -> tuple[int, int]:
    """(price, raises) after `ticks` with this team: equal raises from START_SHARE × ceiling to the ceiling."""
    start = max(1, math.ceil(ceiling * START_SHARE - 1e-9))
    step = min(STEPS, max(0, ticks) // STEP_TICKS)
    return min(ceiling, math.ceil(start + (ceiling - start) * step / STEPS - 1e-9)), step


def turn_ticks() -> int:
    """How long one holder keeps our bid: the raises, then the hold at the ceiling."""
    return STEPS * STEP_TICKS + HOLD_TICKS


def plan_outreach(
    wants: Iterable[Want],
    holders: Callable[[str], list[str]],
    turns: Mapping[str, Turn],
    rules: Guardrails,
    us: str,
    tick: int,
) -> tuple[list[Outreach], dict[str, Turn]]:
    """(the addressed bids this tick, best-scored cards first, at most MAX_CARDS; the turn book after it). A card
    with no team we may address holding a spare stays a public bid."""
    out: list[Outreach] = []
    book: dict[str, Turn] = {}
    for w in sorted(wants, key=lambda w: (-w.score, w.ref)):
        if len(out) >= MAX_CARDS or w.ceiling <= 0:
            continue
        teams = [t for t in holders(w.ref) if TEAM_ID.match(t) and t != us and not rules.never_trades_with(t)]
        if not teams:
            continue
        turn = turns.get(w.ref)
        if turn is None or turn.team not in teams:
            turn = Turn(teams[0], tick)
        elif tick - turn.since >= turn_ticks():  # this holder had its full turn: the next one
            turn = Turn(teams[(teams.index(turn.team) + 1) % len(teams)], tick)
        price, step = outreach_price(w.ceiling, tick - turn.since)
        book[w.ref] = turn
        out.append(Outreach(turn.team, w.ref, w.rarity, price, w.ceiling, step, len(teams)))
    return out, book
