"""Jev questions as the agents' `JevFn`, with every call and its outcome in the masked decision log.

`question_fn` turns one question of a pack (`questions/duels.json`, `questions/maker.json`, ...) into
the `JevFn` the agents already take: state in, `JevAdvice` out, never an exception. Each call is a
`jev_verdict` trace event and, with a `JevJournal`, one decision line in `<data_dir>/jev-decisions/`
(the upstream log format, read by `python -m bazaar_agent.jev report`). The advice carries that line's
state digest, so the agent can write the outcome line once the duel or the listing settles.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from bazaar_agent import telemetry as tm
from bazaar_agent.agents.runtime import JevAdvice, JevFn
from bazaar_agent.jev import (
    JevUsageError,
    JudgeResult,
    append_line,
    decision_line,
    iso_now,
    judge,
    load_questions,
    log_date,
    outcome_line,
)

JOURNAL_DIRECTORY = "jev-decisions"  # under the data dir: `.local/jev-decisions` locally, the volume on Railway

Judge = Callable[..., JudgeResult]


class JevJournal:
    """Appends decision and outcome lines. A failed write is logged and dropped: it never breaks a tick."""

    def __init__(self, directory: Path, log: Callable[[str], None] = lambda message: None) -> None:
        self.directory = directory
        self._log = log
        self._lock = threading.Lock()  # duel calls run on worker threads

    def decided(
        self, state: Mapping[str, Any], questions: Mapping[str, Mapping[str, object]], result: JudgeResult
    ) -> str | None:
        """Write one decision line; return its state digest (None when it could not be written)."""
        at = iso_now()
        try:
            line = decision_line(state, questions, result, at)
            with self._lock:
                append_line(self.directory, log_date(at), line)
        except (OSError, JevUsageError) as e:
            self._log(f"jev journal: decision line not written ({type(e).__name__})")
            return None
        digest = json.loads(line).get("stateDigest")
        return digest if isinstance(digest, str) else None

    def outcome(self, digest: str, outcome: str, question: str, note: str | None = None) -> None:
        """How a decided verdict turned out: `right`, `wrong` or `unknown`, for one question of its call."""
        at = iso_now()
        try:
            line = outcome_line(digest, outcome, at, question=question, note=note)
            with self._lock:
                append_line(self.directory, log_date(at), line)
        except (OSError, JevUsageError) as e:
            self._log(f"jev journal: outcome line not written ({type(e).__name__})")


def question_fn(
    pack: Path,
    question_id: str,
    *,
    api_key: str | None,
    timeout_s: float,
    journal: JevJournal | None = None,
    judge_fn: Judge = judge,
) -> JevFn:
    """One question of a pack as a `JevFn`. A missing key, a timeout or a bad answer is `undecided`."""
    question = {question_id: load_questions(pack)[question_id]}

    def ask(state: dict[str, Any]) -> JevAdvice:
        try:
            result = judge_fn(state, question, api_key=api_key, timeout_s=timeout_s)
        except Exception as e:  # a malformed state is our bug, but it must never stop a tick
            return JevAdvice("undecided", 0.0, reason=f"jev error: {type(e).__name__}")
        tm.record_jev(result, question_id)
        verdict = result.verdicts[question_id]
        digest = journal.decided(state, question, result) if journal is not None else None
        return JevAdvice(verdict.verdict, verdict.value, verdict.probabilities, verdict.reason, digest)

    return ask
