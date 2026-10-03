"""The duel go/no-go (night plan W2b) and the zoo report script, on small grids."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

from bazaar_sim import duel_gate, duel_zoo

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("duel_zoo_script", ROOT / "scripts/duel_zoo.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["duel_zoo_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_a_policy_against_itself_fails_only_the_lift_and_the_replay():
    gate = duel_gate.go_no_go(duel_zoo.endgame_accept, duel_zoo.endgame_accept, n=5)
    verdicts = {c.name: c.passed for c in gate.checks}
    assert verdicts == {
        "mean_result": False,  # 1.0 < 1.4
        "deals_conceders": True,
        "deals_one_shot": True,
        "outside_limit": True,
        "replay": False,  # not strictly better
    }
    assert not gate.go and len(gate.candidate) == len(gate.baseline) == 6 * 5 * 2 * 2


def test_silence_beats_accepting_at_once_on_the_replay_but_loses_deals_to_tit_for_tat():
    gate = duel_gate.go_no_go(duel_zoo.endgame_accept, duel_zoo.accept_first_inside, n=20)
    checks = {c.name: c for c in gate.checks}
    assert checks["replay"].passed and checks["replay"].value == 185.0 and checks["replay"].threshold == 85.0
    assert checks["mean_result"].passed and checks["outside_limit"].value == 0


def test_the_report_script_renders_every_section(tmp_path):
    script = _load()
    out = tmp_path / "zoo.md"
    assert script.main(["--policy", "endgame_accept", "--gate", "anchor_once", "--n", "4", "--out", str(out)]) == 0
    text = out.read_text()
    for heading in ("practice duels, labelled", "Realism", "classifier", "Tournament", "Replay", "Go/no-go"):
        assert heading in text
    assert "| endgame_accept | 185 | 185 |" in text


def test_a_baseline_that_earns_nothing_cannot_be_beaten_by_another_that_earns_nothing():
    never = lambda duel, tick, started: duel_zoo.HOLD  # noqa: E731
    gate = duel_gate.go_no_go(never, never, n=3)
    lift = next(c for c in gate.checks if c.name == "mean_result")
    assert not lift.passed and "baseline ≤ 0" in lift.detail


def test_a_check_with_no_duels_of_its_styles_does_not_pass():
    gate = duel_gate.go_no_go(duel_zoo.endgame_accept, duel_zoo.accept_first_inside, n=3, styles=("linear",))
    one_shot = next(c for c in gate.checks if c.name == "deals_one_shot")
    assert not one_shot.passed and one_shot.detail.startswith("not run") and not gate.go


def test_the_outside_limit_check_plays_distinct_duels_under_each_days_truth():
    signed = duel_zoo.scenarios(n=5, two_issues=True, days_truth="signed")
    worst = duel_zoo.scenarios(n=5, two_issues=True, days_truth="worst")
    assert len({(s.limit, s.rival_limit, s.days_weight) for s in signed + worst}) > len(signed) * 1.5
    price_only = duel_zoo.scenarios(n=5)
    same = [replace(sc, days_truth="signed") for sc in duel_zoo.scenarios(n=5, days_truth="worst")]
    assert price_only == same  # price-only grids draw the same duels whatever the label
