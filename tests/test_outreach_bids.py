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


class Valued(Team):
    def __init__(self, values, **kw):
        super().__init__(**kw)
        self.values, self.value_reads = values, []

    def value(self, card):
        self.value_reads.append(card)
        return {"card": card, "your_value": self.values.get(card, 500.0)}


def test_a_missing_page_card_a_team_holds_spare_gets_a_bid_even_when_the_strategy_bids_nothing(tmp_path):
    # Sun 10:25: the maker had no bid at all (the strategy sent our missing cards to dealers). LAV-02 is missing and
    # t09 holds a spare: bid from 70 % of min(max_price_common 12, official 20 - min_buy_surplus 2) = 12, up to 12.
    team = Valued({"LAV-02": 20.0})
    m = maker(tmp_path, team, Matrix({"LAV-02": ["t09"]}))
    for k in range(25):
        team.now = clock(tick=TICK + k)
        m.on_tick(team.now)
    lav02 = [
        (r["inputs"]["price"], r["inputs"].get("to")) for r in rows(tmp_path) if r["inputs"].get("ref") == "LAV-02"
    ]
    assert lav02[0] == (9, "t09") and max(p for p, _ in lav02) == 12
    assert team.value_reads.count("LAV-02") <= 3  # cached for 20 ticks, not read every tick


def test_the_ceiling_stays_under_the_official_value_and_the_approval_threshold(tmp_path):
    team = Valued({"LAV-09": 50.0})  # official 50 - 2 = 48; the approval threshold 40 caps it at 39
    m = maker(tmp_path, team, Matrix({"LAV-09": ["t09"]}), human_approval_above=40)
    for k in range(15):
        team.now = clock(tick=TICK + k)
        m.on_tick(team.now)
    lav09 = [
        r["inputs"]["price"] for r in rows(tmp_path) if r["inputs"].get("ref") == "LAV-09" and r["inputs"].get("to")
    ]
    assert lav09 and max(lav09) == 39
