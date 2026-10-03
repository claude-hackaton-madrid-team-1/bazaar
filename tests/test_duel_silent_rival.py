"""A rival that never prices: v2's free ladder must reach our floor before the deadline, not on it.

Duels I (Sat 3 Oct): in every silent-rival duel the last offer we sent (D − 1) was still ~9 % off our limit, because the
ladder measured its progress to the deadline tick, which we never send. `silent_floor_lead` puts our floor on the last
that many ticks we send and leaves every earlier offer where it was (#215 review: Duels I's silent deals closed at
D − 2, so moving D − 3/D − 2 would only give surplus away). Generic numbers here: no real limit in a committed file.
"""

import random

from bazaar_agent import guardrails as gr
from bazaar_agent.agents.duel_v2 import (
    DEFAULTS,
    V2Params,
    _offer,
    counter_offer,
    duel_plan,
    first_offer_wait,
    jittered,
    payload_start,
    plan_moves,
    value_of,
)
from bazaar_agent.agents.duelist import duel_action, our_target
from tests.test_duel_v2 import duel

DEADLINE, SEEN = 116, 100  # a 16-tick duel, first seen at its start
LEGACY = V2Params(silent_floor_lead=0)  # the ladder measured to the deadline, as before the fix
CTX = gr.Context(cash=0, held={}, tick=0, t_hours=0)


def play_silent(role="seller", limit=100, params=DEFAULTS, issues=("price",), weight=None, upto=DEADLINE - 1):
    """Our offers in a duel whose rival never says anything, one runner from first sight to `upto`: {tick: move}."""
    d = duel(role=role, limit=limit, deadline=DEADLINE, issues=issues, weight=weight)
    sent = {}
    for tick in range(SEEN, upto + 1):
        move = plan_moves([d], tick, {1: SEEN}, params)[1]
        if move.kind == "offer":
            sent[tick] = move
            d["messages"].append({"tick": tick, "from": "you", "price": move.price, "days": move.days})
            d["your_offer"] = {"price": move.price, "days": move.days or 0}
    return sent, d


def surplus_share(role, limit, price):
    return (price - limit) / limit if role == "seller" else (limit - price) / limit


def test_a_silent_rival_sees_our_floor_on_the_last_tick_we_send():
    for role, limit in (("seller", 100), ("buyer", 100), ("seller", 37), ("buyer", 211)):
        floor = our_target(limit, role, 1.0)
        old, _ = play_silent(role, limit, LEGACY)
        new, _ = play_silent(role, limit)
        assert old[DEADLINE - 1].price != floor  # before: still ~9 % off our limit on D − 1
        assert surplus_share(role, limit, old[DEADLINE - 1].price) > 0.08
        assert new[DEADLINE - 1].price == floor, (role, limit)  # after: the floor goes out on D − 1
        assert set(new) == set(old)  # the same ticks: the first offer still waits for the rival to open first
        for tick, move in new.items():
            assert move.kind == "offer" and move.days is None and "no round" in move.reason
            assert gr.check(duel_action(duel(role=role, limit=limit), move), CTX, gr.Guardrails()).allowed
            # never further from the limit than before, and never past the floor
            assert surplus_share(role, limit, floor) <= surplus_share(role, limit, move.price), tick
            assert surplus_share(role, limit, move.price) <= surplus_share(role, limit, old[tick].price), tick
            if tick < DEADLINE - 1:
                assert move == old[tick], (role, limit, tick)  # D − 2 and earlier: today's offer exactly
        prices = [new[t].price for t in sorted(new)]
        assert prices == sorted(prices, reverse=role == "seller")  # monotone toward our limit, no step back


def test_a_longer_lead_holds_the_floor_from_that_tick_on():
    for role in ("seller", "buyer"):
        floor = our_target(100, role, 1.0)
        sent, _ = play_silent(role, 100, V2Params(silent_floor_lead=3))
        assert [sent[t].price for t in (DEADLINE - 3, DEADLINE - 2, DEADLINE - 1)] == [floor] * 3
        assert sent[DEADLINE - 4].price != floor


def test_price_and_days_against_a_silent_rival_keep_days_in_every_message():
    for role in ("seller", "buyer"):
        floor = our_target(100, role, 1.0)
        worst, _ = play_silent(role, 100, issues=("price", "days"), weight=2.5)
        assert all(m.days == 0 for m in worst.values())  # worst case: 0 days, as #60
        assert worst[DEADLINE - 1].price == floor
        signed, d = play_silent(role, 100, V2Params(days_signed=True), issues=("price", "days"), weight=2.5)
        assert all(m.days == 10 for m in signed.values())  # signed, a positive weight: the end that pays
        last = signed[DEADLINE - 1]
        assert value_of(d, last.price, last.days, signed=True) == floor  # repriced: our VALUE lands on the floor
        rules = gr.Guardrails(duel_policy="v2", duel_days_signed=True)
        for move in signed.values():
            assert gr.check(duel_action(d, move), CTX, rules).allowed
        unsigned, _ = play_silent(role, 100, V2Params(days_signed=True), issues=("price", "days"), weight=-2.5)
        assert all(m.days == 0 for m in unsigned.values()) and unsigned[DEADLINE - 1].price == floor


def test_a_restarted_runner_carries_on_the_same_ladder():
    """Every merge to main redeploys `duel run`: the new process reads the duel's start from the payload."""
    params = V2Params()
    for role in ("seller", "buyer"):
        whole, _ = play_silent(role, 100, params)
        for restart in (DEADLINE - 6, DEADLINE - 3, DEADLINE - 2):
            _, d = play_silent(role, 100, params, upto=restart - 1)  # the old process sent up to here
            start = payload_start(d, restart, first_offer_wait(params))
            assert start == SEEN
            for tick in range(restart, DEADLINE):
                move = duel_plan(d, tick, start, params).move
                assert (move.kind, move.price) == ("offer", whole[tick].price), (role, restart, tick)
                d["messages"].append({"tick": tick, "from": "you", "price": move.price, "days": move.days})


def test_a_silent_rival_stays_within_the_free_offers_and_jev_counters_on_the_same_ladder():
    sent, d = play_silent("seller", 100, upto=DEADLINE - 2)
    assert len(sent) + 1 <= V2Params().free_offers  # the D − 1 floor is not cut by the cap
    assert counter_offer(d, DEADLINE - 1, SEEN).price == our_target(100, "seller", 1.0)
    assert duel_plan(d, DEADLINE - 1, SEEN).move.price == our_target(100, "seller", 1.0)


def test_jitter_floors_against_a_silent_rival_stay_strictly_inside_the_limit():
    rng = random.Random(11)
    for i in range(300):
        role, limit = rng.choice(("seller", "buyer")), rng.randint(3, 250)
        params = V2Params(jitter=0.5, jitter_seed=5, silent_floor_lead=rng.randint(0, 4))
        d = duel(i, role=role, limit=limit, deadline=DEADLINE, ours=[(SEEN + 3, limit * 2 if role == "seller" else 1)])
        for tick in range(SEEN + 4, DEADLINE):
            move = plan_moves([d], tick, {i: SEEN}, params)[i]
            if move.kind == "offer":
                assert gr.check(duel_action(d, move), CTX, gr.Guardrails(duel_policy="v2")).allowed, (d, move)


def test_a_rival_that_priced_gets_exactly_the_moves_it_got_before():
    """The lead only changes duels whose rival never priced: every other move is the old one."""
    rng = random.Random(23)
    checked = 0
    for i in range(4000):
        limit, role = rng.randint(30, 150), rng.choice(("seller", "buyer"))
        two = rng.random() < 0.4
        days = (rng.randint(0, 10),) if two else ()
        rival = [(100 + t, rng.randint(1, 300), *days) for t in sorted(rng.sample(range(12), rng.randint(1, 5)))]
        ours = [(100 + t, rng.randint(1, 300), *days) for t in sorted(rng.sample(range(12), rng.randint(0, 4)))]
        issues = ("price", "days") if two else ("price",)
        weight = round(rng.uniform(-4, 4), 1) if two else None
        d = duel(i, role=role, limit=limit, rival=rival, ours=ours, issues=issues, weight=weight)
        tick = rng.randint(100, 111)
        for signed in (False, True):
            new = plan_moves([d], tick, {i: 100}, V2Params(days_signed=signed, min_share=0.3, endgame_ticks=1))
            old = plan_moves([d], tick, {i: 100}, V2Params(days_signed=signed, min_share=0.3, endgame_ticks=1,
                                                         silent_floor_lead=0))  # fmt: skip
            assert new == old, (d, tick)
            assert counter_offer(d, tick, 100, V2Params(days_signed=signed)) == counter_offer(
                d, tick, 100, V2Params(days_signed=signed, silent_floor_lead=0)
            )
            checked += 1
    assert checked == 8000


def test_a_standing_rival_offer_without_its_message_still_counts_as_priced():
    d = duel(limit=100, deadline=DEADLINE, ours=[(SEEN + 3, 150)], rival_offer={"price": 104, "days": 0})
    for tick in range(SEEN + 4, DEADLINE):
        assert duel_plan(d, tick, SEEN).move == duel_plan(d, tick, SEEN, LEGACY).move, tick


def test_a_rival_offer_outside_our_limit_without_its_message_still_gets_the_floor():
    """Review #215: the silent branch and the curve must agree on "the rival has not priced"."""
    d = duel(limit=100, deadline=DEADLINE, ours=[(SEEN + 3, 150)], rival_offer={"price": 60, "days": 0})
    move = duel_plan(d, DEADLINE - 1, SEEN).move
    assert (move.kind, move.price) == ("offer", our_target(100, "seller", 1.0))


def test_a_long_silent_duel_still_sends_the_floor_past_the_free_offers_cap():
    d = duel(limit=100, deadline=SEEN + 30)
    for tick in range(SEEN, SEEN + 30):
        move = plan_moves([d], tick, {1: SEEN})[1]
        if move.kind == "offer":
            d["messages"].append({"tick": tick, "from": "you", "price": move.price, "days": None})
    assert len(d["messages"]) == V2Params().free_offers + 1  # the cap holds, but for the floor itself
    assert d["messages"][-1] == {"tick": SEEN + 29, "from": "you", "price": 105, "days": None}


def test_at_lead_1_our_next_counter_never_steps_back_once_the_rival_starts_pricing():
    rng = random.Random(31)
    for _ in range(400):
        role, limit = rng.choice(("seller", "buyer")), rng.randint(20, 250)
        sent, d = play_silent(role, limit, upto=rng.randint(SEEN + 3, DEADLINE - 2))
        last_tick = max(sent)
        rival_tick = last_tick + rng.randint(0, 1)
        bid = limit // 2 if role == "seller" else limit * 2
        d["messages"].append({"tick": rival_tick, "from": "Rival Azul", "price": bid, "days": None})
        d["rival_offer"] = {"price": bid, "days": 0}
        for tick in range(rival_tick + 1, DEADLINE):
            counter = counter_offer(d, tick, SEEN)
            if counter.kind == "offer":
                last = sent[last_tick].price
                assert (counter.price <= last) if role == "seller" else (counter.price >= last), (role, limit, tick)


def test_against_a_silent_rival_lead_1_changes_only_the_last_tick_we_send():
    """#215 review: the lead must not lower our D − 3/D − 2 offers (the ticks Duels I's silent rivals took). Random
    silent duels, with restarts mid-duel: every move with two or more ticks left is today's move (lead 0), and D − 1
    carries our floor, strictly inside our limit, from v2's plan and from Jev's counter alike."""
    rng = random.Random(47)
    compared = floors = 0
    for i in range(600):
        role, limit = rng.choice(("seller", "buyer")), rng.randint(20, 300)  # a floor 1 off the limit is a hold
        two = rng.random() < 0.5
        issues, weight = (("price", "days"), round(rng.uniform(-4, 4), 1)) if two else (("price",), None)
        seen = 100
        deadline = seen + rng.randint(6, 30)
        signed = two and rng.random() < 0.5
        base = {
            "days_signed": signed,
            "jitter": rng.choice((0.0, 0.3)),
            "jitter_seed": rng.randint(0, 9),
            "open_wait_ticks": rng.randint(0, 3),
            "free_offers": rng.choice((4, 16)),
        }
        lead1, lead0 = V2Params(**base, silent_floor_lead=1), V2Params(**base, silent_floor_lead=0)
        rules = gr.Guardrails(duel_policy="v2", duel_days_signed=signed)
        d = duel(i, role=role, limit=limit, deadline=deadline, issues=issues, weight=weight)
        restart = rng.choice((None, rng.randint(seen + 1, deadline - 1)))
        start = seen
        for tick in range(seen, deadline):
            if tick == restart:  # a redeploy: the new process reads the duel's start from the payload
                start = payload_start(d, tick, first_offer_wait(lead1))
            new, old = duel_plan(d, tick, start, lead1).move, duel_plan(d, tick, start, lead0).move
            jev = counter_offer(d, tick, start, lead1)
            if deadline - tick >= 2:
                assert new == old, (i, tick, new, old)
                assert jev == counter_offer(d, tick, start, lead0), (i, tick)
                compared += 1
            else:
                mine = jittered(lead1, d)
                floor = our_target(limit, role, 1.0, mine.anchor, mine.floor)
                if new.kind == "hold":  # as today: no legal offer at our floor, or still waiting for the rival to open
                    assert old.kind == "hold", (i, new, old)
                    assert _offer(d, floor, signed, "floor") is None or "wait" in new.reason, (i, new)
                    continue
                assert (jev.kind, jev.price, jev.days) == (new.kind, new.price, new.days), (i, new, jev)
                value = value_of(d, new.price, new.days, signed)
                assert value is not None and surplus_share(role, limit, value) > 0, (i, new)  # strictly inside
                assert value == floor, (i, new, floor)  # our floor, as a value (days repriced when signed)
                assert gr.check(duel_action(d, new), CTX, rules).allowed, (i, new)
                floors += 1
            if new.kind == "offer":
                d["messages"].append({"tick": tick, "from": "you", "price": new.price, "days": new.days})
                d["your_offer"] = {"price": new.price, "days": new.days or 0}
    assert compared > 5000 and floors > 550


def test_the_lead_is_a_guardrails_knob():
    rules = gr.load_guardrails().rules
    assert rules.duel_silent_floor_lead == 1 and V2Params.from_rules(rules).silent_floor_lead == 1
    assert "duel_silent_floor_lead" in gr.ENFORCED_BY
