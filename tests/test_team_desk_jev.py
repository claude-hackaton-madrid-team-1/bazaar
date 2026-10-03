"""N17 enable: the Jev gate on every swap send, the hourly swap cash cap, and the money rules on the propose path
(fakes, no network)."""

import pytest

from bazaar_agent.agents.runtime import JevAdvice, no_jev
from bazaar_agent.agents.team_desk import NO_JEV_BUDGET, TEAM_SPEND, DeskView
from bazaar_agent.guardrails import Context, Ledger
from tests.agent_fakes import rows
from tests.test_official_value_paths import book
from tests.test_team_desk import THEM, TICK, Team, desk, their_offer, thread, trade, view

HELD = {"LAV-01": 1, "LAV-06": 1, "LAT-03": 2, "LAT-09": 1}


class Asked:
    """A stub Jev that records every state it reads and answers `advice` (or raises it)."""

    def __init__(self, advice: JevAdvice | Exception) -> None:
        self.advice, self.states = advice, []

    def __call__(self, state: dict) -> JevAdvice:
        self.states.append(state)
        if isinstance(self.advice, Exception):
            raise self.advice
        return self.advice


def opened(team: Team) -> list[tuple]:
    return [s for s in team.sent if s[0] in ("open_thread", "say")]


# ---------------------------------------------------------------- fail closed


@pytest.mark.parametrize(
    "advice",
    [
        JevAdvice("undecided", 0.6, reason="below_threshold"),
        JevAdvice("no", 0.9),
        JevAdvice("yes", 0.7),  # a yes under team_swap_jev_min_confidence 0.75
        JevAdvice("undecided", 0.0, reason="no tick budget for jev"),
        RuntimeError("jev down"),
    ],
)
def test_anything_but_a_confident_yes_opens_no_thread_and_sends_no_offer(tmp_path, advice):
    team = Team()
    d, lines = desk(tmp_path, team)
    jev = Asked(advice)
    d.converse(view(jev=jev), set())
    assert opened(team) == [] and len(jev.states) == 1
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "team_open"]
    assert row["status"] == "rejected" and not row["chosen"]
    if not isinstance(advice, Exception):
        assert row["jev"]["verdict"] == advice.verdict  # the verdict is kept in the decision row


def test_the_taker_without_jev_sends_no_swap(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(jev=no_jev), set())  # `agent taker --no-jev`, or no Jev key: fail closed
    assert opened(team) == []


def test_a_refused_opening_rests_the_team_so_jev_is_not_asked_every_tick(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    jev = Asked(JevAdvice("no", 0.9))
    d.converse(view(jev=jev), set())
    d.converse(view(tick=TICK + 1, jev=jev), set())
    assert len(jev.states) == 1 and d.rest_until[THEM] > TICK + 1


def test_a_confident_yes_opens_the_thread_and_jev_is_asked_once_for_it(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    jev = Asked(JevAdvice("yes", 0.92))
    d.converse(view(jev=jev), set())
    assert [s[0] for s in opened(team)] == ["open_thread", "say"] and len(jev.states) == 1
    kinds = {r["kind"]: r for r in rows(tmp_path) if r.get("kind") in ("team_open", "team_offer")}
    assert kinds["team_open"]["jev"]["verdict"] == "yes" and kinds["team_offer"]["jev"]["verdict"] == "yes"


def test_the_gate_off_lets_the_rules_alone_decide(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team, team_swap_jev_gate=False)
    d.converse(view(jev=no_jev), set())
    assert [s[0] for s in opened(team)] == ["open_thread", "say"]


def test_a_later_proposal_jev_refuses_walks_the_thread(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(), set())  # opened and anchored on a yes
    reply = thread(messages=[{"sender": "t01", "tick": TICK}, {"sender": THEM, "tick": TICK + 1, "text": "más"}])
    team.sent.clear()
    nay = Asked(JevAdvice("no", 0.88))
    d.proposals(view([reply], tick=TICK + 1, jev=nay))
    d.converse(view([reply], tick=TICK + 1, jev=nay), set())
    assert not [s for s in team.sent if s[0] == "say"] and ("close_thread", 42) in team.sent and 42 not in d.talks


def test_jev_reads_both_cards_at_official_and_private_values_the_cash_and_the_history(tmp_path):
    d, _ = desk(tmp_path, Team())
    jev = Asked(JevAdvice("yes", 0.95))
    values = book({"LAT-03": 2.0, "LAV-02": 14.0})
    base = view(jev=jev)
    v = DeskView(**{**base.__dict__, "ctx": lambda _t: Context(400, dict(HELD), TICK, 1.5, values=values)})
    d.converse(v, set())
    swap = jev.states[0]["swap"]
    assert swap["give"] == {"card": "LAT-03", "copies_held": 2, "official_value": 2.0, "private_value": 1.2}
    assert swap["get"] == {
        "card": "LAV-02",
        "copies_held": 0,
        "official_value": 14.0,
        "private_value": 16.0,
        "page": None,
    }
    assert swap["cash"] == -1 and swap["fee"] == 0 and swap["kind"] == "propose"
    assert 0 < swap["their_share"] <= 0.6 and swap["our_gain"] > 0
    assert jev.states[0]["history"] == {"settled_with_team": 0, "proposal_step": 0}


# ---------------------------------------------------------------- the accept path (taker)


def _taker(tmp, team, jev):
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from bazaar_agent.agents.team_desk import _Plan
    from tests.agent_fakes import FakePublic, parts

    t = Taker(
        team,
        FakePublic(),
        live=True,
        log=lambda _: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0),
        swap_jev=jev,
        **parts(tmp, team_threads_enabled=True),
    )
    t.team_desk.env = {}
    t.team_desk._plan = _Plan(TICK, (trade(),), {"LAV-02": 16.0})
    return t


@pytest.mark.parametrize(
    ("advice", "taken"),
    [(JevAdvice("yes", 0.9), True), (JevAdvice("undecided", 0.5), False), (JevAdvice("no", 0.95), False)],
)
def test_the_taker_takes_a_teams_offer_only_on_jevs_confident_yes(tmp_path, advice, taken):
    from tests.agent_fakes import clock

    team = Team(threads=[thread(offers=[their_offer(cash_out=1)], opened_by=THEM)])
    jev = Asked(advice)
    _taker(tmp_path, team, jev).on_tick(clock())
    accepts = [s for s in team.sent if s[0] == "accept"]
    assert bool(accepts) is taken
    assert jev.states and jev.states[0]["swap"]["kind"] == "accept" and jev.states[0]["swap"]["fee"] == 3
    (row,) = [r for r in rows(tmp_path) if r.get("kind") == "team_accept"]  # taken, or the skip row
    assert row["jev"]["verdict"] == advice.verdict and row["status"] == ("approved" if taken else "rejected")


# ---------------------------------------------------------------- money caps


def test_the_ledger_sums_team_swap_spend_by_prefix(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.record("spend", 1, 1.0, 30, f"{TEAM_SPEND}LAV-02")
    ledger.record("spend", 1, 1.0, 50, "LAV-08")
    ledger.record("spend", 2, 1.1, -10, f"{TEAM_SPEND}LAV-02")  # a refund nets out
    assert ledger.spent_since(0.5) == 70 and ledger.spent_since(0.5, TEAM_SPEND) == 20


def test_a_swap_proposal_past_the_hourly_swap_cash_cap_is_not_sent(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.ledger.record("spend", TICK - 5, 1.2, 40, f"{TEAM_SPEND}MAL-09")  # this hour's swap cash is used up
    d.converse(view(), set())  # our anchor adds 1 P
    assert opened(team) == []


def test_swap_cash_under_the_cap_is_sent_and_booked_as_team_spend(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.ledger.record("spend", TICK - 5, 1.2, 39, f"{TEAM_SPEND}MAL-09")
    d.converse(view(), set())
    assert [s[0] for s in opened(team)] == ["open_thread", "say"]
    assert d.ledger.spent_since(0.5, TEAM_SPEND) == 40


def test_taking_an_offer_past_the_hourly_swap_cash_cap_is_refused(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.converse(view(), set())
    fair = thread(messages=[{"sender": THEM, "tick": TICK + 1, "text": "trato"}], offers=[their_offer(cash_out=1)])
    (a,) = d.proposals(view([fair], tick=TICK + 1))
    assert d.guard_accept(view(tick=TICK + 1), a).allowed
    d.ledger.record("spend", TICK, 1.4, 37, f"{TEAM_SPEND}MAL-09")  # + the anchor's 1 = 38; + 1 + fee 3 > 40
    verdict = d.guard_accept(view(tick=TICK + 1), a)
    assert not verdict.allowed and any("team_swap_max_cash_per_hour 40" in p for p in verdict.violations)


def test_a_swap_that_would_break_the_cash_floor_is_not_sent(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team, cash_floor=100)
    base = view()
    v = DeskView(**{**base.__dict__, "ctx": lambda _t: Context(100, dict(HELD), TICK, 1.5)})  # 100 - 1 < floor
    d.converse(v, set())
    assert opened(team) == []


@pytest.mark.official_values
def test_a_swap_proposal_above_the_official_value_is_not_sent(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    base = view()
    low = book({"LAV-02": 1.5})  # our 1 P + the copy given (1.2) > 1.5
    v = DeskView(**{**base.__dict__, "ctx": lambda _t: Context(400, dict(HELD), TICK, 1.5, values=low)})
    d.converse(v, set())
    assert opened(team) == []
    fair = book({"LAV-02": 14.0})
    v2 = DeskView(**{**base.__dict__, "ctx": lambda _t: Context(400, dict(HELD), TICK, 1.5, values=fair)})
    d.converse(v2, set())
    assert [s[0] for s in opened(team)] == ["open_thread", "say"]


# ---------------------------------------------------------------- review fixes (#188)


def test_a_tick_with_no_jev_budget_holds_the_thread_instead_of_walking_it(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.converse(view(), set())  # opened and anchored on a yes
    reply = thread(messages=[{"sender": "t01", "tick": TICK}, {"sender": THEM, "tick": TICK + 1, "text": "más"}])
    team.sent.clear()
    busy = Asked(JevAdvice("undecided", 0.0, reason=NO_JEV_BUDGET))
    d.proposals(view([reply], tick=TICK + 1, jev=busy))
    d.converse(view([reply], tick=TICK + 1, jev=busy), set())
    assert team.sent == [] and 42 in d.talks  # nothing sent, nothing closed
    d.proposals(view([reply], tick=TICK + 2))
    d.converse(view([reply], tick=TICK + 2), set())  # budget back: the concession goes out
    assert [s[0] for s in team.sent] == ["cancel", "say"]


def test_a_refund_of_an_older_unprefixed_spend_never_lifts_the_swap_cap(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.ledger.record("spend", TICK - 5, 1.2, -30, f"{TEAM_SPEND}LAV-09")  # its spend was booked as plain LAV-09
    assert d._over_cash_cap(view(), 40) is None and d._over_cash_cap(view(), 41) is not None


def test_a_concession_nets_out_the_standing_offer_it_replaces(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.ledger.record("spend", TICK - 5, 1.2, 39, f"{TEAM_SPEND}LAV-02")  # our standing offer's 18 + 21 elsewhere
    assert d._over_cash_cap(view(), 3) is not None  # 39 + 3 > 40
    assert d._over_cash_cap(view(), 3, replacing=18) is None  # 21 + 3: the old 18 is cancelled and refunded


def test_jev_reads_an_accept_as_an_accept_even_with_no_fee(tmp_path):
    d, _ = desk(tmp_path, Team())
    state = d.swap_state(view(), trade(), 0, 0, None, 0, "accept")
    assert state["swap"]["kind"] == "accept" and state["swap"]["fee"] == 0


def test_the_maker_lists_again_when_the_desk_is_killed_by_its_environment(monkeypatch):
    from bazaar_agent.agents.team_desk import maker_may_list
    from bazaar_agent.guardrails import Guardrails

    me = {"assets": [{"id": 3, "ref": "LAT-03", "your_value": 1.2}, {"id": 4, "ref": "LAT-03", "your_value": 1.2}]}
    rules = Guardrails(team_threads_enabled=True)
    assert not maker_may_list(me, "LAT-03", 4, rules)
    monkeypatch.setenv("BAZAAR_TEAM_THREADS", "0")
    assert maker_may_list(me, "LAT-03", 4, rules)


def test_a_cancel_answered_settled_posts_no_new_offer_and_nets_nothing(tmp_path):
    # security-auditor #188 r2 P2-1: a settled offer is not seen; netting it let the cap be passed by its cash.
    class Settled(Team):
        def cancel(self, offer_id):
            self.sent.append(("cancel", offer_id))
            return {"status": "settled"}

    team = Settled()
    d, _ = desk(tmp_path, team)
    d.ledger = Ledger(tmp_path / "ledger.jsonl")
    d.converse(view(), set())  # anchor: books 1 P
    d.ledger.record("spend", TICK, 1.4, 37, f"{TEAM_SPEND}MAL-09")  # 38 booked
    reply = thread(messages=[{"sender": "t01", "tick": TICK}, {"sender": THEM, "tick": TICK + 1, "text": "más"}])
    team.sent.clear()
    d.proposals(view([reply], tick=TICK + 1))  # our offer is gone from the thread: unseen, never netted
    assert not d._seen_open(view([reply], tick=TICK + 1), d.talks[42])
    d.converse(view([reply], tick=TICK + 1), set())
    assert not [s for s in team.sent if s[0] == "say"]
    assert d.ledger.spent_since(0.5, TEAM_SPEND) <= 40


# ---------------------------------------------------------------- missing page cards first (Omar, Sat 3 Oct)


def _swap(ref: str, expected_ours: float, team: str = THEM):
    from dataclasses import replace as _replace

    t = trade(team)
    return _replace(t, refs=("LAT-03", ref), want={"cards": [ref]}, ours=expected_ours, p_fill=1.0)


def test_the_page_closest_to_complete_is_asked_for_first_whatever_the_gain():
    from bazaar_agent.agents.team_desk import PageNeed, TeamDesk

    pages = {
        "LAV": PageNeed("LAV", 8, 10, 1.6, 106.0),
        "MAL": PageNeed("MAL", 8, 10, 1.1, 72.9),
        "LAT": PageNeed("LAT", 2, 10, 0.5, 33.1),
    }
    plan = [_swap("LAT-04", 30.0), _swap("MAL-09", 20.0), _swap("LAV-10", 5.0), _swap("LAV-09", 9.0)]
    ranked = sorted(plan, key=lambda t: TeamDesk._priority(t, pages))
    assert [t.refs[1] for t in ranked] == ["LAV-09", "LAV-10", "MAL-09", "LAT-04"]


def test_page_needs_reads_our_album_from_the_market():
    from bazaar_agent.agents.team_desk import page_needs
    from bazaar_agent.strategy import build_market
    from tests.agent_fakes import CATALOG, ME

    needs = page_needs(build_market(ME, CATALOG, [], []))
    assert needs and all(n.have < n.of and n.bonus >= 0 for n in needs.values())


def test_jev_reads_which_page_the_card_completes_and_its_bonus(tmp_path):
    from bazaar_agent.agents.team_desk import PageNeed, _Plan

    d, _ = desk(tmp_path, Team())
    d._plan = _Plan(TICK, (trade(),), {"LAV-02": 16.0}, {"LAV": PageNeed("LAV", 9, 10, 1.6, 106.0)})
    page = d.swap_state(view(), trade(), -1, 0, None, 0)["swap"]["get"]["page"]
    assert page == {
        "set": "LAV",
        "have": 9,
        "of": 10,
        "missing_after": 0,
        "completes_page": True,
        "affinity": 1.6,
        "page_bonus": 106.0,  # no official value read in this view: our model
        "page_bonus_source": "model",
    }


def test_the_card_scan_places_holders_the_feed_never_shows():
    from bazaar_agent.trade_desk import scanned_copies

    scan = [
        {"id": 301, "ref": "LAV-09", "kind": "card", "owner": "t05"},
        {"id": 302, "ref": "LAV-09", "kind": "card", "owner": "t07"},
        {"id": 303, "ref": "LAV-10", "kind": "card", "owner": "t01"},  # ours: never a counterparty
    ]
    copies = scanned_copies(scan, [], {"id": "t01", "assets": []}, "t01")
    assert copies["t05"]["LAV-09"] == 1 and copies["t07"]["LAV-09"] == 1 and "t01" not in copies


def test_the_closest_pages_are_the_ones_with_the_fewest_cards_missing():
    from bazaar_agent.agents.team_desk import PageNeed, closest_pages

    pages = {
        "LAV": PageNeed("LAV", 8, 10, 1.6, 106.0),
        "MAL": PageNeed("MAL", 8, 10, 1.1, 72.9),
        "LAT": PageNeed("LAT", 2, 10, 0.5, 33.1),
    }
    assert closest_pages(pages) == {"LAV", "MAL"} and closest_pages({}) == frozenset()


def test_the_official_value_is_the_source_of_the_page_bonus_when_the_card_completes_the_page(tmp_path):
    # Omar / team-lead: /api/me/value includes the completion gain (SAL-09 read 177.1 = 70 x 1.3 + 86.1 at 9/10).
    from bazaar_agent.agents.team_desk import PageNeed, _Plan

    d, _ = desk(tmp_path, Team())
    need = PageNeed("SAL", 9, 10, 1.3, 70.0)
    d._plan = _Plan(TICK, (trade(),), {"LAV-02": 16.0}, {"LAV": need}, {"LAV-02": 70.0})
    base = view()
    v = DeskView(
        **{**base.__dict__, "ctx": lambda _t: Context(400, dict(HELD), TICK, 1.5, values=book({"LAV-02": 177.1}))}
    )
    page = d.swap_state(v, trade(), -1, 0, None, 0)["swap"]["get"]["page"]
    assert page["page_bonus"] == 86.1 and page["page_bonus_source"] == "official"
    short = PageNeed("SAL", 8, 10, 1.3, 70.0)  # one card short after it: the model's estimate
    d._plan = _Plan(TICK, (trade(),), {"LAV-02": 16.0}, {"LAV": short}, {"LAV-02": 70.0})
    page = d.swap_state(v, trade(), -1, 0, None, 0)["swap"]["get"]["page"]
    assert page["page_bonus"] == 70.0 and page["page_bonus_source"] == "model"
