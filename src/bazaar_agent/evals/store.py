"""Outcomes in Postgres: idempotent upserts keyed by (target, subject), and what the report reads.

An unchanged outcome is not touched (no write, no new annotation). A changed one (a better floor was
learned, a duel's price arrived) is rewritten and queued to be annotated in Phoenix again.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from bazaar_agent.decisions import scrubbed
from bazaar_agent.evals.model import TARGETS, Outcome

MAX_ANNOTATION_TRIES = 3  # a span lands in Phoenix when its trace ends; after this many misses, stop looking
NO_SPAN = MAX_ANNOTATION_TRIES  # tries value for an outcome that has no trace to attach to (Market Test)

_UPSERT = """
insert into outcomes (target, subject, decision_id, score, label, explanation, details, day, recorded_tick,
                      realized_surplus, ladder_share, jev_question, jev_verdict, jev_right, scored_at,
                      annotation_tries)
values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, now(), %s)
on conflict (target, subject) do update set
  decision_id = excluded.decision_id, score = excluded.score, label = excluded.label,
  explanation = excluded.explanation, details = excluded.details, day = excluded.day,
  recorded_tick = excluded.recorded_tick, realized_surplus = excluded.realized_surplus,
  ladder_share = excluded.ladder_share, jev_question = excluded.jev_question,
  jev_verdict = excluded.jev_verdict, jev_right = excluded.jev_right, scored_at = now(),
  annotated_at = null, annotation_tries = excluded.annotation_tries
where (outcomes.score, outcomes.label, outcomes.explanation, outcomes.details, outcomes.day,
       outcomes.recorded_tick, outcomes.jev_right, outcomes.decision_id)
  is distinct from (excluded.score, excluded.label, excluded.explanation, excluded.details, excluded.day,
       excluded.recorded_tick, excluded.jev_right, excluded.decision_id)
"""


def _row(o: Outcome) -> tuple[Any, ...]:
    jev = o.jev
    tries = NO_SPAN if o.target == "market_test" else 0
    return (
        o.target,
        o.subject,
        o.decision_id if o.decision_id is not None and o.decision_id > 0 else None,
        o.score,
        o.label,
        scrubbed(o.explanation),
        json.dumps(scrubbed(dict(o.details)), default=str, ensure_ascii=False, sort_keys=True),
        o.day,
        o.tick,
        o.surplus,
        o.ladder_share,
        jev.question if jev else None,
        jev.verdict if jev else None,
        jev.right if jev else None,
        tries,
    )


def upsert_outcomes(conn: psycopg.Connection, outcomes: Iterable[Outcome]) -> int:
    """Write outcomes; returns how many rows were new or changed (0 on an idempotent re-run)."""
    rows = sorted((_row(o) for o in outcomes), key=lambda r: (r[0], r[1]))
    changed = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(_UPSERT, row)
            changed += max(cur.rowcount, 0)
    conn.commit()
    return changed


@dataclass(frozen=True)
class Pending:
    """An outcome to attach to its Phoenix trace."""

    target: str
    subject: str
    score: float | None
    label: str
    explanation: str
    details: Mapping[str, Any]
    day: str | None
    tick: int | None


def pending_annotations(conn: psycopg.Connection, targets: Collection[str] = TARGETS) -> list[Pending]:
    """Outcomes of `targets` still waiting for their Phoenix span (each agent annotates only its own)."""
    rows = conn.execute(
        "select target, subject, score, label, explanation, details, day, recorded_tick from outcomes "
        "where target = any(%s) and annotated_at is null and coalesce(annotation_tries, 0) < %s "
        "order by target, subject",
        (list(targets), MAX_ANNOTATION_TRIES),
    ).fetchall()
    conn.commit()
    return [
        Pending(t, s, None if sc is None else float(sc), lb or "ok", ex or "", d or {}, day, tick)
        for t, s, sc, lb, ex, d, day, tick in rows
    ]


def mark_annotated(conn: psycopg.Connection, target: str, subject: str, trace_id: str, span_id: str) -> None:
    conn.execute(
        "update outcomes set annotated_at = now(), trace_id = %s, span_id = %s where target = %s and subject = %s",
        (trace_id, span_id, target, subject),
    )
    conn.commit()


def mark_missed(conn: psycopg.Connection, target: str, subject: str, *, never: bool = False) -> None:
    """No span found this time (or `never`: this target has no trace to attach to)."""
    conn.execute(
        "update outcomes set annotation_tries = case when %s then %s else coalesce(annotation_tries, 0) + 1 end "
        "where target = %s and subject = %s",
        (never, MAX_ANNOTATION_TRIES, target, subject),
    )
    conn.commit()


# ---------------------------------------------------------------- what the report reads


def _dicts(conn: psycopg.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(query, params)  # type: ignore[arg-type]  # constant SQL in this module
    names = [c.name for c in cur.description or []]
    rows = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    conn.commit()
    return rows


def scorecard(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return _dicts(conn, "select * from eval_scorecard order by target, day")


def ladder(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return _dicts(conn, "select * from eval_ladder order by level")


def worst(conn: psycopg.Connection, per_target: int = 5) -> list[dict[str, Any]]:
    return _dicts(
        conn,
        "select target, subject, score, label, day, recorded_tick, realized_surplus, explanation, span_id from ("
        "  select o.*, row_number() over (partition by target order by score asc nulls last, subject) as n"
        "  from outcomes o where target is not null) w where n <= %s order by target, n",
        (per_target,),
    )


def calibration(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return _dicts(conn, "select * from eval_jev_calibration order by question")


def annotation_status(conn: psycopg.Connection) -> dict[str, int]:
    rows = _dicts(
        conn,
        "select count(*) filter (where annotated_at is not null) as annotated, "
        "count(*) filter (where annotated_at is null and coalesce(annotation_tries, 0) < %s) as pending, "
        "count(*) filter (where annotated_at is null and coalesce(annotation_tries, 0) >= %s) as no_span "
        "from outcomes where target is not null",
        (MAX_ANNOTATION_TRIES, MAX_ANNOTATION_TRIES),
    )
    return {k: int(v or 0) for k, v in rows[0].items()} if rows else {}
