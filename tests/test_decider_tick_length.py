"""The LLM decider steps aside on ticks too short for it (15 s Sunday ticks), so gated moves are not all dropped."""

import pytest

from bazaar_agent.jev import decider as dec
from bazaar_agent.jev.decider import decider, needed_budget_s, note_tick_seconds


@pytest.fixture(autouse=True)
def _forget_tick():
    note_tick_seconds(None)
    yield
    note_tick_seconds(None)


LLM = {"BAZAAR_DECIDER": "llm"}


def test_an_unknown_tick_length_keeps_the_llm():
    assert decider(LLM) == "llm"


@pytest.mark.parametrize("seconds, expected", [(60.0, "llm"), (30.0, "llm"), (15.0, "jev"), (5.0, "jev")])
def test_the_llm_needs_a_tick_of_at_least_the_floor(seconds, expected):
    note_tick_seconds(seconds)
    assert decider(LLM) == expected


def test_a_short_tick_asks_jev_with_its_own_small_budget_not_the_llm_timeout():
    note_tick_seconds(15.0)
    assert needed_budget_s(4.0, LLM) == 4.0  # was 13.0: never inside a 15 s tick's ~10 s window
    note_tick_seconds(30.0)
    assert needed_budget_s(4.0, LLM) == 13.0


def test_the_floor_is_tunable_and_a_bad_tick_length_is_ignored():
    note_tick_seconds(15.0)
    assert decider({**LLM, dec.MIN_TICK_VARIABLE: "10"}) == "llm"
    note_tick_seconds(float("nan"))
    assert decider(LLM) == "llm"
    note_tick_seconds(-3.0)
    assert decider(LLM) == "llm"


def test_jev_stays_jev():
    note_tick_seconds(60.0)
    assert decider({"BAZAAR_DECIDER": "jev"}) == "jev" and decider({}) == "jev"
