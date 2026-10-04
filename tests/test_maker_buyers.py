"""The maker with `buyer_rank_enabled`: an ask it already decided to post goes to the best non-rival buyer,
falls back to a public ask after `buyer_rank_fallback_ticks`, and never goes to one team twice at one price."""

from bazaar_agent.agents.maker import Maker
from bazaar_agent.guardrails import Guardrails
from tests.agent_fakes import CHEAP, RASTRO, TICK, FakePublic, FakeTeam, clock, our_ask, parts, rows

BOARD = {
    "teams": [
        {"team": "t14", "rank": 1},
        {"team": "t13", "rank": 4},
        {"team": "t07", "rank": 12},
        {"team": "t01", "rank": 9},
    ]
}


class Team(FakeTeam):
    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.sent.append(("list_offer", give, want, venue, to))
        return {"id": next(self._ids), "status": "open"}


class Public(FakePublic):
    def __init__(self, board=BOARD, **kw):
        super().__init__(**kw)
        self.board_reads = 0
        self._board = board

    def leaderboard(self):
        self.board_reads += 1
        if self._board is None:
            raise RuntimeError("leaderboard down")
        return self._board


def maker(tmp_path, team, public, **rules):
    lines: list[str] = []
    m = Maker(team, public, live=True, log=lines.append, now=lambda: 1000.0, **parts(tmp_path, **rules))
    return m, lines


def asks(team):
    return [(s[2]["cash"], s[4]) for s in team.sent if s[0] == "list_offer" and s[2].get("cash")]


def test_off_by_default_every_ask_stays_public_and_no_leaderboard_is_read(tmp_path):
    assert Guardrails().buyer_rank_enabled is False
    team, public = Team(), Public()
    maker(tmp_path, team, public)[0].on_tick(clock())
    assert asks(team) and all(to is None for _, to in asks(team))
    assert public.board_reads == 0


def test_on_an_ask_goes_to_the_best_buyer_and_never_to_a_rival(tmp_path):
    team, public = Team(), Public()
    maker(tmp_path, team, public, buyer_rank_enabled=True)[0].on_tick(clock())
    sent = dict((price, to) for price, to in asks(team))
    assert sent[68] is not None and sent[68] not in ("t14", "t13", "t01")  # LAT-09: t14 is rank 1, t13 top 5
    row = next(
        r for r in rows(tmp_path) if r.get("kind") == "post_ask" and r.get("chosen") and r["inputs"]["ref"] == "LAT-09"
    )
    assert row["inputs"]["to"] == sent[68] and "buyer rank" in row["reason"]


def test_prices_are_unchanged_by_the_buyer_rank(tmp_path):
    off, on = Team(), Team()
    maker(tmp_path / "off", off, Public())[0].on_tick(clock())
    maker(tmp_path / "on", on, Public(), buyer_rank_enabled=True)[0].on_tick(clock())
    assert sorted(p for p, _ in asks(off)) == sorted(p for p, _ in asks(on))


def test_an_unreadable_leaderboard_keeps_every_ask_public(tmp_path):
    team, public = Team(), Public(board=None)
    m, lines = maker(tmp_path, team, public, buyer_rank_enabled=True)
    m.on_tick(clock())
    assert asks(team) and all(to is None for _, to in asks(team))
    assert any("leaderboard" in line for line in lines)


def test_the_leaderboard_is_read_once_per_window_of_ticks(tmp_path):
    team, public = Team(), Public()
    m, _ = maker(tmp_path, team, public, buyer_rank_enabled=True)
    for tick in range(TICK, TICK + 5):
        m.on_tick(clock(tick=tick))
    assert public.board_reads == 1


def test_an_addressed_ask_unfilled_for_n_ticks_falls_back_to_public_at_the_same_price(tmp_path):
    team, public = Team(), Public()
    m, _ = maker(tmp_path, team, public, buyer_rank_enabled=True, buyer_rank_fallback_ticks=3)
    m.on_tick(clock())
    to = dict(asks(team))[68]
    lists = [s for s in team.sent if s[0] == "list_offer"]
    posted_id = 5000 + next(i for i, s in enumerate(lists) if s[2].get("cash") == 68)  # FakeTeam ids from 5000
    team.sent.clear()
    standing = {**our_ask(posted_id, 5, "LAT-09", 68, created=TICK, expires=TICK + 40), "to": to}
    team.offers = [standing]
    m.on_tick(clock(tick=TICK + 2))  # 2 ticks old: kept
    assert ("cancel", posted_id) not in team.sent
    m.on_tick(clock(tick=TICK + 3))  # 3 ticks old: cancelled and reposted for anyone, same price
    assert ("cancel", posted_id) in team.sent
    assert (68, None) in asks(team)


def test_with_jev_the_fallback_is_public_at_the_exact_price(tmp_path):
    from bazaar_agent.agents.maker_jev import MakerJev
    from tests.test_maker_jev import FakeJev

    team, public = Team(), Public()
    jev = MakerJev(FakeJev("fair"), FakeJev("no"))
    rules = parts(tmp_path, buyer_rank_enabled=True, buyer_rank_fallback_ticks=3)
    m = Maker(team, public, live=True, log=lambda line: None, now=lambda: 1000.0, jev=jev, **rules)
    m.on_tick(clock())
    [(price, to)] = [(p, t) for p, t in asks(team) if t is not None and p > 20]
    lists = [s for s in team.sent if s[0] == "list_offer"]
    posted_id = 5000 + next(i for i, s in enumerate(lists) if s[2].get("cash") == price)
    team.sent.clear()
    team.offers = [{**our_ask(posted_id, 5, "LAT-09", price, created=TICK, expires=TICK + 40), "to": to}]
    m.on_tick(clock(tick=TICK + 3))
    assert ("cancel", posted_id) in team.sent
    assert (price, None) in asks(team) and all(t != to for p, t in asks(team) if p != price or t is not None)


def test_a_hostile_feed_string_keeps_asks_public_and_the_tick_alive(tmp_path, monkeypatch):
    from bazaar_agent import buyers

    def boom(*a, **k):
        raise AttributeError("'str' object has no attribute 'get'")

    monkeypatch.setattr(buyers, "market_inputs", boom)
    team, public = Team(), Public()
    m, lines = maker(tmp_path, team, public, buyer_rank_enabled=True)
    m.on_tick(clock())
    assert asks(team) and all(to is None for _, to in asks(team))
    assert any("buyer rank failed (AttributeError)" in line for line in lines)


# ---------------------------------------------------------------- `venue_avoid_rivals`: never list on a rival's venue

RIVAL_VENUE = {**CHEAP, "owner": "t13", "trades": 60}  # t13 is rank 4: top 5, a rival; its free venue is the busiest


def venues_listed(team):
    return {s[3] for s in team.sent if s[0] == "list_offer"}


def test_off_the_maker_lists_on_the_busiest_venue_even_a_rivals(tmp_path):
    assert Guardrails().venue_avoid_rivals is False
    team, public = Team(), Public(venues=(RASTRO, RIVAL_VENUE))
    maker(tmp_path, team, public)[0].on_tick(clock())
    assert venues_listed(team) == {"v02"}
    assert public.board_reads == 0  # neither switch on: no leaderboard read


def test_on_the_maker_never_lists_on_a_rivals_venue_and_falls_back_to_the_house(tmp_path):
    team, public = Team(), Public(venues=(RASTRO, RIVAL_VENUE))
    m, _ = maker(tmp_path, team, public, venue_avoid_rivals=True)
    m.on_tick(clock())
    assert venues_listed(team) == {"rastro"}
    assert public.board_reads == 1
    assert m._avoided_owners("t01") == frozenset({"t14", "t13"})  # top 5; nobody sits 1-3 ranks above our 9th


def test_on_without_a_readable_leaderboard_no_venue_is_avoided(tmp_path):
    team, public = Team(), Public(board=None, venues=(RASTRO, RIVAL_VENUE))
    maker(tmp_path, team, public, venue_avoid_rivals=True)[0].on_tick(clock())
    assert venues_listed(team) == {"v02"}


def test_team_penalty_the_maker_weighs_el_rastro_up_against_a_busier_free_team_venue(tmp_path):
    assert Guardrails().venue_team_penalty == 0.0
    team, public = Team(), Public(venues=(RASTRO, {**CHEAP, "trades": 60}))  # t12 is no rival in BOARD
    maker(tmp_path, team, public, venue_team_penalty=0.5)[0].on_tick(clock())
    assert venues_listed(team) == {"rastro"}
