import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bazaar_agent.guardrails import Guardrails
from bazaar_agent.ladder import floor_table, from_rows
from bazaar_agent.ladder_plan import (
    DEFAULT_QUOTAS,
    UNLIMITED,
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
    odd = {"id": "x", "level": 3, "menu": {"sells": [{"pack": "p", "per_team_per_hour": 0}]}}
    assert quotas_from_dealers([odd]) == {"x": DealerQuota("x", 3, UNLIMITED, {"p": 0})}  # 0 is none, missing is no cap


def test_packs_share_one_hourly_cap_across_pack_ids(plans):
    """GUARDRAILS.md max_packs_per_game_hour (3) counts every pack id together."""
    loose = Guardrails(max_price_pack=200)  # what-if caps so both packs are plannable
    real = from_rows(json.loads(FIXTURE.read_text())["rows"])
    caps = {("abuela", "pack:sobre_barrio"): 24, ("chato", "pack:sobre_plata"): 190}
    both = class_plans(real, floor_table(real), loose, caps=caps, runs=100)
    plata = both[("chato", "pack:sobre_plata")]
    assert plata.choice.plan is None  # one closed thread: not enough to trust a floor
    both = dict(both) | {("chato", "pack:sobre_plata"): both[("abuela", "pack:sobre_barrio")]}  # stand-in plan
    targets = [Target("abuela", "pack:sobre_barrio"), Target("chato", "pack:sobre_plata")] * 3
    quotas = {d: DealerQuota(d, 1, 8, {"sobre_barrio": 3, "sobre_plata": 2}) for d in ("abuela", "chato")}
    sched = schedule(both, targets, quotas, loose.model_copy(update={"max_spend_per_game_hour": 10_000}), cash=10_000)
    in_first_hour = [s for s in sched.slots if s.game_hour == 4]
    assert len(in_first_hour) == loose.max_packs_per_game_hour


def test_the_dealers_option_refuses_a_body_that_is_not_dealers(tmp_path):
    from bazaar_agent.cli import app

    bad = tmp_path / "dealers.json"
    bad.write_text(json.dumps({"personas": [{"name": "no id"}]}))
    args = ["ladder", "plan", "--cash", "353", "--source", "fixture", "--dealers", str(bad)]
    result = CliRunner().invoke(app, args, env={"COLUMNS": "300"})
    assert result.exit_code == 2 and "every dealer needs a string id" in result.output


def test_plan_document_for_saturday_morning(real):
    doc = plan_document(real, floor_table(real), RULES, cash=353, grants=[Grant(6, 150)], runs=300)
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
    caps = {("chato", "card:uncommon"): 31}
    doc = plan_document(real, floor_table(real), RULES, cash=353, grants=[Grant(6, 150)], caps=caps, runs=300)
    first = [(s["wall"], s["dealer"]) for s in doc["schedule"][:2]]
    assert first == [("09:00:00", "abuela"), ("09:00:00", "chato")]
    assert doc["what_if_caps"] == {"chato:card:uncommon": 31}


def test_refs_go_to_a_dealer_that_can_plan_their_rarity_best_share_first(plans):
    refs = [("LAV-08", "uncommon"), ("SAL-02", "common"), ("LAV-09", "rare"), ("SAL-05", "common")]
    targets = default_targets(plans, DEFAULT_QUOTAS, refs=refs)
    named = [(t.dealer, t.price_class, t.ref) for t in targets if t.ref]
    # commons (0.97+) before the uncommon (0.94); the rare has no plan anywhere: listed, then blocked
    assert named[:3] == [
        ("abuela", "card:common", "SAL-02"),
        ("abuela", "card:common", "SAL-05"),
        ("abuela", "card:uncommon", "LAV-08"),
    ]
    sched = schedule(plans, targets, DEFAULT_QUOTAS, RULES, cash=503)
    assert [s.target.ref for s in sched.slots] == ["SAL-02", "SAL-05", "LAV-08"]
    assert ("chato", "card:rare") in [(d, c) for d, c, _ in sched.blocked]
    assert ("chato", "card:rare", "LAV-09") in named  # Chato sells rares: blocked by the cap, not unknown
