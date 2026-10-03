"""Backtest a ladder plan against dealers fitted to every team's real conversations.

A `DealerModel` is one dealer × price class × opening ask, fitted from the feed (`ladder.conversations`):
its secret limits (bounds per closed conversation), its first counter, its later steps, its patience
(our bids before it names a final) and whether it opens before we speak. What the threads showed:
Abuela opens, drops 2–5 on her first counter, then 0–2 per round, names a final after a median of
five bids, and takes our bid the moment it reaches her limit. El Chato holds the first move, then
moves one per small step and matches a big one (the simulator's rule, #55, on the same threads).

`play()` runs OUR side through the real `agents.dealer.decide()` (never a copy of its rules), one move
per tick, against an `Episode` drawn from a model, or rebuilt from one real conversation
(`episode_from`). Pure functions with an explicit `random.Random`, so every number is reproducible.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Any

from bazaar_agent.agents.dealer import BidPlan, Negotiation, decide
from bazaar_agent.ladder import Conversation, limit_bounds, limit_point

DEFAULT_PATIENCE = 5  # median bids before Abuela's final on Friday (pack, uncommon)


@dataclass(frozen=True)
class DealerModel:
    dealer: str
    price_class: str
    opening: int
    limits: tuple[tuple[int, int], ...]  # (lo, hi) of the secret limit per closed conversation
    first_drops: tuple[int, ...]
    later_drops: tuple[int, ...]  # moves on later counters after we raised (0 = she held)
    patience: tuple[int, ...]
    opens_first: float  # share of conversations where the dealer spoke before the team
    matches_moves: bool = False  # El Chato: hold the first move, 1 per small step, match a big one


def _counters(c: Conversation) -> list[tuple[int, int, bool]]:
    """(ask before, ask after, final) for each dealer counter that answered at least one team price."""
    out, ask, raised = [], None, False
    for t in c.turns:
        if t.dealer and t.price is not None:
            if ask is not None and raised:
                out.append((ask, t.price, t.final))
            ask, raised = t.price, False
        elif not t.dealer and t.price is not None:
            raised = True
    return out


def fit(convs: Iterable[Conversation], dealer: str, price_class: str, opening: int) -> DealerModel:
    """A model of one regime (dealer × price class × opening ask) from every team's conversations."""
    members = [
        c
        for c in convs
        if c.dealer == dealer and c.price_class == price_class and c.opening == opening and c.side == "buy"
    ]
    limits: list[tuple[int, int]] = []
    first: list[int] = []
    later: list[int] = []
    for c in members:
        lo, hi, _ = limit_bounds(c)
        if limit_point(c) is not None and hi is not None:
            limits.append((min(lo if lo is not None else hi, hi), hi))
        counters = _counters(c)
        if counters:
            first.append(counters[0][0] - counters[0][1])
            later.extend(before - after for before, after, _ in counters[1:])
    patience = [p for c in members if (p := c.patience) is not None]
    spoke = [c for c in members if c.turns]
    return DealerModel(
        dealer=dealer,
        price_class=price_class,
        opening=opening,
        limits=tuple(limits),
        first_drops=tuple(d for d in first if d >= 0) or (1,),
        later_drops=tuple(d for d in later if d >= 0) or (1,),
        patience=tuple(patience) or (DEFAULT_PATIENCE,),
        opens_first=sum(c.turns[0].dealer for c in spoke) / len(spoke) if spoke else 0.5,
        matches_moves=dealer == "chato",
    )


@dataclass(frozen=True)
class Episode:
    """One conversation's hidden state: what the dealer will do, fixed before we speak."""

    opening: int
    limit: int
    patience: int
    first_drop: int
    later_drops: tuple[int, ...]  # cycled when the conversation runs longer
    opens_first: bool
    matches_moves: bool = False


def draw(model: DealerModel, rng: random.Random) -> Episode:
    lo, hi = rng.choice(model.limits)
    return Episode(
        opening=model.opening,
        limit=rng.randint(lo, hi),
        patience=rng.choice(model.patience),
        first_drop=rng.choice(model.first_drops),
        later_drops=tuple(rng.choice(model.later_drops) for _ in range(16)),
        opens_first=rng.random() < model.opens_first,
        matches_moves=model.matches_moves,
    )


def episode_from(c: Conversation, *, at: str = "hi") -> Episode | None:
    """Replay one real conversation: its own opening, first counter, later steps and patience, and its
    limit at the top (`hi`, the price that closed it) or the bottom (`lo`) of what its turns allow."""
    lo, hi, _ = limit_bounds(c)
    if limit_point(c) is None or hi is None or c.opening is None:
        return None
    counters = _counters(c)
    later = tuple(max(0, b - a) for b, a, _ in counters[1:]) or (1,)
    return Episode(
        opening=c.opening,
        limit=hi if at == "hi" or lo is None else min(lo, hi),
        patience=c.patience if c.patience is not None else max(DEFAULT_PATIENCE, len(c.team_prices)),
        first_drop=max(0, counters[0][0] - counters[0][1]) if counters else 1,
        later_drops=later,
        opens_first=bool(c.turns) and c.turns[0].dealer,
        matches_moves=c.dealer == "chato",
    )


@dataclass(frozen=True)
class Result:
    price: int | None  # None: no deal
    ticks: int  # ticks from opening the thread to the settlement (or to giving up)
    bids: tuple[int, ...]
    limit: int
    opening: int
    how: str  # "our_bid", "her_ask", "her_final", "walk", "timeout"

    @property
    def share(self) -> float:
        """Captured share of this conversation's range (opening → secret limit); no deal captures 0."""
        if self.price is None or self.opening <= self.limit:
            return 0.0
        return max(0.0, min(1.0, (self.opening - self.price) / (self.opening - self.limit)))

    @property
    def repeated(self) -> bool:
        return any(b <= a for a, b in zip(self.bids, self.bids[1:], strict=False))


class _Dealer:
    """The dealer's side of one episode: answers each team price, one counter per message."""

    def __init__(self, ep: Episode) -> None:
        self.ep = ep
        self.ask: int | None = ep.opening if ep.opens_first else None
        self.final = False
        self.bids = 0
        self.counters = 0
        self.last_bid: int | None = None
        self.offer_id = 1

    def answer(self, bid: int) -> str | None:
        """'deal' when it takes our price, 'walk' when it leaves, None when it counters."""
        ep, step = self.ep, bid - self.last_bid if self.last_bid is not None else 0
        self.bids += 1
        self.last_bid = bid
        if bid >= ep.limit or (self.ask is not None and bid >= self.ask):
            return "deal"
        if self.final:
            return "walk"
        if self.ask is None:  # it answers our first price with its opening ask
            self.ask = ep.opening
        else:
            self.counters += 1
            if ep.matches_moves:
                give = 0 if self.counters <= 1 else (step if step >= 4 else 1) if step > 0 else 0
            elif self.counters == 1:
                give = ep.first_drop
            else:
                give = ep.later_drops[(self.counters - 2) % len(ep.later_drops)] if step > 0 else 0
            self.ask = max(ep.limit, self.ask - give)
        if self.bids >= ep.patience:
            self.final = True
        self.offer_id += 1
        return None


def play(plan: BidPlan, ep: Episode, *, max_ticks: int = 14) -> Result:
    """Our `decide()` against one episode, one move per tick. An accept settles on the next tick."""
    neg, dealer = Negotiation(plan), _Dealer(ep)
    for tick in range(1, max_ticks + 1):
        offer = dealer.offer_id if dealer.ask is not None else None
        move = decide(neg, dealer.ask, offer, dealer.final)
        if move.kind == "accept" and move.price is not None:
            how = "her_final" if dealer.final else "her_ask"
            return Result(move.price, tick + 1, tuple(neg.bids), ep.limit, ep.opening, how)
        if move.kind == "walk":
            return Result(None, tick, tuple(neg.bids), ep.limit, ep.opening, "walk")
        if move.kind == "bid" and move.price is not None:
            neg.bids.append(move.price)
            verdict = dealer.answer(move.price)
            if verdict == "deal":
                return Result(move.price, tick + 1, tuple(neg.bids), ep.limit, ep.opening, "our_bid")
            if verdict == "walk":
                return Result(None, tick, tuple(neg.bids), ep.limit, ep.opening, "walk")
    return Result(None, max_ticks, tuple(neg.bids), ep.limit, ep.opening, "timeout")


@dataclass(frozen=True)
class Summary:
    runs: int
    mean_share: float  # no deal counts as zero
    deal_rate: float
    fill_within: float  # deals settled within `within` ticks, over all runs
    mean_price: float | None
    mean_ticks: float | None  # of the deals
    repeated: int  # runs that sent the same (or a lower) price twice
    at_opening: int  # deals at the dealer's opening ask (they do not count for unlocking)
    hows: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def summarise(results: Sequence[Result], *, within: int = 8) -> Summary:
    deals = [r for r in results if r.price is not None]
    hows: dict[str, int] = {}
    for r in results:
        hows[r.how] = hows.get(r.how, 0) + 1
    n = max(1, len(results))
    return Summary(
        runs=len(results),
        mean_share=mean(r.share for r in results) if results else 0.0,
        deal_rate=len(deals) / n,
        fill_within=sum(r.ticks <= within for r in deals) / n,
        mean_price=mean(r.price for r in deals if r.price is not None) if deals else None,
        mean_ticks=mean(r.ticks for r in deals) if deals else None,
        repeated=sum(r.repeated for r in results),
        at_opening=sum(r.price == r.opening for r in deals),
        hows=dict(sorted(hows.items())),
    )


def backtest(plan: BidPlan, model: DealerModel, *, runs: int = 2000, seed: int = 0, max_ticks: int = 14) -> Summary:
    rng = random.Random(seed)
    return summarise([play(plan, draw(model, rng), max_ticks=max_ticks) for _ in range(runs)])
