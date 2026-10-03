"""Typed Jev judgments for Bazaar decisions. A verdict informs a move; it never authorizes one.

Python port of `vendor/jev-sdk/src/judge.ts`. One request asks every question about one state and
returns one verdict per question id. A malformed request raises `JevUsageError`; every other failure
(missing key, timeout, network, HTTP status, bad body, low confidence) is the verdict `undecided`
with a reason from `JEV_UNDECIDED_REASONS`, so a failure never raises into the trading loop.

`timeout_s` is the budget for the WHOLE call, rate-limit waits included. Each HTTP attempt runs on a
daemon thread that the caller stops waiting for at the deadline, because httpx timeouts bound each
phase of a request, not the request as a whole.
"""

from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import httpx

from bazaar_agent.jev.decider import decider
from bazaar_agent.jev.mask import (
    JEV_QUESTION_TYPES,
    JEV_STAKES,
    JS_WHITESPACE,
    JsonValue,
    js_json_dumps,
    js_number,
    mask_questions,
    mask_state,
    utf16_length,
)

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
JEV_API_KEY_VARIABLE = "TYPESAFE_API_KEY"
JEV_DEFAULT_THRESHOLD = 0.8
# A floor at 0.6 catches genuine uncertainty; a decision that costs real money waits for 0.9.
JEV_STAKES_THRESHOLDS: Mapping[str, float] = MappingProxyType({"passive": 0.6, "design": 0.75, "critical": 0.9})
JEV_DEFAULT_TIMEOUT_S = 10.0
JEV_DEFAULT_TIMEOUT_MS = 10_000
JEV_MAX_TIMEOUT_MS = 120_000
# The documented budget is 32k tokens for the state plus the longest question.
JEV_MAX_STATE_CHARACTERS = 96_000
JEV_MAX_RATE_LIMIT_RETRIES = 2
# The least time worth spending on another attempt once a rate limit wait has been paid for.
JEV_MIN_ATTEMPT_MS = 1_000
JEV_RATE_LIMIT_FALLBACK_MS = 1_000
JEV_UNDECIDED = "undecided"

# Every reason a verdict can be `undecided`. Nothing outside this list closes a decision.
JEV_UNDECIDED_REASONS = (
    "typesafe_api_key_missing",
    "request_timeout",
    "network_error",
    "http_client_error",
    "http_server_error",
    "rate_limited",
    "response_schema_mismatch",
    "answer_missing",
    "answer_type_mismatch",
    "below_threshold",
    # BAZAAR_DECIDER=llm only (`bazaar_agent.llm.decider`):
    "llm_unavailable",
    "decider_call_cap",
)

Questions = Mapping[str, Mapping[str, object]]


class JevUsageError(ValueError):
    """A caller mistake. It is never a verdict: a bad request must be fixed, not judged."""


@dataclass(frozen=True)
class Verdict:
    """One question's answer after the bar. `value` is a noul probability or a confidence."""

    type: str
    verdict: str
    value: float
    value_kind: str
    threshold: float
    reason: str | None = None
    # The answer the model gave, whatever the bar did with it; absent when nothing was answered.
    leaning: str | None = None
    # The distribution as returned: every option for a choice, every level for a score.
    probabilities: Mapping[str, float] | None = None
    # Top minus second; for a noul, the distance from the coin flip.
    margin: float | None = None

    @property
    def decided(self) -> bool:
        return self.verdict != JEV_UNDECIDED

    def to_dict(self) -> dict[str, JsonValue]:
        """The upstream camelCase shape, in the upstream key order, unknown keys left out."""
        written: dict[str, JsonValue] = {
            "type": self.type,
            "verdict": self.verdict,
            "value": self.value,
            "valueKind": self.value_kind,
            "threshold": self.threshold,
        }
        optional: dict[str, JsonValue] = {
            "reason": self.reason,
            "leaning": self.leaning,
            "probabilities": None if self.probabilities is None else dict(self.probabilities),
            "margin": self.margin,
        }
        return {**written, **{key: value for key, value in optional.items() if value is not None}}


@dataclass(frozen=True)
class JudgeResult:
    # The model version the service reported, or "" when no answer arrived.
    model: str
    latency_ms: int
    verdicts: Mapping[str, Verdict]

    def to_dict(self) -> dict[str, JsonValue]:
        verdicts: dict[str, JsonValue] = {key: verdict.to_dict() for key, verdict in self.verdicts.items()}
        return {"model": self.model, "latencyMs": self.latency_ms, "verdicts": verdicts}

    def to_json(self) -> str:
        """Exactly what the upstream CLI prints for the same result."""
        return js_json_dumps(self.to_dict())


@dataclass(frozen=True)
class MaskedRequest:
    """A request with every string masked, as it will leave the machine."""

    state: JsonValue
    questions: Mapping[str, Mapping[str, JsonValue]]
    thresholds: Mapping[str, float]
    timeout_ms: int

    def written_state(self) -> str:
        return self.state if isinstance(self.state, str) else js_json_dumps(self.state)


@dataclass(frozen=True)
class _Answered:
    model: str
    answers: Mapping[str, object]


@dataclass(frozen=True)
class _Failed:
    reason: str


@dataclass(frozen=True)
class _RateLimited:
    retry_after_ms: int


@dataclass(frozen=True)
class _Reply:
    status: int
    retry_after: str | None
    content: bytes


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_fraction(value: object) -> bool:
    return _is_number(value) and 0 <= value <= 1  # type: ignore[operator]


def _js_round(value: float) -> int:
    """`Math.round`, which rounds a half up; Python's `round` rounds it to even."""
    return math.floor(value + 0.5)


# --- validation ----------------------------------------------------------------------------------


def _validate_criteria(question_id: str, question: Mapping[str, object]) -> None:
    question_type, criteria = question.get("type"), question.get("criteria")
    if question_type == "choice" and (not isinstance(criteria, Mapping) or len(criteria) < 2):
        raise JevUsageError(f"question {question_id}: a choice needs at least two criteria options")
    if question_type == "score" and (not isinstance(criteria, list | tuple) or not 2 <= len(criteria) <= 10):
        raise JevUsageError(f"question {question_id}: a score needs between two and ten criteria levels")


def _validate_question(question_id: str, question: object) -> None:
    if not isinstance(question, Mapping):
        raise JevUsageError(f"question {question_id} is not an object")
    if question.get("type") not in JEV_QUESTION_TYPES:
        raise JevUsageError(f"question {question_id}: type must be one of {', '.join(JEV_QUESTION_TYPES)}")
    if "stakes" in question and question["stakes"] not in JEV_STAKES:
        raise JevUsageError(f"question {question_id}: stakes must be one of {', '.join(JEV_STAKES)}")
    instructions = question.get("instructions")
    if len(js_json_dumps("" if instructions is None else instructions)) < 3:
        raise JevUsageError(f"question {question_id}: instructions are empty")
    _validate_criteria(question_id, question)


def threshold_for(question: Mapping[str, object], override: object = None) -> object:
    """An explicit threshold wins, then the stakes the question declares, then the flat default."""
    if override is not None:
        return override
    stakes = question.get("stakes")
    if isinstance(stakes, str) and stakes in JEV_STAKES_THRESHOLDS:
        return JEV_STAKES_THRESHOLDS[stakes]
    return JEV_DEFAULT_THRESHOLD


def _validated_threshold(question_id: str, question: Mapping[str, object], override: object) -> float:
    threshold = threshold_for(question, override)
    # A noul threshold at or below 0.5 would call the same probability both yes and no.
    floor = 0.5 if question.get("type") == "noul" else 0
    if not _is_number(threshold) or threshold > 1 or threshold <= floor:  # type: ignore[operator]
        raise JevUsageError(f"question {question_id}: threshold must be above {floor} and at most 1")
    return float(threshold)  # type: ignore[arg-type]


def _timeout_ms(timeout_s: object) -> int:
    if not _is_number(timeout_s):
        raise JevUsageError(f"timeout must be between 0.001 and {JEV_MAX_TIMEOUT_MS // 1000} seconds")
    return _js_round(float(timeout_s) * 1000)  # type: ignore[arg-type]


def mask_request(
    state: object,
    questions: Questions,
    thresholds: Mapping[str, float] | None = None,
    timeout_ms: int = JEV_DEFAULT_TIMEOUT_MS,
) -> MaskedRequest:
    """Validate the request and mask everything in it. Raises `JevUsageError` for a caller mistake."""
    if not isinstance(questions, Mapping) or len(questions) == 0:
        raise JevUsageError("at least one question is required")
    for question_id, question in questions.items():
        _validate_question(question_id, question)

    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 0 < timeout_ms <= JEV_MAX_TIMEOUT_MS:
        raise JevUsageError(f"timeout must be between 1 ms and {JEV_MAX_TIMEOUT_MS} ms")

    try:
        masked_state = mask_state(state)
    except TypeError as error:
        raise JevUsageError(str(error)) from error
    written = masked_state if isinstance(masked_state, str) else js_json_dumps(masked_state)
    if len(written.strip(JS_WHITESPACE)) == 0:
        raise JevUsageError("state is empty")
    if utf16_length(written) > JEV_MAX_STATE_CHARACTERS:
        raise JevUsageError(f"state is larger than {JEV_MAX_STATE_CHARACTERS} characters")

    overrides = thresholds or {}
    bars = {
        question_id: _validated_threshold(question_id, question, overrides.get(question_id))
        for question_id, question in questions.items()
    }
    return MaskedRequest(
        state=masked_state,
        questions=MappingProxyType(mask_questions(questions)),
        thresholds=MappingProxyType(bars),
        timeout_ms=timeout_ms,
    )


# --- transport -----------------------------------------------------------------------------------


def _post(body: bytes, timeout_s: float, api_key: str, transport: httpx.BaseTransport | None) -> _Reply:
    headers = {
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json",
        "accept": "application/json",
    }
    # Redirects are not followed: the bearer token goes to the documented endpoint and nowhere else.
    with httpx.Client(transport=transport, timeout=timeout_s, follow_redirects=False) as client:
        response = client.post(JEV_ENDPOINT, content=body, headers=headers)
        return _Reply(response.status_code, response.headers.get("retry-after"), response.content)


def _reply_within(
    body: bytes, timeout_ms: float, api_key: str, transport: httpx.BaseTransport | None
) -> _Reply | _Failed:
    """One attempt that the caller abandons at `timeout_ms`, whatever the socket is doing."""
    timeout_s = timeout_ms / 1000
    replies: queue.SimpleQueue[_Reply | Exception] = queue.SimpleQueue()

    def run() -> None:
        try:
            replies.put(_post(body, timeout_s, api_key, transport))
        except Exception as error:  # handed to the caller below and mapped to a reason
            replies.put(error)

    threading.Thread(target=run, name="jev-judge-attempt", daemon=True).start()
    try:
        reply = replies.get(timeout=timeout_s)
    except queue.Empty:
        return _Failed("request_timeout")
    if isinstance(reply, httpx.TimeoutException | TimeoutError):
        return _Failed("request_timeout")
    if isinstance(reply, Exception):
        return _Failed("network_error")
    return reply


def _retry_after_ms(header: str | None) -> int:
    raw = (header or "").strip(JS_WHITESPACE)
    try:
        seconds = float(raw) if raw and "_" not in raw else 0.0
    except ValueError:
        seconds = 0.0
    if not math.isfinite(seconds) or seconds <= 0:
        return JEV_RATE_LIMIT_FALLBACK_MS
    return min(_js_round(seconds * 1000), JEV_MAX_TIMEOUT_MS)


def _status_failure(status: int) -> str | None:
    if status >= 500:
        return "http_server_error"
    if status >= 400:
        return "http_client_error"
    if status >= 300 or status < 200:
        return "response_schema_mismatch"
    return None


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} is not JSON")


def _parsed_body(content: bytes) -> object:
    try:
        return json.loads(content.decode("utf-8"), parse_constant=_reject_constant)
    except ValueError:
        return None


def _attempt(
    body: bytes, timeout_ms: float, api_key: str, transport: httpx.BaseTransport | None
) -> _Answered | _Failed | _RateLimited:
    reply = _reply_within(body, timeout_ms, api_key, transport)
    if isinstance(reply, _Failed):
        return reply
    if reply.status == 429:
        return _RateLimited(_retry_after_ms(reply.retry_after))
    failure = _status_failure(reply.status)
    if failure is not None:
        return _Failed(failure)
    payload = _parsed_body(reply.content)
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        return _Failed("response_schema_mismatch")
    model = payload.get("model")
    return _Answered(model if isinstance(model, str) else "", payload["answers"])


def _send(
    request: MaskedRequest,
    api_key: str,
    transport: httpx.BaseTransport | None,
    now_ms: Callable[[], float],
    sleep: Callable[[float], None],
    deadline_ms: float,
) -> _Answered | _Failed:
    body = js_json_dumps({"state": request.state, "model": JEV_MODEL, "questions": request.questions})
    encoded = body.encode("utf-8")
    # Only a rate limit is retried, and only twice. Nothing else is retried at all.
    for round_index in range(JEV_MAX_RATE_LIMIT_RETRIES + 1):
        remaining = max(deadline_ms - now_ms(), 1)
        outcome = _attempt(encoded, remaining, api_key, transport)
        if not isinstance(outcome, _RateLimited):
            return outcome
        if round_index == JEV_MAX_RATE_LIMIT_RETRIES:
            break
        # Wait only when the wait AND a useful attempt after it both still fit the deadline.
        if deadline_ms - now_ms() - outcome.retry_after_ms < JEV_MIN_ATTEMPT_MS:
            break
        sleep(outcome.retry_after_ms / 1000)
    return _Failed("rate_limited")


# --- verdicts ------------------------------------------------------------------------------------


def _value_kind(question_type: str) -> str:
    return "noul-probability" if question_type == "noul" else "confidence"


def _rounded_fraction(value: float) -> float:
    """Binary floats: 0.6 - 0.3 is 0.30000000000000004, and a margin is read by a human."""
    return min(max(_js_round(value * 1_000_000) / 1_000_000, 0.0), 1.0)


def _distribution_of(answer: Mapping[str, object]) -> Mapping[str, float] | None:
    raw = answer.get("probabilities")
    if not isinstance(raw, dict) or len(raw) == 0:
        return None
    if not all(_is_fraction(value) for value in raw.values()):
        return None
    return MappingProxyType(dict(raw))


def _margin_of(distribution: Mapping[str, float]) -> float:
    ordered = sorted(distribution.values(), reverse=True)
    top = ordered[0] if ordered else 0
    second = ordered[1] if len(ordered) > 1 else 0
    return _rounded_fraction(top - second)


@dataclass(frozen=True)
class _Detail:
    """What an answer said beyond its one number. Every field is None when it is not known."""

    leaning: str | None = None
    probabilities: Mapping[str, float] | None = None
    margin: float | None = None


_NO_DETAIL = _Detail()


def _detail_for(leaning: str, distribution: Mapping[str, float] | None) -> _Detail:
    if distribution is None:
        return _Detail(leaning=leaning)
    return _Detail(leaning, distribution, _margin_of(distribution))


def _undecided(
    question_type: str, threshold: float, reason: str, value: float = 0, detail: _Detail = _NO_DETAIL
) -> Verdict:
    return Verdict(
        type=question_type,
        verdict=JEV_UNDECIDED,
        value=value,
        value_kind=_value_kind(question_type),
        threshold=threshold,
        reason=reason,
        leaning=detail.leaning,
        probabilities=detail.probabilities,
        margin=detail.margin,
    )


def _decided(question_type: str, verdict: str, value: float, threshold: float, detail: _Detail = _NO_DETAIL) -> Verdict:
    return Verdict(
        type=question_type,
        verdict=verdict,
        value=value,
        value_kind=_value_kind(question_type),
        threshold=threshold,
        leaning=detail.leaning,
        probabilities=detail.probabilities,
        margin=detail.margin,
    )


def _noul_verdict(answer: Mapping[str, object], threshold: float) -> Verdict:
    probability = answer.get("noul")
    if not _is_fraction(probability):
        return _undecided("noul", threshold, "response_schema_mismatch")
    assert isinstance(probability, int | float)
    # A noul carries one probability, so its distribution is that number and its mirror.
    detail = _Detail(
        leaning="yes" if probability >= 0.5 else "no",
        margin=_rounded_fraction(abs(probability * 2 - 1)),
    )
    if probability >= threshold:
        return _decided("noul", "yes", probability, threshold, detail)
    # The mirror of the yes test on the probability of no; `p <= 1 - t` misreads 0.2 at 0.8.
    if 1 - probability >= threshold:
        return _decided("noul", "no", probability, threshold, detail)
    return _undecided("noul", threshold, "below_threshold", probability, detail)


def _choice_verdict(answer: Mapping[str, object], question: Mapping[str, object], threshold: float) -> Verdict:
    criteria = question.get("criteria")
    options = list(criteria.keys()) if isinstance(criteria, Mapping) else []
    chosen, confidence = answer.get("choice"), answer.get("confidence")
    if not isinstance(chosen, str) or chosen not in options or not _is_fraction(confidence):
        return _undecided("choice", threshold, "response_schema_mismatch")
    assert isinstance(confidence, int | float)
    detail = _detail_for(chosen, _distribution_of(answer))
    if confidence < threshold:
        return _undecided("choice", threshold, "below_threshold", confidence, detail)
    return _decided("choice", chosen, confidence, threshold, detail)


def _score_verdict(answer: Mapping[str, object], threshold: float) -> Verdict:
    score, confidence = answer.get("score"), answer.get("confidence")
    if not _is_number(score) or not _is_fraction(confidence):
        return _undecided("score", threshold, "response_schema_mismatch")
    assert isinstance(score, int | float) and isinstance(confidence, int | float)
    written = js_number(score)
    detail = _detail_for(written, _distribution_of(answer))
    if confidence < threshold:
        return _undecided("score", threshold, "below_threshold", confidence, detail)
    return _decided("score", written, confidence, threshold, detail)


def _verdict_for(question: Mapping[str, object], threshold: float, answer: object) -> Verdict:
    question_type = str(question.get("type"))
    if not isinstance(answer, dict):
        return _undecided(question_type, threshold, "answer_missing")
    if answer.get("type") != question_type:
        return _undecided(question_type, threshold, "answer_type_mismatch")
    if question_type == "noul":
        return _noul_verdict(answer, threshold)
    if question_type == "choice":
        return _choice_verdict(answer, question, threshold)
    if question_type == "score":
        return _score_verdict(answer, threshold)
    return _undecided(question_type, threshold, "response_schema_mismatch")


def _all_undecided(request: MaskedRequest, reason: str) -> Mapping[str, Verdict]:
    return MappingProxyType(
        {
            question_id: _undecided(str(question.get("type")), request.thresholds[question_id], reason)
            for question_id, question in request.questions.items()
        }
    )


# --- public entry points -------------------------------------------------------------------------


def judge(
    state: object,
    questions: Questions,
    *,
    api_key: str | None = None,
    timeout_s: float = JEV_DEFAULT_TIMEOUT_S,
    thresholds: Mapping[str, float] | None = None,
    transport: httpx.BaseTransport | None = None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> JudgeResult:
    """Ask every question about one state in one request and return one verdict per question id.

    `api_key` defaults to `$TYPESAFE_API_KEY`; without one every verdict is undecided and no request
    is sent. `now` returns seconds and `sleep` takes seconds; both exist so tests can move a clock.
    Raises `JevUsageError` for a malformed request; every other failure is `undecided`.

    BAZAAR_DECIDER=llm sends the same masked request to Claude instead (`bazaar_agent.jev.decider`),
    with no TypeSafe key needed, and returns the same verdicts under the same bars.
    """
    request = mask_request(state, questions, thresholds, _timeout_ms(timeout_s))
    if decider() == "llm":
        return _llm_result(request)
    key = (os.environ.get(JEV_API_KEY_VARIABLE, "") if api_key is None else api_key).strip()
    if not key:
        return JudgeResult("", 0, _all_undecided(request, "typesafe_api_key_missing"))

    def now_ms() -> float:
        return now() * 1000

    started = now_ms()
    outcome = _send(request, key, transport, now_ms, sleep, started + request.timeout_ms)
    latency_ms = _js_round(now_ms() - started)
    if isinstance(outcome, _Failed):
        return JudgeResult("", latency_ms, _all_undecided(request, outcome.reason))
    verdicts = {
        question_id: _verdict_for(question, request.thresholds[question_id], outcome.answers.get(question_id))
        for question_id, question in request.questions.items()
    }
    return JudgeResult(outcome.model, latency_ms, MappingProxyType(verdicts))


def _llm_result(request: MaskedRequest) -> JudgeResult:
    # Imported here: the llm package imports telemetry, which imports this package.
    from bazaar_agent.llm.decider import process_decider

    outcome = process_decider().decide(request.questions, request.thresholds, request.written_state())
    if outcome.answers is None:
        return JudgeResult(
            outcome.model, outcome.latency_ms, _all_undecided(request, outcome.reason or "llm_unavailable")
        )
    verdicts = {
        question_id: _verdict_for(
            question, request.thresholds[question_id], _own_options(question, outcome.answers.get(question_id))
        )
        for question_id, question in request.questions.items()
    }
    return JudgeResult(outcome.model, outcome.latency_ms, MappingProxyType(verdicts))


def _own_options(question: Mapping[str, object], answer: object) -> object:
    """An LLM names its probability keys itself: keep only the question's choice options, never free text
    that would reach logs, spans and a terminal (a `[/red]` key once crashed `bazaar llm`)."""
    if not isinstance(answer, Mapping) or "probabilities" not in answer:
        return answer
    criteria = question.get("criteria")
    raw = answer["probabilities"]
    kept = (
        {option: p for option, p in raw.items() if option in criteria}
        if question.get("type") == "choice" and isinstance(criteria, Mapping) and isinstance(raw, Mapping)
        else {}
    )
    trimmed = {key: value for key, value in answer.items() if key != "probabilities"}
    return {**trimmed, "probabilities": kept} if kept else trimmed


def questions_from_text(text: str, source: str = "questions") -> dict[str, dict[str, object]]:
    """A pack holds `{"questions": {...}}`; a bare question map is accepted too."""
    try:
        parsed = json.loads(text, parse_constant=_reject_constant)
    except ValueError as error:
        raise JevUsageError(f"{source} is not JSON") from error
    if not isinstance(parsed, dict):
        raise JevUsageError(f"{source} must hold a question map")
    found = parsed.get("questions")
    questions = parsed if found is None else found
    if not isinstance(questions, dict):
        raise JevUsageError(f"{source} must hold a question map")
    return questions


def load_questions(path: str | Path) -> dict[str, dict[str, object]]:
    """The question map in a pack file such as `questions/negotiation.json`."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise JevUsageError(f"cannot read {path}") from error
    return questions_from_text(text, str(path))


def state_from_text(text: str) -> object:
    """JSON (an object or an array) is judged as structure, anything else as text."""
    try:
        parsed = json.loads(text, parse_constant=_reject_constant)
    except ValueError:
        return text
    return parsed if isinstance(parsed, dict | list) else text
