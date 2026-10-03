from bazaar_agent import level_path as lp
from tests.test_intel import opened, settle


def event(eid, tick, kind, **payload):
    return {"id": eid, "tick": tick, "type": kind, "payload": payload}


def bid(eid, thread, team, price, tick):
    offer = {"give": {"cash": price}, "want": {"types": ["card:LAV-03"]}}
    payload = {"kind": "persona", "thread": thread, "sender": team, "with": "abuela", "offer": offer}
    return {"id": eid, "tick": tick, "type": "thread.message", "payload": payload}


def ask(eid, thread, price, tick):
    offer = {"give": {"types": ["card:LAV-03"]}, "want": {"cash": price}}
    payload = {"kind": "persona", "thread": thread, "sender": "abuela", "with": "abuela", "offer": offer}
    return {"id": eid, "tick": tick, "type": "thread.message", "payload": payload}


def deal(base, thread, team, opening, fill, tick, our_bid=True):
    """One Abuela thread: her opening ask, our bid (or none), settled at `fill`."""
    out = [opened(base, thread, team, {"buy": {"card": "LAV-03"}}, tick=tick), ask(base + 1, thread, opening, tick)]
    if our_bid:
        out.append(bid(base + 2, thread, team, fill, tick))
    out.append(settle(base + 3, base, "abuela", team, "LAV-03", fill, tick=tick + 1, kind="card"))
    return out


def friday():
    events = []
    for i in range(3):  # t02: three negotiated buys -> unlocked at activation
        events += deal(100 + 10 * i, 10 + i, "t02", 12, 9, 5 + i)
    for i in range(3):  # t15: three buys at her opening price, no bid -> never early
        events += deal(200 + 10 * i, 20 + i, "t15", 12, 12, 5 + i, our_bid=False)
    events += [
        event(1, 71, "level.announced", level="chato", name="El Chato", teaser="If I like you."),
        event(2, 98, "level.activated", level="chato", name="El Chato", how="he buys", opens_to_all_in_hours=1.0),
        event(3, 98, "level.unlocked", team="t02", persona="chato", level=2, why="3 deals with abuela"),
        event(4, 158, "persona.open_to_all", persona="chato", name="El Chato", level=2),
        event(5, 158, "level.unlocked", team="t15", persona="chato", level=2, why="open to everyone now"),
    ]
    return sorted(events, key=lambda e: e["id"])


def test_timeline_and_unlocks_from_the_feed():
    (t,) = lp.timelines(friday())
    assert (t.dealer, t.announced, t.activated, t.opened_to_all, t.head_start_hours) == ("chato", 71, 98, 158, 1.0)
    assert (t.early, t.late) == (1, 1)
    early = next(u for u in lp.unlocks(friday()) if u.team == "t02")
    assert (early.deals, early.previous, early.open_to_all) == (3, "abuela", False)


def test_a_rule_that_credits_a_late_team_its_minimum_is_contradicted():
    fits = {f.rule: f for f in lp.fit_rules(friday())}
    assert (
        fits["buys not at the opening price"].exact == 1 and fits["buys not at the opening price"].contradicted_by == ()
    )
    assert fits["buys"].contradicted_by == ("t15",)  # t15's three opening-price buys did not open El Chato


def test_requirements_read_the_dealers_unlock_rule_and_count_our_deals():
    dealers = [
        {"id": "abuela", "level": 1, "status": "active", "unlock": {"always": True, "early_min_deals": 3}},
        {"id": "chato", "level": 2, "status": "active", "unlock": {"early_deals_with": "abuela", "early_min_deals": 3}},
    ]
    reqs = {r.dealer: r for r in lp.requirements(dealers, friday(), "t02")}
    assert reqs["abuela"].deals_with is None
    assert (reqs["chato"].ours, reqs["chato"].missing) == (3, 0)
    assert {r.dealer: r.missing for r in lp.requirements(dealers, friday(), "t15")}["chato"] == 3
