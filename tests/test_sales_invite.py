"""SALES invites teams to our free venue with words only: no offer, one invite per team."""

from dataclasses import replace

from bazaar_agent.agents.market import venues_from
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.agents.sales_invite import SalesInvite, pick_team
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails, Ledger
from tests.test_rivals import bid
from tests.test_team_desk import RASTRO, TICK, US, Team, view

OWN = {
    **RASTRO,
    "venue": "v19",
    "house": False,
    "owner": US,
    "name": "Team 1 market",
    "fee_bps": 0,
    "fee_per_card": 0,
    "status": "open",
}


def setup(tmp_path, **kw):
    rules = Guardrails(team_threads_enabled=True, pause_file=str(tmp_path / "PAUSE"))
    team, ledger = Team(), Ledger(tmp_path / "ledger.jsonl")
    rec = Recorder("sales", DecisionLog(tmp_path), True, lambda _: None)
    v = view()
    events = [
        bid(1, TICK - 5, "t05", "LAT-03", 9, expires=TICK + 50),
        bid(2, TICK - 4, "t07", "LAV-09", 9, expires=TICK + 50),
    ]
    v = replace(v, events=events, venues=venues_from({"venues": [RASTRO, OWN]}), **kw)
    return SalesInvite(team, rules, ledger, rec, lambda _: None, True), team, ledger, v


def test_invite_goes_to_the_team_bidding_for_our_duplicate_with_words_only(tmp_path):
    actor, team, ledger, v = setup(tmp_path)
    assert actor.on_tick(v) is True
    assert team.sent[0] == ("open_thread", "t05", {"trade": "cards"}, "v19")
    kind, tid, offer = team.sent[1]
    assert (kind, tid, offer) == ("say", 42, None)  # no structured offer, no cash
    assert actor.on_tick(v) is True  # t07 next
    assert team.sent[2][1] == "t07"
    assert actor.on_tick(v) is False  # everyone invited once


def test_no_invite_without_our_free_venue_or_when_paused(tmp_path):
    actor, team, _, v = setup(tmp_path)
    assert actor.on_tick(replace(v, venues=venues_from({"venues": [RASTRO]}))) is False
    (tmp_path / "PAUSE").touch()
    assert actor.on_tick(v) is False
    assert team.sent == []


def test_busy_and_blocked_teams_are_skipped(tmp_path):
    actor, _, _, v = setup(tmp_path)
    busy = replace(v, threads=[{"id": 9, "kind": "team", "with": "t05", "team": "t05", "status": "open"}])
    assert pick_team(busy, actor.rules, lambda _: False) == "t07"
