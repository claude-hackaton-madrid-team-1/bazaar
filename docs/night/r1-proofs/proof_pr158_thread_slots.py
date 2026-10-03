"""r1 proof (PR #158): `_open` overwrites the thread-slot `room` with the cash room, so with
dealer_final_lift = 0 (today) the taker opens more dealer threads than max_dealer_threads /
max_open_threads_per_team allow. Fakes only, no network."""

from copy import deepcopy

from bazaar_agent.agents.taker import Taker, TakerConfig
from tests.agent_fakes import FakePublic, FakeTeam, clock, parts
from tests.test_strategy import ABUELA, ME


def test_lift_off_never_opens_more_threads_than_the_slot_room(tmp_path):
    clone = {**deepcopy(ABUELA), "id": "abuelo"}
    clone["menu"] = {"sells": [{"rarity": "common", "sets": "released", "list_price": 9}]}
    me = {**ME, "unlocked": ["abuela", "abuelo"]}
    team = FakeTeam(me=me)
    kw = parts(tmp_path)  # dealer_final_lift = 0: today
    t = Taker(
        team,
        FakePublic(dealers=[ABUELA, clone]),
        live=True,
        log=lambda s: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=1),  # one dealer conversation at a time
        **kw,
    )
    t.on_tick(clock())
    opened = [s for s in team.sent if s[0] == "open_thread"]
    assert len(opened) <= 1, opened
