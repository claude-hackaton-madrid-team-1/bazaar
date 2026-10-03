"""BITE X18: a card accepted at tick T is not "held" until `/api/me` shows it; the next tick can buy it again.

`block_buying_held_cards` and the cash floor read `/api/me` only. An accept settles "at the next tick"; if the
read at T+1 lands before the server has settled (a slow tick under Sunday load, a clock read that wakes
us early), the card is still missing and the cash still full, and the taker accepts a second copy of the same
card from another seller: a duplicate (worth 0.25× to us) at full price. The ledger already knows: it holds
the accept row for that card at tick T. Real-server settlement timing is unverified; the test models the lag.

Strict xfail: passes once recent accepts count as held (then drop the marker).
"""

import pytest

from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask, clock
from tests.test_taker import at, taker


@pytest.mark.xfail(strict=True, reason="BITE X18: an unsettled accept is not counted as held; a duplicate is bought")
def test_a_card_accepted_last_tick_is_not_bought_again_before_it_settles(tmp_path):
    team = FakeTeam()
    public = FakePublic(boards={"rastro": [ask(2, "LAV-08", 20, asset=901), ask(3, "LAV-08", 21, asset=902)]})
    t, _, ledger = taker(tmp_path, team, public, live=True)
    t.on_tick(clock())
    assert team.sent == [("accept", 2)] and ledger.accept_items(TICK) == ["LAV-08"]

    public.boards["rastro"] = [ask(3, "LAV-08", 21, asset=902)]  # the other copy is still listed
    t.on_tick(at(team, TICK + 1))  # /api/me read before the server settled offer 2
    assert team.sent == [("accept", 2)], f"bought LAV-08 twice: {team.sent}"
