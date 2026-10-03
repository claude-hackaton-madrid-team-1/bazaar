import math

import pytest

from bazaar_agent import affinity as af

SETS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
MULT = (0.5, 0.75, 1.0, 1.0001, 1.25, 1.5)  # a made-up multiset: the real one comes from /api/me
CATALOG = {
    "sets": [
        {
            "id": s,
            "cards": [{"id": f"{s}-01", "book": 10}, {"id": f"{s}-06", "book": 25}, {"id": f"{s}-09", "book": 70}],
        }
        for s in SETS
    ]
}


def settle(eid, frm, to, refs, price, tick=1, persona=None):
    items = [{"id": 1000 + eid * 10 + i, "ref": r, "frm": frm, "to": to, "kind": "card"} for i, r in enumerate(refs)]
    return {
        "id": eid,
        "tick": tick,
        "type": "settlement",
        "payload": {"items": items, "price": price, "persona": persona, "parties": [frm, to], "settlement": eid},
    }


def listed(eid, team, give=None, want=None, tick=1, to=None):
    offer = {"id": eid, "maker": team, "to": to, "give": give or {}, "want": want or {}}
    return {"id": eid, "tick": tick, "type": "offer.listed", "actor": team, "payload": {"offer": offer}}


def ask(eid, team, asset_id, ref, price, tick=1):
    return listed(eid, team, give={"assets": [{"id": asset_id, "ref": ref}]}, want={"cash": price}, tick=tick)


def bid(eid, team, ref, price, tick=1):
    return listed(eid, team, give={"cash": price}, want={"types": [f"card:{ref}"]}, tick=tick)


def topic(eid, team, card, tick=1):
    payload = {"kind": "persona", "team": team, "with": "abuela", "topic": {"buy": {"card": card}}, "thread": eid}
    return {"id": eid, "tick": tick, "type": "thread.opened", "payload": payload}


def test_signals_dedupe_reprices_and_keep_the_strongest_price():
    events = [
        ask(1, "t05", 77, "CCC-01", 9),
        ask(2, "t05", 77, "CCC-01", 7),  # the same copy repriced: one ask, at its lowest
        bid(3, "t05", "AAA-09", 60),
        bid(4, "t05", "AAA-09", 75),  # the same wish raised: one bid, at its highest
        topic(5, "t05", "AAA-06"),
        topic(6, "t05", "AAA-06"),
        settle(7, "abuela", "t05", ["AAA-06"], 22, persona="abuela"),
    ]
    sigs = af.signals(events, CATALOG)
    kinds = sorted((s.kind, s.ref, s.price) for s in sigs)
    assert kinds == [
        ("ask", "CCC-01", 7),
        ("bid", "AAA-09", 75),
        ("buy", "AAA-06", 22),
        ("topic", "AAA-06", None),
    ]
    assert {s.book for s in sigs if s.kind == "buy"} == {25.0}


def test_bundles_and_swaps_carry_interest_but_no_price():
    events = [
        settle(1, "t04", "t15", ["CCC-01", "CCC-06"], 18),  # two cards for 18: no per-card price
        listed(2, "t13", give={"assets": [{"id": 5, "ref": "AAA-01"}]}, want={"types": ["card:BBB-09"]}),
    ]
    sigs = {(s.team, s.kind, s.ref): s.price for s in af.signals(events, CATALOG)}
    assert sigs == {
        ("t15", "buy", "CCC-01"): None,
        ("t15", "buy", "CCC-06"): None,
        ("t04", "sell", "CCC-01"): None,
        ("t04", "sell", "CCC-06"): None,
        ("t13", "ask", "AAA-01"): None,
        ("t13", "bid", "BBB-09"): None,
    }


def test_a_dealer_sell_topic_counts_as_an_ask_of_the_card_it_names():
    events = [
        ask(1, "t09", 42, "DDD-06", 30),
        {"id": 2, "tick": 2, "type": "thread.opened", "payload": {"team": "t09", "topic": {"sell": {"assets": [42]}}}},
        {"id": 3, "tick": 2, "type": "thread.opened", "payload": {"team": "t09", "topic": {"sell": {"assets": [99]}}}},
    ]
    sigs = [s for s in af.signals(events, CATALOG) if s.team == "t09"]
    assert [(s.kind, s.ref) for s in sigs] == [("ask", "DDD-06")]  # 42 once; 99 never seen, so unknown


def test_no_evidence_is_the_uniform_prior():
    t = af.team_affinity("t02", [], SETS, MULT)
    assert all(p == pytest.approx(1 / 6, abs=1e-3) for p in t.p_top.values())
    assert sum(t.expected.values()) == pytest.approx(sum(MULT), abs=1e-3)


def test_the_posterior_is_a_distribution_over_assignments():
    events = [settle(i, "t07", "t05", ["AAA-06"], 20, tick=i) for i in range(1, 4)]
    events += [ask(10 + i, "t05", 500 + i, "CCC-01", 9) for i in range(3)]
    t = af.affinity_map(events, SETS, MULT, CATALOG).teams["t05"]
    assert sum(t.p_top.values()) == pytest.approx(1.0, abs=1e-3)  # exactly one set holds the top multiplier
    for dist in t.distribution.values():
        assert sum(dist.values()) == pytest.approx(1.0, abs=1e-3)
    assert sum(t.expected.values()) == pytest.approx(sum(MULT), abs=1e-3)  # each multiplier used once
    assert t.top_set == "AAA"
    assert t.expected["CCC"] == min(t.expected.values())
    # the sets with no evidence share what is left, alike
    assert t.p_top["BBB"] == pytest.approx(t.p_top["FFF"], abs=1e-4)


def test_a_price_above_book_rules_out_the_low_multipliers():
    # 34 for a book-25 card: only a multiplier of at least 34 / (25 × 1.25) ≈ 1.09 explains it
    events = [settle(1, "t08", "t17", ["BBB-06"], 34)]
    t = af.affinity_map(events, SETS, MULT, CATALOG).teams["t17"]
    assert t.p_at_least("BBB", 1.25) > 0.6
    assert t.distribution["BBB"][0.5] < 0.05
    # the same buy at book price says much less about the multiplier
    cheap = af.affinity_map([settle(1, "t08", "t17", ["BBB-06"], 20)], SETS, MULT, CATALOG).teams["t17"]
    assert cheap.p_at_least("BBB", 1.25) < t.p_at_least("BBB", 1.25)


def test_noise_keeps_one_overpay_from_being_proof():
    events = [settle(1, "t08", "t17", ["BBB-01"], 30)]  # 3× book: only a bot or a page hunt pays that
    t = af.affinity_map(events, SETS, MULT, CATALOG).teams["t17"]
    assert t.distribution["BBB"][0.5] > 0 and t.p_top["BBB"] < 0.9


def test_damping_counts_a_repeating_bot_less_than_linearly():
    events = [ask(i, "t06", 100 + i, "CCC-01", 9) for i in range(30)]
    damped = af.affinity_map(events, SETS, MULT, CATALOG).teams["t06"]
    linear = af.affinity_map(events, SETS, MULT, CATALOG, af.ModelParams(damp=False)).teams["t06"]
    assert damped.evidence["CCC"] == linear.evidence["CCC"] == pytest.approx(-9.0)
    assert damped.distribution["CCC"][0.5] < linear.distribution["CCC"][0.5]
    assert damped.distribution["CCC"][0.5] > 1 / 6


def test_the_map_lists_every_team_seen_or_named_except_the_excluded():
    events = [bid(1, "t03", "AAA-09", 70), bid(2, "t01", "AAA-09", 70), settle(3, "t04", "t03", ["AAA-06"], 30)]
    amap = af.affinity_map(events, SETS, MULT, CATALOG, exclude=["t01"], teams=["t11"])
    assert list(amap.teams) == ["t03", "t04", "t11"]
    assert amap.chasers("AAA", min_p=0.4) == ["t03"]
    assert amap.chasers("AAA", min_p=0.99) == []
    assert amap.expected("t99", "AAA") == 1.0  # unknown team: the neutral default


def test_multipliers_come_from_our_me_and_must_match_the_sets():
    assert af.multipliers_from({"affinity": {"X": 2.0, "Y": 0.5}}) == (0.5, 2.0)
    with pytest.raises(ValueError):
        af.team_affinity("t02", [], SETS, MULT[:5])


def test_price_loglik_is_bounded_by_the_noise_floor():
    s = af.Signal("t1", "AAA", "buy", "AAA-01", 1, price=1000, book=10.0)
    assert af._price_loglik(s, 0.5, af.ModelParams()) == pytest.approx(math.log(0.2), abs=1e-6)
    assert af._price_loglik(af.Signal("t1", "AAA", "ask", "AAA-01", 1, 5, 10.0), 0.5, af.ModelParams()) == 0.0


def test_a_team_that_joined_but_never_traded_keeps_the_prior():
    events = [{"id": 1, "tick": 0, "type": "team.joined", "payload": {"team": "t11", "name": "Team 11"}}]
    t = af.affinity_map(events, SETS, MULT, CATALOG).teams["t11"]
    assert t.signals == 0 and t.confidence == pytest.approx(1 / 6, abs=1e-3)


def test_the_cli_prints_the_map_from_files(tmp_path):
    import json

    from typer.testing import CliRunner

    from bazaar_agent.cli import app

    events = [settle(i, "t07", "t05", ["AAA-06"], 30, tick=i) for i in range(1, 4)]
    (tmp_path / "feed.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    me = {"id": "t01", "affinity": dict(zip(SETS, MULT, strict=True))}
    (tmp_path / "me.json").write_text(json.dumps({"type": "agent.me", "payload": me}))
    (tmp_path / "catalog.json").write_text(json.dumps({"body": CATALOG}))
    args = ["--events", str(tmp_path / "feed.jsonl"), "--me", str(tmp_path / "me.json")]
    args += ["--catalog", str(tmp_path / "catalog.json")]
    out = CliRunner().invoke(app, ["affinity", *args, "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.stdout)  # the target banner goes to stderr
    assert set(data) == {"t05", "t07"} and data["t05"]["p_top"]["AAA"] > 0.4
    table = CliRunner().invoke(app, ["affinity", *args], env={"COLUMNS": "200"})
    assert table.exit_code == 0 and "AAA: chased by t05" in table.output


def test_the_table_never_names_the_top_set_as_its_runner_up():
    from rich.console import Console

    from bazaar_agent.render import affinity_table

    tie = af.TeamAffinity("t02", ("A", "B"), {"A": 0.5, "B": 0.5}, {}, {}, {}, 3)
    assert tie.top_set == "B"
    console = Console(width=200, record=True)
    console.print(affinity_table(af.AffinityMap({"t02": tie})))
    row = next(line for line in console.export_text().splitlines() if "t02" in line)
    assert "│ B " in row and "│ A 0.50" in row


def test_the_strategy_takes_its_chasers_from_the_map_only_when_asked():
    from bazaar_agent import strategy
    from tests.test_strategy import CATALOG, DEALERS, EVENTS, ME, PARAMS, RULES

    # t16 buys two cheap LAV commons (team flows: LAV) and pays 120 for a book-70 LAT rare: only the top
    # multiplier explains that price, so the map says LAT
    t16 = [settle(100 + i, "abuela", "t16", ["LAV-01"], 8, tick=10 + i, persona="abuela") for i in range(2)]
    t16 += [settle(110, "t07", "t16", ["LAT-09"], 120, tick=20)]
    events = EVENTS + t16
    today = strategy.build_playbook(ME, CATALOG, events, DEALERS, PARAMS, RULES)
    on = strategy.build_playbook(ME, CATALOG, events, DEALERS, PARAMS.model_copy(update={"chaser_min_p": 0.5}), RULES)
    flows = {mv.ref: mv.counterparties for mv in today.sells}
    mapped = {mv.ref: mv.counterparties for mv in on.sells}
    assert "t16" not in flows.get("LAT-09", ()) and "t16" in mapped["LAT-09"]
    m = strategy.build_market(ME, CATALOG, events, DEALERS)
    bad = {**ME, "affinity": {"LAV": 1.6}}  # one multiplier for three sets: no map, the flows stand
    assert strategy.map_chasers(bad, CATALOG, events, 0.5, m.chasers) == m.chasers
