"""Sun 4 Oct: the days latch per role and per session. During Duels II one global latch read the buyer's text as a
cost and the first scored seller deal as signed, called it a conflict and kept it for good on the duels volume, so
372 of 372 offers went out at day 0 and Duels III would have started there again."""

import json

from bazaar_agent import guardrails as gr
from bazaar_agent.agents import duel_days as dd
from bazaar_agent.agents.duel_v2 import V2Params, plan_moves
from bazaar_agent.agents.duelist import duel_action

SELLER_TEXT = "each delivery day adds this much cash to your side"  # the real Duels II payloads, verbatim
BUYER_TEXT = "each delivery day costs you this much cash"
V2_AUTO = gr.Guardrails(duel_policy="v2", duel_days_auto=True)
CTX = gr.Context(cash=0, held={}, tick=0, t_hours=0)
KEPT = 0.92**2  # 2 rounds at decay 0.08


def live(session: int, role: str = "seller", did: int = 1, **over) -> dict:
    """A live two-issue duel one tick in; the rival's 70 at 10 days is outside our limit either way: v2 counters."""
    seller = role == "seller"
    base = {
        "duel": did,
        "session": session,
        "status": "live",
        "role": role,
        "issues": ["price", "days"],
        "your_days_weight": 2.0,
        "days_meaning": SELLER_TEXT if seller else BUYER_TEXT,
        "your_limit": 100,
        "rival": "Rival Azul",
        "deadline_tick": 112,
        "decay_per_round": 0.08,
        "rounds": 0,
        "rival_offer": {"price": 70 if seller else 130, "days": 0},
        "your_offer": None,
        "messages": [{"tick": 100, "from": "Rival Azul", "price": 70 if seller else 130, "days": 0, "text": ""}],
    }
    return base | over


def seller_deal(session: int, did: int, days: int) -> dict:
    """A finished seller deal scored as the real game did on Saturday: (price - limit + weight × days) × kept."""
    result = round((120 - 100 + 2.0 * days) * KEPT, 1)
    return live(session, "seller", did) | {"status": "deal", "price": 120, "days": days, "rounds": 2, "result": result}


def buyer_deal(session: int, did: int, days: int) -> dict:
    """A finished buyer deal: (limit - price - weight × days) × kept, the day a cost."""
    result = round((100 - 80 - 2.0 * days) * KEPT, 1)
    return live(session, "buyer", did) | {"status": "deal", "price": 80, "days": days, "rounds": 2, "result": result}


def test_the_real_texts_read_as_a_gain_for_the_seller_and_a_cost_for_the_buyer():
    assert dd.evidence(live(3, "seller"), True) == "signed"
    assert dd.evidence(live(3, "buyer"), True) == "cost"
    assert dd.scored_evidence(seller_deal(3, 11, 10), True) == "signed"
    assert dd.scored_evidence(buyer_deal(3, 12, 10), True) == "cost"
    for text in ("each delivery day adds nothing to your side", "each delivery day adds this much cash to their side"):
        assert dd.evidence(live(3, days_meaning=text), True) == "unknown"
    assert dd.evidence(live(3, days_meaning=SELLER_TEXT), False) == "unknown"  # the simulator is never evidence


def test_saturdays_sequence_no_longer_conflicts(tmp_path):
    # The order the runner saw on Saturday: a buyer text first, then scored deals of both roles.
    latch = dd.latch(tmp_path)
    latch.observe([live(3, "seller", 1), live(3, "buyer", 2)], True)
    assert latch.verdict == "seller=signed buyer=cost"
    latch.observe([live(3, "seller", 1), seller_deal(3, 11, 10), buyer_deal(3, 12, 10)], True)
    assert latch.verdict == "seller=signed buyer=cost"  # was: conflict, for good
    rules = dd.effective_rules(V2_AUTO, latch)
    assert gr.days_signed_for(rules, "seller") and not gr.days_signed_for(rules, "buyer")
    assert (rules.duel_days_signed, rules.duel_days_signed_roles) == (False, "seller")


def test_a_conflict_persisted_in_one_session_never_reaches_the_next(tmp_path):
    latch = dd.latch(tmp_path)
    latch.observe([live(3), live(3, days_meaning="each day costs you primas", did=2)], True)  # a real seller conflict
    assert latch.for_role("seller").verdict == "conflict"
    assert not gr.days_signed_for(dd.effective_rules(V2_AUTO, latch), "seller")
    saved = json.loads((tmp_path / "duels" / "days_sign_seller.json").read_text())
    assert (saved["verdict"], saved["formed"], saved["role"]) == ("conflict", 3, "seller")
    sunday = dd.latch(tmp_path)  # a restart or a redeploy: the volume keeps the file
    assert sunday.for_role("seller").verdict == "conflict"  # before any live duel it can only keep days off
    sunday.observe([live(4)], True)  # Duels III's first live duel names the session
    assert sunday.verdict == "seller=signed buyer=unknown" and sunday.for_role("seller").formed == 4


def test_saturdays_global_file_is_never_read(tmp_path):
    # What sits on the bazaar-duels volume this morning: the old single file, `conflict`, no session.
    (tmp_path / "duels").mkdir()
    (tmp_path / dd.LATCH_FILE).write_text(json.dumps({"verdict": "conflict", "duel": 5744, "text": None}))
    latch = dd.latch(tmp_path)
    assert latch.verdict == "seller=unknown buyer=unknown"
    assert dd.effective_rules(V2_AUTO, latch) is V2_AUTO


def test_two_seller_scores_at_two_days_arm_the_seller_and_v2_offers_ten_days_inside_our_limit(tmp_path):
    latch = dd.latch(tmp_path)
    latch.observe([live(4, days_meaning=None), live(4, "buyer", 2)], True)  # no seller text this time
    assert not gr.days_signed_for(dd.effective_rules(V2_AUTO, latch), "seller")  # nothing corroborated yet
    latch.observe([live(4, days_meaning=None), seller_deal(4, 11, 10), seller_deal(4, 12, 3)], True)
    rules = dd.effective_rules(V2_AUTO, latch)
    duels = [live(4, days_meaning=None), live(4, "buyer", 2)]
    moves = plan_moves(duels, 101, {1: 100, 2: 100}, V2Params.from_rules(rules))
    assert moves[1].kind == "offer" and moves[1].days == 10  # the seller asks for the days that add to its side
    assert moves[2].kind == "offer" and moves[2].days == 0  # the buyer keeps 0: each day costs it
    for d in duels:
        verdict = gr.check(duel_action(d, moves[d["duel"]]), CTX, rules)
        assert verdict.allowed, verdict.reasons
    today = plan_moves(duels, 101, {1: 100, 2: 100}, V2Params.from_rules(V2_AUTO))
    assert today[1].days == 0 and today[2].days == 0  # without the latch: the worst case everywhere


def test_the_guard_values_each_role_by_its_own_sign():
    seller_only = gr.Guardrails(duel_policy="v2", duel_days_signed_roles="seller")
    seller = gr.Action("duel_offer", "1", price=95, limit=100, role="seller", days=10, days_weight=2.0)
    buyer = gr.Action("duel_offer", "2", price=95, limit=100, role="buyer", days=10, days_weight=2.0)
    assert gr.check(seller, CTX, seller_only).allowed  # 95 + 2 × 10 = 115 above our cost of 100
    assert not gr.check(buyer, CTX, seller_only).allowed  # 95 + 2 × 10 = 115 above our value of 100: refused
    assert not gr.check(seller, CTX, gr.Guardrails(duel_policy="v2")).allowed  # the worst case: 95 - 20 = 75
    v1 = gr.Guardrails(duel_policy="v1", duel_days_signed_roles="seller")
    assert not gr.check(seller, CTX, v1).allowed  # v1 keeps #60's worst case whatever the rules say


def test_a_hand_set_role_arms_from_the_first_tick_and_real_evidence_against_it_still_wins(tmp_path):
    by_hand = gr.Guardrails(duel_policy="v2", duel_days_signed_roles="seller")
    latch = dd.latch(tmp_path)
    latch.observe([live(4, days_meaning=None), live(4, "buyer", 2)], True)
    assert dd.effective_rules(by_hand, latch) is by_hand  # the buyer's cost text never touches the seller
    cost = {"days_meaning": None, "result": round((120 - 100 - 2.0 * 3) * KEPT, 1)}  # the game scored a cost
    latch.observe([live(4, days_meaning=None), seller_deal(4, 11, 3) | cost], True)
    assert latch.for_role("seller").verdict == "cost"
    assert not gr.days_signed_for(dd.effective_rules(by_hand, latch), "seller")
    both = gr.Guardrails(duel_policy="v2", duel_days_signed=True)  # the old global switch, set by hand
    rules = dd.effective_rules(both, latch)
    assert (rules.duel_days_signed, rules.duel_days_signed_roles) == (False, "none")  # both roles' costs win


def test_a_restart_mid_session_keeps_what_the_session_learned(tmp_path):
    latch = dd.latch(tmp_path)
    latch.observe([live(4), seller_deal(4, 11, 10)], True)
    assert gr.days_signed_for(dd.effective_rules(V2_AUTO, latch), "seller")
    restarted = dd.latch(tmp_path)
    assert not gr.days_signed_for(dd.effective_rules(V2_AUTO, restarted), "seller")  # no session known yet
    restarted.observe([live(4)], True)  # the first read after the restart: same session, same verdict and signals
    assert restarted.for_role("seller").formed == 4
    assert gr.days_signed_for(dd.effective_rules(V2_AUTO, restarted), "seller")


def test_a_missing_or_corrupt_file_is_handled_per_role(tmp_path):
    assert dd.latch(tmp_path / "none").verdict == "seller=unknown buyer=unknown"  # missing: nothing learned
    (tmp_path / "duels").mkdir()
    (tmp_path / "duels" / "days_sign_buyer.json").write_text("{not json")
    latch = dd.latch(tmp_path)
    assert latch.verdict == "seller=unknown buyer=conflict"  # unreadable: the safe side until a session is known
    latch.observe([live(4), live(4, "buyer", 2)], True)
    assert latch.verdict == "seller=signed buyer=cost"  # the session starts fresh and the file is rewritten
    assert json.loads((tmp_path / "duels" / "days_sign_buyer.json").read_text())["verdict"] == "cost"


def test_rows_of_an_older_session_never_move_the_current_verdict(tmp_path):
    latch = dd.latch(tmp_path)  # `?done=true` lists every finished duel: Saturday's deals arrive on Sunday too
    latch.observe([live(4, days_meaning=None), buyer_deal(3, 12, 10) | {"role": "seller"}], True)
    assert latch.for_role("seller").verdict == "unknown"


def test_reads_done_while_any_role_can_still_learn():
    real = True
    signed = dd.DaysLatch(
        {"seller": dd.DaysSwitch(verdict="cost", role="seller"), "buyer": dd.DaysSwitch(verdict="unknown")}
    )
    assert dd.reads_done(V2_AUTO, signed, real)
    settled = dd.DaysLatch(
        {"seller": dd.DaysSwitch(verdict="cost", role="seller"), "buyer": dd.DaysSwitch(verdict="conflict")}
    )
    assert not dd.reads_done(V2_AUTO, settled, real)
