import pytest

from bazaar_agent import strategy
from bazaar_agent.config import REPO_ROOT
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.jev import load_questions, mask_request
from bazaar_agent.pack_gate import gate_packs, pack_state, slots_left
from tests.test_strategy import playbook

RULES = Guardrails()
FREE = strategy.PackSlots(used=0, limit=3)


class FakeJudge:
    def __init__(self, verdict: str, probability: float) -> None:
        self.answer = (verdict, probability)
        self.states: list[dict] = []

    def __call__(self, state: dict) -> tuple[str, float]:
        self.states.append(state)
        return self.answer


def barrio(book: strategy.Playbook) -> strategy.Move:
    return next(m for m in book.packs if m.ref == "sobre_barrio")


def test_jev_yes_keeps_the_pack_move_and_shows_its_probability():
    judge = FakeJudge("yes", 0.91)
    book = gate_packs(playbook(), judge, FREE, {}, RULES, t_hours=1.5)
    move = barrio(book)
    assert move.command.startswith("uv run bazaar dealer buy sobre_barrio") and move.jev == "yes 0.91"
    assert book.pack_slots == FREE and len(judge.states) == 1  # packs nobody sells are never judged


@pytest.mark.parametrize(("verdict", "probability"), [("no", 0.1), ("undecided", 0.59)])
def test_jev_no_or_undecided_keeps_the_slot(verdict, probability):
    move = barrio(gate_packs(playbook(), FakeJudge(verdict, probability), FREE, {}, RULES, t_hours=1.5))
    assert move.command == "" and move.jev == f"{verdict} {probability:.2f}"
    assert move.reason.endswith(f"Jev {verdict}: keep the slot")


def test_with_no_slot_left_jev_is_not_asked():
    judge = FakeJudge("yes", 0.99)
    spent = strategy.PackSlots(used=3, limit=3)
    move = barrio(gate_packs(playbook(), judge, spent, {"sobre_barrio": 3}, RULES, t_hours=1.5))
    assert move.command == "" and judge.states == [] and "no pack slot left" in move.reason


def test_the_dealer_quota_caps_slots_below_our_guardrail():
    book = playbook()  # abuela sells sobre_barrio 3 per team per hour
    roomy = strategy.PackSlots(used=3, limit=5)
    assert slots_left(book, "sobre_barrio", roomy, {"sobre_barrio": 3}) == 0
    assert slots_left(book, "sobre_barrio", roomy, {"sobre_barrio": 1}) == 2
    assert slots_left(book, "sobre_oro", roomy, {}) == 2  # no dealer quota: the guardrail alone


def test_jev_reads_value_price_slots_best_alternative_and_cash():
    book = playbook()
    state = pack_state(barrio(book), book, 2, FREE, RULES, t_hours=1.5)
    assert (state["pack"], state["pack_expected_value_to_us"], state["pack_learned_price"]) == (
        "sobre_barrio",
        25.0,
        17.0,
    )
    assert (state["pack_slots_left_this_game_hour"], state["pack_slots_per_game_hour"]) == (2, 3)
    assert state["best_alternative_buy"]["card"] == "LAV-09"
    assert (state["cash"], state["cash_floor"], state["cash_above_floor"]) == (400, 270, 130)
    assert (state["game_hour"], state["tick"]) == (1.5, 50)


def test_the_pack_question_is_a_valid_design_stakes_noul():
    questions = load_questions(REPO_ROOT / "questions" / "packs.json")
    question = questions["spend_pack_slot_now"]
    assert (question["type"], question["stakes"]) == ("noul", "design")
    book = playbook()
    mask_request(pack_state(barrio(book), book, 2, FREE, RULES, 1.5), questions)  # raises when malformed


def test_slots_serialise_with_what_is_left():
    data = strategy.playbook_dict(
        gate_packs(playbook(), FakeJudge("no", 0.2), FREE, {}, RULES, 1.0), strategy.load_strategy()
    )
    assert data["pack_slots"] == {"used": 0, "limit": 3, "left": 3}
