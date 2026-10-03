"""The masked Jev decision log, one JSONL file per UTC day.

Python port of `vendor/jev-sdk/src/decision-log.ts`, writing the same line format so the upstream
`jev-report` can read our logs. Two kinds of line share a file. A `decision` line is one call: the
masked state digest, the masked questions, the bar each one had to clear and the verdict it gave. An
`outcome` line is what happened afterwards, keyed on the same digest.

The raw state never reaches this file. Only the digest of the masked text does.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bazaar_agent.jev.judge import JEV_UNDECIDED, JevUsageError, JudgeResult, Questions, mask_request
from bazaar_agent.jev.mask import JS_WHITESPACE, js_json_dumps, mask_text, masked_digest, utf16_length

# Consumer-relative default; `.local/` is gitignored.
JEV_DECISION_LOG_DIRECTORY = ".local/jev-decisions"
# How a decision turned out, once the move it informed settled.
JEV_OUTCOMES = ("right", "wrong", "unknown")

LogEntry = Mapping[str, object]


def iso_now() -> str:
    """`new Date().toISOString()`: UTC, millisecond precision, `Z` suffix."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log_date(at: str) -> str:
    """The day a line belongs to, which is the file it is appended to."""
    return at[:10]


def call_digest(state_digest: str, question_ids: Iterable[str]) -> str:
    """One call's identity: the state it judged AND the questions it asked."""
    # UTF-16 order, which is what the upstream `Array.prototype.sort` compares.
    ordered = sorted(question_ids, key=lambda question_id: question_id.encode("utf-16-be", "surrogatepass"))
    return masked_digest(f"{state_digest}\n" + "\n".join(ordered))


def decision_line(
    state: object,
    questions: Questions,
    result: JudgeResult,
    at: str,
    *,
    thresholds: Mapping[str, float] | None = None,
) -> str:
    """One call: the masked state digest, the masked questions, the bars and the verdicts."""
    masked = mask_request(state, questions, thresholds)
    written = masked.written_state()
    state_digest = masked_digest(written)
    stakes = {question_id: question["stakes"] for question_id, question in questions.items() if "stakes" in question}
    line: dict[str, object] = {
        "kind": "decision",
        "at": at,
        "model": result.model,
        # BAZAAR_DECIDER=llm only: a Jev line keeps the upstream format, key for key.
        **({"decider": "llm"} if result.model.startswith("llm:") else {}),
        "latencyMs": result.latency_ms,
        "stateDigest": state_digest,
        "callDigest": call_digest(state_digest, masked.questions.keys()),
        "stateCharacters": utf16_length(written),
        "questions": masked.questions,
        "thresholds": masked.thresholds,
        **({"stakes": stakes} if stakes else {}),
        "verdicts": {question_id: verdict.to_dict() for question_id, verdict in result.verdicts.items()},
    }
    return js_json_dumps(line)


def outcome_line(
    state_digest: str,
    outcome: str,
    at: str,
    *,
    question: str | None = None,
    note: str | None = None,
) -> str:
    """What a call turned out to be. The note is masked, exactly like a state."""
    if outcome not in JEV_OUTCOMES:
        raise JevUsageError(f"outcome must be one of {', '.join(JEV_OUTCOMES)}")
    line: dict[str, object] = {
        "kind": "outcome",
        "at": at,
        "stateDigest": state_digest,
        "outcome": outcome,
        **({} if question is None else {"question": question}),
        **({} if note is None else {"note": mask_text(note)}),
    }
    return js_json_dumps(line)


def append_line(directory: str | Path, date: str, line: str) -> Path:
    """Append one line to `<directory>/<date>.jsonl` and return that file's path."""
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{date}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")
    return path


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} is not JSON")


def _entry_from(parsed: dict[str, object]) -> LogEntry | None:
    """A line written before `kind` existed is a decision, because outcomes came later."""
    if parsed.get("kind") != "outcome":
        return {**parsed, "kind": "decision"} if isinstance(parsed.get("verdicts"), dict) else None
    if not isinstance(parsed.get("stateDigest"), str) or parsed.get("outcome") not in JEV_OUTCOMES:
        return None
    return parsed


def _entries_in(path: Path) -> list[LogEntry]:
    entries: list[LogEntry] = []
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        if not line.strip(JS_WHITESPACE):
            continue
        try:
            parsed = json.loads(line, parse_constant=_reject_constant)
        except ValueError:
            continue  # a truncated or hand-edited line loses itself, never the rest of the day
        entry = _entry_from(parsed) if isinstance(parsed, dict) else None
        if entry is not None:
            entries.append(entry)
    return entries


def read_log(directory: str | Path) -> tuple[LogEntry, ...]:
    """Every entry in the log, oldest day first. A line it cannot read is skipped, not raised."""
    try:
        names = sorted(path.name for path in Path(directory).iterdir() if path.name.endswith(".jsonl"))
    except OSError:
        return ()
    return tuple(entry for name in names for entry in _entries_in(Path(directory) / name))


@dataclass(frozen=True)
class QuestionTally:
    """What a report prints for one question id, so a bar is tuned on counted results."""

    question: str
    calls: int = 0
    decided: int = 0
    undecided: int = 0
    right: int = 0
    wrong: int = 0
    unknown: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "calls": self.calls,
            "decided": self.decided,
            "undecided": self.undecided,
            "right": self.right,
            "wrong": self.wrong,
            "unknown": self.unknown,
        }


def _calls_by_digest(decisions: Sequence[LogEntry]) -> dict[str, list[frozenset[str]]]:
    """Every logged call's question ids, reachable by its call digest and by its state digest."""
    calls: dict[str, list[frozenset[str]]] = {}
    for entry in decisions:
        verdicts = entry.get("verdicts")
        ids = frozenset(verdicts.keys()) if isinstance(verdicts, Mapping) else frozenset()
        call, state = entry.get("callDigest"), entry.get("stateDigest")
        # A line written before `callDigest` existed is reachable by its state digest alone.
        for digest in (call, state if state != call else None):
            if isinstance(digest, str):
                calls[digest] = [*calls.get(digest, []), ids]
    return calls


def _ids_for(entry: LogEntry, calls: Mapping[str, Sequence[frozenset[str]]]) -> frozenset[str]:
    """Which question ids one outcome speaks for: the named one, or every id its call asked."""
    matched = calls.get(str(entry.get("stateDigest")), [])
    if not matched:
        return frozenset()
    if "question" in entry:
        named = entry.get("question")
        found = isinstance(named, str) and any(named in ids for ids in matched)
        return frozenset({named}) if found and isinstance(named, str) else frozenset()
    # A digest that covers two calls with different questions names no single call.
    return matched[0] if all(ids == matched[0] for ids in matched) else frozenset()


def _decision_counts(decisions: Sequence[LogEntry]) -> dict[str, dict[str, int]]:
    counters: dict[str, dict[str, int]] = {}
    for entry in decisions:
        verdicts = entry.get("verdicts")
        for question_id, verdict in verdicts.items() if isinstance(verdicts, Mapping) else ():
            row = counters.get(question_id, {"calls": 0, "decided": 0, "undecided": 0})
            undecided = isinstance(verdict, Mapping) and verdict.get("verdict") == JEV_UNDECIDED
            counters[question_id] = {
                "calls": row["calls"] + 1,
                "decided": row["decided"] + (0 if undecided else 1),
                "undecided": row["undecided"] + (1 if undecided else 0),
            }
    return counters


def log_tally(entries: Iterable[LogEntry]) -> tuple[QuestionTally, ...]:
    """Calls, decided, undecided and recorded outcomes per question id, ordered by id.

    An outcome with no `question` counts once for every question its call asked. An outcome for a
    call that is not in the log counts for nothing, and so does one whose digest covers two calls
    that asked different questions.
    """
    listed = list(entries)
    decisions = [entry for entry in listed if entry.get("kind") == "decision"]
    counters = _decision_counts(decisions)
    calls = _calls_by_digest(decisions)
    outcomes: dict[str, dict[str, int]] = {question_id: {} for question_id in counters}
    for entry in listed:
        if entry.get("kind") != "outcome":
            continue
        outcome = str(entry.get("outcome"))
        for question_id in _ids_for(entry, calls):
            if question_id in outcomes:
                tallied = outcomes[question_id]
                outcomes[question_id] = {**tallied, outcome: tallied.get(outcome, 0) + 1}
    # Upstream orders with `localeCompare`; casefold-then-codepoint matches it for snake_case ids.
    ordered = sorted(counters, key=lambda question_id: (question_id.casefold(), question_id))
    return tuple(
        QuestionTally(
            question=question_id,
            calls=counters[question_id]["calls"],
            decided=counters[question_id]["decided"],
            undecided=counters[question_id]["undecided"],
            right=outcomes[question_id].get("right", 0),
            wrong=outcomes[question_id].get("wrong", 0),
            unknown=outcomes[question_id].get("unknown", 0),
        )
        for question_id in ordered
    )


_REPORT_COLUMNS = ("calls", "decided", "undecided", "right", "wrong", "unknown")


def report_table(rows: Sequence[QuestionTally]) -> str:
    """A fixed-width table, so two runs of the report line up and a diff reads."""
    if not rows:
        return "no decisions in the log yet\n"
    width = max(8, *(len(row.question) for row in rows))
    header = "question".ljust(width) + "".join(name.rjust(10) for name in _REPORT_COLUMNS)
    body = [
        row.question.ljust(width) + "".join(str(getattr(row, name)).rjust(10) for name in _REPORT_COLUMNS)
        for row in rows
    ]
    return "\n".join([header, *body]) + "\n"


def report_json(rows: Sequence[QuestionTally]) -> str:
    """`JSON.stringify(rows, null, 2)`."""
    return json.dumps([row.to_dict() for row in rows], indent=2, ensure_ascii=False) + "\n"
