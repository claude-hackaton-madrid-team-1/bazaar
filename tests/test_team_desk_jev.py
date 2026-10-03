"""N17 enable: the Jev gate on every swap send, the hourly swap cash cap, and the money rules on the propose path
(fakes, no network)."""

import pytest

from bazaar_agent.agents.runtime import JevAdvice, no_jev
from bazaar_agent.agents.team_desk import TEAM_SPEND, DeskView
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
    assert swap["get"] == {"card": "LAV-02", "copies_held": 0, "official_value": 14.0, "private_value": 16.0}
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
