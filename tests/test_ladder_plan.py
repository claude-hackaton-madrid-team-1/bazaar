import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bazaar_agent.guardrails import Guardrails
from bazaar_agent.ladder import floor_table, from_rows
from bazaar_agent.ladder_plan import (
    DEFAULT_QUOTAS,
    DealerQuota,
    Grant,
    Target,
    Window,
    class_plans,
    default_targets,
    plan_document,
    quotas_from_dealers,
    schedule,
)

FIXTURE = Path(__file__).parent / "fixtures" / "evals" / "dealer_threads.json"
RULES = Guardrails()  # the GUARDRAILS.md defaults: cash_floor 270, 150 P per game hour, caps 12/26/80/20


@pytest.fixture(scope="module")
def real():
    return from_rows(json.loads(FIXTURE.read_text())["rows"])


@pytest.fixture(scope="module")
def plans(real):
    return class_plans(real, floor_table(real), RULES, runs=300)


def test_window_clock():
    w = Window()
    assert (w.ticks, w.game_hours, w.wall(0), w.wall(6), w.wall(119)) == (180, 2, "09:00:00", "09:03:00", "09:59:30")
    assert int(w.t_at(119)) == 4 and int(w.t_at(120)) == 5


def test_blocked_classes_say_why(plans):
    assert plans[("abuela", "card:uncommon")].choice.plan is not None
    for key in [("chato", "card:uncommon"), ("chato", "card:rare"), ("abuela", "pack:sobre_barrio")]:
        assert plans[key].choice.plan is None and "below market" in plans[key].choice.reason


def test_best_three_come_first_highest_level_first(plans):
    caps = {("chato", "card:uncommon"): 31}
    what_if = class_plans_with(plans, caps)
    targets = default_targets(what_if, DEFAULT_QUOTAS, hours=1)
    assert [(t.dealer, t.price_class) for t in targets[:6]] == [("chato", "card:uncommon")] * 3 + [
        ("abuela", "card:common")
    ] * 3


def class_plans_with(plans, caps):
    real = from_rows(json.loads(FIXTURE.read_text())["rows"])
    return class_plans(real, floor_table(real), RULES, caps=caps, runs=300)


def test_schedule_keeps_quotas_budget_and_the_cash_floor(plans):
    targets = [Target("abuela", "card:uncommon")] * 20
    sched = schedule(plans, targets, DEFAULT_QUOTAS, RULES, cash=503)
    per_hour: dict[int, list] = {}
    for s in sched.slots:
        per_hour.setdefault(s.game_hour, []).append(s)
    for slots in per_hour.values():
        assert len(slots) <= 8  # Abuela: 8 deals per team per hour
        assert sum(s.max_price for s in slots) <= RULES.max_spend_per_game_hour
    assert 503 - sum(s.max_price for s in sched.slots) >= RULES.cash_floor
    ticks = [s.tick for s in sched.slots]
    assert all(b - a >= 4 for a, b in zip(ticks, ticks[1:], strict=False))  # one conversation at a time
    assert sched.notes  # the rest did not fit


def test_the_grant_unlocks_slots_the_cash_floor_held_back(plans):
    targets = [Target("abuela", "card:uncommon")] * 4
    before = schedule(plans, targets, DEFAULT_QUOTAS, RULES, cash=290)
    assert before.slots == () and before.notes
    after = schedule(plans, targets, DEFAULT_QUOTAS, RULES, cash=290, grants=[Grant(6, 150)])
    assert after.slots and min(s.tick for s in after.slots) == 6


def test_a_dealer_out_of_play_is_blocked(plans):
    sched = schedule(plans, [Target("chato", "card:uncommon")], {"abuela": DEFAULT_QUOTAS["abuela"]}, RULES, cash=503)
    assert sched.slots == () and "not in play" in sched.blocked[0][2]


def test_quotas_from_a_dealers_payload():
    body = json.loads((FIXTURE.parent.parent / "api" / "get_api_dealers.anon.json").read_text())["body"]
    assert quotas_from_dealers(body["personas"]) == {"abuela": DealerQuota("abuela", 1, 8, {"sobre_barrio": 3})}


def test_plan_document_for_saturday_morning(real):
    doc = plan_document(real, floor_table(real), RULES, cash=353, runs=300)
    assert doc["window"]["ticks"] == 180 and doc["budget"]["grants"][0]["wall"] == "09:03:00"
    first = doc["schedule"][0]
    assert (first["wall"], first["dealer"], first["plan"]) == ("09:00:00", "abuela", {"start": 8, "step": 1, "max": 12})
    assert {b["dealer"] for b in doc["blocked"]} == {"abuela", "chato"}
    for hour in doc["per_game_hour"].values():
        assert hour["reserved"] <= RULES.max_spend_per_game_hour
    assert doc["what_if_caps"] is None


def test_cli_writes_the_plan(tmp_path):
    from bazaar_agent.cli import app

    out = tmp_path / "ladder_plan.json"
    result = CliRunner().invoke(
        app, ["ladder", "plan", "--cash", "353", "--source", "fixture", "--out", str(out), "--runs", "200"]
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(out.read_text())
    assert doc["schedule"] and "Friday snapshot" in doc["source"]
    floors = CliRunner().invoke(app, ["ladder", "floors", "--source", "fixture"], env={"COLUMNS": "200"})
    assert floors.exit_code == 0 and "abuela" in floors.output


def test_a_later_slot_never_blocks_an_earlier_one_on_cash(real):
    """Chato's 2nd and 3rd slots land after the 09:03 grant; Abuela's first still opens at 09:00."""
    doc = plan_document(real, floor_table(real), RULES, cash=353, caps={("chato", "card:uncommon"): 31}, runs=300)
    first = [(s["wall"], s["dealer"]) for s in doc["schedule"][:2]]
    assert first == [("09:00:00", "abuela"), ("09:00:00", "chato")]
    assert doc["what_if_caps"] == {"chato:card:uncommon": 31}
