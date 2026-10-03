"""Saturday's Market Tests simulated: the schedule, the points curve, plans and the broker-down risk."""

import json
from pathlib import Path

import pytest

from bazaar_agent.evals import saturday as sat

FIXTURE = Path(__file__).parent / "fixtures" / "api" / "get_api_schedule.anon.json"


def test_the_sessions_are_saturdays_bench_actions_in_the_schedule():
    upcoming = json.loads(FIXTURE.read_text())["body"]["upcoming"]
    saturday = [e for e in upcoming if e["action"] == "bench" and 4.0 <= e["at_hours"] < 18.0]
    expected = tuple((e["at_hours"], "hard" if e["params"]["traders"] == 12 else "normal") for e in saturday)
    assert expected == sat.SESSIONS


def test_the_points_curve_reads_the_rules_as_w1a_does():
    assert sat.session_points(0.8, 0.8, [0.8, 0.8]) == 0.5
    assert sat.session_points(0.81, 0.8, [0.8, 0.8]) == 1.0  # above a stall-level field: the full point
    assert sat.session_points(0.4, 0.8, [0.8, 0.8]) == 0.25
    assert sat.session_points(0.85, 0.8, [0.9, 0.8]) == pytest.approx(0.5 + 0.5 * 0.05 / (0.85 - 0.8))
    assert sat.session_points(0.0, 0.0, [0.0, 0.0]) == 0.5


def fixed_runner(ours, stall=0.8, oracle=0.9):
    def run(preset, variant, rule, seed, policy, probes):
        return sat.Session(ours, stall, oracle)

    return run


def test_plans_count_only_the_sessions_after_the_venue_opens():
    rows = sat.simulate_days(
        3, worlds=("default/quote",), fields=("stall",), policies=("edge",), runner=fixed_runner(0.85)
    )
    by = {r.plan: r for r in rows}
    full = sat.BENCH_ROUND_POINTS * sat.FINAL_PER_ROUND_POINT  # every session at 1.0
    assert by["never"].final_mean == pytest.approx(full / 2) and by["never"].sessions_ours == 0
    assert by["09:00"].final_mean == pytest.approx(full) and by["09:00"].sessions_ours == 8
    assert by["11:30"].sessions_ours == 7 and by["11:30"].final_mean == pytest.approx(full * (7 + 0.5) / 8)
    assert by["09:00"].p_worse == 0.0


def test_a_broker_down_in_every_session_scores_nothing():
    rows = sat.simulate_days(
        2, worlds=("default/quote",), fields=("stall",), policies=("edge",), down=1.0, runner=fixed_runner(0.85)
    )
    by = {r.plan: r for r in rows}
    assert by["09:00"].final_mean == 0.0 and by["09:00"].p_worse == 1.0
    assert by["11:30"].final_mean == pytest.approx(sat.BENCH_ROUND_POINTS * sat.FINAL_PER_ROUND_POINT * 0.5 / 8)


def test_the_in_process_runner_plays_real_sessions():
    rows = sat.simulate_days(
        4, worlds=("default/quote", "tick0/quote"), fields=("stall",), policies=("edge",), runner=sat.own_runner()
    )
    assert {r.world for r in rows} == {"default/quote", "tick0/quote"}
    tick0 = {r.plan: r for r in rows if r.world == "tick0/quote"}
    assert tick0["09:00"].delta_mean > 0
    assert "| tick0/quote | stall | edge | 09:00 |" in sat.markdown(rows)


def test_main_writes_json(tmp_path, capsys):
    out = tmp_path / "sat.json"
    sat.main(["--days", "2", "--worlds", "default/quote", "--fields", "stall", "--policies", "edge", "--own"])
    sat.main(["--days", "2", "--worlds", "tick0/quote", "--fields", "stall", "--own", "--json", str(out)])
    assert "Saturday bench" in capsys.readouterr().out
    assert {r["policy"] for r in json.loads(out.read_text())} == {"exact", "edge", "edge+probe"}


def test_each_plan_has_its_own_broker_that_learns_only_from_the_sessions_it_ran():
    seen = []

    def run(preset, variant, rule, seed, policy, probes):
        seen.append((seed % 10, id(probes)))
        return sat.Session(0.85, 0.8, 0.9)

    sat.simulate_days(1, worlds=("default/quote",), fields=("stall",), policies=("edge",), runner=run)
    by_probes = {}
    for k, probes in seen:
        by_probes.setdefault(probes, []).append(k)
    assert sorted(by_probes.values()) == [[0, 1, 2, 3, 4, 5, 6, 7], [1, 2, 3, 4, 5, 6, 7]]  # 09:00, then 11:30


def test_against_three_oracle_level_rivals_only_the_margin_counts():
    rows = sat.simulate_days(
        1, worlds=("default/quote",), fields=("top3",), policies=("edge",), runner=fixed_runner(0.85)
    )
    share = 0.5 + 0.5 * (0.85 - 0.8) / (0.9 - 0.8)  # top three = the three oracle venues
    by = {r.plan: r for r in rows}
    assert by["09:00"].final_mean == pytest.approx(sat.BENCH_ROUND_POINTS * sat.FINAL_PER_ROUND_POINT * share, abs=1e-3)


def test_the_plans_follow_the_keepers_opening_hour():
    assert sat.PLANS == {"09:00": 4.05, "11:30": 6.5, "never": None}
