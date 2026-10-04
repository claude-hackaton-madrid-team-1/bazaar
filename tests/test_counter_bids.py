"""Counters to bids other teams address to us (Sun 4 Oct, Marius: "treat it as a duel, fight to get on top of our
floor"): an ask addressed back to the bidder, from an anchor down to our floor in steps, never under it."""

from bazaar_agent.agents import counter_bids as cb
from bazaar_agent.agents.maker import Maker, MakerConfig
from tests.agent_fakes import TICK, FakePublic, FakeTeam, bid, clock, parts, rows

# LAT-09 (#5, our only copy, LAT page complete): your_value 35 + page bonus 10 = 45 lost by selling it; with
# sell_min_surplus 5 our floor is 50 and the anchor 63 (floor × 1.25).


class Team(FakeTeam):
    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.sent.append(("list_offer", give, want, venue, to))
        return {"id": next(self._ids), "status": "open", "expires_tick": self.now.tick + 20}


def to_us(offer, maker="t15"):
    return {**offer, "maker": maker, "to": "t01"}


def maker(tmp_path, team, *, live=False, counter=True, **rules):
    lines: list[str] = []
    m = Maker(
        team,
        FakePublic(),
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        config=MakerConfig(counter_bids=counter),
        **parts(tmp_path, **rules),
    )
    return m, lines


def counter_posts(root):
    return [
        r
        for r in rows(root)
        if r.get("kind") == "post_ask" and r["inputs"].get("ref") == "LAT-09" and r["inputs"].get("to")
    ]


def skips(root):
    return {(r["inputs"]["team"], r["inputs"]["ref"]): r["reason"] for r in rows(root) if r["kind"] == "counter_bid"}


# ---------------------------------------------------------------- the price path (pure)


def test_the_counter_opens_above_the_floor_and_concedes_down_to_it_never_under():
    assert cb.anchor_for(87) == 109 and cb.anchor_for(2) == 3  # always at least one above the floor
    path = [cb.counter_price(109, 87, t)[0] for t in range(0, 20, 3)]
    assert path == [109, 104, 98, 93, 87, 87, 87]
    assert min(cb.counter_price(109, 87, t)[0] for t in range(200)) == 87


def test_the_interest_book_keeps_the_first_tick_and_forgets_a_team_after_the_ttl():
    from bazaar_agent.agents.market import BoardOffer

    b = BoardOffer(1, "rastro", "t15", "bid", "LAT-09", 20, None, None, 140, 90)
    seen = cb.observe({}, [b], 100)
    seen = cb.observe(seen, [BoardOffer(2, "rastro", "t15", "bid", "LAT-09", 24, None, None, 140, 90)], 103)
    assert seen[("t15", "LAT-09")] == cb.Seen(100, 103, 24)
    assert cb.observe(seen, [], 103 + cb.TTL_TICKS) != {}  # their bid lapsed: our counter still stands
    assert cb.observe(seen, [], 104 + cb.TTL_TICKS) == {}


# ---------------------------------------------------------------- the maker


def test_a_low_bid_addressed_to_us_gets_an_addressed_counter_that_steps_to_our_floor(tmp_path):
    team = Team(offers=[to_us(bid(76297, "LAT-09", 20))])
    m, _ = maker(tmp_path, team)
    m.on_tick(clock())
    (first,) = counter_posts(tmp_path)
    assert (first["inputs"]["price"], first["inputs"]["to"], first["inputs"]["asset_id"]) == (63, "t15", 5)
    assert "counter to t15's bid 20" in first["reason"] and first["status"] == "approved"
    for k in range(1, 16):  # their bid stays; we concede every STEP_TICKS ticks down to the floor
        team.now = clock(tick=TICK + k)
        m.on_tick(team.now)
    prices = [r["inputs"]["price"] for r in counter_posts(tmp_path)]
    assert prices[0] == 63 and prices[-1] == 50 and min(prices) == 50
    assert prices == sorted(prices, reverse=True)  # only ever down, never under the floor


def test_a_live_counter_is_posted_addressed_to_the_bidder(tmp_path):
    team = Team(offers=[to_us(bid(76297, "LAT-09", 20))])
    m, _ = maker(tmp_path, team, live=True)
    m.on_tick(clock())
    posts = [s for s in team.sent if s[0] == "list_offer" and s[4] == "t15"]
    assert posts and posts[0][2] == {"cash": 63}


def test_no_counter_for_a_protected_only_copy_a_pseudonym_a_rival_or_a_bid_that_already_clears(tmp_path):
    protected = Team(offers=[to_us(bid(1, "LAT-09", 20))])
    maker(tmp_path / "p", protected, protect_page_sets="LAT")[0].on_tick(clock())
    assert counter_posts(tmp_path / "p") == []
    assert "protect_page_sets" in skips(tmp_path / "p")[("t15", "LAT-09")]

    pseudonym = Team(offers=[to_us(bid(2, "LAT-09", 20), maker="m9")])
    maker(tmp_path / "m", pseudonym)[0].on_tick(clock())
    assert counter_posts(tmp_path / "m") == [] and "not a team id" in skips(tmp_path / "m")[("m9", "LAT-09")]

    rival = Team(offers=[to_us(bid(3, "LAT-09", 20), maker="t05")])
    maker(tmp_path / "r", rival, team_desk_never_trade="t05")[0].on_tick(clock())
    assert counter_posts(tmp_path / "r") == [] and "never_trade" in skips(tmp_path / "r")[("t05", "LAT-09")]

    rich = Team(offers=[to_us(bid(4, "LAT-09", 70))])  # 70 - fee 5 = 65 >= 45 + 5: the taker sells into it
    maker(tmp_path / "c", rich)[0].on_tick(clock())
    assert counter_posts(tmp_path / "c") == [] and "taker sells into it" in skips(tmp_path / "c")[("t15", "LAT-09")]

    hopeless = Team(offers=[to_us(bid(5, "LAT-09", 10))])  # floor 50 > 3 x 10
    maker(tmp_path / "h", hopeless)[0].on_tick(clock())
    assert counter_posts(tmp_path / "h") == [] and "nothing to meet" in skips(tmp_path / "h")[("t15", "LAT-09")]


def test_the_higher_bidder_gets_the_counter_and_off_turns_it_off(tmp_path):
    team = Team(offers=[to_us(bid(1, "LAT-09", 20), maker="t15"), to_us(bid(2, "LAT-09", 24), maker="t16")])
    maker(tmp_path / "two", team)[0].on_tick(clock())
    (post,) = counter_posts(tmp_path / "two")
    assert post["inputs"]["to"] == "t16" and "t16 bids more" in skips(tmp_path / "two")[("t15", "LAT-09")]

    off = Team(offers=[to_us(bid(1, "LAT-09", 20))])
    maker(tmp_path / "off", off, counter=False)[0].on_tick(clock())
    assert counter_posts(tmp_path / "off") == [] and skips(tmp_path / "off") == {}
