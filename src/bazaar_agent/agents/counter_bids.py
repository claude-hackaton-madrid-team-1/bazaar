"""Counter a bid another team addresses to us below our floor, like a duel (Marius, Sun 4 Oct: "treat it as a duel,
fight to get on top of our floor").

A team that bids to us directly wants the card. When its bid (less the fee we would pay accepting it) is under what
selling our copy costs us plus `sell_min_surplus`, the taker skips it; the maker now answers with an ask ADDRESSED to
that team (they pay the fee when they accept it): it opens `ANCHOR` above our floor and concedes in `STEPS` equal
steps, one every `STEP_TICKS`, down to the floor, never under it. If the team raises its bid above our floor, the
taker sells into it. The counter stays up `TTL_TICKS` after their last bid was seen (their bid may lapse while ours
stands), then goes. Pure: payloads and our values in, counter targets out; the maker posts them through every sell
guard (`protect_page_sets`, approvals, the move-impact guard, the counterparty cap).

A copy the maker lists (or wants to list) for anyone is countered too: the counter takes over that copy's ask while
it stands, never above the public price (we never ask the team that bids more than anyone), and the public ask comes
back when the counter goes. Not countered: a card we hold no free copy of, our only copy of a protected page card
(`protect_page_sets`), a bid from a pseudonym (an ask can only be addressed to a team id), a team in
`team_desk_never_trade` (a counter is a proposal), or a floor more than `MAX_RATIO` × their bid (nothing to meet).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from bazaar_agent.agents.market import BoardOffer, Venue
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.intel import TEAM_ID
from bazaar_agent.strategy import Market, StrategyParams, bonus_at_stake

ANCHOR = 0.25  # the opening counter: this share above our floor
STEPS = 4  # concessions from the anchor down to the floor
STEP_TICKS = 3  # ticks between two concessions (45 s at Sunday's 15 s ticks): the floor after 12 ticks
TTL_TICKS = 40  # a counter stands this long after the team's last bid to us was seen (10 min at 15 s)
MAX_RATIO = 3.0  # no counter when our floor is more than this × their bid


@dataclass(frozen=True)
class Seen:
    """A team's interest in a card: when we first and last saw its bid to us, and its best bid."""

    first_tick: int
    last_tick: int
    best_bid: int


@dataclass(frozen=True)
class Counter:
    team: str
    ref: str
    rarity: str
    asset_id: int
    loss: float  # what selling this copy costs us: its your_value + the page bonus at stake
    floor: int  # loss + sell_min_surplus, rounded up: the lowest counter
    anchor: int
    price: int  # this tick's counter
    their_bid: int
    step: int  # concessions made so far (0 = the anchor; STEPS = the floor)

    @property
    def reason(self) -> str:
        return (
            f"counter to {self.team}'s bid {self.their_bid} for {self.ref}: ask {self.price} addressed to them "
            f"(anchor {self.anchor}, floor {self.floor} = what selling costs us {self.loss:.1f} + sell_min_surplus, "
            f"step {self.step}/{STEPS})"
        )


def counter_price(anchor: int, floor: int, ticks: int) -> tuple[int, int]:
    """(price, step) after `ticks` since the first bid: equal steps from the anchor to the floor, never under it."""
    step = min(STEPS, max(0, ticks) // STEP_TICKS)
    return max(floor, math.ceil(anchor - (anchor - floor) * step / STEPS - 1e-9)), step


def anchor_for(floor: int) -> int:
    return max(floor + 1, math.ceil(floor * (1 + ANCHOR) - 1e-9))


def observe(seen: Mapping[tuple[str, str], Seen], bids: Iterable[BoardOffer], tick: int) -> dict[tuple[str, str], Seen]:
    """The interest book after this tick's bids to us: new (team, card) pairs start now, a seen pair keeps its first
    tick and best bid; a pair unseen for more than TTL_TICKS is dropped."""
    out = {k: s for k, s in seen.items() if tick - s.last_tick <= TTL_TICKS}
    for b in bids:
        if b.side != "bid":
            continue
        key = (b.maker, b.ref)
        old = out.get(key)
        out[key] = (
            Seen(tick, tick, b.price)
            if old is None
            else Seen(old.first_tick, tick, max(old.best_bid, b.price) if old.last_tick == tick else b.price)
        )
    return out


def plan_counters(
    seen: Mapping[tuple[str, str], Seen],
    m: Market,
    me: dict[str, Any],
    params: StrategyParams,
    rules: Guardrails,
    venue: Venue | None,
    unavailable: Iterable[int],
    public_prices: Mapping[int, int],
    tick: int,
) -> tuple[list[Counter], dict[tuple[str, str], str]]:
    """(the counters to stand this tick, one per card: the most interested team's; why each other pair is not
    countered). `unavailable`: our copies already promised; `public_prices`: copy -> the price we ask (or want to
    ask) anyone for it, a ceiling for its counter."""
    blocked = set(unavailable)
    skipped: dict[tuple[str, str], str] = {}
    best: dict[str, Counter] = {}
    for (team, ref), s in sorted(seen.items()):
        key = (team, ref)
        card = m.cards.get(ref)
        if not TEAM_ID.match(team):
            skipped[key] = f"{team} is not a team id we can address an ask to"
            continue
        if rules.never_trades_with(team):
            skipped[key] = f"{team} is in team_desk_never_trade"
            continue
        if card is None:
            skipped[key] = f"{ref} is not a card we know"
            continue
        copies = [
            a
            for a in me.get("assets") or []
            if a.get("kind") == "card"
            and a.get("ref") == ref
            and isinstance(a.get("id"), int)
            and isinstance(a.get("your_value"), int | float)
        ]
        free = [a for a in copies if a["id"] not in blocked]
        if not free:
            skipped[key] = f"no free copy of {ref}"
            continue
        if rules.protects(ref, card.rarity, len(free)):
            skipped[key] = f"{ref} is our only copy of a page card (protect_page_sets): never sold"
            continue
        copy = min(free, key=lambda a: (float(a["your_value"]), -int(a["id"])))
        held = m.held.get(ref, 0)
        as_held = m if held == len(free) else replace(m, held={**m.held, ref: len(free)})
        loss = float(copy["your_value"]) + bonus_at_stake(as_held, card, params)
        floor = max(1, math.ceil(loss + params.sell_min_surplus - 1e-9))
        fee = venue.fee(s.best_bid) if venue is not None else 0
        if s.best_bid - fee >= loss + params.sell_min_surplus:
            skipped[key] = f"their bid {s.best_bid} already clears our floor: the taker sells into it"
            continue
        if floor > MAX_RATIO * max(1, s.best_bid):
            skipped[key] = f"our floor {floor} is over {MAX_RATIO:g}x their bid {s.best_bid}: nothing to meet"
            continue
        anchor = anchor_for(floor)
        price, step = counter_price(anchor, floor, tick - s.first_tick)
        if (ceiling := public_prices.get(int(copy["id"]))) is not None:
            price = max(floor, min(price, ceiling))
        c = Counter(team, ref, card.rarity, int(copy["id"]), round(loss, 1), floor, anchor, price, s.best_bid, step)
        other = best.get(ref)
        if other is None or (c.their_bid, -c.price) > (other.their_bid, -other.price):
            if other is not None:
                skipped[(other.team, ref)] = f"{c.team} bids more for {ref}: our counter goes to them"
            best[ref] = c
        else:
            skipped[key] = f"{other.team} bids more for {ref}: our counter goes to them"
    return sorted(best.values(), key=lambda c: c.ref), skipped
