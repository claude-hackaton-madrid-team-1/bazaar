"""Omar, Sat 21:55: weaker teams and teams that answer us first; every swap proposal invites them to our venue."""

from bazaar_agent.agents.team_desk import TeamDesk, venue_invite
from bazaar_agent.guardrails import load_guardrails
from tests.test_team_desk import TICK, Team, desk, thread, trade, view


class Talking(Team):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.texts: list[str] = []

    def say(self, tid, text="", price=None, offer=None, topic=None):
        self.texts.append(text)
        return super().say(tid, text, price, offer, topic)


def order(d: TeamDesk, *teams: str) -> list[str]:
    plans = [trade(t) for t in teams]
    keyed = sorted(plans, key=lambda t: d._priority(t, {}, d.answered, d.ranks))
    return [t.counterparty for t in keyed]


def test_a_team_that_answered_us_comes_first_then_the_weaker_by_rank(tmp_path):
    d, _ = desk(tmp_path, Team())
    d.ranks = {"t02": 3, "t08": 15, "t16": 11}
    assert order(d, "t02", "t08", "t16") == ["t08", "t16", "t02"]  # weaker (higher rank number) first
    d.answered["t02"] += 1
    assert order(d, "t02", "t08", "t16") == ["t02", "t08", "t16"]  # it answers our proposals: likelier to deal
    assert order(d, "t99", "t16") == ["t16", "t99"]  # an unknown rank goes after the known weaker ones


def test_a_reply_after_our_proposal_counts_as_an_answer(tmp_path):
    team = Team()
    d, _ = desk(tmp_path, team)
    d.proposals(view())
    d.converse(view(), set())  # opens thread 42 and proposes
    reply = thread(messages=[{"sender": "t05", "tick": TICK + 1, "text": "mmm"}])
    d.proposals(view([reply], tick=TICK + 1))
    assert d.answered["t05"] == 1


def test_every_proposal_carries_one_true_invite_to_our_venue(tmp_path):
    team = Talking()
    d, _ = desk(tmp_path, team, team_words_venue_invite="v19")
    d.proposals(view())
    d.converse(view(), set())
    (text,) = team.texts
    assert text.count("v19") == 1 and "0 %" in text and "5 % + 1 P" in text
    anchor = [s[2] for s in team.sent if s[0] == "say"]
    plain = Talking()
    d2, _ = desk(tmp_path / "plain", plain)
    d2.proposals(view())
    d2.converse(view(), set())
    assert anchor == [s[2] for s in plain.sent if s[0] == "say"]  # words only: the same structured offer


def test_none_sends_no_invite_and_the_shipped_rules_invite_to_v19(tmp_path):
    team = Talking()
    d, _ = desk(tmp_path, team)  # code default: none
    d.proposals(view())
    d.converse(view(), set())
    assert team.texts and not any("v19" in m for m in team.texts)
    assert load_guardrails().rules.team_words_venue_invite == "v19"
    line = venue_invite("v19")
    assert "v19" in line and "amigo" not in line.lower() and len(line) < 200


def test_expected_gain_beats_reply_history_and_rival_rank(tmp_path):
    from dataclasses import replace

    d, _ = desk(tmp_path, Team())
    d.answered["t08"] = 10
    d.ranks = {"t10": 1, "t08": 18}
    plans = [replace(trade("t08"), ours=2, p_fill=1), replace(trade("t10"), ours=8, p_fill=0.5)]
    assert sorted(plans, key=lambda t: d._priority(t, {}, d.answered, d.ranks))[0].counterparty == "t10"
    # An uncertain large nominal gain is not mistaken for a better expected outcome.
    plans[1] = replace(plans[1], p_fill=0.1)
    assert sorted(plans, key=lambda t: d._priority(t, {}, d.answered, d.ranks))[0].counterparty == "t08"


def test_agent_opens_highest_surplus_team_from_one_all_page_plan(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from bazaar_agent.agents import team_desk as td

    calls = []
    low = replace(trade("t08"), p_fill=0.1)
    high = trade("t10")

    def plan(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(threads=(low, high))

    monkeypatch.setattr(td, "build_plan", plan)
    team = Team()
    d, _ = desk(tmp_path, team, team_threads_max_open=1)
    d._plan = None
    d.answered["t08"] = 3
    d.ranks = {"t08": 18, "t10": 1}
    d.proposals(view())
    d.converse(view(), set())
    assert calls == [{}]  # no separate nearest-page search consumes or biases this tick
    assert [s[1] for s in team.sent if s[0] == "open_thread"] == ["t10"]
    assert len([s for s in team.sent if s[0] == "say"]) == 1
