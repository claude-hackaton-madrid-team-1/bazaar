"""Port of vendor/jev-sdk/test/jev-decision-log.test.ts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from bazaar_agent.jev import (
    JEV_DECISION_LOG_DIRECTORY,
    JEV_OUTCOMES,
    JevUsageError,
    JudgeResult,
    QuestionTally,
    Verdict,
    append_line,
    decision_line,
    log_tally,
    outcome_line,
    read_log,
    report_table,
)


def noul(verdict: str, threshold: float, **detail: Any) -> Verdict:
    return Verdict(
        type="noul",
        verdict=verdict,
        value=0.72,
        value_kind="noul-probability",
        threshold=threshold,
        **detail,
    )


def result_for(verdicts: dict[str, Verdict]) -> JudgeResult:
    return JudgeResult(model="jev-1.13.0", latency_ms=640, verdicts=verdicts)


@pytest.fixture(scope="module")
def stakes_line() -> dict[str, Any]:
    line = decision_line(
        {"note": "operator.fixture@example.com asked for a second round"},
        {
            "cheap": {"type": "noul", "instructions": "Is the line an error?", "stakes": "passive"},
            "dear": {"type": "noul", "instructions": "Does it touch money?", "stakes": "critical"},
        },
        result_for(
            {
                "cheap": noul("yes", 0.6, leaning="yes", margin=0.44),
                "dear": noul("undecided", 0.9, reason="below_threshold", leaning="yes", margin=0.44),
            }
        ),
        "2026-09-22T12:00:00.000Z",
    )
    parsed: dict[str, Any] = json.loads(line)
    return parsed


# --- a decision line -----------------------------------------------------------------------------


def test_a_decision_line_says_which_kind_it_is(stakes_line: dict[str, Any]) -> None:
    assert stakes_line["kind"] == "decision"
    assert list(stakes_line) == [
        "kind",
        "at",
        "model",
        "latencyMs",
        "stateDigest",
        "callDigest",
        "stateCharacters",
        "questions",
        "thresholds",
        "stakes",
        "verdicts",
    ]


def test_records_the_stakes_each_question_named_beside_its_bar(stakes_line: dict[str, Any]) -> None:
    assert stakes_line["stakes"] == {"cheap": "passive", "dear": "critical"}
    assert stakes_line["thresholds"] == {"cheap": 0.6, "dear": 0.9}


def test_carries_the_leaning_and_margin_of_an_undecided_row(stakes_line: dict[str, Any]) -> None:
    dear = stakes_line["verdicts"]["dear"]
    assert (dear["verdict"], dear["leaning"], dear["margin"], dear["valueKind"]) == (
        "undecided",
        "yes",
        0.44,
        "noul-probability",
    )


def test_holds_the_masked_digest_and_never_the_state(stakes_line: dict[str, Any]) -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", stakes_line["stateDigest"])
    assert "operator.fixture@example.com" not in json.dumps(stakes_line)


def test_a_question_without_stakes_writes_no_stakes_key() -> None:
    line = json.loads(
        decision_line(
            "state",
            {"q": {"type": "noul", "instructions": "?!"}},
            result_for({"q": noul("yes", 0.8)}),
            "2026-09-22T12:00:00.000Z",
        )
    )
    assert "stakes" not in line


# --- an outcome line -----------------------------------------------------------------------------


def test_an_outcome_names_the_call_the_outcome_and_the_masked_note() -> None:
    line = json.loads(
        outcome_line(
            "a" * 64,
            "wrong",
            "2026-09-22T13:00:00.000Z",
            note="round 6 did not move the halt rate; mail operator.fixture@example.com",
        )
    )
    assert line == {
        "kind": "outcome",
        "at": "2026-09-22T13:00:00.000Z",
        "stateDigest": "a" * 64,
        "outcome": "wrong",
        "note": "round 6 did not move the halt rate; mail [redacted]",
    }


def test_an_outcome_can_name_one_question() -> None:
    line = json.loads(outcome_line("b" * 64, "right", "2026-09-22T13:00:00.000Z", question="worker_profile"))
    assert line["question"] == "worker_profile"
    assert "note" not in line


def test_the_outcome_words_are_a_closed_vocabulary() -> None:
    assert JEV_OUTCOMES == ("right", "wrong", "unknown")
    with pytest.raises(JevUsageError):
        outcome_line("c" * 64, "maybe", "2026-09-22T13:00:00.000Z")


# --- reading the log back ------------------------------------------------------------------------


def test_reads_every_day_oldest_first_and_skips_a_bad_line(tmp_path: Path) -> None:
    first = decision_line(
        "the first call",
        {"worker_profile": {"type": "noul", "instructions": "Is it one writer?"}},
        result_for({"worker_profile": noul("yes", 0.8)}),
        "2026-09-21T09:00:00.000Z",
    )
    append_line(tmp_path, "2026-09-22", outcome_line("c" * 64, "right", "2026-09-22T09:00:00.000Z"))
    append_line(tmp_path, "2026-09-21", first)
    append_line(tmp_path, "2026-09-21", "not json at all")
    assert [entry["kind"] for entry in read_log(tmp_path)] == ["decision", "outcome"]


def test_a_directory_that_was_never_written_reads_as_no_entries(tmp_path: Path) -> None:
    assert read_log(tmp_path / "absent") == ()


def test_a_line_from_before_the_kind_field_existed_is_a_decision(tmp_path: Path) -> None:
    append_line(tmp_path, "2026-09-20", json.dumps({"at": "x", "verdicts": {}}))
    assert read_log(tmp_path)[0]["kind"] == "decision"


def test_the_log_directory_is_the_one_git_ignores() -> None:
    assert JEV_DECISION_LOG_DIRECTORY == ".local/jev-decisions"


# --- the tally a report prints -------------------------------------------------------------------


def logged_decision(at: str, state_digest: str, verdict: str, question_id: str = "worker_profile") -> str:
    return json.dumps(
        {
            "kind": "decision",
            "at": at,
            "stateDigest": state_digest,
            "questions": {question_id: {"type": "noul", "instructions": "?"}},
            "verdicts": {question_id: {"type": "noul", "verdict": verdict}},
        }
    )


@pytest.fixture
def tally(tmp_path: Path) -> dict[str, QuestionTally]:
    digest = "d" * 64
    lines = [
        logged_decision("2026-09-22T01:00:00.000Z", digest, "yes"),
        logged_decision("2026-09-22T02:00:00.000Z", "e" * 64, "undecided"),
        logged_decision("2026-09-22T03:00:00.000Z", "f" * 64, "no", "task_size"),
        outcome_line(digest, "right", "2026-09-22T04:00:00.000Z"),
        outcome_line("f" * 64, "wrong", "2026-09-22T05:00:00.000Z", question="task_size"),
    ]
    (tmp_path / "2026-09-22.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = log_tally(read_log(tmp_path))
    assert [row.question for row in rows] == ["task_size", "worker_profile"]
    return {row.question: row for row in rows}


def test_counts_calls_decided_and_undecided_per_question(tally: dict[str, QuestionTally]) -> None:
    profile = tally["worker_profile"]
    assert (profile.calls, profile.decided, profile.undecided) == (2, 1, 1)


def test_an_outcome_on_a_call_counts_for_every_question_it_asked(tally: dict[str, QuestionTally]) -> None:
    assert tally["worker_profile"].right == 1


def test_an_outcome_naming_one_question_counts_only_for_it(tally: dict[str, QuestionTally]) -> None:
    task = tally["task_size"]
    assert (task.calls, task.decided, task.wrong, task.right) == (1, 1, 1, 0)
    assert tally["worker_profile"].wrong == 0


def test_an_outcome_for_a_call_not_in_the_log_counts_for_nothing() -> None:
    orphan = json.loads(outcome_line("9" * 64, "right", "2026-09-22T06:00Z"))
    assert log_tally([orphan]) == ()


def test_the_report_table_lines_up(tally: dict[str, QuestionTally]) -> None:
    table = report_table(list(tally.values()))
    assert table.splitlines()[0] == "question      " + "".join(
        name.rjust(10) for name in ("calls", "decided", "undecided", "right", "wrong", "unknown")
    )
    assert report_table([]) == "no decisions in the log yet\n"


# --- one state, two calls ------------------------------------------------------------------------

STATE = {"unit": "rename the engine's refusal codes"}


def call(question_id: str, at: str) -> str:
    questions = {question_id: {"type": "noul", "instructions": f"Does {question_id} fit?"}}
    return decision_line(STATE, questions, result_for({question_id: noul("yes", 0.6)}), at)


FIRST = call("gate::acts_on_repository", "2026-09-22T01:00:00.000Z")
SECOND = call("fits::cpp-pro", "2026-09-22T02:00:00.000Z")


def tally_of(tmp_path: Path, *lines: str) -> dict[str, QuestionTally]:
    (tmp_path / "2026-09-22.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {row.question: row for row in log_tally(read_log(tmp_path))}


def test_each_call_carries_its_own_digest_beside_the_states() -> None:
    one, two = json.loads(FIRST), json.loads(SECOND)
    assert one["stateDigest"] == two["stateDigest"]
    assert one["callDigest"] != two["callDigest"]
    assert re.fullmatch(r"[0-9a-f]{64}", one["callDigest"])


def test_an_outcome_on_one_call_digest_counts_for_that_call_only(tmp_path: Path) -> None:
    rows = tally_of(tmp_path, FIRST, SECOND, outcome_line(json.loads(SECOND)["callDigest"], "right", "t"))
    assert rows["fits::cpp-pro"].right == 1
    assert rows["gate::acts_on_repository"].right == 0


def test_an_outcome_on_a_shared_state_digest_reaches_neither_call(tmp_path: Path) -> None:
    rows = tally_of(tmp_path, FIRST, SECOND, outcome_line(json.loads(FIRST)["stateDigest"], "right", "t"))
    assert rows["fits::cpp-pro"].right == 0
    assert rows["gate::acts_on_repository"].right == 0


def test_a_state_digest_that_covers_one_call_still_counts(tmp_path: Path) -> None:
    rows = tally_of(tmp_path, FIRST, outcome_line(json.loads(FIRST)["stateDigest"], "right", "t"))
    assert rows["gate::acts_on_repository"].right == 1


def test_a_named_question_still_counts_under_the_shared_state_digest(tmp_path: Path) -> None:
    digest = json.loads(FIRST)["stateDigest"]
    rows = tally_of(tmp_path, FIRST, SECOND, outcome_line(digest, "wrong", "t", question="fits::cpp-pro"))
    assert rows["fits::cpp-pro"].wrong == 1
    assert rows["gate::acts_on_repository"].wrong == 0
