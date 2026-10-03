import pytest

from bazaar_agent.agents.dealer import (
    BidPlan,
    Move,
    Negotiation,
    apply_advice,
    decide,
    latest_dealer_offer,
    reopen_start,
    settled_price,
    words,
)


def neg(start=6, step=1, max_price=10, bids=(), opened=None):
    """`opened=(ask, bids_sent_before_it)`: her opening ask, already seen and countered."""
    n = Negotiation(BidPlan(start, step, max_price), list(bids))
    if opened is not None:
        n.opening_ask, n.lowest_ask, n.bids_at_opening = opened[0], opened[0], opened[1]
    return n


def test_opens_with_the_start_bid_when_she_has_no_offer():
    assert decide(neg(), None, None, False) == Move("bid", 6, reason="small distinct step up")


def test_accepts_when_her_ask_meets_our_next_bid():
    assert decide(neg(bids=[6, 7, 8], opened=(12, 1)), 9, 55, False).kind == "accept"


def test_keeps_stepping_while_her_ask_is_above_our_next_bid():
    move = decide(neg(bids=[6, 7]), 10, 55, False)
    assert (move.kind, move.price) == ("bid", 8)


def test_takes_a_final_below_her_opening_inside_the_limit_and_walks_from_one_above_it():
    assert decide(neg(bids=[6, 7], opened=(12, 1)), 10, 9, True) == Move("accept", 10, 9, "final within limit")
    above = decide(neg(bids=[6, 7], opened=(12, 1)), 11, 9, True)
    assert above.kind == "walk" and not above.reopen  # above our limit: no lower thread to try


def test_never_repeats_a_price_and_walks_when_the_limit_is_spent():
    n = neg(bids=[6, 8, 10], step=2, opened=(14, 1))
    assert n.next_bid() is None
    assert decide(n, 12, 3, False).kind == "walk"
    assert decide(n, 10, 3, False).kind == "accept"  # her ask came down to our limit


def test_bad_plans_are_refused():
    with pytest.raises(ValueError):
        BidPlan(start=12, step=1, max_price=10)


def test_jev_can_accept_earlier_but_never_above_the_limit():
    n = neg(bids=[6], opened=(12, 0))
    bid = decide(n, 9, 4, False)
    assert apply_advice(bid, "accept", n, 9, 4).kind == "accept"
    assert apply_advice(bid, "accept", n, 11, 4).kind == "bid"
    assert apply_advice(bid, "counter", n, 9, 4) == bid


def test_latest_dealer_offer_reads_structure_not_words():
    thread = {
        "standing_offers": [
            {"id": 1, "maker": "abuela", "status": "expired", "want": {"cash": 12}},
            {"id": 2, "maker": "t01", "status": "open", "give": {"cash": 7}},
            {"id": 3, "maker": "abuela", "status": "open", "want": {"cash": 10}, "final": True},
        ]
    }
    assert latest_dealer_offer(thread, "abuela") == (10, 3, True)
    assert latest_dealer_offer({"standing_offers": []}, "abuela") == (None, None, False)


def test_an_opening_ask_below_our_start_is_countered_not_accepted():
    n = Negotiation(BidPlan(18, 1, 20))
    assert decide(n, 17, 5, False) == Move("bid", 16, reason="counter below her unconceded ask 17")
    n.bids.append(16)
    assert decide(n, 16, 6, False) == Move("accept", 16, 6, "ask meets our next bid")  # she came down
    wide = Negotiation(BidPlan(18, 3, 20))
    assert decide(wide, 17, 5, False) == Move("bid", 14, reason="counter below her unconceded ask 17")
    wide.bids.append(14)
    assert decide(wide, 17, 5, False) == Move("bid", 15, reason="counter below her unconceded ask 17")


def test_a_welcome_offer_is_never_taken_on_the_first_tick():
    assert decide(neg(start=6, max_price=12), 9, 9, False) == Move("bid", 6, reason="small distinct step up")
    assert decide(neg(start=10, step=2, max_price=12), 9, 9, False) == Move(
        "bid", 7, reason="counter below her unconceded ask 9"
    )


def test_lav03_replay_walks_from_her_opening_ask_and_reopens_lower():
    # The real LAV-03 thread 99: we bid 6, her opening ask was 7 and we took it. It settled at her opening
    # price, which scores nothing on the ladder and unlocks nothing. Now: walk, and reopen from 5.
    n = Negotiation(BidPlan(6, 1, 9), [6])
    assert decide(n, 7, 9, False) == Move(
        "walk", reason="she held her opening ask 7: no counter left below it", reopen=True
    )
    assert reopen_start(n) == 5
    spent = neg(bids=[8], max_price=8)  # spent at our max and her opening ask meets it: still never taken
    assert decide(spent, 8, 9, False).kind == "walk" and reopen_start(spent) == 7


def test_a_final_offer_at_her_opening_price_is_walked_and_reopened_lower():
    n = neg(start=8)
    assert decide(n, 9, 1, True) == Move(
        "walk", reason="her final 9 is her opening price: it scores nothing", reopen=True
    )
    assert reopen_start(n) == 7  # one step under our planned first bid
    assert decide(neg(start=8), 11, 1, True).kind == "walk"
    assert reopen_start(Negotiation(BidPlan(1, 1, 5))) is None  # nothing lower than 1 to try


def test_settled_price_reads_the_deal_from_the_thread_messages():
    # The real thread 101: our bids are offers 720, 732, 744 (cancelled when replaced) and 759 at 9 (settled).
    msgs = [
        {"offer": {"id": i, "status": s, "give": {"cash": p}}}
        for i, s, p in [(720, "cancelled", 6), (759, "settled", 9)]
    ]
    assert settled_price({"messages": [*msgs, {"offer": None}]}) == 9
    assert settled_price({"messages": [{"offer": {"status": "settled", "want": {"cash": 7}}}]}) == 7  # her ask
    assert settled_price({"messages": []}) is None


def test_jev_accept_on_her_opening_ask_is_overridden():
    n = neg()
    bid = decide(n, 9, 4, False)
    assert bid.kind == "bid"
    assert apply_advice(bid, "accept", n, 9, 4) == bid  # not countered, she has not come down
    n.bids.append(6)
    assert apply_advice(decide(n, 9, 4, False), "accept", n, 9, 4) == decide(n, 9, 4, False)  # countered only
    assert apply_advice(decide(n, 8, 5, False), "accept", n, 8, 5).kind == "accept"  # countered and conceded


def test_words_vary_with_each_step():
    assert words(0, 7) != words(1, 8) and "7" in words(0, 7)


class FakeDealerClient:
    """Abuela on a fake clock: the clock stays on a tick for `reads_per_tick` reads, then advances."""

    def __init__(self, asks, reads_per_tick=5, welcome=None):
        self.asks, self.reads_per_tick, self.reads = list(asks), reads_per_tick, 0
        self.sent, self.accepted, self.closed, self.status = [], [], False, "open"
        self.welcome = welcome  # an ask she posts as soon as the thread opens, before any bid

    def clock(self):
        self.reads += 1
        return {"tick": 100 + self.reads // self.reads_per_tick, "next_tick_in": 30, "tick_seconds": 60}

    def open_thread(self, dealer, topic):
        return {"id": 85}

    def thread(self, tid):
        n = len(self.sent)
        offers = []
        if self.accepted:
            self.status = "deal"
        elif 0 < n <= len(self.asks) or (n == 0 and self.welcome is not None):
            offers = [
                {
                    "id": 500 + n,
                    "maker": "abuela",
                    "status": "open",
                    "give": {"cash": 0, "types": ["card:LAV-03"]},
                    "want": {"cash": self.asks[n - 1] if n else self.welcome, "assets": [], "types": []},
                }
            ]
        return {"status": self.status, "standing_offers": offers}

    def say(self, tid, text, price):
        self.sent.append(price)

    def accept(self, offer_id):
        self.accepted.append(offer_id)

    def close_thread(self, tid):
        self.closed = True


def test_negotiate_sends_one_message_per_tick_even_when_the_clock_is_read_many_times():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[12, 10, 9])
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
    )
    assert client.sent == [6, 7, 8]  # one distinct bid per tick, never a repeat on the same tick
    assert (out.status, out.price, client.accepted, client.closed) == ("deal", 9, [503], False)


def test_negotiate_times_out_by_ticks_and_closes_the_thread():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[30] * 20)
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=3,
    )
    assert (out.status, len(client.sent), client.closed) == ("timeout", 3, True)


def test_a_guardrail_denial_turns_the_move_into_a_walk_and_deals_are_reported():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[12, 10, 9])
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        guard=lambda m, _tid: "cash_floor" if m.price and m.price >= 8 else None,
    )
    assert (client.sent, out.status, client.closed) == ([6, 7], "walked", True)

    deals = []
    client = FakeDealerClient(asks=[12, 10, 9])
    negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        on_deal=lambda price, tick, t: deals.append(price),
    )
    assert deals == [9]


def test_offer_terms_must_be_exactly_the_requested_item_for_cash_only():
    from bazaar_agent.agents.dealer import offer_terms_problem, requested_item

    real = {
        "give": {"cash": 0, "assets": [], "types": ["card:LAV-06"]},
        "want": {"cash": 29, "assets": [], "types": []},
    }
    assert offer_terms_problem(real, "LAV-06") is None
    assert "instead of exactly" in offer_terms_problem(real, "LAV-05")
    sneaky = {"give": {"types": ["card:LAV-06"]}, "want": {"cash": 9, "assets": [{"id": 3, "ref": "LAV-01"}]}}
    assert "our assets" in offer_terms_problem(sneaky, "LAV-06")
    swapped = {"give": {"types": ["card:LAV-01"]}, "want": {"cash": 9}}
    assert offer_terms_problem(swapped, "LAV-06")
    assert requested_item({"buy": {"pack": "sobre_barrio"}}) == "sobre_barrio"
    assert requested_item({"sell": {"assets": [1]}}) is None


def test_a_mismatched_offer_is_never_accepted():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[9, 9, 9])
    client.thread = lambda tid, c=client: {  # she quotes 9 but for a different card
        "status": "open",
        "standing_offers": [
            {"id": 777, "maker": "abuela", "status": "open", "give": {"types": ["card:LAV-01"]}, "want": {"cash": 9}}
        ],
    }
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=3,
    )
    assert client.accepted == [] and out.status == "timeout"


def test_an_accept_on_the_last_tick_waits_for_settlement_instead_of_timing_out():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[9, 6])  # she opens at 9, we counter 7, she comes down to 6 on tick 3 of 3
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=3,
    )
    assert (client.accepted, client.closed, out.status, out.price) == ([502], False, "deal", 6)


def test_a_broken_observer_never_changes_or_breaks_the_negotiation():
    from bazaar_agent.agents.dealer import Observer, negotiate

    class Exploding(Observer):
        def __getattribute__(self, name):
            if name in ("opened", "thread_read", "guardrail", "move", "refused", "finished"):

                def boom(*_a):
                    raise RuntimeError("tracing backend down")

                return boom
            return object.__getattribute__(self, name)

    logs: list[str] = []
    client = FakeDealerClient(asks=[12, 10, 9])
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=logs.append,
        sleep=lambda _: None,
        observer=Exploding(),
    )
    assert (client.sent, out.status, out.price) == ([6, 7, 8], "deal", 9)
    assert sum("tracing hook" in line for line in logs) == 1  # warned once, not every tick


def test_words_address_the_dealer_we_are_talking_to():
    texts = [words(step, 20, "chato") for step in range(6)]
    assert not any("Carmen" in t for t in texts)
    assert any("Chato" in t for t in texts)
    assert any("Carmen" in words(step, 9, "abuela") for step in range(6))
    assert all("Carmen" not in words(step, 9, "nuevo") for step in range(6))  # unknown dealer: neutral


def test_negotiate_counters_a_welcome_offer_instead_of_taking_it_on_the_first_tick():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[6], welcome=7)  # her welcome 7 is below our start 8
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(8, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
    )
    assert client.sent == [6]  # countered below her welcome, never accepted it with zero bids
    assert (client.accepted, out.status, out.price) == ([501], "deal", 6)


def test_negotiate_overrides_a_jev_accept_on_her_opening_ask():
    from bazaar_agent.agents.dealer import negotiate

    client = FakeDealerClient(asks=[9, 8])
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        advisor=lambda neg, ask, final: "accept",
    )
    assert client.sent == [6, 7]  # Jev said accept at her opening 9: we countered 7 instead
    assert (client.accepted, out.status, out.price) == ([502], "deal", 8)


def test_a_busy_accept_slot_bids_her_ask_instead_of_going_silent():
    from bazaar_agent.agents.dealer import negotiate

    class TakesOurBid(FakeDealerClient):
        def say(self, tid, text, price):
            standing = self.asks[len(self.sent) - 1] if self.sent else None
            super().say(tid, text, price)
            if standing is not None and price >= standing:  # our bid meets her ask: she takes it
                self.status = "deal"

    client = TakesOurBid(asks=[12, 10, 9])
    reserved: list[int] = []

    def reserve(move, clock):
        reserved.append(clock.tick)
        return False  # the duel player holds the team's accept this tick

    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        reserve=reserve,
    )
    assert client.sent == [6, 7, 8, 9] and client.accepted == [] and len(reserved) == 1  # bid her 9 that tick
    assert (client.closed, out.status, out.price) == (False, "deal", 9)


def test_a_busy_accept_slot_never_bids_her_opening_ask():
    from bazaar_agent.agents.dealer import meet_ask

    n = neg(bids=[8], opened=(12, 1))
    assert meet_ask(n, 9) == Move("bid", 9, reason="accept slot used: meet her ask")
    assert meet_ask(n, 12).kind == "wait" and meet_ask(n, 11).kind == "wait"  # her opening / above our max
    assert meet_ask(neg(bids=[8]), 9).kind == "wait"  # no opening seen: we cannot tell, so never


def test_negotiate_replays_lav03_and_walks_from_her_opening_ask_with_a_lower_reopen():
    from bazaar_agent.agents.dealer import negotiate

    logs: list[str] = []
    client = FakeDealerClient(asks=[7])  # we bid 6, then her non-final opening ask is 7
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 9),
        log=logs.append,
        sleep=lambda _: None,
    )
    assert client.sent == [6] and client.accepted == [] and client.closed
    assert (out.status, out.price, out.reopen_start) == ("walked", None, 5)
    assert any("→ walk  (she held her opening ask 7: no counter left below it)" in line for line in logs)


def test_with_no_ask_standing_we_never_bid_up_to_her_opening_ask():
    # pr-reviewer #72 row 2: opening 9, our 6, 7, 8, a tick with no standing offer of hers → we bid 9,
    # and if she takes it, the deal is at her opening price (scores nothing).
    assert decide(neg(bids=[6, 7, 8], opened=(9, 1)), None, None, False) == Move(
        "walk", reason="she held her opening ask 9: no bid left below it", reopen=True
    )
    wide = neg(start=6, step=5, max_price=20, bids=[6], opened=(9, 1))
    assert decide(wide, None, None, False) == Move("bid", 8, reason="capped below her opening ask 9")
    came_down = neg(step=5, max_price=20, bids=[6], opened=(12, 1))
    came_down.see_ask(10)  # she conceded to 10: a bid at 10 closes below her opening
    assert decide(came_down, None, None, False) == Move("bid", 10, reason="capped below her opening ask 12")
    assert decide(neg(), None, None, False).kind == "bid"  # before she named a price: no cap


class LapsingAbuela:
    """Her opening ask 7, held; her offers lapse 2 ticks after they are made (every dealer offer in the
    feed has expires - created = 2). Our bid at or above 7 she takes at once (security audit, v7)."""

    OPENING = 7

    def __init__(self, hold_ticks, proactive=True):
        self.tick, self.hold_ticks, self.status, self.deal = 1, hold_ticks, "open", None
        self.offers, self.msgs, self.next_id, self.sent, self.proactive = [], [], 100, [], proactive

    def _offer(self, ask):
        offer = {
            "id": self.next_id,
            "maker": "abuela",
            "status": "open",
            "final": False,
            "give": {"types": ["card:LAV-03"]},
            "want": {"cash": ask},
            "expires_tick": self.tick + 2,
        }
        self.offers = [offer]
        self.msgs.append({"sender": "abuela", "offer": offer})  # the message keeps it, whatever its status
        self.next_id += 1

    def open_thread(self, dealer, topic=None):
        if self.proactive:
            self._offer(self.OPENING)
        return {"id": 9}

    def clock(self):
        return {"tick": self.tick, "tick_seconds": 60, "next_tick_in": 50}

    def thread(self, tid):
        for o in self.offers:
            if o["expires_tick"] < self.tick:
                o["status"] = "expired"
        live = [o for o in self.offers if o["status"] == "open"]
        settled = [{"offer": {"status": "settled", "give": {"cash": self.deal}}}] if self.deal else []
        return {"status": self.status, "standing_offers": live, "messages": self.msgs + settled}

    def say(self, tid, text, price=None):
        self.sent.append((self.tick, "bid", price))
        if price >= self.OPENING:
            self.status, self.deal = "deal", price
        else:
            self._offer(self.OPENING)

    def accept(self, oid):
        self.sent.append((self.tick, "accept", oid))

    def close_thread(self, tid):
        self.sent.append((self.tick, "close", tid))
        self.status = "closed"


@pytest.mark.parametrize("proactive", [True, False])  # False: she answers only our first bid (thread 99)
@pytest.mark.parametrize("hold", [(), (2, 3, 4)])
def test_a_hold_that_lets_her_offer_lapse_never_ends_at_her_opening_ask(hold, proactive):
    from bazaar_agent.agents.dealer import negotiate

    a = LapsingAbuela(hold, proactive)
    out = negotiate(
        a,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: setattr(a, "tick", a.tick + 1),
        kill_switch=lambda: ("pause",) if a.tick in a.hold_ticks else (),
    )
    assert a.deal is None and [s[2] for s in a.sent if s[1] == "bid"] == [6]  # before the fix: bid 7, deal 7
    assert (out.status, out.reopen_start) == ("walked", 5)


@pytest.mark.parametrize("cash", [True, "17", 17.9, -5, 0, 10_000_001])
def test_a_dealer_price_must_be_whole_primas(cash):
    offer = {"id": 3, "maker": "abuela", "status": "open", "give": {"types": ["card:LAV-03"]}, "want": {"cash": cash}}
    assert latest_dealer_offer({"standing_offers": [offer]}, "abuela") == (None, None, False)
    assert settled_price({"messages": [{"offer": {**offer, "status": "settled"}}]}) is None


def test_a_second_bid_waits_for_her_first_ask():
    # In the feed her first ask lands a tick after the thread opens: a second bid before it is blind.
    assert decide(neg(bids=[6]), None, None, False) == Move("wait", reason="waiting for her first ask")


def test_a_counter_never_reaches_her_opening_even_if_she_raises_her_ask():
    n = neg(start=20, step=3, max_price=40, bids=[23], opened=(24, 1))
    move = decide(n, 26, 5, False)  # she raised to 26: before the fix we countered at 24, her opening
    assert move == Move("walk", reason="she held her opening ask 26: no counter left below it", reopen=True)


def test_at_the_cap_we_wait_for_her_answer_before_walking():
    from bazaar_agent.agents.dealer import see_history

    n = Negotiation(BidPlan(6, 1, 10), [6])
    ours = {"sender": "t01", "offer": {"maker": "t01", "status": "open", "give": {"cash": 6}}}
    hers = {
        "sender": "abuela",
        "offer": {"maker": "abuela", "status": "open", "give": {"types": ["card:LAV-03"]}, "want": {"cash": 7}},
    }
    see_history(n, {"messages": [ours, hers, ours]}, "abuela", "LAV-03")  # our last bid is not answered yet
    assert decide(n, 7, 9, False) == Move("wait", reason="her answer to our last bid is not in yet")
    see_history(n, {"messages": [ours, hers, ours, hers]}, "abuela", "LAV-03")  # she answered: held at 7
    assert decide(n, 7, 9, False).reopen


def test_her_answer_is_read_by_message_id_not_by_list_position():
    # The real GET /api/threads/187 lists a slow reply AFTER our next bid: ids 1145 us, 1159 us, 1153 her.
    from bazaar_agent.agents.dealer import see_history

    n = Negotiation(BidPlan(29, 1, 40), [29, 31])
    ask = {"maker": "chato", "status": "open", "give": {"types": ["card:SAL-09"]}, "want": {"cash": 33}}
    listed = [
        {"id": 1145, "sender": "t01"},
        {"id": 1159, "sender": "t01"},
        {"id": 1153, "sender": "chato", "offer": ask},
    ]
    see_history(n, {"messages": listed}, "chato", "SAL-09")
    assert n.awaiting_reply  # by id the last message is our 1159: her answer to it is not in yet


def test_the_waits_for_her_answer_are_bounded():
    n = Negotiation(BidPlan(6, 1, 10), [6])
    assert [decide(n, None, None, False).kind for _ in range(3)] == ["wait", "wait", "walk"]  # she never answers
    n = neg(bids=[6, 7, 8], opened=(9, 1))
    n.awaiting_reply = True  # held at her opening, our 8 unanswered
    assert [decide(n, 9, 5, False).kind for _ in range(3)] == ["wait", "wait", "walk"]


def test_at_our_max_we_wait_for_her_answer_before_walking():
    n = neg(start=12, max_price=14, bids=[12, 13, 14], opened=(17, 0))
    n.awaiting_reply = True  # our max 14 is unanswered: it may be a "Deal!"
    assert decide(n, 17, 5, False) == Move("wait", reason="her answer to our max bid is not in yet")
    n.awaiting_reply = False
    assert decide(n, 17, 5, False) == Move("walk", reason="no higher bid left inside our limit")


def test_a_refused_timeout_close_books_a_deal_that_landed_first():
    # pr-reviewer #72 round 3 (P2): her "Deal!" to our last bid lands between our last read and the timeout
    # close; the close is refused. Before: `dealer buy` died with a traceback and the deal was never booked.
    from bazaar_agent.agents.dealer import negotiate
    from bazaar_agent.sdk import BazaarError

    class LateDeal(FakeDealerClient):
        def close_thread(self, tid):
            self.status = "deal"
            raise BazaarError("thread_closed", "thread 85 is deal", 400)

        def thread(self, tid):
            if self.status == "deal":
                return {"status": "deal", "messages": [{"offer": {"status": "settled", "give": {"cash": 8}}}]}
            return super().thread(tid)

    booked: list[int] = []
    client = LateDeal(asks=[30] * 20)
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=3,
        on_deal=lambda price, tick, t_hours: booked.append(price),
    )
    assert (out.status, out.price, booked) == ("deal", 8, [8])


class CloseSaysDeal(FakeDealerClient):
    """Her "Deal!" lands just before our close, and the close is answered 200 with the ended thread, as our
    simulator does (bazaar_sim/threads.py close_thread): security audit #72 round 4, P2."""

    def close_thread(self, tid):
        self.status = "deal"
        return {"ok": True, "thread": tid, "status": "deal"}

    def thread(self, tid):
        if self.status == "deal":
            return {"status": "deal", "messages": [{"offer": {"status": "settled", "give": {"cash": 8}}}]}
        return super().thread(tid)


def test_a_timeout_close_answered_with_a_deal_books_it():
    from bazaar_agent.agents.dealer import negotiate

    booked: list[int] = []
    out = negotiate(
        CloseSaysDeal(asks=[30] * 20),
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=3,
        on_deal=lambda price, tick, t_hours: booked.append(price),
    )
    assert (out.status, out.price, booked) == ("deal", 8, [8])


def test_a_close_that_fails_and_a_thread_that_cannot_be_read_never_crash_dealer_buy():
    from bazaar_agent.agents.dealer import negotiate
    from bazaar_agent.sdk import BazaarError

    class Down(FakeDealerClient):
        def close_thread(self, tid):
            self.down = True
            raise BazaarError("network", "connection reset", 0)

        def thread(self, tid):
            if getattr(self, "down", False):
                raise BazaarError("network", "connection reset", 0)
            return super().thread(tid)

    lines: list[str] = []
    out = negotiate(
        Down(asks=[30] * 20),
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lines.append,
        sleep=lambda _: None,
        max_ticks=3,
    )
    assert out.status == "open" and any("check it by hand" in line for line in lines)


def test_a_failed_booking_after_a_late_deal_is_said_loudly_never_raised():
    from bazaar_agent.agents.dealer import negotiate

    def ledger_down(price, tick, t_hours):
        raise RuntimeError("ledger write failed (Postgres unreachable)")

    lines: list[str] = []
    out = negotiate(
        CloseSaysDeal(asks=[30] * 20),
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lines.append,
        sleep=lambda _: None,
        max_ticks=3,
        on_deal=ledger_down,
    )
    assert out.status == "deal" and any("NOT booked" in line for line in lines)


def test_a_late_deal_while_the_switch_holds_is_booked_on_the_way_out():
    # Bids 6 and 7 use the 2 ticks; the switch goes on after the second, so negotiate exits "held". Her "Deal!"
    # to our 7 lands meanwhile: the exit's one read books it (pr-reviewer #72 round 5: the old test never
    # reached its deal).
    from bazaar_agent.agents.dealer import negotiate

    class LateDealWhileHeld(FakeDealerClient):
        thread_reads = 0

        def thread(self, tid):
            self.thread_reads += 1
            if self.thread_reads >= 3:  # the exit's read
                return {"status": "deal", "messages": [{"offer": {"status": "settled", "give": {"cash": 7}}}]}
            return super().thread(tid)

    client = LateDealWhileHeld(asks=[30] * 20)
    booked: list[int] = []
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=2,
        kill_switch=lambda: ("pause",) if len(client.sent) >= 2 else (),
        on_deal=lambda price, tick, t_hours: booked.append(price),
    )
    assert client.sent == [6, 7] and not client.closed and (out.status, booked) == ("deal", [7])


def test_a_rate_limited_timeout_close_is_retried_on_the_next_tick_not_the_same_one():
    # pr-reviewer #72 round 5 (P1): the retry went out on the same tick, right after the 429.
    from bazaar_agent.agents.dealer import negotiate
    from bazaar_agent.sdk import BazaarError

    class Limited(FakeDealerClient):
        close_ticks: list[int] = []

        def close_thread(self, tid):
            self.close_ticks.append(100 + self.reads // self.reads_per_tick)
            if len(self.close_ticks) == 1:
                raise BazaarError("wait_for_tick", "too early", 429)
            self.closed = True
            return {"ok": True, "thread": tid, "status": "closed"}

    client = Limited(asks=[30] * 20)
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        max_ticks=2,
    )
    first, second = client.close_ticks
    assert second > first and out.status == "timeout" and client.closed


def test_no_second_close_waits_out_closed_doors():
    # Security audit #72 round 5: a 429 just before 23:00 made the retry wait for the next live tick (all night).
    from bazaar_agent.agents.dealer import negotiate
    from bazaar_agent.sdk import BazaarError

    class ClosingTime(FakeDealerClient):
        closes = 0

        def close_thread(self, tid):
            self.closes += 1
            self.doors = "closed"
            raise BazaarError("rate_limited", "slow down", 429)

        def clock(self):
            c = super().clock()
            return {**c, "doors": getattr(self, "doors", "open")}

    slept: list[float] = []
    client = ClosingTime(asks=[30] * 20)
    lines: list[str] = []
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lines.append,
        sleep=slept.append,
        max_ticks=2,
    )
    assert client.closes == 1 and out.status == "open" and max(slept, default=0) < 300
    assert any("no live tick for a second close" in line for line in lines)


def test_on_thread_sees_every_read_and_a_failing_inspector_never_breaks_the_deal():
    from bazaar_agent.agents.dealer import negotiate

    seen, lines = [], []

    def inspector(thread):
        seen.append(thread["status"])
        raise RuntimeError("boom")

    client = FakeDealerClient(asks=[12, 10, 9])
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lines.append,
        sleep=lambda _: None,
        on_thread=inspector,
    )
    assert (out.status, out.price) == ("deal", 9) and len(seen) >= 4
    assert any("offer inspection failed (RuntimeError)" in line for line in lines)


def test_the_accept_gate_runs_before_the_guard_and_a_refusal_never_accepts_nor_claims_the_slot():
    from bazaar_agent.agents.dealer import negotiate

    guarded, lines = [], []

    def guard(move, thread_id):
        guarded.append(move.kind)
        return None

    client = FakeDealerClient(asks=[12, 10, 9])
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=lines.append,
        sleep=lambda _: None,
        max_ticks=6,
        guard=guard,
        inspect=lambda thread, move: "block: it binds LAV-01",
    )
    assert client.accepted == [] and "accept" not in guarded and out.status != "deal"
    assert any("INSPECTOR refused the accept of offer" in line and "LAV-01" in line for line in lines)


def test_the_real_gate_lets_the_offer_we_priced_through():
    from bazaar_agent.agents.accept_gate import dealer_gate
    from bazaar_agent.agents.dealer import negotiate
    from bazaar_agent.agents.inspector import CardIndex

    topic = {"buy": {"card": "LAV-03"}}

    def inspect(thread, move):
        gate = dealer_gate(thread, "abuela", move.offer_id, move.price, topic, CardIndex.from_catalog({}))
        return None if gate.allowed else gate.reason

    client = FakeDealerClient(asks=[12, 10, 9])
    out = negotiate(client, "abuela", topic, BidPlan(6, 1, 10), log=print, sleep=lambda _: None, inspect=inspect)
    assert (out.status, out.price) == ("deal", 9) and client.accepted == [503]
