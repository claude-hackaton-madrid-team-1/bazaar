"""`bazaar evals report`: the scorecard, the ladder, the worst cases, Jev calibration, official numbers.

The official numbers are the organisers' own, from the newest `/me` snapshot (`score`): they sit next
to our evals so a drift between the two shows up (practice duels, for instance, never score).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg
from rich.console import Group
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from bazaar_agent.evals import inputs, store
from bazaar_agent.jev.log import QuestionTally, report_table

OFFICIAL_KEYS = (
    "duel_points",
    "ladder_points",
    "neg_points",
    "negotiating",
    "bench_efficiency",
    "bench_points",
    "mm_points",
    "market",
)


@dataclass(frozen=True)
class Report:
    scorecard: list[dict[str, Any]]
    ladder: list[dict[str, Any]]
    worst: list[dict[str, Any]]
    calibration: tuple[QuestionTally, ...]
    official: dict[str, Any]
    annotations: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return _plain(
            {
                "scorecard": self.scorecard,
                "ladder": self.ladder,
                "worst": self.worst,
                "jev_calibration": [t.to_dict() for t in self.calibration],
                "official": self.official,
                "annotations": self.annotations,
            }
        )


def _plain(value: Any) -> Any:
    """JSON-ready: Decimal → float, nested containers walked."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def jev_tally(conn: psycopg.Connection) -> tuple[QuestionTally, ...]:
    """Calls and decided from the decisions table, right/wrong/unknown from the settled outcomes."""
    calls = inputs.jev_calls(conn)
    settled = {row["question"]: row for row in store.calibration(conn)}
    questions = sorted(set(calls) | set(settled), key=lambda q: (q.casefold(), q))
    out = []
    for q in questions:
        n, decided = calls.get(q, (0, 0))
        s = settled.get(q, {})
        out.append(
            QuestionTally(
                question=q,
                calls=n,
                decided=decided,
                undecided=n - decided,
                right=int(s.get("n_right") or 0),
                wrong=int(s.get("n_wrong") or 0),
                unknown=int(s.get("n_unknown") or 0),
            )
        )
    return tuple(out)


def build(conn: psycopg.Connection) -> Report:
    latest = inputs.latest_score(conn)
    official: dict[str, Any] = {}
    if latest is not None:
        tick, score = latest
        official = {"tick": tick, **{k: score.get(k) for k in OFFICIAL_KEYS}}
    return Report(
        scorecard=store.scorecard(conn),
        ladder=store.ladder(conn),
        worst=store.worst(conn),
        calibration=jev_tally(conn),
        official=official,
        annotations=store.annotation_status(conn),
    )


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, Decimal | float):
        return f"{float(value):.3f}".rstrip("0").rstrip(".") if float(value) != 0 else "0"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _table(title: str, rows: list[dict[str, Any]], columns: tuple[str, ...]) -> Table:
    table = Table(title=title, title_justify="left", show_lines=False)
    for c in columns:
        table.add_column(c, overflow="fold")
    for row in rows:
        table.add_row(*(escape(_fmt(row.get(c))) for c in columns))
    return table


def render(report: Report) -> Group:
    parts: list[Any] = [
        _table(
            "Scorecard (outcomes per target and game day)",
            report.scorecard,
            (
                "target",
                "day",
                "outcomes",
                "scored",
                "mean_score",
                "worst_score",
                "good",
                "ok",
                "bad",
                "surplus",
                "worst",
            ),
        )
        if report.scorecard
        else Text("Scorecard: no outcomes yet (run `bazaar evals run`)"),
        _table(
            "Dealer ladder (best three shares per level; a missing deal counts zero)",
            report.ladder,
            ("level", "dealers", "threads", "deals", "best3_share", "best3"),
        )
        if report.ladder
        else Text("Dealer ladder: no dealer threads of ours yet"),
        _table(
            "Worst 5 per target",
            report.worst,
            ("target", "subject", "score", "label", "day", "realized_surplus", "explanation"),
        ),
        Text("Jev calibration (calls/decided from decisions; right/wrong once settled)", style="bold"),
        Text(report_table(report.calibration).rstrip("\n")),
        _table(
            "Official numbers (newest /me snapshot)",
            [{"key": k, "value": v} for k, v in report.official.items()],
            ("key", "value"),
        ),
        Text(
            "Phoenix annotations: "
            + ", ".join(f"{k} {v}" for k, v in report.annotations.items())
            + " (outcomes on traces / waiting for a span / no trace to attach to)"
        ),
    ]
    return Group(*parts)
