"""Port of vendor/jev-sdk/test/jev-judge.test.ts. No live network: httpx.MockTransport only."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from bazaar_agent.jev import (
    JEV_ENDPOINT,
    JEV_MODEL,
    JevUsageError,
    JudgeResult,
    decision_line,
    judge,
    load_questions,
    mask_request,
)
from bazaar_agent.jev.__main__ import main

REPO = Path(__file__).resolve().parents[2]
KEY = "-".join(["fixture", "key", "value", "0001"])

Reply = tuple[int, str, dict[str, str]] | Exception


def answered(answers: object) -> Reply:
    return (200, json.dumps({"model": "jev-1.13.0", "answers": answers}), {})


class Recorder:
    """A fake TypeSafe: replays replies in order, and a clock that each request and sleep moves."""

    def __init__(self, replies: list[Reply]) -> None:
        self.replies = replies
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []
        self.clock = 1.0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.clock += 0.012
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        status, body, headers = reply
        return httpx.Response(status, content=body.encode(), headers=headers)

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds * 1000))
        self.clock += seconds  # a sleep spends the call's budget, so the clock has to move

    def judge(self, state: object, questions: Mapping[str, Any], **options: Any) -> JudgeResult:
        options.setdefault("api_key", KEY)
        return judge(
            state,
            questions,
            transport=httpx.MockTransport(self.handle),
            now=lambda: self.clock,
            sleep=self.sleep,
            **options,
        )

    def body(self, index: int = 0) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.requests[index].content)
        return parsed


NOUL_STATE = "the worker refused to settle"
NOUL_QUESTIONS = {"needs_owner": {"type": "noul", "instructions": "Does the owner have to act?"}}
NOUL = {"thresholds": {"needs_owner": 0.8}, "timeout_s": 5.0}
LAYERS = {"frontend": None, "api": None, "data": None, "blockchain": None}
CHOICE_QUESTIONS = {"failure_layer": {"type": "choice", "instructions": "Which layer failed?", "criteria": LAYERS}}
SCORE_QUESTIONS = {"task_risk": {"type": "score", "instructions": "How risky?", "criteria": ["low", "high"]}}


def noul_judge(replies: list[Reply], **options: Any) -> tuple[Recorder, JudgeResult]:
    recorder = Recorder(replies)
    return recorder, recorder.judge(NOUL_STATE, NOUL_QUESTIONS, **{**NOUL, **options})


# --- the request that leaves the machine ---------------------------------------------------------


def test_posts_one_masked_request_to_the_documented_endpoint() -> None:
    recorder = Recorder([answered({"needs_owner": {"type": "noul", "noul": 0.9}})])
    recorder.judge(
        "worker Bearer fixture-token-value-0001 refused",
        {"needs_owner": {"type": "noul", "instructions": "Does operator.fixture@example.com have to act?"}},
        **NOUL,
    )
    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert str(request.url) == JEV_ENDPOINT
    assert request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"
    body = recorder.body()
    assert body["model"] == JEV_MODEL
    assert body["state"] == "worker [redacted] refused"
    assert body["questions"]["needs_owner"]["instructions"] == "Does [redacted] have to act?"
    assert b"fixture-token-value-0001" not in request.content


def test_never_sends_structured_credentials_in_state_or_question_fields() -> None:
    secret = "-".join(["synthetic", "credential", "value"])
    state = {"nested": {"apiKey": secret}, "items": [{"password": secret}, {"refreshToken": secret}]}
    questions = {
        "needs_owner": {
            "type": "noul",
            "instructions": {"context": [{"authorization": secret}]},
            "criteria": {"true": {"privateKey": secret}, "false": "no action"},
        }
    }
    recorder = Recorder([answered({"needs_owner": {"type": "noul", "noul": 0.9}})])
    result = recorder.judge(state, questions)
    logged = decision_line(state, questions, result, "2026-10-02T12:00:00.000Z")
    assert secret.encode() not in recorder.requests[0].content
    assert secret not in logged
    body = recorder.body()
    assert body["state"]["nested"]["apiKey"] == "[redacted]"
    assert body["state"]["items"] == [{"password": "[redacted]"}, {"refreshToken": "[redacted]"}]
    assert body["questions"]["needs_owner"]["instructions"]["context"][0]["authorization"] == "[redacted]"
    assert body["questions"]["needs_owner"]["criteria"]["true"]["privateKey"] == "[redacted]"


def test_the_body_is_compact_json_in_the_upstream_key_order() -> None:
    recorder = Recorder([answered({"needs_owner": {"type": "noul", "noul": 0.9}})])
    recorder.judge({"cash": 400, "ratio": 1.0}, NOUL_QUESTIONS)
    assert recorder.requests[0].content.decode() == (
        '{"state":{"cash":400,"ratio":1},"model":"jev-latest",'
        '"questions":{"needs_owner":{"type":"noul","instructions":"Does the owner have to act?"}}}'
    )


def test_measures_one_latency_for_the_call() -> None:
    _, result = noul_judge([answered({"needs_owner": {"type": "noul", "noul": 0.9}})])
    assert result.latency_ms == 12


# --- noul answers --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("noul", "verdict", "margin"),
    [(0.96, "yes", 0.92), (0.8, "yes", 0.6), (0.04, "no", 0.92), (0.2, "no", 0.6)],
)
def test_a_noul_probability_is_yes_or_no(noul: float, verdict: str, margin: float) -> None:
    _, result = noul_judge([answered({"needs_owner": {"type": "noul", "noul": noul}})])
    assert result.verdicts["needs_owner"].to_dict() == {
        "type": "noul",
        "verdict": verdict,
        "value": noul,
        "valueKind": "noul-probability",
        "threshold": 0.8,
        "leaning": verdict,
        "margin": margin,
    }


def test_a_probability_inside_the_uncertain_band_is_undecided_never_yes() -> None:
    _, result = noul_judge([answered({"needs_owner": {"type": "noul", "noul": 0.55}})])
    assert result.verdicts["needs_owner"].to_dict() == {
        "type": "noul",
        "verdict": "undecided",
        "value": 0.55,
        "valueKind": "noul-probability",
        "threshold": 0.8,
        "reason": "below_threshold",
        "leaning": "yes",
        "margin": 0.1,
    }


# --- choice and score answers --------------------------------------------------------------------


def choice_judge(answer: object, threshold: float = 0.7, **question: Any) -> JudgeResult:
    recorder = Recorder([answered({"failure_layer": answer})])
    questions = {"failure_layer": {**CHOICE_QUESTIONS["failure_layer"], **question}}
    options: dict[str, Any] = {} if "stakes" in question else {"thresholds": {"failure_layer": threshold}}
    return recorder.judge("the api returned 500 for every order", questions, **options)


def test_a_confident_choice_is_its_option() -> None:
    probabilities = {"frontend": 0.05, "api": 0.85, "data": 0.1, "blockchain": 0}
    result = choice_judge({"type": "choice", "choice": "api", "probabilities": probabilities, "confidence": 0.82})
    assert result.verdicts["failure_layer"].to_dict() == {
        "type": "choice",
        "verdict": "api",
        "value": 0.82,
        "valueKind": "confidence",
        "threshold": 0.7,
        "leaning": "api",
        "probabilities": probabilities,
        "margin": 0.75,
    }


def test_an_unconfident_choice_is_undecided() -> None:
    probabilities = {"frontend": 0.3, "api": 0.35, "data": 0.35, "blockchain": 0}
    result = choice_judge({"type": "choice", "choice": "api", "probabilities": probabilities, "confidence": 0.4})
    assert result.verdicts["failure_layer"].to_dict() == {
        "type": "choice",
        "verdict": "undecided",
        "value": 0.4,
        "valueKind": "confidence",
        "threshold": 0.7,
        "reason": "below_threshold",
        "leaning": "api",
        "probabilities": probabilities,
        "margin": 0,
    }


def test_a_chosen_option_outside_the_declared_criteria_is_a_schema_mismatch() -> None:
    result = choice_judge({"type": "choice", "choice": "engine", "probabilities": {}, "confidence": 0.99})
    assert result.verdicts["failure_layer"].reason == "response_schema_mismatch"


def test_a_confident_score_is_the_score_it_returned() -> None:
    recorder = Recorder(
        [
            answered(
                {
                    "task_risk": {
                        "type": "score",
                        "score": 1.25,
                        "legend": {"0": "low", "1": "high"},
                        "probabilities": {"0": 0.25, "1": 0.75},
                        "confidence": 0.75,
                    }
                }
            )
        ]
    )
    result = recorder.judge("three retries, still failing", SCORE_QUESTIONS, thresholds={"task_risk": 0.6})
    assert result.verdicts["task_risk"].to_dict() == {
        "type": "score",
        "verdict": "1.25",
        "value": 0.75,
        "valueKind": "confidence",
        "threshold": 0.6,
        "leaning": "1.25",
        "probabilities": {"0": 0.25, "1": 0.75},
        "margin": 0.5,
    }


def test_an_integral_score_reads_like_the_upstream_string() -> None:
    recorder = Recorder([answered({"task_risk": {"type": "score", "score": 2.0, "confidence": 0.9}})])
    assert recorder.judge("state", SCORE_QUESTIONS).verdicts["task_risk"].verdict == "2"


# --- every failure is the closed verdict undecided -----------------------------------------------


def test_a_missing_key_refuses_without_one_request() -> None:
    recorder, result = noul_judge([answered({})], api_key="")
    assert recorder.requests == []
    assert result.model == ""
    assert result.verdicts["needs_owner"].to_dict() == {
        "type": "noul",
        "verdict": "undecided",
        "value": 0,
        "valueKind": "noul-probability",
        "threshold": 0.8,
        "reason": "typesafe_api_key_missing",
    }


def test_the_key_defaults_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    recorder = Recorder([answered({})])
    result = judge(NOUL_STATE, NOUL_QUESTIONS, transport=httpx.MockTransport(recorder.handle))
    assert recorder.requests == []
    assert result.verdicts["needs_owner"].reason == "typesafe_api_key_missing"

    monkeypatch.setenv("TYPESAFE_API_KEY", f"  {KEY}  ")
    recorder = Recorder([answered({"needs_owner": {"type": "noul", "noul": 0.9}})])
    result = judge(NOUL_STATE, NOUL_QUESTIONS, transport=httpx.MockTransport(recorder.handle))
    assert recorder.requests[0].headers["authorization"] == f"Bearer {KEY}"
    assert result.verdicts["needs_owner"].verdict == "yes"


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (httpx.ReadTimeout("timed out"), "request_timeout"),
        (httpx.ConnectTimeout("timed out"), "request_timeout"),
        (httpx.ConnectError("connect ECONNREFUSED"), "network_error"),
        (RuntimeError("anything else"), "network_error"),
    ],
)
def test_a_transport_failure_is_undecided(failure: Exception, reason: str) -> None:
    _, result = noul_judge([failure])
    assert result.verdicts["needs_owner"].verdict == "undecided"
    assert result.verdicts["needs_owner"].reason == reason


def test_the_deadline_holds_even_when_the_socket_hangs() -> None:
    def hang(request: httpx.Request) -> httpx.Response:
        time.sleep(2.0)
        return httpx.Response(200, json={"model": "late", "answers": {}})

    started = time.monotonic()
    result = judge(NOUL_STATE, NOUL_QUESTIONS, api_key=KEY, timeout_s=0.2, transport=httpx.MockTransport(hang))
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    assert result.verdicts["needs_owner"].reason == "request_timeout"


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "http_client_error"),
        (422, "http_client_error"),
        (500, "http_server_error"),
        (529, "http_server_error"),
        (302, "response_schema_mismatch"),
    ],
)
def test_a_failing_status_is_undecided(status: int, reason: str) -> None:
    _, result = noul_judge([(status, "{}", {})])
    assert result.verdicts["needs_owner"].reason == reason


@pytest.mark.parametrize(
    "reply",
    [
        (200, "[]", {}),
        (200, json.dumps({"model": "jev-1.13.0"}), {}),
        (200, "not json", {}),
        (200, "NaN", {}),
    ],
)
def test_a_body_without_answers_is_a_schema_mismatch(reply: Reply) -> None:
    _, result = noul_judge([reply])
    assert result.verdicts["needs_owner"].reason == "response_schema_mismatch"


def test_an_absent_answer_is_undecided_for_that_id_only() -> None:
    recorder = Recorder([answered({"needs_owner": {"type": "noul", "noul": 0.95}})])
    questions = {
        **NOUL_QUESTIONS,
        "touches_money_path": {"type": "noul", "instructions": "Does it touch money?"},
    }
    result = recorder.judge(NOUL_STATE, questions, thresholds={"needs_owner": 0.8, "touches_money_path": 0.8})
    assert result.verdicts["needs_owner"].verdict == "yes"
    assert result.verdicts["touches_money_path"].to_dict() == {
        "type": "noul",
        "verdict": "undecided",
        "value": 0,
        "valueKind": "noul-probability",
        "threshold": 0.8,
        "reason": "answer_missing",
    }


def test_an_answer_of_the_wrong_type_is_undecided() -> None:
    _, result = noul_judge([answered({"needs_owner": {"type": "choice", "choice": "yes", "confidence": 0.99}})])
    assert result.verdicts["needs_owner"].reason == "answer_type_mismatch"


@pytest.mark.parametrize("noul", [1.4, -0.1, True, "0.9"])
def test_a_probability_that_is_not_a_fraction_is_a_schema_mismatch(noul: object) -> None:
    _, result = noul_judge([answered({"needs_owner": {"type": "noul", "noul": noul}})])
    assert result.verdicts["needs_owner"].verdict == "undecided"
    assert result.verdicts["needs_owner"].reason == "response_schema_mismatch"


# --- rate limits retry, and nothing else does ----------------------------------------------------


def test_honours_retry_after_twice_then_answers() -> None:
    limited: Reply = (429, "{}", {"retry-after": "2"})
    recorder, result = noul_judge(
        [limited, limited, answered({"needs_owner": {"type": "noul", "noul": 0.93}})], timeout_s=10.0
    )
    assert len(recorder.requests) == 3
    assert recorder.slept == [2000, 2000]
    assert result.verdicts["needs_owner"].verdict == "yes"


def test_stops_after_two_retries_and_stays_undecided() -> None:
    recorder, result = noul_judge([(429, "{}", {"retry-after": "1"})])
    assert len(recorder.requests) == 3
    assert recorder.slept == [1000, 1000]
    assert result.verdicts["needs_owner"].reason == "rate_limited"


def test_a_wait_longer_than_the_budget_is_refused_at_once_with_no_sleep() -> None:
    recorder, result = noul_judge([(429, "{}", {"retry-after": "60"})], timeout_s=8.0)
    assert len(recorder.requests) == 1
    assert recorder.slept == []
    assert result.latency_ms < 8000
    assert result.verdicts["needs_owner"].reason == "rate_limited"


def test_stops_retrying_when_the_next_wait_plus_an_attempt_no_longer_fits() -> None:
    recorder, result = noul_judge([(429, "{}", {"retry-after": "2"})], timeout_s=5.0)
    assert recorder.slept == [2000]
    assert result.latency_ms <= 5000
    assert result.verdicts["needs_owner"].reason == "rate_limited"


@pytest.mark.parametrize("header", [None, "", "soon", "1_000", "-3", "inf"])
def test_a_missing_or_unreadable_retry_after_waits_one_second(header: str | None) -> None:
    headers = {} if header is None else {"retry-after": header}
    recorder, _ = noul_judge([(429, "{}", headers)])
    assert recorder.slept == [1000, 1000]


def test_a_500_is_never_retried() -> None:
    recorder, _ = noul_judge([(500, "{}", {})])
    assert len(recorder.requests) == 1


# --- a caller mistake is a usage error, never a verdict ------------------------------------------


@pytest.mark.parametrize(
    ("state", "questions", "options"),
    [
        (NOUL_STATE, {}, {}),
        (NOUL_STATE, {"q": {"type": "guess", "instructions": "?"}}, {}),
        (NOUL_STATE, {"q": {"type": "choice", "instructions": "?", "criteria": {"only": None}}}, {}),
        (NOUL_STATE, {"q": {"type": "score", "instructions": "?", "criteria": ["one"]}}, {}),
        (NOUL_STATE, {"q": {"type": "noul", "instructions": ""}}, {}),
        (NOUL_STATE, {"q": {"type": "noul", "instructions": "?", "stakes": "low"}}, {}),
        (NOUL_STATE, NOUL_QUESTIONS, {"thresholds": {"needs_owner": 1.2}}),
        (NOUL_STATE, NOUL_QUESTIONS, {"thresholds": {"needs_owner": 0}}),
        (NOUL_STATE, NOUL_QUESTIONS, {"thresholds": {"needs_owner": 0.5}}),
        ("   ", NOUL_QUESTIONS, {}),
        (NOUL_STATE, NOUL_QUESTIONS, {"timeout_s": 0}),
        (NOUL_STATE, NOUL_QUESTIONS, {"timeout_s": 121}),
        ("x" * 96_001, NOUL_QUESTIONS, {}),
        ({"when": object()}, NOUL_QUESTIONS, {}),
    ],
    ids=[
        "no question",
        "unknown type",
        "choice with one option",
        "score with one level",
        "empty instructions",
        "unknown stakes",
        "threshold above one",
        "threshold at zero",
        "noul threshold at the coin flip",
        "empty state",
        "timeout of zero",
        "timeout above the cap",
        "state over the budget",
        "state that is not JSON",
    ],
)
def test_a_caller_mistake_raises(state: object, questions: dict[str, Any], options: dict[str, Any]) -> None:
    recorder = Recorder([answered({})])
    with pytest.raises(JevUsageError):
        recorder.judge(state, questions, **options)
    assert recorder.requests == []


def test_a_question_with_no_threshold_uses_the_default() -> None:
    _, result = noul_judge([answered({"needs_owner": {"type": "noul", "noul": 0.9}})], thresholds={})
    assert result.verdicts["needs_owner"].threshold == 0.8
    assert result.verdicts["needs_owner"].verdict == "yes"


# --- the bar a question clears is set by its stakes ----------------------------------------------


@pytest.mark.parametrize(("stakes", "threshold"), [("passive", 0.6), ("design", 0.75), ("critical", 0.9)])
def test_stakes_set_the_bar(stakes: str, threshold: float) -> None:
    recorder = Recorder([answered({"needs_owner": {"type": "noul", "noul": 0.95}})])
    questions = {"needs_owner": {**NOUL_QUESTIONS["needs_owner"], "stakes": stakes}}
    assert recorder.judge(NOUL_STATE, questions).verdicts["needs_owner"].threshold == threshold


def test_an_explicit_threshold_overrides_the_declared_stakes() -> None:
    recorder = Recorder([answered({"needs_owner": {"type": "noul", "noul": 0.95}})])
    questions = {"needs_owner": {**NOUL_QUESTIONS["needs_owner"], "stakes": "passive"}}
    result = recorder.judge(NOUL_STATE, questions, thresholds={"needs_owner": 0.92})
    assert result.verdicts["needs_owner"].threshold == 0.92


def test_the_passive_bar_decides_what_the_critical_bar_leaves_undecided() -> None:
    answer = {
        "type": "choice",
        "choice": "api",
        "probabilities": {"frontend": 0.1, "api": 0.72, "data": 0.14, "blockchain": 0.04},
        "confidence": 0.64,
    }
    passive = choice_judge(answer, stakes="passive").verdicts["failure_layer"]
    critical = choice_judge(answer, stakes="critical").verdicts["failure_layer"]
    assert (passive.verdict, passive.threshold) == ("api", 0.6)
    assert (critical.verdict, critical.reason) == ("undecided", "below_threshold")


def test_the_stakes_word_never_reaches_the_model() -> None:
    recorder = Recorder([answered({"needs_owner": {"type": "noul", "noul": 0.95}})])
    recorder.judge(NOUL_STATE, {"needs_owner": {**NOUL_QUESTIONS["needs_owner"], "stakes": "critical"}})
    assert b"critical" not in recorder.requests[0].content
    assert b"stakes" not in recorder.requests[0].content


# --- a verdict carries the distribution it came from ---------------------------------------------


def test_a_choice_keeps_every_probability_the_margin_and_the_leaning() -> None:
    probabilities = {"frontend": 0.05, "api": 0.6, "data": 0.3, "blockchain": 0.05}
    verdict = (
        Recorder(
            [
                answered(
                    {
                        "failure_layer": {
                            "type": "choice",
                            "choice": "api",
                            "probabilities": probabilities,
                            "confidence": 0.47,
                        }
                    }
                )
            ]
        )
        .judge("the api returned 500", CHOICE_QUESTIONS)
        .verdicts["failure_layer"]
    )
    assert verdict.verdict == "undecided"
    assert dict(verdict.probabilities or {}) == probabilities
    assert verdict.margin == 0.3
    assert verdict.leaning == "api"


def test_a_noul_leans_and_carries_its_distance_from_the_coin_flip() -> None:
    _, result = noul_judge([answered({"needs_owner": {"type": "noul", "noul": 0.72}})])
    verdict = result.verdicts["needs_owner"]
    assert (verdict.verdict, verdict.leaning, verdict.margin, verdict.probabilities) == (
        "undecided",
        "yes",
        0.44,
        None,
    )


def test_a_transport_failure_carries_no_leaning() -> None:
    _, result = noul_judge([(500, "{}", {})])
    verdict = result.verdicts["needs_owner"]
    assert (verdict.leaning, verdict.margin, verdict.probabilities) == (None, None, None)


def test_a_missing_distribution_is_left_out_and_the_verdict_still_decides() -> None:
    verdict = (
        Recorder([answered({"failure_layer": {"type": "choice", "choice": "api", "confidence": 0.92}})])
        .judge("the api returned 500", CHOICE_QUESTIONS)
        .verdicts["failure_layer"]
    )
    assert (verdict.verdict, verdict.leaning, verdict.probabilities, verdict.margin) == (
        "api",
        "api",
        None,
        None,
    )


def test_a_single_option_distribution_has_the_whole_probability_as_its_margin() -> None:
    answer = {"type": "score", "score": 0, "probabilities": {"0": 1}, "confidence": 1}
    verdict = Recorder([answered({"task_risk": answer})]).judge("nothing failed", SCORE_QUESTIONS)
    assert verdict.verdicts["task_risk"].margin == 1


# --- the Bazaar question pack and the command line -----------------------------------------------


def test_the_negotiation_pack_loads_and_validates() -> None:
    questions = load_questions(REPO / "questions" / "negotiation.json")
    masked = mask_request({"cash": 400}, questions)
    assert set(masked.questions) == {"offer_is_worth_accepting", "counterpart_needs_card", "negotiation_move"}
    assert dict(masked.thresholds) == {
        "offer_is_worth_accepting": 0.75,
        "counterpart_needs_card": 0.6,
        "negotiation_move": 0.75,
    }


def test_a_pack_that_is_not_json_is_a_usage_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(JevUsageError):
        load_questions(broken)
    with pytest.raises(JevUsageError):
        load_questions(tmp_path / "absent.json")


def test_the_cli_prints_the_upstream_shape_and_exits_zero_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"cash": 400, "tick": 17}))
    directory = tmp_path / "log"
    code = main(
        [
            "judge",
            "--state",
            str(state),
            "--questions",
            str(REPO / "questions" / "negotiation.json"),
            "--log",
            "--directory",
            str(directory),
        ]
    )
    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert list(printed) == ["model", "latencyMs", "verdicts"]
    assert {verdict["reason"] for verdict in printed["verdicts"].values()} == {"typesafe_api_key_missing"}
    assert len(list(directory.glob("*.jsonl"))) == 1

    assert main(["report", "--directory", str(directory)]) == 0
    assert "negotiation_move" in capsys.readouterr().out


def test_the_cli_exits_two_for_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["judge", "--state", str(tmp_path / "absent.json"), "--questions", str(tmp_path / "q.json")])
    assert code == 2
    assert "cannot read" in capsys.readouterr().err
