"""Duel policy v2 (`duel_policy` = v2): silence is free, few priced messages, one accept per tick, never outside."""

import random
from pathlib import Path

from bazaar_agent import duel_arena as arena
from bazaar_agent import guardrails as gr
from bazaar_agent.agents.duel_jev import DuelJev, legal_moves
from bazaar_agent.agents.duel_v2 import V2Params, counter_offer, duel_plan, plan_moves, rounds_spent
from bazaar_agent.agents.duelist import DuelMove, duel_action
from bazaar_agent.agents.runtime import JevAdvice

FIXTURE = Path(__file__).parent / "fixtures" / "evals" / "duels_done.json"


def duel(did=1, role="seller", limit=100, deadline=112, rival=(), ours=(), issues=("price",), weight=None, **kw):
    """A live duel in the practice shape. `rival` and `ours` are (tick, price[, days]) priced messages."""
    msgs = [{"tick": m[0], "from": "Rival Azul", "price": m[1], "days": m[2] if len(m) > 2 else None} for m in rival]
    msgs += [{"tick": m[0], "from": "you", "price": m[1], "days": m[2] if len(m) > 2 else None} for m in ours]
    msgs.sort(key=lambda m: m["tick"])
    last_rival = rival[-1] if rival else None
    last_ours = ours[-1] if ours else None
    return {
        "duel": did,
        "status": "live",
        "role": role,
        "issues": list(issues),
        "your_days_weight": weight,
        "your_limit": limit,
        "rival": "Rival Azul",
        "deadline_tick": deadline,
        "decay_per_round": 0.06,
        "rounds": min(len(rival), len(ours)),
        "rival_offer": None if last_rival is None else {"price": last_rival[1], "days": (last_rival[2:] or (0,))[0]},
        "your_offer": None if last_ours is None else {"price": last_ours[1], "days": (last_ours[2:] or (0,))[0]},
        "messages": msgs,
        **kw,
    }


def test_the_default_is_todays_policy_and_every_v2_knob_is_in_guardrails_md():
    rules = gr.load_guardrails().rules
    assert rules.duel_policy == "v1" and not rules.duel_days_signed
    params = V2Params.from_rules(rules)
    assert (params.max_own_offers, params.stall_ticks, params.open_wait_ticks) == (3, 3, 0)
    assert V2Params.from_rules(rules, anchor=0.4, floor=0.1).anchor == 0.4  # steering still applies


def test_holds_in_silence_while_the_rival_concedes():
    d = duel(rival=[(100, 105), (101, 108), (102, 111)], ours=[(100, 160)])
    move = duel_plan(d, 103, 100).move
    assert move.kind == "hold" and "silence is free" in move.reason


def test_anchors_once_then_waits_for_the_rival():
    opening = duel(rival=[(100, 70)])  # the rival bids below our cost: nothing to take
    first = duel_plan(opening, 100, 100).move
    assert first.kind == "offer" and first.price == 160 and "anchor" in first.reason
    answered = duel(rival=[(100, 70), (101, 75)], ours=[(100, 160)])
    assert duel_plan(answered, 101, 100).move.kind == "hold"


def test_never_more_than_duel_max_own_offers_rounds_against_a_rival_that_answers():
    answered = duel(rival=[(100, 70), (101, 72), (104, 74)], ours=[(100, 160), (103, 150)])  # it answers each offer
    assert rounds_spent(answered) == 2
    two = V2Params(max_own_offers=2)
    for tick in range(105, 109):
        assert duel_plan(answered, tick, 100, two).move.kind == "hold", tick  # 2 rounds spent, it is not quiet
    last_call = duel_plan(answered, 109, 100, two).move  # no deal scores 0: the floor goes out whatever the cap
    assert (last_call.kind, last_call.price) == ("offer", 105)


def test_a_quiet_rival_gets_free_descending_offers():
    one_shot = duel(rival=[(100, 70)], ours=[(100, 160)])  # it priced once and has ignored our anchor since
    move = duel_plan(one_shot, 103, 100, V2Params(max_own_offers=1)).move
    assert move.kind == "offer" and move.price < 160 and "quiet" in move.reason  # rounds stay min(2, 1) = 1
    assert duel_plan(one_shot, 102, 100).move.kind == "hold"  # silent only 2 ticks: it may still be conceding


def test_a_silent_rival_gets_v1s_descending_offers_for_free():
    d = duel(ours=[(100, 160), (101, 155)])
    move = duel_plan(d, 102, 100).move
    assert move.kind == "offer" and move.price < 155 and "no round" in move.reason
    assert duel_plan(d, 102, 100, V2Params(free_offers=2)).move.kind == "hold"


def test_a_stalled_rival_gets_one_counter_then_an_accept():
    stalled = duel(rival=[(100, 110), (101, 110), (102, 110)], ours=[(100, 160)])
    counter = duel_plan(stalled, 104, 100).move
    assert counter.kind == "offer" and counter.price > 110 and "stall" in counter.reason
    spent = duel(rival=[(100, 110), (101, 110), (105, 110)], ours=[(100, 160), (104, 150)])
    move = duel_plan(spent, 108, 100, V2Params(max_own_offers=2)).move
    assert (move.kind, move.price) == ("accept", 110)
    deaf = duel(rival=[(100, 110), (101, 110), (102, 110)], ours=[(100, 160), (104, 150)])
    move = duel_plan(deaf, 108, 100).move  # it never answered our counter: no third round
    assert (move.kind, move.price) == ("accept", 110) and "ignored" in move.reason


def test_the_endgame_takes_any_offer_strictly_inside_the_limit_and_never_on_it():
    assert duel_plan(duel(rival=[(100, 101)], ours=[(100, 160)]), 110, 100).move.kind == "accept"
    on_limit = duel_plan(duel(rival=[(100, 100)], ours=[(100, 160)]), 110, 100).move
    assert (on_limit.kind, on_limit.price) == ("offer", 105)  # never accepts 100: says our floor once more
    buyer = duel(role="buyer", rival=[(100, 99)], ours=[(100, 40)])
    assert duel_plan(buyer, 110, 100).move.kind == "accept"


def test_one_accept_per_tick_and_six_duels_on_one_deadline_all_get_theirs():
    duels = [duel(did, rival=[(100, 120 + did)], ours=[(100, 160)]) for did in range(1, 7)]
    accepted = []
    for tick in range(101, 112):
        live = [d for d in duels if d["duel"] not in accepted]
        moves = plan_moves(live, tick, {d["duel"]: 100 for d in duels})
        takes = [did for did, m in moves.items() if m.kind == "accept"]
        assert len(takes) <= 1, (tick, takes)
        accepted += takes
    assert sorted(accepted) == [1, 2, 3, 4, 5, 6]  # v1's endgame of 2 ticks would take only 2 of them


def test_every_v2_move_passes_duel_inside_limit_in_both_days_modes():
    rng = random.Random(4)
    for signed in (False, True):
        rules = gr.Guardrails(duel_policy="v2", duel_days_signed=signed)
        params = V2Params(days_signed=signed)
        for _ in range(3000):
            limit, role = rng.randint(30, 150), rng.choice(("seller", "buyer"))
            two = rng.random() < 0.5
            days = (rng.randint(0, 10),) if two else ()
            rival = [(100 + t, rng.randint(1, 300), *days) for t in range(rng.randint(0, 4))]
            ours = [(100 + t, rng.randint(1, 300), *days) for t in range(rng.randint(0, 3))]
            weight = round(rng.uniform(-4, 4), 1) if two else None
            issues = ("price", "days") if two else ("price",)
            d = duel(role=role, limit=limit, rival=rival, ours=ours, issues=issues, weight=weight)
            move = plan_moves([d], rng.randint(100, 111), {1: 100}, params)[1]
            if move.kind != "hold":
                verdict = gr.check(duel_action(d, move), gr.Context(cash=0, held={}, tick=0, t_hours=0), rules)
                assert verdict.allowed, (d, move, verdict)


def test_signed_days_reach_the_guardrail_only_under_v2():
    action = gr.Action("duel_offer", "1", price=95, limit=100, role="seller", days=10, days_weight=2.0)
    ctx = gr.Context(cash=0, held={}, tick=0, t_hours=0)
    assert gr.check(action, ctx, gr.Guardrails(duel_policy="v2", duel_days_signed=True)).allowed  # 95 + 20
    assert not gr.check(action, ctx, gr.Guardrails(duel_policy="v1", duel_days_signed=True)).allowed  # #60's case
    assert not gr.check(action, ctx, gr.Guardrails(duel_policy="v2")).allowed  # 95 − 20 by default


def test_jev_under_v2_may_not_take_the_accept_the_planner_gave_away_nor_pass_the_cap():
    d = duel(rival=[(100, 120), (101, 121), (104, 122)], ours=[(100, 160), (103, 150)])  # 2 rounds spent
    held = DuelMove("hold", reason="accept queued")
    legal = legal_moves(d, 105, held, counter_offer(d, 105, 100), 2, V2Params(max_own_offers=2))
    assert set(legal) == {"hold"}  # no accept (not this duel's slot) and no counter (2 rounds spent)
    assert "accept" in legal_moves(d, 105, held, counter_offer(d, 105, 100), 2)  # v1 keeps its rules


def test_jev_reads_the_v2_default_move():
    d = duel(rival=[(100, 105), (101, 108)], ours=[(100, 160)])
    jev = DuelJev(lambda state: JevAdvice("undecided", 0.0))
    pick = jev.pick([d], 102, {1: 100}, anchor=0.6, floor=0.05, endgame_ticks=2, left=lambda: 30.0, v2=V2Params())
    assert pick[1].default.kind == "hold" and pick[1].state["duel"]["default_move"] == "hold"


def test_the_arena_scores_rounds_as_the_practice_session_did():
    scenario = arena.Scenario("seller", 50, 120, "linear", rival_open=0.6, rival_floor=0.3)
    o = arena.run_session([scenario], arena.v1_policy())[0]  # v1 counters every tick: rounds pile up
    assert o.status == "deal" and o.rounds >= 4 and abs(o.result - o.gain_signed * 0.94**o.rounds) < 1e-9
    quiet = arena.run_session([scenario], arena.v2_policy())[0]
    assert quiet.status == "deal" and quiet.rounds <= 1 and quiet.result > o.result


def test_v2_beats_v1_in_a_small_tournament_and_never_closes_outside():
    res = arena.tournament({"v1": arena.v1_policy(), "v2": arena.v2_policy()}, scenarios=10, decays=(0.08,))
    v1, v2 = arena.summarize(res["v1"]), arena.summarize(res["v2"])
    assert v2.mean_result > v1.mean_result and v2.outside == v2.denied == v1.outside == 0


def test_replay_of_the_unanswered_practice_duels_beats_v1():
    duels = arena.load_practice(FIXTURE)
    v1 = sum(r["result"] for r in arena.replay(duels, arena.v1_policy()))
    v2 = sum(r["result"] for r in arena.replay(duels, arena.v2_policy()))
    assert v2 > v1 > 0


def test_jev_cannot_hold_back_the_accepts_the_planner_times_across_duels():
    duels = [duel(did, rival=[(100, 120 + did)], ours=[(100, 160)]) for did in range(1, 7)]
    jev = DuelJev(lambda state: JevAdvice("hold", 0.95))  # a Jev that always wants to wait
    accepted = []
    for tick in range(101, 112):
        live = [d for d in duels if d["duel"] not in accepted]
        picks = jev.pick(live, tick, {d["duel"]: 100 for d in duels}, anchor=0.6, floor=0.05, endgame_ticks=2,
                         left=lambda: 30.0, v2=V2Params())  # fmt: skip
        accepted += [did for did, p in picks.items() if p.move.kind == "accept"]
    assert sorted(accepted) == [1, 2, 3, 4, 5, 6]


def test_free_offers_wait_for_the_rival_to_open_and_the_accept_margin_is_a_knob():
    silent = duel(rival=[], ours=[])
    assert duel_plan(silent, 101, 100).move.kind == "hold"  # it may still open: an offer now could cost a round
    assert duel_plan(silent, 103, 100).move.kind == "offer"  # 3 ticks of silence (duel_stall_ticks)
    conceding = duel(rival=[(100, 105), (109, 109), (110, 110)], ours=[(100, 160)])
    assert duel_plan(conceding, 110, 100).move.kind == "accept"  # by D − 2 (default margin 1)
    late = duel_plan(conceding, 110, 100, V2Params(accept_margin=0)).move
    assert late.kind == "hold" and duel_plan(conceding, 111, 100, V2Params(accept_margin=0)).move.kind == "accept"


def test_messages_without_ticks_never_look_stalled_and_a_bad_row_holds_only_itself():
    tickless = duel(rival=[(100, 110)], ours=[(100, 160)])
    for m in tickless["messages"]:
        m.pop("tick")
    for tick in range(103, 109):
        assert duel_plan(tickless, tick, 100).move.kind == "hold", tick  # no stall-counter burning rounds
    good = duel(2, rival=[(100, 120)], ours=[(100, 160)])
    moves = plan_moves([{**duel(1), "messages": 5}, good], 110, {1: 100, 2: 100})
    assert moves[1].kind == "hold" and "unreadable" in moves[1].reason and moves[2].kind == "accept"


def test_the_arena_can_let_us_move_before_the_rival_within_a_tick():
    scenario = arena.Scenario("seller", 50, 120, "linear", rival_open=0.6, rival_floor=0.3)
    for team_first in (False, True):
        o = arena.run_session([scenario], arena.v2_policy(), team_first=team_first)[0]
        assert o.status == "deal" and o.gain_signed > 0 and o.denied == 0


def test_when_accepts_must_queue_the_slowest_rival_is_taken_first():
    fast = duel(1, rival=[(100, 110), (105, 120), (106, 125), (107, 130)], ours=[(100, 160)])  # +5 a tick
    slow = duel(2, rival=[(100, 112), (107, 113)], ours=[(100, 160)])
    moves = plan_moves([fast, slow], 109, {1: 100, 2: 100})  # 3 ticks left, 2 accepts to make: one is due now
    assert moves[2].kind == "accept" and moves[1].kind == "hold"


# ---------------------------------------------------------------- B11: endgame squeeze


def test_a_squeeze_is_refused_before_the_last_ticks_and_our_last_offer_stays_fair():
    squeeze = duel(rival=[(100, 60), (109, 101)], ours=[(100, 160)])  # 1 P inside our cost 100, 3 ticks left
    assert duel_plan(squeeze, 110, 100).move.kind == "accept"  # today: anything inside the limit at D − 2
    guarded = V2Params(min_share=0.3, endgame_ticks=1)  # threshold 0.3 × max(1, 0.4 × 100) = 12 P
    move = duel_plan(squeeze, 110, 100, guarded).move
    assert (move.kind, move.price) == ("offer", 112)  # refuse; the rival can still take 112 at its last move
    early = duel(rival=[(100, 60), (108, 101)], ours=[(100, 160)])
    assert (
        duel_plan(early, 109, 100, guarded).move.kind == "hold"
    )  # one fair offer (at D − 2), not two: each is a round
    assert duel_plan(squeeze, 111, 100, guarded).move.kind == "accept"  # the true last tick: anything > 0
    fair = duel(rival=[(100, 60), (109, 130)], ours=[(100, 160)])
    assert duel_plan(fair, 110, 100, guarded).move.kind == "accept"  # 30 P is no squeeze


def test_jitter_is_seeded_per_duel_and_never_leaves_our_limit():
    from bazaar_agent.agents.duel_v2 import jittered

    p = V2Params(jitter=0.25, jitter_seed=3)
    a, b = jittered(p, duel(1)), jittered(p, duel(2))
    assert a == jittered(p, duel(1)) and a.anchor != b.anchor and 0.45 <= a.anchor <= 0.75
    assert jittered(V2Params(), duel(1)) == V2Params()
    rules = gr.Guardrails(duel_policy="v2")
    rng = random.Random(9)
    for i in range(500):
        d = duel(
            i, role=rng.choice(("seller", "buyer")), limit=rng.randint(30, 150), rival=[(100, rng.randint(1, 300))]
        )
        move = plan_moves([d], rng.randint(100, 111), {i: 100}, V2Params(jitter=0.5, min_share=0.4))[i]
        if move.kind != "hold":
            assert gr.check(duel_action(d, move), gr.Context(cash=0, held={}, tick=0, t_hours=0), rules).allowed


def test_the_squeeze_mitigation_raises_our_share_against_exploiters():
    exploiters = {"today": arena.v2_policy(), "guarded": arena.v2_policy(V2Params(min_share=0.3, endgame_ticks=1))}
    res = arena.tournament(exploiters, styles=arena.EXPLOITERS, scenarios=20, decays=(0.08,))
    today, guarded = arena.summarize(res["today"]), arena.summarize(res["guarded"])
    assert guarded.mean_share > today.mean_share + 0.03 and guarded.outside == today.outside == 0
    assert arena.leakage(res["today"])["priced"] > 0


def test_after_a_restart_v2_recovers_the_duels_start_from_its_messages():
    from bazaar_agent.agents.duel_v2 import payload_start

    assert payload_start(duel(rival=[(103, 70)], ours=[(104, 160)]), 108) == 103  # not 108: the clock survives
    assert payload_start(duel(), 108) == 108


# ---------------------------------------------------------------- r2 bites B2a / B2c


def test_days_are_valued_even_when_the_payload_drops_issues():
    from bazaar_agent.agents.duelist import duel_move

    no_issues = duel(rival=[(100, 104, 10)], ours=[(100, 160)], issues=(), weight=3.0)  # 104 − 3 × 10 = 74 < 100
    assert duel_move(no_issues, 110, 100).kind != "accept" and duel_plan(no_issues, 110, 100).move.kind != "accept"
    accept = DuelMove("accept", 104)
    ctx = gr.Context(cash=0, held={}, tick=0, t_hours=0)
    assert not gr.check(duel_action(no_issues, accept), ctx, gr.Guardrails()).allowed  # #60's guard sees the days


def test_v2_plays_zero_days_without_a_weight_and_v1_still_holds():
    from bazaar_agent.agents.duelist import duel_move

    unweighted = duel(rival=[(100, 101, 0)], ours=[(100, 160, 0)], issues=("price", "days"), weight=None)
    move = duel_plan(unweighted, 110, 100).move
    assert (move.kind, move.price) == ("accept", 101)  # 0 days cost nothing under either sign
    ctx = gr.Context(cash=0, held={}, tick=0, t_hours=0)
    assert gr.check(duel_action(unweighted, move), ctx, gr.Guardrails(duel_policy="v2")).allowed
    assert duel_move(unweighted, 110, 100).kind == "hold"  # #60's v1, unchanged: it cannot value days
    assert not gr.check(duel_action(unweighted, move), ctx, gr.Guardrails()).allowed


def test_missed_ticks_accept_earlier_but_never_drop_to_the_floor_earlier():
    """r1 on #103: B4's missed-tick bump must not move the last-offer window (#86 offers its curve at D − 5/D − 4)."""
    from dataclasses import replace as with_

    stalled = duel(rival=[(100, 60), (101, 70)], ours=[(100, 160)])  # seller cost 100, deadline 112
    for missed in (0, 2):
        params = with_(V2Params(), missed=missed)
        for tick in (107, 108):  # D − 5, D − 4
            move = duel_plan(stalled, tick, 100, params).move
            assert move.kind != "offer" or move.price > 105, (missed, tick, move)  # no floor before D − 3
    inside = duel(rival=[(100, 60), (105, 120)], ours=[(100, 160)])
    assert duel_plan(inside, 108, 100, with_(V2Params(), missed=2)).move.kind == "accept"  # 4 left ≤ 1 + 2 + 1
    assert duel_plan(inside, 108, 100).move.kind != "accept"


def test_an_explicit_price_only_duel_stays_price_only_even_with_a_weight():
    from bazaar_agent.agents.duelist import _two_issue

    assert not _two_issue({"issues": ["price"], "your_days_weight": 2.0, "rival_offer": {"price": 90, "days": 0}})
    assert _two_issue({"issues": ["price"], "your_days_weight": 2.0, "rival_offer": {"price": 90, "days": 4}})
    assert _two_issue({"your_days_weight": 2.0, "rival_offer": {"price": 90}})  # no issues list: the weight decides


# ---------------------------------------------------------------- B7: within-tick order and the Jev path


def test_the_practice_payloads_show_a_mixed_within_tick_order():
    order = arena.tick_order(arena.load_practice(FIXTURE))
    assert (order["we_first"], order["rival_first"]) == (27, 22)  # 55 % of shared ticks we spoke first
    assert order["per_duel"][85] == (5, 0) and order["per_duel"][274] == (1, 6)  # it depends on the rival


def test_the_arena_draws_each_rivals_order_and_v2_keeps_its_lead_under_the_mix():
    res = arena.tournament(
        {"v1": arena.v1_policy(), "v2": arena.v2_policy()}, scenarios=10, decays=(0.08,), team_first_share=0.55
    )
    v1, v2 = arena.summarize(res["v1"]), arena.summarize(res["v2"])
    assert v2.mean_result > v1.mean_result and v2.outside == v2.denied == 0


def test_under_v2_jev_may_counter_within_the_caps_and_its_counter_stays_inside_our_limit():
    d = duel(rival=[(100, 110), (101, 110), (102, 110)], ours=[(100, 160)])  # stalled at 110 against our cost 100
    jev = DuelJev(lambda state: JevAdvice("counter", 0.9))
    pick = jev.pick([d], 103, {1: 100}, anchor=0.6, floor=0.05, endgame_ticks=2, left=lambda: 30.0, v2=V2Params())[1]
    assert pick.move.kind == "offer" and pick.move.price > 110  # Jev's counter, at v2's target
    ctx = gr.Context(cash=0, held={}, tick=0, t_hours=0)
    assert gr.check(duel_action(d, pick.move), ctx, gr.Guardrails(duel_policy="v2")).allowed
    hold = DuelJev(lambda state: JevAdvice("hold", 0.9))
    held = hold.pick([d], 104, {1: 100}, anchor=0.6, floor=0.05, endgame_ticks=2, left=lambda: 30.0, v2=V2Params())[1]
    assert held.default.kind == "offer" and held.move.kind == "hold"  # Jev may keep a v2 counter from costing a round
