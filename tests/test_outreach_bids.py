"""Outreach (Sun 4 Oct, Marius: "constantly interact with other teams for cards that might have a good value for
us"): our bids for wanted cards go addressed to the teams holding a spare, stepping up to our ceiling, holder after
holder."""

from bazaar_agent.agents import outreach_bids as ob
from bazaar_agent.agents.maker import Maker, MakerConfig
from tests.agent_fakes import TICK, FakePublic, clock, parts, rows
from tests.test_counter_bids import Team

# The fixture's maker bids 65 for LAV-09 (a missing page card).


class Matrix:
    def __init__(self, spare):
        self.spare, self.tick = spare, TICK

    def card(self, ref):
        return {"spare": [{"team": t, "holds": 2} for t in self.spare.get(ref, [])], "missing": []}


class Latest:
    def __init__(self, matrix):
        self.matrix = matrix

    def current(self, tick):
        return self.matrix

    def refresh(self, tick):
        pass


def maker(tmp_path, team, matrix, *, outreach=True, **rules):
    return Maker(
        team,
        FakePublic(),
        live=False,
        log=lambda line: None,
        now=lambda: 1000.0,
        config=MakerConfig(counter_bids=False, outreach_bids=outreach),
        latest_matrix=Latest(matrix),
        **parts(tmp_path, **rules),
    )


def bids(root):
    return [(r["inputs"]["price"], r["inputs"].get("to")) for r in rows(root) if r["kind"] == "post_bid"]


def test_the_bid_opens_low_and_steps_up_to_the_ceiling_never_over():
    assert [ob.outreach_price(65, t)[0] for t in range(0, 16, 3)] == [46, 51, 56, 61, 65, 65]
    assert max(ob.outreach_price(65, t)[0] for t in range(500)) == 65
    assert ob.turn_ticks() == 24


def test_our_bid_goes_to_the_teams_holding_a_spare_one_after_the_other(tmp_path):
    team = Team()
    m = maker(tmp_path, team, Matrix({"LAV-09": ["t09", "t11"]}))
    for k in range(ob.turn_ticks() + 1):
        team.now = clock(tick=TICK + k)
        m.on_tick(team.now)
    seen = bids(tmp_path)
    assert seen[0] == (46, "t09") and (65, "t09") in seen  # opens low, reaches our ceiling
    assert seen[-1] == (46, "t11")  # t09 had its turn: t11 gets the bid, from the opening price again
    assert all(price <= 65 for price, _ in seen)
    (row,) = [r for r in rows(tmp_path) if r["kind"] == "post_bid"][:1]
    assert "outreach: bid 46 for LAV-09 addressed to t09" in row["reason"]


def test_without_a_holder_a_matrix_or_the_switch_the_bid_stays_public(tmp_path):
    maker(tmp_path / "none", Team(), Matrix({})).on_tick(clock())
    assert bids(tmp_path / "none") == [(65, None)]
    m = maker(tmp_path / "stale", Team(), None)
    m.on_tick(clock())
    assert bids(tmp_path / "stale") == [(65, None)]
    maker(tmp_path / "off", Team(), Matrix({"LAV-09": ["t09"]}), outreach=False).on_tick(clock())
    assert bids(tmp_path / "off") == [(65, None)]


def test_a_team_we_never_trade_with_is_skipped(tmp_path):
    maker(tmp_path, Team(), Matrix({"LAV-09": ["t09", "t11"]}), team_desk_never_trade="t09").on_tick(clock())
    assert bids(tmp_path) == [(46, "t11")]
