import json
from datetime import datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from bazaar_agent import verify as vf
from tests.test_intel import opened, settle

FIXTURES = Path(__file__).parent / "fixtures" / "api"
OPENS = datetime.fromisoformat("2026-10-03T09:00:00+02:00")
DAYS = [{"day": "sat", "opens": OPENS.isoformat()}]
SAT = {"today": "sat", "tick_seconds": 30.0, "t_hours": 4.0, "tick": 160, "days": DAYS, "limits": {}}
CATALOG = json.loads((FIXTURES / "get_api_catalog.anon.json").read_text())
CATALOG = CATALOG.get("body", CATALOG)


def test_every_automatic_check_is_in_the_catalogue_and_every_entry_is_complete():
    checks = vf.load_checks()
    ids = [c.id for c in checks]
    assert len(ids) == len(set(ids))  # no duplicate ids
    assert set(vf.AUTOMATIC) <= set(ids)
    for c in checks:
        assert c.assumption and c.when and c.flips and c.priority in ("high", "medium", "low")
        assert c.run is not None or c.how  # a manual check says how to settle it


def test_on_fridays_fixtures_nothing_saturday_is_claimed():
    snap = vf.snapshot_from_dir(FIXTURES)
    results = {c.id: r for c, r in vf.evaluate(vf.load_checks(), snap)}
    for saturday_only in ("clock-pace-30s", "clock-anchor", "ladder-resets-per-round"):
        assert results[saturday_only].status == "UNKNOWN"
    assert results["grant-150-saturday"].status == "PASS"  # the schedule fixture lists it
    assert results["abuela-menu"].status == "PASS"


def test_clock_checks_on_saturday():
    assert vf.clock_pace(vf.Snapshot(clock=SAT)).status == "PASS"
    assert vf.clock_pace(vf.Snapshot(clock={**SAT, "tick_seconds": 20.0})).status == "FAIL"
    assert vf.clock_anchor(vf.Snapshot(clock=SAT, now=OPENS)).status == "PASS"
    resumed = vf.clock_anchor(vf.Snapshot(clock={**SAT, "t_hours": 2.65}, now=OPENS))
    assert resumed.status == "FAIL" and "resumed" in resumed.evidence
    # a late run: a resumed clock reads h3.98 at 10:20, which is still the resume, not the jump
    late = vf.Snapshot(clock={**SAT, "t_hours": 2.65 + 4 / 3}, now=OPENS + timedelta(minutes=80))
    assert vf.clock_anchor(late).status == "FAIL"
    # missing fields or time are UNKNOWN, never a plan
    assert vf.clock_anchor(vf.Snapshot(clock=SAT)).status == "UNKNOWN"
    assert vf.clock_anchor(vf.Snapshot(clock={**SAT, "t_hours": None}, now=OPENS)).status == "UNKNOWN"
    assert vf.clock_pace(vf.Snapshot(clock={**SAT, "tick_seconds": None})).status == "UNKNOWN"


def test_tick_numbering_and_todays_first_tick():
    later = OPENS + timedelta(minutes=80)  # 160 ticks of 30 s
    assert vf.first_tick_today({**SAT, "tick": 320}, later) == 160
    assert vf.tick_continues(vf.Snapshot(clock={**SAT, "tick": 320}, now=later)).status == "PASS"
    assert vf.tick_continues(vf.Snapshot(clock={**SAT, "tick": 161}, now=later)).status == "FAIL"
    assert vf.tick_continues(vf.Snapshot(clock=SAT)).status == "UNKNOWN"


def test_the_ladder_reset_reads_our_ladder_points_on_saturday():
    me = {"score": {"ladder_points": 0.0}}
    assert vf.ladder_resets(vf.Snapshot(clock=SAT, me=me)).status == "PASS"
    friday = [
        {"id": 1, "tick": 100, "type": "settlement", "payload": {"parties": ["abuela", "t01"], "persona": "abuela"}}
    ]
    kept = vf.Snapshot(clock=SAT, me={"score": {"ladder_points": 0.058}}, events=friday, team="t01", since_tick=160)
    result = vf.ladder_resets(kept)
    assert result.status == "FAIL" and "still count" in result.evidence
    # a renamed field is UNKNOWN, not a reset
    assert vf.ladder_resets(vf.Snapshot(clock=SAT, me={"score": {}})).status == "UNKNOWN"
    # after a Saturday deal of ours, points above 0 prove nothing
    today = [{**friday[0], "tick": 170}]
    after = vf.Snapshot(clock=SAT, me={"score": {"ladder_points": 0.3}}, events=today, team="t01", since_tick=160)
    assert vf.ladder_resets(after).status == "UNKNOWN"
    # without the feed we cannot rule that out either
    assert vf.ladder_resets(vf.Snapshot(clock=SAT, me={"score": {"ladder_points": 0.3}})).status == "UNKNOWN"


def test_feed_checks_read_only_todays_threads():
    events = []
    for i, (opening, fill) in enumerate([(29, 23), (29, 22), (12, 9), (12, 10)]):
        ref = "LAV-07" if opening == 29 else "LAV-03"
        kind = {"buy": {"card": ref}}
        ask = {"give": {"types": [f"card:{ref}"]}, "want": {"cash": opening}}
        payload = {"kind": "persona", "thread": 50 + i, "sender": "abuela", "with": "abuela", "offer": ask}
        events += [
            opened(100 + 10 * i, 50 + i, "t05", kind, tick=200 + i),
            {"id": 101 + 10 * i, "tick": 200 + i, "type": "thread.message", "payload": payload},
            settle(102 + 10 * i, 900 + i, "abuela", "t05", ref, fill, tick=201 + i, kind="card"),
        ]
    # a sale to Abuela: her opening there is a bid (here 4), not one of her asks, so it never moves the regime
    bid = {"give": {"cash": 4}, "want": {"types": ["card:LAV-01"]}}
    sale = {"kind": "persona", "thread": 60, "sender": "abuela", "with": "abuela", "offer": bid}
    events += [
        opened(150, 60, "t05", {"sell": {"card": "LAV-01"}}, tick=210),
        {"id": 151, "tick": 210, "type": "thread.message", "payload": sale},
        settle(152, 960, "t05", "abuela", "LAV-01", 4, tick=211, kind="card"),
    ]
    snap = vf.Snapshot(events=events, since_tick=200, catalog=CATALOG)
    assert any(t.side == "sell" for t in vf.intel.dealer_threads(events))
    assert vf.openings_hold(snap).status == "PASS"
    assert vf.floors_hold(snap).status == "PASS"
    assert vf.floors_hold(vf.Snapshot(events=events, since_tick=300, catalog=CATALOG)).status == "UNKNOWN"
    assert vf.openings_hold(vf.Snapshot(events=events, since_tick=200)).status == "UNKNOWN"  # no catalog


def test_openings_are_checked_per_rarity():
    events = []
    for i in range(3):  # uncommons (LAV-07) opening at 17: inside Friday's set of openings, but not 29
        ask = {"give": {"types": ["card:LAV-07"]}, "want": {"cash": 17}}
        payload = {"kind": "persona", "thread": 70 + i, "sender": "abuela", "with": "abuela", "offer": ask}
        events += [
            opened(300 + 10 * i, 70 + i, "t05", {"buy": {"card": "LAV-07"}}, tick=200 + i),
            {"id": 301 + 10 * i, "tick": 200 + i, "type": "thread.message", "payload": payload},
        ]
    assert vf.openings_hold(vf.Snapshot(events=events, since_tick=200, catalog=CATALOG)).status == "FAIL"


def test_menus_and_missing_fields():
    chato = {"id": "chato", "menu": {"deals_per_team_per_hour": 6, "sells": [{"rarity": "uncommon", "list_price": 30}]}}
    assert vf.chato_menu(vf.Snapshot(dealers=[chato])).status == "UNKNOWN"  # no rare entry
    chato["menu"]["sells"].append({"rarity": "rare", "list_price": 90})
    assert vf.chato_menu(vf.Snapshot(dealers=[chato])).status == "PASS"
    chato["menu"]["deals_per_team_per_hour"] = 4
    assert vf.chato_menu(vf.Snapshot(dealers=[chato])).status == "FAIL"
    assert vf.limits_hold(vf.Snapshot(clock={"limits": {}})).status == "UNKNOWN"
    assert vf.level_and_unlocks(vf.Snapshot(me={"level": 2})).status == "UNKNOWN"
    assert vf.unlock_rule(vf.Snapshot(dealers=[{"id": "collector"}])).status == "UNKNOWN"


def test_later_runs_do_not_fail_on_what_already_happened():
    # the h5 session already ran and left `upcoming`
    schedule = {"now_hours": 5.5, "upcoming": [{"action": "bench", "at_hours": h} for h in (7, 9, 11, 13, 15, 16, 17)]}
    assert vf.market_tests_scheduled(vf.Snapshot(schedule=schedule)).status == "PASS"
    # the grant fired and left `upcoming`: the feed says so
    fired = [{"id": 9, "tick": 166, "type": "schedule.fired", "payload": {"action": "grant_all"}}]
    assert vf.grant_scheduled(vf.Snapshot(schedule=schedule, events=fired, since_tick=160)).status == "PASS"
    # cash after the grant, before any settlement of ours
    snap = vf.Snapshot(clock=SAT, me={"cash": 503}, events=fired, team="t01", since_tick=160)
    assert vf.cash_at_open(snap).status == "PASS"


def test_level_and_market_checks():
    l3 = {"id": "collector", "unlock": {"early_deals_with": "chato", "early_min_deals": 3}}
    mechanic = {"id": 1, "tick": 170, "type": "level.announced", "payload": {"kind": "mechanic", "level": "auction"}}
    assert vf.l3_announced(vf.Snapshot(events=[mechanic])).status == "UNKNOWN"
    snap = vf.Snapshot(dealers=[{"id": "abuela"}, {"id": "chato"}, l3])
    assert vf.l3_announced(snap).status == "PASS" and vf.unlock_rule(snap).status == "PASS"
    other = vf.Snapshot(dealers=[{"id": "collector", "unlock": {"early_deals_with": "chato", "early_min_deals": 4}}])
    assert vf.unlock_rule(other).status == "FAIL"
    assert vf.stall_scores(vf.Snapshot(me={"score": {"bench_points": None}})).status == "UNKNOWN"
    assert vf.stall_scores(vf.Snapshot(me={"score": {"bench_points": 0.5, "bench_efficiency": 0.8}})).status == "PASS"
    assert vf.stall_scores(vf.Snapshot(me={"venue": {"id": "v1"}, "score": {"bench_points": 0.5}})).status == "UNKNOWN"
    assert vf.stall_scores(vf.Snapshot(me={"score": {"bench_points": 0.0, "bench_venue": None}})).status == "FAIL"


def test_a_failing_check_never_hides_the_others():
    def boom(snap):
        raise KeyError("x")

    check = vf.Check("boom", "a", "09:00", "GET /api/x", "nothing", "default", run=boom)
    ((_, result),) = vf.evaluate([check], vf.Snapshot())
    assert result.status == "UNKNOWN" and "KeyError" in result.evidence


def test_the_cli_runs_on_the_fixtures_and_prints_json():
    from bazaar_agent.cli import app

    result = CliRunner().invoke(app, ["verify", "--fixtures", str(FIXTURES), "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output[result.output.index("[") :])
    assert {r["status"] for r in rows} <= {"PASS", "FAIL", "UNKNOWN"} and len(rows) == len(vf.load_checks())
