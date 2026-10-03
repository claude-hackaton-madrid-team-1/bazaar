"""Every dealer move in the public feed as a `trader_behaviors` row: open, concede, hold, final, deal.

One row per dealer message (and one per fill), keyed by the feed event, so re-reading the feed adds
nothing. `our_price` is the team's latest bid before the dealer answered (any team's: the feed is
public), `source` is `ours` for our own threads and `feed` for other teams'. Structure only: the
dealer's words are never stored here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from bazaar_agent.feed import Event
from bazaar_agent.intel import dealer_threads, offer_price


@dataclass(frozen=True)
class BehaviourRow:
    trader_id: str
    thread_id: int
    tick: int
    event: str  # open | concede | hold | counter | final | deal
    our_price: int | None
    their_price: int | None
    step: int  # the dealer's n-th priced message in the thread (1-based); fills use the last step
    final: bool
    source: str  # ours | feed
    dedupe_key: str

    def as_tuple(self) -> tuple[Any, ...]:
        return (
            self.trader_id,
            self.thread_id,
            self.tick,
            self.event,
            self.our_price,
            self.their_price,
            self.step,
            self.final,
            self.source,
            self.dedupe_key,
        )


INSERT = (
    "insert into trader_behaviors (trader_id, thread_id, tick, event, our_price, their_price, step, final, source, "
    "dedupe_key) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (dedupe_key) do nothing"
)


def _event(previous: int | None, price: int, final: bool) -> str:
    if final:
        return "final"
    if previous is None:
        return "open"
    return "concede" if price < previous else "hold" if price == previous else "counter"


def behaviour_rows(events: Iterable[Event], us: str | None) -> list[BehaviourRow]:
    """Dealer moves per thread, in feed order, plus one `deal` row per fill."""
    events = sorted(events, key=lambda e: int(e.get("id", 0)))
    meta: dict[int, tuple[str, str]] = {}  # thread -> (dealer, team)
    last_bid: dict[int, int] = {}
    last_ask: dict[int, int] = {}
    steps: dict[int, int] = {}
    rows: list[BehaviourRow] = []
    for e in events:
        p = e.get("payload") or {}
        if p.get("kind") != "persona":
            continue
        thread = p.get("thread")
        if not isinstance(thread, int):
            continue
        if e.get("type") == "thread.opened":
            meta[thread] = (str(p.get("with")), str(p.get("team")))
            continue
        if e.get("type") != "thread.message" or thread not in meta:
            continue
        dealer, team = meta[thread]
        offer = p.get("offer") or {}
        price = offer_price(offer) if offer else None
        if price is None:
            continue
        if p.get("sender") != dealer:
            last_bid[thread] = price
            continue
        final = bool(offer.get("final"))
        steps[thread] = steps.get(thread, 0) + 1
        rows.append(
            BehaviourRow(
                dealer,
                thread,
                int(e.get("tick") or 0),
                _event(last_ask.get(thread), price, final),
                last_bid.get(thread),
                price,
                steps[thread],
                final,
                "ours" if us and team == us else "feed",
                f"feed:{e.get('id')}",
            )
        )
        last_ask[thread] = price
    for t in dealer_threads(events, us):
        if t.fill_price is None or t.fill_tick is None or t.thread not in meta:
            continue
        rows.append(
            BehaviourRow(
                t.dealer,
                t.thread,
                t.fill_tick,
                "deal",
                t.team_prices[-1] if t.team_prices else None,
                t.fill_price,
                steps.get(t.thread, 0),
                False,
                "ours" if t.ours else "feed",
                f"fill:{t.thread}",
            )
        )
    return rows
