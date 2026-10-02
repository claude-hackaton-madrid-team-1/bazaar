"""Regression tests for the code and security review of the runtime LLM layer (fakes only, no network)."""

import json
import math
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from bazaar_agent.agents import dealer
from bazaar_agent.agents.dealer import BidPlan, negotiate
from bazaar_agent.guardrails import Guardrails, GuardrailsError, parse_guardrails
from bazaar_agent.llm import intent as it
from bazaar_agent.llm import steering as st
from bazaar_agent.llm.chooser import ModelChooser, MoveSituation, cache_key, read_choices
from bazaar_agent.llm.config import RuntimeConfig
from bazaar_agent.llm.providers import LLMError, TextRequest, attempt_timeout_s
from bazaar_agent.llm.runtime import LLMRuntime
from tests.test_dealer import FakeDealerClient
from tests.test_llm import FLOATS, FakeJudge, FakeProvider, settings, verdict

RULES = Guardrails()


def make_chooser(tmp_path, judge, available=None):
    return ModelChooser(
        RuntimeConfig(),
        pin=None,
        jev_timeout_s=3.0,
        jev_api_key="ts",
        log_path=tmp_path / "choices.jsonl",
        judge_fn=judge,
        available=available,
    )


# ---------------------------------------------------------------- chooser


def test_the_cache_never_reuses_a_harmless_choice_for_a_flagged_or_high_stakes_move(tmp_path):
    judge = FakeJudge(verdict("haiku-4-5", 0.9, FLOATS))
    chooser = make_chooser(tmp_path, judge)
    calm = MoveSituation("words", 5, 15.0, 10.0)
    chooser.choose(calm, tick=1)
    chooser.choose(MoveSituation("words", 5, 15.0, 10.0, 80, ("instruction_override",)), tick=1)
    chooser.choose(MoveSituation("words", 90, 15.0, 10.0), tick=1)
    assert len(judge.calls) == 3
    assert cache_key(calm) == ("words", "fast", False, "low")


def test_jev_only_sees_candidates_we_hold_a_key_for_and_none_means_no_call(tmp_path):
    judge = FakeJudge(verdict("sonnet-5-5", 0.9, FLOATS))
    claude_only = make_chooser(tmp_path, judge, available=lambda alias: not alias.startswith("gpt"))
    claude_only.choose(MoveSituation("buy"), tick=1)
    assert set(judge.calls[0]["questions"]["model_for_move"]["criteria"]) == {"haiku-4-5", "sonnet-5-5", "opus-5-5"}
    nothing = FakeJudge(verdict("sonnet-5-5", 0.9, FLOATS))
    choice = make_chooser(tmp_path, nothing, available=lambda alias: False).choose(MoveSituation("buy"), tick=1)
    assert (choice.source, nothing.calls) == ("default", [])


def test_a_malformed_choice_log_never_stops_startup(tmp_path):
    log = tmp_path / "choices.jsonl"
    good = {"tick": 3, "kind": "words", "bucket": "fast", "flagged": False, "stakes": "low"}
    lines = [
        {**good, "model": "sonnet-5-5", "source": "jev", "probabilities": ["not", "a", "map"]},
        {**good, "model": "sonnet-5-5", "source": "jev", "tick": True},
        {"model": "opus-5-5", "source": "jev"},
        {**good, "model": "opus-5-5", "source": "jev", "probabilities": {"opus-5-5": 0.9}, "confidence": 0.9},
    ]
    log.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    judge = FakeJudge(verdict("haiku-4-5", 0.9, FLOATS))
    choice = make_chooser(tmp_path, judge).choose(MoveSituation("words", 5, 15.0, 10.0), tick=4)
    assert (choice.alias, choice.cached, judge.calls) == ("opus-5-5", True, [])
    assert len(read_choices(log, 10)) == 4


# ---------------------------------------------------------------- runtime and providers


def test_providers_are_built_once_and_no_key_fails_before_jev(tmp_path):
    built = []
    judge = FakeJudge(verdict("sonnet-5-5", 0.9, FLOATS))
    config = RuntimeConfig()
    s = settings(tmp_path, anthropic_api_key="sk-ant-x")
    chooser = make_chooser(tmp_path, judge, available=lambda alias: not alias.startswith("gpt"))
    rt = LLMRuntime(config, s, chooser, factory=lambda prov, key: built.append(prov) or FakeProvider())
    rt.warm()
    rt.pick(MoveSituation("buy"), tick=1)
    rt.pick(MoveSituation("sell"), tick=1)
    assert built == ["anthropic"]
    bare = FakeJudge(verdict("sonnet-5-5", 0.9, FLOATS))
    keyless = LLMRuntime(config, settings(tmp_path), make_chooser(tmp_path, bare, available=lambda a: False))
    with pytest.raises(LLMError, match="set ANTHROPIC_API_KEY"):
        keyless.pick(MoveSituation("words"), tick=1)
    assert bare.calls == []


def test_every_retry_fits_inside_the_hard_deadline():
    assert attempt_timeout_s(TextRequest("m", "s", "u", 1, 30.0, retries=2)) == 10.0
    assert attempt_timeout_s(TextRequest("m", "s", "u", 1, 2.5)) == 2.5


# ---------------------------------------------------------------- steering


def test_whole_number_parameters_round_inside_the_fence():
    assert st.clamp("min_buy_surplus", 1, -0.5, RULES) == 1.0  # 0 would be a 100 % change
    assert st.clamp("min_buy_surplus", 1, 0.5, RULES) == 1.0
    assert st.clamp("min_buy_surplus", 5, 9, RULES) == 7.0  # fence 7.5, rounded inside it
    assert st.clamp("duel_anchor", 0.6, math.nan, RULES) == 0.6


def test_the_floor_margin_only_tightens_and_nan_deltas_are_refused():
    assert st.clamp("duel_floor_margin", 0.05, -0.05, RULES) == 0.05
    assert st.clamp("duel_floor_margin", 0.05, 0.02, RULES) == 0.07
    with pytest.raises(ValidationError):
        st.SteerDelta.model_validate({"param": "duel_anchor", "delta": math.nan})
    with pytest.raises(GuardrailsError, match="steer_max_change"):
        parse_guardrails("- `steer_max_change` = 2 — looser than the base itself")


def test_duel_run_reads_active_steering_each_tick(tmp_path):
    path = tmp_path / "steering.json"
    assert st.steered_duel_params(RULES, path, 10) == (RULES.duel_anchor, RULES.duel_floor_margin)
    st.save_steering(path, st.Steering("tougher", "s", {"duel_anchor": 0.2, "duel_floor_margin": 0.01}, 10, 20, "m"))
    assert st.steered_duel_params(RULES, path, 12) == (0.8, 0.06)
    assert st.steered_duel_params(RULES, path, 20) == (RULES.duel_anchor, RULES.duel_floor_margin)
    path.write_text(json.dumps({**json.loads(path.read_text()), "deltas": {"duel_anchor": "nan"}}))
    assert st.load_steering(path) is None


def test_every_steerable_parameter_names_what_reads_it():
    assert {b.used_by for b in st.STEERABLE.values()} == {"duel run", st.USED_BY_STRATEGY}


# ---------------------------------------------------------------- intent


def test_a_counterparty_can_never_start_with_a_dash_in_the_printed_command():
    for bad in ("-live", "--live"):
        with pytest.raises(it.IntentError):
            it.validate_intent(
                it.IntentDraft(
                    kind="buy",
                    item="LAV-09",
                    max_price=9,
                    min_price=None,
                    counterparty=bad,
                    constraints=[],
                    question=None,
                )
            )


# ---------------------------------------------------------------- the live loop never sends late


def test_slow_words_are_dropped_and_the_bid_is_re_decided_next_tick(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(dealer, "time", SimpleNamespace(monotonic=lambda: now["t"], sleep=lambda _: None))

    class Spy(FakeDealerClient):
        def say(self, tid, text, price):
            self.texts = [*getattr(self, "texts", []), text]
            super().say(tid, text, price)

    def slow_first_then_fast(request):
        if request.step == 0 and not getattr(slow_first_then_fast, "done", False):
            slow_first_then_fast.done = True
            now["t"] += 60  # the words used the whole tick
        return "palabras"

    client, logs = Spy(asks=[12, 10, 9]), []
    out = negotiate(
        client,
        "abuela",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 10),
        log=logs.append,
        sleep=lambda _: None,
        words_fn=slow_first_then_fast,
    )
    late = [line for line in logs if "the words took the rest of the tick" in line]
    assert len(late) == 1 and logs.index(late[0]) < next(i for i, line in enumerate(logs) if "→ bid 7" in line)
    assert client.sent == [6, 7, 8] and client.texts == ["palabras"] * 3 and out.status == "deal"
