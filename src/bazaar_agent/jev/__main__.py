"""The command line in front of `judge()`, for a human and for another agent.

    python -m bazaar_agent.jev judge --state <file or -> [--questions <file.json>] [--log]
    python -m bazaar_agent.jev report [--directory <path>] [--json]

`judge` prints one JSON object in the upstream CLI's shape and exits 0 for any verdict, `undecided`
included. Exit 2 means the command was wrong, never that the judge was unsure. The key is read from
`$TYPESAFE_API_KEY` only and is never printed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from bazaar_agent.jev.judge import (
    JEV_DEFAULT_TIMEOUT_S,
    JEV_STAKES_THRESHOLDS,
    JevUsageError,
    judge,
    load_questions,
    state_from_text,
)
from bazaar_agent.jev.log import (
    JEV_DECISION_LOG_DIRECTORY,
    append_line,
    decision_line,
    iso_now,
    log_date,
    log_tally,
    read_log,
    report_json,
    report_table,
)

DEFAULT_QUESTIONS = "questions/negotiation.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bazaar_agent.jev", description="Ask Jev typed questions; count the decision log."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ask = commands.add_parser("judge", help="ask the typed questions about one state")
    ask.add_argument("--state", required=True, help="the state to judge; - reads stdin")
    ask.add_argument("--questions", default=DEFAULT_QUESTIONS, help=f"question pack (default {DEFAULT_QUESTIONS})")
    ask.add_argument("--threshold", type=float, help="one bar for every question in the call")
    ask.add_argument("--stakes", choices=sorted(JEV_STAKES_THRESHOLDS), help="one bar for every question, by stakes")
    ask.add_argument("--timeout", type=float, default=JEV_DEFAULT_TIMEOUT_S, help="whole-call budget in seconds")
    ask.add_argument("--log", action="store_true", help=f"append one line to {JEV_DECISION_LOG_DIRECTORY}/<date>.jsonl")
    ask.add_argument("--directory", help="override the decision-log directory")

    report = commands.add_parser("report", help="count what the decision log holds, per question id")
    report.add_argument("--directory", help=f"where the log lives (default {JEV_DECISION_LOG_DIRECTORY})")
    report.add_argument("--json", action="store_true", help="print the rows as JSON instead of a table")
    return parser


def _call_thresholds(
    questions: Mapping[str, object], threshold: float | None, stakes: str | None
) -> dict[str, float] | None:
    """One bar for every question, or None so each question keeps the bar its own stakes name."""
    bar = threshold if threshold is not None else (JEV_STAKES_THRESHOLDS[stakes] if stakes else None)
    return None if bar is None else dict.fromkeys(questions, bar)


def _state_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise JevUsageError(f"cannot read {path}") from error


def _run_judge(arguments: argparse.Namespace) -> int:
    questions = load_questions(arguments.questions)
    state = state_from_text(_state_text(arguments.state))
    thresholds = _call_thresholds(questions, arguments.threshold, arguments.stakes)
    result = judge(state, questions, timeout_s=arguments.timeout, thresholds=thresholds)
    sys.stdout.write(f"{result.to_json()}\n")
    if arguments.log:
        at = iso_now()
        directory = arguments.directory or Path.cwd() / JEV_DECISION_LOG_DIRECTORY
        append_line(directory, log_date(at), decision_line(state, questions, result, at, thresholds=thresholds))
    return 0


def _run_report(arguments: argparse.Namespace) -> int:
    rows = log_tally(read_log(arguments.directory or Path.cwd() / JEV_DECISION_LOG_DIRECTORY))
    sys.stdout.write(report_json(rows) if arguments.json else report_table(rows))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return _run_judge(arguments) if arguments.command == "judge" else _run_report(arguments)
    except JevUsageError as error:
        sys.stderr.write(f"{error}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
