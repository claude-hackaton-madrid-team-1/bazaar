"""`team_desk_never_trade` (Sat 3 Oct): the team desk never opens, proposes or accepts with the podium or the
teams ranked around us. Opus proposed SAL-03 to t17, a direct rival missing it."""

import pytest

from bazaar_agent.agents.team_desk import _Plan
from bazaar_agent.guardrails import Guardrails, load_guardrails, team_ids
from tests.test_team_desk import TICK, Team, desk, their_offer, thread, trade, view

RIVALS = "t03,t05,t06,t10,t12,t13,t14,t17,t18"


def test_the_shipped_guardrails_trade_with_every_team_but_cap_their_share():
    # Omar, Sat 3 Oct ~21:50: trade with everyone, but every swap leaves them at most half of the pie
    rules = load_guardrails().rules
    assert team_ids(rules.team_desk_never_trade) == ()
    assert not rules.never_trades_with("t17") and not rules.never_trades_with("t05")
    assert rules.team_swap_max_their_share <= 0.5


def test_a_bad_team_id_is_refused_and_none_turns_it_off():
    with pytest.raises(ValueError):
        Guardrails(team_desk_never_trade="t5,abuela")
    assert not Guardrails(team_desk_never_trade="none").never_trades_with("t17")


def test_no_thread_opens_with_a_blocked_team(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team, team_desk_never_trade=RIVALS)
    d._plan = _Plan(TICK, (trade("t17"),), {"LAV-02": 16.0})
    d.proposals(view())
    d.converse(view(), set())
    assert team.sent == []


def test_a_planned_swap_with_a_blocked_team_is_dropped_from_a_fresh_plan(tmp_path, monkeypatch):
    import bazaar_agent.agents.team_desk as td

    class Plan:
        threads = (trade("t17"), trade("t02"))

    monkeypatch.setattr(td, "build_plan", lambda *a, **k: Plan())
    monkeypatch.setattr(td, "closest_pages", lambda pages: frozenset())
    d, _ = desk(tmp_path, Team(), team_desk_never_trade=RIVALS)
    d._plan = None
    assert [t.counterparty for t in d._trades(view())] == ["t02"]


def test_a_blocked_teams_offer_is_never_an_accept_candidate(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team, team_desk_never_trade=RIVALS)
    d._plan = _Plan(TICK, (trade("t17"),), {"LAV-02": 16.0})
    fair = thread(team="t17", messages=[{"sender": "t17", "tick": TICK, "text": "trato"}], offers=[])
    fair["standing_offers"] = [{**their_offer(cash_out=1), "maker": "t17"}]
    assert d.proposals(view([fair])) == []


def test_the_guards_refuse_a_blocked_team_even_if_it_reaches_them(tmp_path):
    from bazaar_agent.agents.team_desk import SwapAccept

    d, _ = desk(tmp_path, Team(), team_desk_never_trade=RIVALS)
    verdict = d._guard(view(), trade("t17"), 0, None)
    assert not verdict.allowed and any("team_desk_never_trade" in p for p in verdict.violations)
    ok = d._guard(view(), trade("t02"), 0, None)
    assert not any("team_desk_never_trade" in p for p in ok.violations)
    accept = SwapAccept.__new__(SwapAccept)
    object.__setattr__(accept, "trade", trade("t17"))
    refused = d.guard_accept(view(), accept)
    assert not refused.allowed and "team_desk_never_trade" in refused.violations[0]


def test_an_inbound_thread_from_a_blocked_team_is_closed_at_once(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team, team_desk_never_trade=RIVALS)
    inbound = thread(tid=51, team="t17", opened_by="t17")
    d.proposals(view([inbound]))
    d.converse(view([inbound]), set())
    assert ("close_thread", 51) in [s[:2] for s in team.sent]
    assert not [s for s in team.sent if s[0] in ("say", "open_thread", "accept")]


def test_after_a_restart_our_offer_in_a_blocked_teams_thread_is_closed_on_the_first_tick(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team, team_desk_never_trade=RIVALS)  # a fresh desk: no talks remembered
    ours = {**their_offer(oid=702), "maker": "t01", "to": "t17"}
    standing = thread(tid=42, team="t17", offers=[ours])
    d.proposals(view([standing]))
    d.converse(view([standing]), set())
    assert ("close_thread", 42) in [s[:2] for s in team.sent]


def test_a_talk_with_a_team_added_to_the_list_gets_no_move_and_is_closed(tmp_path):
    from bazaar_agent.agents.team_desk import Talk

    team = Team()
    d, _ = desk(tmp_path, team, team_desk_never_trade=RIVALS)
    d.talks[42] = Talk(42, "t17", trade("t17"), TICK - 5)
    running = thread(tid=42, team="t17")
    d.proposals(view([running]))
    d.converse(view([running]), set())
    assert ("close_thread", 42) in [s[:2] for s in team.sent] and 42 not in d.talks
    assert not [s for s in team.sent if s[0] == "say"]


def test_two_blocked_threads_never_stop_an_opening_with_an_allowed_team(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team, team_desk_never_trade=RIVALS)
    d._plan = _Plan(TICK, (trade("t02"),), {"LAV-02": 16.0})
    parked = [thread(tid=51, team="t17", opened_by="t17"), thread(tid=52, team="t18", opened_by="t18")]
    d.proposals(view(parked))
    d.converse(view(parked), set())  # both closed this tick
    after = view([])
    d.proposals(after)
    d.converse(after, set())
    assert ("open_thread", "t02") in [s[:2] for s in team.sent]
