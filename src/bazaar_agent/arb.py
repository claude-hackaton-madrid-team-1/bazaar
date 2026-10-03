"""Arbitrage and duplicate buys: what a standing ask is worth when we resell it at once, or keep it again.

Pure: board offers and venues in (`agents.market`), typed rows out; no network, so the scanner, the taker
and the Friday study share one arithmetic.

- Fees: the ACCEPTING side pays `venue.fee(price)` (El Rastro 5 % + 1 P per card, rounded up; a team venue
  its `fee_bps`). An arbitrage accepts twice, so it pays both: buying a standing ask costs
  `ask + fee(ask)`, selling into a standing bid brings `bid − fee(bid)`. Net = proceeds − cost.
- Timing: an accept settles on the next tick, so the bid is hit one tick after the ask (we must hold the
  card) and must still stand then. Each leg uses one of the team's accepts.
- Scoring: cash never scores; each trade scores at our private values. The buy scores `value − cost`
  (value = one more copy: book × affinity × 1 / 0.25 / 0.1), the resale `proceeds − value`: together the
  net spread. A held card's duplicate is worth little to us, so its buy leg is usually NEGATIVE and only the
  pair is positive (`Crossing.legs`). If the organisers clip trades one by one, a negative leg may not be
  clipped like a positive one; `both_legs_positive` marks the crossings that are safe either way.
- Ring guard: a pair where one side keeps handing the other the whole pie scores as an even split, so the
  two makers must differ, and a repeated pair is the caller's to avoid.
- Duplicates: a held card bought to KEEP scores `value(one more copy) − cost` on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from bazaar_agent.agents.market import BoardOffer, Venue
from bazaar_agent.intel import TEAM_ID
from bazaar_agent.strategy import Market, copy_value

ValueFn = Callable[[str], float | None]  # our value of one more copy of a card ref; None: unknown


def leg_cost(venue: Venue, ask: int) -> int:
    """What accepting a standing ask costs us in all."""
    return ask + venue.fee(ask)


def leg_proceeds(venue: Venue, bid: int) -> int:
    """What accepting a standing bid brings us in all."""
    return bid - venue.fee(bid)


@dataclass(frozen=True)
class Crossing:
    """A standing ask we can buy now and a standing bid for the same card we can sell into next tick."""

    ask: BoardOffer
    bid: BoardOffer
    cost: int
    proceeds: int
    value: float | None = None  # our value of the copy in between (one more copy); None: not known

    @property
    def ref(self) -> str:
        return self.ask.ref

    @property
    def net(self) -> int:
        return self.proceeds - self.cost

    @property
    def gross(self) -> int:
        return self.bid.price - self.ask.price

    @property
    def legs(self) -> tuple[float, float] | None:
        """(buy leg, resale leg) at our private values: `value − cost`, `proceeds − value`."""
        if self.value is None:
            return None
        return round(self.value - self.cost, 2), round(self.proceeds - self.value, 2)

    @property
    def both_legs_positive(self) -> bool:
        legs = self.legs
        return legs is not None and min(legs) >= 0


def crossings(
    offers: Iterable[BoardOffer],
    venues: dict[str, Venue],
    *,
    exclude: set[int] | frozenset[int] = frozenset(),
    min_net: int = 1,
    value: ValueFn | None = None,
    known_makers: bool = False,
) -> list[Crossing]:
    """Every (ask, bid) pair for the same card, on any venues we may trade on, whose net after both fees is
    at least `min_net`; the two makers differ and none of the offers is in `exclude` (ours). With
    `known_makers`, both makers must be team ids (a board pseudonym the feed did not resolve may hide the
    same team on both sides: a ring). Best net first; an ask appears once per bid it crosses."""
    asks: dict[str, list[BoardOffer]] = {}
    bids: dict[str, list[BoardOffer]] = {}
    for o in offers:
        if o.id in exclude or o.venue not in venues:
            continue
        (asks if o.side == "ask" else bids).setdefault(o.ref, []).append(o)
    out = []
    for ref, ref_asks in asks.items():
        worth = value(ref) if value is not None and ref in bids else None
        for a in ref_asks:
            cost = leg_cost(venues[a.venue], a.price)
            for b in bids.get(ref, []):
                if b.maker == a.maker or (known_makers and not (TEAM_ID.match(a.maker) and TEAM_ID.match(b.maker))):
                    continue
                c = Crossing(a, b, cost, leg_proceeds(venues[b.venue], b.price), worth)
                if c.net >= min_net:
                    out.append(c)
    return sorted(out, key=lambda c: (-c.net, not c.both_legs_positive, c.ask.id, c.bid.id))


def best_per_ask(found: Iterable[Crossing]) -> list[Crossing]:
    """One crossing per ask and per bid, greedily by net: one card fills one bid."""
    used_asks: set[int] = set()
    used_bids: set[int] = set()
    out = []
    for c in sorted(found, key=lambda c: (-c.net, c.ask.id, c.bid.id)):
        if c.ask.id in used_asks or c.bid.id in used_bids:
            continue
        used_asks.add(c.ask.id)
        used_bids.add(c.bid.id)
        out.append(c)
    return out


@dataclass(frozen=True)
class DupBuy:
    """A standing ask for a card we already hold, worth buying again to keep."""

    ask: BoardOffer
    held: int  # copies we hold now
    cost: int
    value: float  # our value of one more copy

    @property
    def surplus(self) -> float:
        return round(self.value - self.cost, 2)


def dup_buys(
    offers: Iterable[BoardOffer],
    venues: dict[str, Venue],
    held: dict[str, int],
    value: ValueFn,
    *,
    min_surplus: float,
    exclude: set[int] | frozenset[int] = frozenset(),
) -> list[DupBuy]:
    """Asks for cards we hold whose next copy is worth at least `min_surplus` more to us than their cost
    (ask + fee). The cheapest per card, best surplus first."""
    best: dict[str, DupBuy] = {}
    for o in offers:
        if o.side != "ask" or o.id in exclude or o.venue not in venues or held.get(o.ref, 0) < 1:
            continue
        worth = value(o.ref)
        if worth is None:
            continue
        d = DupBuy(o, held[o.ref], leg_cost(venues[o.venue], o.price), worth)
        if d.surplus >= min_surplus and (o.ref not in best or d.cost < best[o.ref].cost):
            best[o.ref] = d
    return sorted(best.values(), key=lambda d: (-d.surplus, d.ask.id))


# ---------------------------------------------------------------- the live scan (read-only)


@dataclass(frozen=True)
class Scan:
    crossings: list[Crossing]  # one per ask and per bid, best net first
    dups: list[DupBuy]
    boards: int  # venues read
    offers: int  # plain offers on them
    near_crossings: list[Crossing]  # the closest pairs below min_net (how far the market is from paying)
    near_dups: list[DupBuy]  # the closest duplicate asks below min_surplus
    sell_ratio: float = 1.0  # sell_min_value_ratio: a resale below it is never taken


def why_not(c: Crossing, sell_ratio: float) -> str:
    """Why the taker would not take this crossing even with `arb_enabled` (empty: it would try)."""
    if not (TEAM_ID.match(c.ask.maker) and TEAM_ID.match(c.bid.maker)):
        return "maker unknown"
    if c.value is not None and c.proceeds < c.value * sell_ratio:
        return "worth more kept"
    if c.ask.asset_id is None:
        return "no asset id"
    return ""


def next_copy_values(market: Market) -> ValueFn:
    """Our value of one more copy (`/api/me/value`): book × affinity × the copy marginal for what we hold."""

    def value(ref: str) -> float | None:
        card = market.cards.get(ref)
        return None if card is None else round(copy_value(market, card, market.held.get(ref, 0)), 2)

    return value


def scan(
    market: Market,
    venues: dict[str, Venue],
    offers: list[BoardOffer],
    *,
    ours: set[int] | frozenset[int],
    min_net: int,
    min_surplus: float,
    near: int = 0,
    sell_ratio: float = 1.0,
) -> Scan:
    """Live crossings (net ≥ `min_net` after both fees) and duplicate buys (surplus ≥ `min_surplus`), and the
    `near` closest of each below those bars."""
    value = next_copy_values(market)
    found = crossings(offers, venues, exclude=ours, min_net=-(10**9), value=value)
    takeable = best_per_ask(c for c in found if not why_not(c, sell_ratio))  # the taker's pairing: eligible first
    used = {c.ask.id for c in takeable} | {c.bid.id for c in takeable}
    rest = best_per_ask(c for c in found if why_not(c, sell_ratio) and not {c.ask.id, c.bid.id} & used)
    every = sorted(takeable + rest, key=lambda c: (-c.net, c.ask.id, c.bid.id))
    all_dups = dup_buys(offers, venues, market.held, value, min_surplus=-(10.0**9), exclude=ours)
    return Scan(
        [c for c in every if c.net >= min_net],
        [d for d in all_dups if d.surplus >= min_surplus],
        len(venues),
        len(offers),
        [c for c in every if c.net < min_net][:near],
        [d for d in all_dups if d.surplus < min_surplus][:near],
        sell_ratio,
    )


def _crossing_rows(found: list[Crossing], sell_ratio: float) -> list[str]:
    rows = [
        "| card | buy (venue, maker) | cost | sell (venue, maker) | proceeds | net | legs at our values | taker |",
        "|---|---|---:|---|---:|---:|---|---|",
    ]
    for c in found:
        legs = c.legs
        leg = "-" if legs is None else f"{legs[0]:+g} / {legs[1]:+g}" + ("" if c.both_legs_positive else " ⚠")
        rows.append(
            f"| {c.ref} | {c.ask.price} on {c.ask.venue} ({c.ask.maker}) | {c.cost} "
            f"| {c.bid.price} on {c.bid.venue} ({c.bid.maker}) | {c.proceeds} | {c.net:+d} | {leg} "
            f"| {why_not(c, sell_ratio) or 'would take'} |"
        )
    return rows


def render_scan(s: Scan, tick: int | None, min_net: int, min_surplus: float) -> str:
    lines = [
        f"# Arbitrage scan{f' at tick {tick}' if tick is not None else ''}: {s.boards} venue(s), "
        f"{s.offers} plain offer(s)",
        "",
        f"## Crossings (net ≥ {min_net} P after both fees; buy now, sell into the bid next tick)",
        "",
    ]
    if s.crossings:
        lines += [
            *_crossing_rows(s.crossings, s.sell_ratio),
            "",
            "⚠ a leg scores negative on its own: the net holds only if trades are not clipped one by one.",
        ]
    else:
        lines.append("None.")
    if s.near_crossings:
        lines += ["", "Closest below the bar:", "", *_crossing_rows(s.near_crossings, s.sell_ratio)]
    lines += ["", f"## Duplicate buys (one more copy − ask − fee ≥ {min_surplus:g} P)", ""]
    if s.dups:
        lines += [
            "| card | ask (venue, maker) | cost | held | value of one more | surplus |",
            "|---|---|---:|---:|---:|---:|",
        ]
        lines += [
            f"| {d.ask.ref} | {d.ask.price} on {d.ask.venue} ({d.ask.maker}) | {d.cost} | {d.held} | {d.value:g} "
            f"| {d.surplus:+g} |"
            for d in s.dups
        ]
    else:
        lines.append("None.")
    if s.near_dups:
        lines += [
            "",
            "Closest below the bar:",
            "",
            "| card | ask (venue) | cost | held | value of one more | surplus |",
        ]
        lines += ["|---|---|---:|---:|---:|---:|"]
        lines += [
            f"| {d.ask.ref} | {d.ask.price} on {d.ask.venue} | {d.cost} | {d.held} | {d.value:g} | {d.surplus:+g} |"
            for d in s.near_dups
        ]
    return "\n".join(lines) + "\n"
