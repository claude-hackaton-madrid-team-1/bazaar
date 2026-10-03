"""The taker: board asks below value (fee included), the team accept quota, the dealer desk, tick budget."""

from bazaar_agent.agents.dealer import BidPlan, Move, Negotiation
from bazaar_agent.agents.desk import Conversation, DeskMove, meet_the_ask, openings, plan_conversation
from bazaar_agent.agents.inspector import FlagBook
from bazaar_agent.agents.market import board_offers, venues_from
from bazaar_agent.agents.runtime import JevAdvice
from bazaar_agent.agents.taker import Taker, TakerConfig, ask_candidates, board_proposal, rank_accepts
from tests.agent_fakes import CHEAP, RASTRO, TICK, FakePublic, FakeTeam, ask, bid, clock, parts, rows
from tests.test_strategy import PARAMS, market, playbook

VENUES = {v.id: v for v in venues_from({"venues": [RASTRO, CHEAP]})}


def taker(tmp_path, team, public, *, live=False, jev=None, config=None, **rules):
    lines: list[str] = []
    kw = parts(tmp_path, **rules)
    t = Taker(
        team,
        public,
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=config or TakerConfig(max_dealer_threads=0),
        **({"jev": jev} if jev else {}),
        **kw,
    )
    return t, lines, kw["ledger"]


def at(team, tick):
    """The next tick, on the fake server's clock too (the taker re-reads it before an accept)."""
    team.now = clock(tick=tick)
    return team.now


def board(*offers):
    return board_offers({"offers": list(offers)}, "rastro", "t01")


# ---------------------------------------------------------------- (a) standing asks


def test_an_ask_is_taken_only_when_its_total_with_the_fee_is_below_its_value():
    # LAV-02 is worth 20.8 to us; min_buy_surplus 2. On El Rastro 16 + fee 2 = 18 passes, 17 + 2 = 19 does not.
    cands = ask_candidates(
        market(), board(ask(1, "LAV-02", 16), ask(2, "LAV-02", 17, asset=901)), VENUES, PARAMS, set(), {}
    )
    assert [(c.offer.id, c.fee, c.total) for c in cands] == [(1, 2, 18)]
    assert cands[0].surplus == 2.8 and "ask 16 + fee 2 on rastro = 18" in cands[0].reason
    # The same 17 on a zero-fee venue is fine: the fee is what made it too dear.
    free = board_offers({"offers": [ask(3, "LAV-02", 17, venue="v02")]}, "v02", "t01")
    assert [c.total for c in ask_candidates(market(), free, VENUES, PARAMS, set(), {})] == [17]


def test_held_cards_our_own_offers_and_unknown_venues_are_never_candidates():
    offers = board(ask(1, "LAV-01", 2), ask(2, "LAV-02", 10, asset=901), ask(3, "LAV-11", 5, asset=902))
    assert ask_candidates(market(), offers, VENUES, PARAMS, {2}, {}) == []  # held, ours, not a page card
    elsewhere = board_offers({"offers": [ask(4, "LAV-02", 10, venue="v99")]}, "v99", "t01")
    assert ask_candidates(market(), elsewhere, VENUES, PARAMS, set(), {}) == []


def test_one_candidate_per_card_the_cheapest_and_our_cheaper_bid_wins():
    offers = board(ask(1, "LAV-08", 30, asset=901), ask(2, "LAV-08", 28, asset=902))
    (only,) = ask_candidates(market(), offers, VENUES, PARAMS, set(), {})
    assert only.offer.id == 2 and only.total == 28 + 3
    from bazaar_agent.agents.market import OpenOffer

    ours = OpenOffer(77, "bid", "LAV-08", 25, "rastro", None, 140, 90)
    assert ask_candidates(market(), offers, VENUES, PARAMS, set(), {"LAV-08": ours}) == []  # our bid is cheaper
    cheaper = OpenOffer(77, "bid", "LAV-08", 40, "rastro", None, 140, 90)
    (c,) = ask_candidates(market(), offers, VENUES, PARAMS, set(), {"LAV-08": cheaper})
    assert c.replaces_bid == cheaper


def test_accepts_rank_finals_first_then_scarce_then_score():
    cands = ask_candidates(
        market(), board(ask(1, "LAV-02", 10), ask(2, "LAV-08", 20, asset=901)), VENUES, PARAMS, set(), {}
    )
    ranked = rank_accepts([board_proposal(c) for c in cands])
    assert [p.ref for p in ranked] == ["LAV-08", "LAV-02"]  # bigger score first
    conv = Conversation("abuela", "LAV-02", "common", 20.8, "r", Negotiation(BidPlan(8, 1, 9)), 50, TICK)
    final = DeskMove(conv, Move("accept", 9, 801, "final within limit"), 9, True, offer_id=801)
    from bazaar_agent.agents.taker import desk_proposal

    assert rank_accepts([*ranked, desk_proposal(final)])[0].source == "abuela"


def test_dry_run_sends_nothing_and_logs_and_records_the_would_accept(tmp_path):
    team = FakeTeam()
    t, lines, _ = taker(tmp_path, team, FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}))
    t.on_tick(clock())
    assert team.sent == []  # dry run: no write of any kind
    assert any(line.startswith(f"tick {TICK} taker: WOULD accept LAV-02 on rastro for 12") for line in lines)
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask" and r.get("chosen")]
    assert (row["status"], row["dry_run"], row["guardrail"]) == ("approved", True, "allowed")
    assert row["inputs"]["fee"] == 2 and row["inputs"]["total"] == 12
    assert rows(tmp_path, "executions.jsonl") == []  # nothing was sent, so nothing was executed
    assert team.reads.count("me") == 1  # album first, once per tick


def test_live_accepts_one_offer_and_writes_the_execution_and_the_spend(tmp_path):
    team = FakeTeam()
    public = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10), ask(2, "LAV-08", 20, asset=901)]})
    t, lines, ledger = taker(tmp_path, team, public, live=True)
    t.on_tick(clock())
    assert team.sent == [("accept", 2)]  # LAV-08 (best score), only one: the team quota is 1 per tick
    assert ledger.accept_items(TICK) == ["LAV-08"] and ledger.spent_since(0) == 22
    (execution,) = rows(tmp_path, "executions.jsonl")
    assert execution["sdk_method"] == "accept" and execution["request"] == {"offer": 2}
    assert any("accept quota 1/tick used" in line for line in lines)


def test_the_team_quota_is_shared_through_the_ledger(tmp_path):
    public = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]})
    first_team, second_team = FakeTeam(), FakeTeam()
    first, _, ledger = taker(tmp_path, first_team, public, live=True)
    first.on_tick(clock())
    second, lines, _ = taker(tmp_path, second_team, public, live=True)  # another process, same ledger file
    second.on_tick(clock())
    assert first_team.sent == [("accept", 1)] and second_team.sent == []
    assert ledger.accepts_in_tick(TICK) == 1
    assert any("accept quota 1/tick used" in line for line in lines)


def test_a_duel_accept_holds_the_slot_and_the_taker_steps_back(tmp_path):
    team = FakeTeam()
    t, lines, ledger = taker(
        tmp_path, team, FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}), live=True, max_accepts_per_tick=2
    )
    assert ledger.reserve_accept(TICK, 1.5, 0, "duel:7", 2)  # the duel player went first
    t.on_tick(clock(accepts_per_team_per_tick=2))
    assert team.sent == []
    assert any("a duel holds the team's accept this tick (duels first)" in line for line in lines)


def test_the_taker_waits_out_the_duel_grace_before_claiming(tmp_path):
    slept: list[float] = []
    team = FakeTeam()
    t, _, _ = taker(tmp_path, team, FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}), live=True)
    t.sleep = slept.append
    t.on_tick(clock(next_tick_in=59.5))  # 0.5 s into a 60 s tick: wait until 2 s in
    assert slept == [1.5] and team.sent == [("accept", 1)]


def test_a_guardrail_denial_or_a_jev_no_keeps_the_slot(tmp_path):
    team = FakeTeam()
    public = FakePublic(boards={"rastro": [ask(1, "LAV-08", 40, asset=901)]})  # 43 > max_price_uncommon 26
    t, lines, _ = taker(tmp_path, team, public, live=True)
    t.on_tick(clock())
    assert team.sent == [] and any("denied: price 43 > max_price_uncommon 26" in line for line in lines)

    no = JevAdvice("no", 0.1, {"yes": 0.1, "no": 0.9})
    team2 = FakeTeam()
    t2, lines2, _ = taker(
        tmp_path / "b", team2, FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}), live=True, jev=lambda state: no
    )
    t2.on_tick(clock())
    assert team2.sent == [] and any("jev no (0.10): kept the accept slot" in line for line in lines2)
    (row,) = [r for r in rows(tmp_path / "b") if r.get("kind") == "accept_ask"]
    assert row["jev"]["verdict"] == "no" and row["jev"]["probabilities"] == {"yes": 0.1, "no": 0.9}


def test_moves_are_dropped_when_the_tick_budget_is_spent(tmp_path):
    team = FakeTeam()
    t, lines, _ = taker(tmp_path, team, FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}), live=True)
    t.on_tick(clock(next_tick_in=1.0))  # 1 s left, 2 s margin: no budget
    assert team.sent == []
    assert f"tick {TICK} taker: DROPPED: LAV-02 at 12 from board: tick budget spent, not sent late" in lines
    assert [r["status"] for r in rows(tmp_path) if r.get("kind") == "accept_ask"] == ["expired"]


# ---------------------------------------------------------------- (b) the dealer desk


def test_openings_take_one_thread_per_dealer_and_skip_busy_dealers():
    book = playbook()
    dealer_buys = [mv for mv in book.buys if mv.source == "abuela"]
    assert [op.item for op in openings(dealer_buys, set(), set(), 3)] == ["LAV-08"]  # one per dealer
    assert openings(dealer_buys, {"abuela"}, set(), 3) == []
    assert [op.item for op in openings(dealer_buys, set(), {"LAV-08"}, 3)] == ["LAV-02"]


def test_the_desk_never_opens_a_second_thread_with_a_dealer(tmp_path):
    team = FakeTeam(threads=[{"id": 40, "with": "abuela", "status": "open"}])
    t, lines, _ = taker(tmp_path, team, FakePublic(), live=True, config=TakerConfig(max_dealer_threads=3))
    t.on_tick(clock())
    assert not [s for s in team.sent if s[0] == "open_thread"]
    assert not any("open thread with abuela" in line for line in lines)


def test_the_desk_opens_then_bids_one_move_per_tick_without_blocking(tmp_path):
    team = FakeTeam()
    t, lines, _ = taker(tmp_path, team, FakePublic(), live=True, config=TakerConfig(max_dealer_threads=3))
    t.on_tick(clock())
    (opened,) = [s for s in team.sent if s[0] == "open_thread"]
    assert opened == ("open_thread", "abuela", {"buy": {"card": "LAV-08"}})
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18)]  # the ladder's opening bid
    t.on_tick(at(team, TICK + 1))
    assert [s for s in team.sent if s[0] == "say"] == [("say", 5000, 18), ("say", 5000, 19)]  # one per tick
    assert set(t.convs) == {"abuela"}


def test_a_final_dealer_offer_inside_our_max_is_accepted_and_spends_the_slot(tmp_path):
    team = FakeTeam()
    t, _, ledger = taker(tmp_path, team, FakePublic(), live=True, config=TakerConfig(max_dealer_threads=3))
    t.on_tick(clock())  # opens thread 5000 and bids 18
    offer = {
        "id": 801,
        "maker": "abuela",
        "status": "open",
        "final": True,
        "give": {"types": ["card:LAV-08"]},
        "want": {"cash": 21},
    }
    team.thread_payloads[5000] = {"id": 5000, "status": "open", "messages": [], "standing_offers": [offer]}
    t.on_tick(at(team, TICK + 1))
    assert team.sent[-1] == ("accept", 801) and ledger.accept_items(TICK + 1) == ["LAV-08"]
    team.thread_payloads[5000] = {"id": 5000, "status": "deal", "messages": [], "standing_offers": []}
    t.on_tick(at(team, TICK + 2))
    assert t.convs == {} and ledger.spent_since(0) == 21  # the deal is recorded as spend once it settles


def test_plan_conversation_ignores_an_offer_that_is_not_our_buy_and_walks_after_max_ticks():
    conv = Conversation("abuela", "LAV-08", "uncommon", 52, "r", Negotiation(BidPlan(18, 1, 22)), 50, TICK)
    trick = {"id": 9, "maker": "abuela", "status": "open", "give": {"types": ["card:LAV-02"]}, "want": {"cash": 5}}
    dm = plan_conversation(conv, {"status": "open", "standing_offers": [trick]}, 14)
    assert dm.move.kind == "bid" and dm.ignored and "instead of exactly [card:LAV-08]" in dm.ignored
    conv.ticks = 14
    assert plan_conversation(conv, {"status": "open"}, 14).move.kind == "walk"
    assert plan_conversation(conv, {"status": "deal"}, 14).status == "deal"


def test_meet_the_ask_bids_her_price_when_the_accept_slot_went_elsewhere():
    conv = Conversation("abuela", "LAV-08", "uncommon", 52, "r", Negotiation(BidPlan(18, 1, 22)), 50, TICK)
    conv.neg.bids.append(18)
    accept = DeskMove(conv, Move("accept", 20, 801, "ask meets our next bid"), 20, False, offer_id=801)
    assert meet_the_ask(accept).move == Move("bid", 20, reason="accept slot used: meet her ask")
    above = DeskMove(conv, Move("accept", 25, 801), 25, False, offer_id=801)
    assert meet_the_ask(above).move.kind == "wait"  # never above our max


def test_jev_yes_accepts_a_dealer_ask_early_but_never_above_the_max(tmp_path):
    team = FakeTeam()
    yes = JevAdvice("yes", 0.92)
    t, _, _ = taker(
        tmp_path, team, FakePublic(), live=True, jev=lambda s: yes, config=TakerConfig(max_dealer_threads=3)
    )
    t.on_tick(clock())
    offer = {
        "id": 802,
        "maker": "abuela",
        "status": "open",
        "final": False,
        "give": {"types": ["card:LAV-08"]},
        "want": {"cash": 21},
    }
    team.thread_payloads[5000] = {"id": 5000, "status": "open", "messages": [], "standing_offers": [offer]}
    t.on_tick(at(team, TICK + 1))
    assert team.sent[-1] == ("accept", 802)  # 21 <= max 22: Jev may close early


def test_a_read_refusal_skips_the_tick_without_sending(tmp_path):
    from bazaar_agent.sdk import BazaarError

    class Refusing(FakeTeam):
        def me(self):
            raise BazaarError("rate_limited", "slow down", 429)

    team = Refusing()
    t, lines, _ = taker(tmp_path, team, FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}), live=True)
    t.on_tick(clock())
    assert team.sent == [] and any("read refused rate_limited" in line for line in lines)


def test_a_cheaper_ask_replaces_our_open_bid(tmp_path):
    team = FakeTeam(offers=[bid(77, "LAV-02", 19)])
    t, _, ledger = taker(tmp_path, team, FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]}), live=True)
    ledger.record("spend", TICK - 5, 1.4, 19, "LAV-02")  # the bid was counted when it was posted
    t.on_tick(clock())
    assert team.sent == [("accept", 1), ("cancel", 77)]
    assert ledger.spent_since(0) == 19 + 12 - 19  # the bid's spend is refunded


def test_a_pack_thread_opens_only_on_jevs_yes_with_time_to_ask(tmp_path):
    asked = []

    def judge(state):
        asked.append(state["pack"])
        return "yes", 0.9

    no_cards = [
        d
        for d in [
            {
                "id": "abuela",
                "status": "active",
                "level": 1,
                "menu": {"sells": [{"pack": "sobre_barrio", "list_price": 26, "per_team_per_hour": 3}]},
            }
        ]
    ]
    team = FakeTeam()
    t, lines, _ = taker(
        tmp_path, team, FakePublic(dealers=no_cards), live=True, config=TakerConfig(max_dealer_threads=3)
    )
    t.pack_judge = judge
    t.on_tick(clock())
    assert asked == ["sobre_barrio"] and ("open_thread", "abuela", {"buy": {"pack": "sobre_barrio"}}) in team.sent

    late = FakeTeam()
    t2, _, _ = taker(
        tmp_path / "b", late, FakePublic(dealers=no_cards), live=True, config=TakerConfig(max_dealer_threads=3)
    )
    t2.pack_judge = judge
    t2.on_tick(clock(next_tick_in=5.0))  # 3 s of budget: no time for Jev, so no pack
    assert asked == ["sobre_barrio"] and not [s for s in late.sent if s[0] == "open_thread"]


# ---------------------------------------------------------------- the accept gate (S1)


def test_a_board_copy_of_a_lesser_rarity_than_its_card_is_never_accepted(tmp_path):
    bait = ask(2, "LAV-08", 20, asset=901, rarity="common")  # LAV-08 is an uncommon: the copy says common
    team = FakeTeam()
    t, lines, ledger = taker(tmp_path, team, FakePublic(boards={"rastro": [bait]}), live=True)
    t.on_tick(clock())
    assert team.sent == [] and ledger.accept_items(TICK) == []
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "accept_ask"]
    assert (row["status"], row["chosen"], row["inputs"]["inspector"]["verdict"]) == ("rejected", False, "block")
    assert row["inputs"]["inspector"]["findings"] == ["the copy says common; the catalog has LAV-08 as uncommon"]
    assert any("inspector block on offer 2" in line for line in lines)


def test_the_inspect_accepts_kill_flag_keeps_the_older_checks_only(tmp_path):
    bait = ask(2, "LAV-08", 20, asset=901, rarity="common")
    team = FakeTeam()
    t, _, _ = taker(tmp_path, team, FakePublic(boards={"rastro": [bait]}), live=True, inspect_accepts=False)
    t.on_tick(clock())
    assert team.sent == [("accept", 2)]
    assert "inspector" not in next(r for r in rows(tmp_path) if r.get("kind") == "accept_ask")["inputs"]


def test_a_clean_dealer_accept_carries_the_inspection_in_its_decision_row(tmp_path):
    team = FakeTeam()
    t, _, _ = taker(tmp_path, team, FakePublic(), live=True, config=TakerConfig(max_dealer_threads=3))
    t.on_tick(clock())
    offer = {"id": 801, "maker": "abuela", "status": "open", "final": True}
    offer |= {"give": {"types": ["card:LAV-08"]}, "want": {"cash": 21}}
    message = {"message": 9000, "sender": "abuela", "text": "LAV-08, 21 P, cariño", "offer": offer}
    team.thread_payloads[5000] = {"id": 5000, "status": "open", "messages": [message], "standing_offers": [offer]}
    t.on_tick(at(team, TICK + 1))
    assert team.sent[-1] == ("accept", 801)
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_accept"]
    assert row["inputs"]["inspector"] == {
        "kind": "dealer",
        "offer_id": 801,
        "message_id": 9000,
        "verdict": "clean",
        "findings": [],
        "words": None,
    }


def test_a_dealer_trickster_is_never_accepted_and_only_logged_as_would_flag(tmp_path):
    trick = {"id": 802, "maker": "abuela", "status": "open", "final": True}
    trick |= {"give": {"types": ["card:LAV-01"]}, "want": {"cash": 21}}  # a common, for the LAV-08 we asked
    message = {"message": 9001, "sender": "abuela", "text": "LAV-08 para ti, 21 P", "offer": trick}
    for allow in (False, True):
        team = FakeTeam()
        root = tmp_path / str(allow)
        root.mkdir()
        config = TakerConfig(max_dealer_threads=3)
        t, lines, _ = taker(root, team, FakePublic(), live=True, config=config, allow_flags=allow)
        t.flags = FlagBook(trusted=frozenset())  # the fake trickster plays Abuela, a trusted dealer by default
        t.on_tick(clock())
        team.thread_payloads[5000] = {"id": 5000, "status": "open", "messages": [message], "standing_offers": [trick]}
        t.on_tick(at(team, TICK + 1))
        assert ("accept", 802) not in team.sent and not [s for s in team.sent if s[0] == "flag"]
        (would,) = [line for line in lines if "would flag message 9001" in line]
        assert ("allow_flags" in would) is (not allow) and ("dry run" in would) is allow
