"""BITE X21: nothing ever opens a pack we hold.

No agent and no CLI command calls the SDK's `open_pack` (`grep -rn open_pack src/` is empty). The taker buys
packs when Jev's pack gate says yes (up to `max_packs_per_game_hour` = 3, at most `max_price_pack` = 20 P each,
judged on `pack_expected_value_to_us`, a value that exists only once the pack is opened), and Saturday's
`grant_all` gives every team a `sobre_barrio`. Those packs stay sealed all weekend: their cards never reach
the album, the page bonus or the trade desk. The fixture `/api/me` holds one sealed pack (asset 6).
"""

import pytest

from tests.agent_fakes import FakePublic, FakeTeam, clock
from tests.bites.strictness import STRICT
from tests.test_taker import taker


class Opens(FakeTeam):
    def open_pack(self, asset_id):
        self.sent.append(("open_pack", asset_id))
        return {"pulled": []}


@pytest.mark.xfail(strict=STRICT, reason="BITE X21: no agent opens the packs we hold (bought or granted)")
def test_a_sealed_pack_we_hold_gets_opened(tmp_path):
    team = Opens()
    assert any(a.get("kind") == "pack" for a in team._me["assets"])  # sobre_barrio, asset 6
    t, _, _ = taker(tmp_path, team, FakePublic(), live=True)
    for tick in range(100, 103):
        t.on_tick(clock(tick=tick))
    assert ("open_pack", 6) in team.sent
