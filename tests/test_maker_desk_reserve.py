"""The maker leaves the team desk its swap copy: never both copies' worth of a pair, never the desk's copy."""

from bazaar_agent.agents import maker as maker_module
from bazaar_agent.agents.maker import Target, _leave_desk_copy
from bazaar_agent.agents.team_desk import desk_copy, spare_copy
from bazaar_agent.guardrails import Guardrails
from tests.agent_fakes import clock, our_ask
from tests.test_maker import NoAccept, maker, posted
from tests.test_strategy import ME

ON = Guardrails(team_threads_enabled=True)


def with_assets(*extra):
    return {**ME, "assets": [*ME["assets"], *extra]}


def lat03(asset_id, value=1.2):
    return {"id": asset_id, "kind": "card", "ref": "LAT-03", "rarity": "common", "your_value": value}


def asked_ids(team):
    return [a for p in posted(team) for a in p[1].get("assets", [])]


def ask(ref, asset_id, value=1.2, price=10):
    return Target("ask", ref, "common", price, asset_id, value, 5.0, "r")


def test_with_team_threads_off_the_maker_lists_a_duplicate_as_today(tmp_path):
    team = NoAccept()
    m, _ = maker(tmp_path, team, live=True)
    m.on_tick(clock())
    assert 4 in asked_ids(team)  # LAT-03 #4, the strategy's copy of our pair


def test_with_two_copies_no_ask_is_posted_and_a_standing_one_is_cancelled(tmp_path):
    team = NoAccept()
    m, _ = maker(tmp_path, team, live=True, team_threads_enabled=True)
    m.on_tick(clock())
    assert not {3, 4} & set(asked_ids(team))
    assert 5 in asked_ids(team)  # LAT-09, a single copy, is listed as before

    standing = NoAccept(offers=[our_ask(1, 4, "LAT-03", 10)])
    m2, lines = maker(tmp_path / "b", standing, live=True, team_threads_enabled=True)
    m2.on_tick(clock())
    assert ("cancel", 1) in standing.sent
    assert any("LAT-03 is no longer a sell target" in line for line in lines)


def test_with_three_copies_the_ask_goes_on_a_copy_that_is_not_the_desks(tmp_path):
    me = with_assets(lat03(7))
    team = NoAccept(me=me)
    m, _ = maker(tmp_path, team, live=True, team_threads_enabled=True)
    m.on_tick(clock())
    lat = [a for a in asked_ids(team) if a in (3, 4, 7)]
    assert lat and desk_copy(me, "LAT-03") not in lat


def test_a_target_on_the_desks_copy_moves_to_an_equal_copy_or_is_dropped():
    me = with_assets(lat03(7))
    assert desk_copy(me, "LAT-03") == 3
    (moved,) = _leave_desk_copy([ask("LAT-03", 3)], me, ON)
    assert moved.asset_id == 4 and moved.value == 1.2
    dear = with_assets(lat03(7, value=9.0))
    dear["assets"] = [lat03(3), lat03(4, value=9.0), lat03(7, value=9.0)]
    assert _leave_desk_copy([ask("LAT-03", 3)], dear, ON) == []  # the others cost us more: no ask at all
    bid = Target("bid", "LAT-03", "common", 5, None, 9.0, 1.0, "r")
    assert _leave_desk_copy([bid], me, ON) == [bid]  # bids untouched


def test_the_maker_and_the_desk_never_promise_the_same_copy_in_one_tick(tmp_path, monkeypatch):
    me = {
        **ME,
        "assets": [
            *ME["assets"],
            lat03(7),
            {"id": 8, "kind": "card", "ref": "LAV-01", "rarity": "common", "your_value": 16.0},
        ],
    }  # LAT-03 x3, LAV-01 x2
    every_copy = [ask(a["ref"], a["id"], a["your_value"]) for a in me["assets"] if a.get("ref") in ("LAT-03", "LAV-01")]
    monkeypatch.setattr(maker_module, "targets_from", lambda book: every_copy)
    team = NoAccept(me=me)
    m, _ = maker(tmp_path, team, live=True, team_threads_enabled=True)
    m.on_tick(clock())
    posted_ids = set(asked_ids(team))
    assert posted_ids and posted_ids.isdisjoint({desk_copy(me, "LAT-03"), desk_copy(me, "LAV-01")})
    asks = [our_ask(100 + i, a, "X", 10) for i, a in enumerate(sorted(posted_ids))]
    for ref in ("LAT-03", "LAV-01"):
        copy = spare_copy(me, asks, "t01", ref)
        assert copy is not None and copy not in posted_ids


def test_a_single_copy_is_listed_as_before_with_team_threads_on():
    target = ask("LAT-09", 5, 35.0, 68)
    assert _leave_desk_copy([target], ME, ON) == [target]
