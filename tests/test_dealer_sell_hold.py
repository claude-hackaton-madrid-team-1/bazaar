"""A dealer sell holds at our floor while her bid is still rising, instead of walking 2-3 P from it (Sat 3 Oct:
Pilar's SAL-10 finals 65, 68, 69, 71). It walks once she repeats her bid, holds one bid too long, or names a final
below our floor; a final at or above the floor is taken."""

from __future__ import annotations

from typing import Any

from bazaar_agent.agents.dealer import MAX_WAITS, Move
from bazaar_agent.agents.dealer_sell import AskPlan, SellNegotiation, decide_sell, negotiate_sell

ASSET = 171
PLAN = AskPlan(72, 2, 68)  # asks 72, 70, 68: the floor is reached on the third ask


def at_floor() -> SellNegotiation:
    """Our asks already reached the floor (68); her bids 60 then 63 came in as offers 501, 502."""
    neg = SellNegotiation(PLAN, asks=[72, 70, 68])
    neg.see_bid(60, 501)  # her opening
    neg.see_bid(63, 502)
    return neg


def test_a_rising_bid_under_our_floor_holds_instead_of_walking():
    neg = at_floor()
    move = decide_sell(neg, 66, 503, False)  # 60 → 63 → 66, our floor 68
    assert move.kind == "wait" and "still rising" in move.reason and move.price is None
    move = decide_sell(neg, 67, 504, False)  # she came up again: a new rising offer, still held
    assert move.kind == "wait"


def test_a_repeated_bid_walks():
    neg = at_floor()
    assert decide_sell(neg, 66, 503, False).kind == "wait"
    move = decide_sell(neg, 66, 504, False)  # she repeated 66: stopped rising
    assert move == Move("walk", reason="no lower ask left above our floor")


def test_her_first_bid_is_not_rising_and_a_lower_bid_walks():
    neg = SellNegotiation(PLAN, asks=[72, 70, 68])
    assert decide_sell(neg, 60, 501, False).kind == "walk"  # nothing before it: not rising
    neg = at_floor()
    assert decide_sell(neg, 61, 503, False).kind == "walk"  # came down from 63


def test_one_rising_bid_is_held_at_most_max_waits_ticks():
    neg = at_floor()
    kinds = [decide_sell(neg, 66, 503, False).kind for _ in range(MAX_WAITS + 1)]
    assert kinds == ["wait"] * MAX_WAITS + ["walk"]


def test_a_final_at_or_above_the_floor_is_taken_and_one_below_it_walks():
    neg = at_floor()
    assert decide_sell(neg, 66, 503, False).kind == "wait"
    assert decide_sell(neg, 69, 504, True) == Move("accept", 69, 504, "final above our floor")
    neg = at_floor()
    assert decide_sell(neg, 66, 503, False).kind == "wait"
    below = decide_sell(neg, 67, 504, True)  # a FINAL, rising but below 68
    assert below.kind == "walk" and "below our floor" in below.reason


def test_awaiting_her_answer_to_our_floor_ask_is_the_old_patience_not_a_hold():
    neg = at_floor()
    neg.awaiting_reply = True  # our floor ask is the thread's last message
    moves = [decide_sell(neg, 63, 502, False) for _ in range(MAX_WAITS + 1)]
    assert [m.kind for m in moves] == ["wait"] * MAX_WAITS + ["walk"]
    assert "not in yet" in moves[0].reason


# ---------------------------------------------------------------- the CLI loop (negotiate_sell)


class ScriptedDealer:
    """A dealer whose thread, read by read (one read per tick), shows the bids in `script[k]` (cumulative offer
    ids 501, 502, ...; her message last). Accepting an offer settles it on the next read."""

    def __init__(self, script: list[list[tuple[int, bool]]]):
        self.script, self.reads, self.clock_reads = script, 0, 0
        self.sent: list[int] = []
        self.accepted: list[int] = []
        self.closed = False

    def clock(self) -> dict[str, Any]:
        self.clock_reads += 1
        return {"tick": 100 + self.clock_reads // 4, "next_tick_in": 30, "tick_seconds": 60}

    def open_thread(self, dealer: str, topic: dict[str, Any]) -> dict[str, Any]:
        return {"id": 85}

    def _offer(self, i: int, cash: int, final: bool) -> dict[str, Any]:
        want = {"assets": [{"id": ASSET}]}
        return {"id": 501 + i, "maker": "pilar", "status": "open", "give": {"cash": cash}, "want": want, "final": final}

    def thread(self, tid: int) -> dict[str, Any]:
        if self.accepted:
            return {"status": "deal", "messages": [], "standing_offers": []}
        bids = self.script[min(self.reads, len(self.script) - 1)]
        self.reads += 1
        offers = [self._offer(i, cash, final) for i, (cash, final) in enumerate(bids)]
        messages = [{"sender": "t01"}, *({"sender": "pilar", "offer": o} for o in offers)]
        return {"status": "open", "messages": messages, "standing_offers": offers[-1:]}

    def say(self, tid: int, text: str, price: int) -> None:
        self.sent.append(price)

    def accept(self, offer_id: int) -> None:
        self.accepted.append(offer_id)

    def close_thread(self, tid: int) -> dict[str, Any]:
        self.closed = True
        return {"status": "closed"}


def run(client: ScriptedDealer, **kw: Any):
    return negotiate_sell(client, "pilar", ASSET, PLAN, log=lambda _: None, sleep=lambda _: None, **kw)


RISING = [(60, False), (63, False), (66, False)]


def test_negotiate_sell_holds_at_the_floor_and_takes_her_final_above_it():
    client = ScriptedDealer(
        [[], RISING[:1], RISING[:2], RISING, [*RISING, (67, False)], [*RISING, (67, False), (69, True)]]
    )
    out = run(client)
    assert client.sent == [72, 70, 68]  # the floor ask is never re-sent
    assert (out.status, out.price, client.accepted, client.closed) == ("deal", 69, [505], False)


def test_negotiate_sell_walks_when_she_repeats_her_bid():
    client = ScriptedDealer([[], RISING[:1], RISING[:2], RISING, [*RISING, (66, False)]])
    out = run(client)
    assert (client.sent, out.status, client.accepted, client.closed) == ([72, 70, 68], "walked", [], True)


def test_negotiate_sell_hold_is_bounded_when_she_never_moves_again():
    client = ScriptedDealer([[], RISING[:1], RISING[:2], RISING])  # 66 stands forever
    out = run(client)
    assert (client.sent, out.status, client.closed) == ([72, 70, 68], "walked", True)
    assert out.ticks == 4 + MAX_WAITS  # three asks, MAX_WAITS holds, then the walk
