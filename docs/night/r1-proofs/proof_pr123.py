"""r1 proofs for #123 (scratch, not committed)."""

from bazaar_agent.agents.status import public_decision
from bazaar_agent.ledger_pg import LedgerUnavailable
from tests.agent_fakes import FakePublic, clock, parts
from tests.agent_fakes import rows as decision_rows
from tests.test_team_desk import TICK, THEM, Team, _Plan, their_offer, thread, trade


def _taker(tmp_path, team, ledger=None):
    from bazaar_agent.agents.taker import Taker, TakerConfig

    p = parts(tmp_path, team_threads_enabled=True)
    if ledger is not None:
        p["ledger"] = ledger
    t = Taker(team, FakePublic(), live=True, log=lambda _: None, now=lambda: 1000.0, sleep=lambda s: None,
              config=TakerConfig(max_dealer_threads=0), **p)
    t.team_desk.env = {}
    t.team_desk._plan = _Plan(TICK, (trade(),), {"LAV-02": 16.0})
    return t


def test_a_team_accept_on_public_state_names_neither_card(tmp_path):
    fair = thread(messages=[{"sender": THEM, "tick": TICK, "text": "trato"}], offers=[their_offer(cash_out=1)])
    team = Team(threads=[fair])
    _taker(tmp_path, team).on_tick(clock())
    assert ("accept", 900, None) in team.sent
    (row,) = [r for r in decision_rows(tmp_path) if r.get("kind") == "team_accept"]
    shown = repr(public_decision({**row, "dry_run": False}))
    # The desk's own rows hide the cards (give_card/want_card); the taker's team_accept row does not:
    assert "LAT-03" not in shown and "LAV-02" not in shown, shown


def test_a_ledger_outage_at_the_accept_slot_sends_no_accept(tmp_path):
    from bazaar_agent.guardrails import Ledger

    class Down(Ledger):
        def accept_items(self, tick):
            raise LedgerUnavailable("down")

        def reserve_accept(self, *a, **k):
            raise LedgerUnavailable("down")

    fair = thread(messages=[{"sender": THEM, "tick": TICK, "text": "trato"}], offers=[their_offer(cash_out=1)])
    team = Team(threads=[fair])
    _taker(tmp_path, team, Down(tmp_path / "l.jsonl")).on_tick(clock())
    assert not [s for s in team.sent if s[0] in ("accept", "say", "cancel")]
