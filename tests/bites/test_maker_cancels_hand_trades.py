"""BITE X19: the live maker cancels every board offer of ours that is not one of its targets.

By design (`agents/maker.py` docstring: "one we listed by hand that is not a strategy target is cancelled,
so stop the maker before trading by hand"). The bite is operational: the Railway maker is LIVE from 09:00, and
the night plans post by hand at 09:00 (W4 `trade-plan --live` → `sell list --to tNN` / `sell bid`; W8 arbitrage
exits; Marius's own listings). Each such offer is cancelled on the maker's next tick, ~30 s later, and still
counts toward the 12 new listings per tick ("a cancelled one still counts").

This test documents the behaviour (it passes today); it is the evidence for the runbook rule, not a code bug.
"""

import pytest

from tests.agent_fakes import ask, bid, clock
from tests.bites.strictness import STRICT
from tests.test_maker import NoAccept, maker


def test_the_live_maker_cancels_a_hand_posted_listing_and_bid(tmp_path):
    hand_ask = {**ask(77, "LAV-06", 60, asset=2, maker="t01"), "to": "t12"}  # `sell list 2 60 --to t12`
    hand_bid = bid(78, "MAL-07", 20)  # `sell bid MAL-07 20`
    team = NoAccept(offers=[hand_ask, hand_bid])
    m, _ = maker(tmp_path, team, live=True)
    m.on_tick(clock())
    cancelled = {s[1] for s in team.sent if s[0] == "cancel"}
    assert cancelled == {77, 78}


@pytest.mark.xfail(strict=STRICT, reason="BITE X19: on main PAUSE stops the maker's posts, not its cancels (#68 fixes)")
def test_the_pause_file_stops_the_makers_cancels_too(tmp_path, monkeypatch):
    """On main `Maker._cancel` never consults the kill switch: PAUSE holds posts but the hand bid is still
    cancelled. #68 (inside #72) makes the switch a hold: this test passes there."""
    from bazaar_agent import guardrails

    monkeypatch.setattr(guardrails, "REPO_ROOT", tmp_path)
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "PAUSE").touch()
    team = NoAccept(offers=[bid(78, "MAL-07", 20)])
    m, _ = maker(tmp_path, team, live=True)
    m.on_tick(clock())
    assert not [s for s in team.sent if s[0] == "list_offer"]  # posts are held
    assert ("cancel", 78) not in team.sent, "PAUSE is on, yet the maker cancelled the hand bid"
