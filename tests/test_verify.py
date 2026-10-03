import json
from pathlib import Path

from typer.testing import CliRunner

from bazaar_agent import verify as vf
from tests.test_intel import opened, settle

FIXTURES = Path(__file__).parent / "fixtures" / "api"
SAT = {"today": "sat", "tick_seconds": 30.0, "t_hours": 4.0, "limits": {}}


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
    assert vf.clock_anchor(vf.Snapshot(clock=SAT)).status == "PASS"
    resumed = vf.clock_anchor(vf.Snapshot(clock={**SAT, "t_hours": 2.65}))
    assert resumed.status == "FAIL" and "resumed" in resumed.evidence


def test_the_ladder_reset_reads_our_ladder_points_on_saturday():
    me = {"score": {"ladder_points": 0.0}}
    assert vf.ladder_resets(vf.Snapshot(clock=SAT, me=me)).status == "PASS"
    kept = vf.ladder_resets(vf.Snapshot(clock=SAT, me={"score": {"ladder_points": 0.058}}))
    assert kept.status == "FAIL" and "still count" in kept.evidence


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
    snap = vf.Snapshot(events=events, since_tick=200)
    assert vf.openings_hold(snap).status == "PASS"
    assert vf.floors_hold(snap).status == "PASS"
    assert vf.floors_hold(vf.Snapshot(events=events, since_tick=300)).status == "UNKNOWN"  # nothing today yet


def test_level_and_market_checks():
    l3 = {"id": "collector", "unlock": {"early_deals_with": "chato", "early_min_deals": 3}}
    snap = vf.Snapshot(dealers=[{"id": "abuela"}, {"id": "chato"}, l3])
    assert vf.l3_announced(snap).status == "PASS" and vf.unlock_rule(snap).status == "PASS"
    other = vf.Snapshot(dealers=[{"id": "collector", "unlock": {"early_deals_with": "chato", "early_min_deals": 4}}])
    assert vf.unlock_rule(other).status == "FAIL"
    assert vf.stall_scores(vf.Snapshot(me={"score": {"bench_points": None}})).status == "UNKNOWN"
    assert vf.stall_scores(vf.Snapshot(me={"score": {"bench_points": 0.5, "bench_efficiency": 0.8}})).status == "PASS"


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
