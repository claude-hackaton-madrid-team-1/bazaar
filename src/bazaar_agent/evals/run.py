"""One evals pass: score every settled decision from Postgres, store it, attach it to its Phoenix trace.

Targets in the order Jev chose (questions/evals.json): duels, the dealer ladder, team trades, and the
Market Test (a stub until we run a venue). Reads Postgres only: no game API call, ever.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

import httpx
import psycopg

from bazaar_agent.evals import inputs, store
from bazaar_agent.evals.dealers import learned_ranges, score_thread
from bazaar_agent.evals.duels import score_duel
from bazaar_agent.evals.market import score_market_test
from bazaar_agent.evals.model import Outcome, day_of, jev_question
from bazaar_agent.evals.phoenix import PhoenixAnnotator, annotation_payload, span_query
from bazaar_agent.evals.trades import score_trade
from bazaar_agent.intel import dealer_threads


@dataclass(frozen=True)
class RunSummary:
    team: str | None
    scored: dict[str, int]  # outcomes per target this pass
    changed: int  # rows new or changed in Postgres (0 on an idempotent re-run)
    annotated: int = 0
    annotation_ids: tuple[str, ...] = ()
    no_span: int = 0
    phoenix: str = "off"
    notes: tuple[str, ...] = field(default_factory=tuple)


def duel_outcomes(
    conn: psycopg.Connection, since_tick: int | None, warn: Callable[[str], None] = lambda message: None
) -> list[Outcome]:
    """Every finished duel. One unreadable stored payload is skipped with a warning, never the whole pass."""
    closures = inputs.duel_closures(conn)
    out = []
    for d in inputs.duels(conn, since_tick):
        try:
            found = score_duel(d, closures.get(d.get("duel", -1)))
        except (TypeError, ValueError, KeyError, AttributeError, ArithmeticError) as e:  # 1e400 → OverflowError
            warn(f"evals: duel {d.get('duel')!r} skipped, unreadable payload ({type(e).__name__})")
            continue
        if found is not None:
            out.append(found)
    return out


def dealer_outcomes(conn: psycopg.Connection, ours: str, since_tick: int | None) -> list[Outcome]:
    ranges = learned_ranges(inputs.curve_rows(conn))
    levels = inputs.dealer_levels(conn)
    threads = [t for t in dealer_threads(inputs.dealer_events(conn), ours) if t.ours]
    return [score_thread(t, ranges, levels) for t in threads if since_tick is None or (t.last_tick or 0) >= since_tick]


def trade_outcomes(conn: psycopg.Connection, ours: str, since_tick: int | None) -> list[Outcome]:
    out = []
    for s in inputs.our_settlements(conn, ours, since_tick):
        link = inputs.linked_decision(conn, s, ours)
        value = inputs.valuation(conn, s, ours)
        o = score_trade(
            s,
            ours,
            value,
            decision_id=link.id if link else None,
            jev=link.jev if link else None,
            jev_asked=jev_question(link.agent, link.kind) if link else None,
        )
        if link is not None:
            o = replace(o, details={**o.details, "decision_agent": link.agent, "decision_tick": link.tick})
        out.append(o)
    return out


def market_outcomes(conn: psycopg.Connection, day: Callable[[int | None], str | None]) -> list[Outcome]:
    latest = inputs.latest_score(conn)
    if latest is None:
        return []
    tick, score = latest
    found = score_market_test(score, tick, day(tick))
    return [found] if found is not None else []


def score_all(
    conn: psycopg.Connection,
    ours: str | None,
    since_tick: int | None = None,
    warn: Callable[[str], None] = lambda message: None,
) -> list[Outcome]:
    openings = inputs.day_openings(conn)

    def day(tick: int | None) -> str | None:
        return day_of(tick, openings)

    found = duel_outcomes(conn, since_tick, warn)
    if ours:
        found += dealer_outcomes(conn, ours, since_tick) + trade_outcomes(conn, ours, since_tick)
    found += market_outcomes(conn, day)
    return [replace(o, day=o.day or day(o.tick)) for o in found]


def annotate(
    conn: psycopg.Connection, annotator: PhoenixAnnotator, warn: Callable[[str], None]
) -> tuple[int, tuple[str, ...], int]:
    """(outcomes annotated, Phoenix annotation ids, outcomes with no span this time)."""
    annotated, ids, missed = 0, [], 0
    for p in store.pending_annotations(conn):
        query = span_query(p)
        if query is None:
            store.mark_missed(conn, p.target, p.subject, never=True)
            missed += 1
            continue
        try:
            spans = annotator.find(query)
            if not spans:
                store.mark_missed(conn, p.target, p.subject)
                missed += 1
                continue
            ids += annotator.annotate([annotation_payload(p, s) for s in spans])
        except httpx.HTTPError as e:
            warn(f"phoenix: annotating {p.subject} failed ({type(e).__name__}); retrying next pass")
            break
        store.mark_annotated(conn, p.target, p.subject, spans[0].trace_id, spans[0].span_id)
        annotated += 1
    return annotated, tuple(ids), missed


def run_once(
    conn: psycopg.Connection,
    ours: str | None,
    *,
    since_tick: int | None = None,
    annotator: PhoenixAnnotator | None = None,
    warn: Callable[[str], None] = lambda message: None,
) -> RunSummary:
    outcomes = score_all(conn, ours, since_tick, warn)
    changed = store.upsert_outcomes(conn, outcomes)
    notes = _notes(outcomes, ours)
    summary = RunSummary(ours, _count(outcomes), changed, notes=notes)
    if annotator is None:
        return summary
    done, ids, missed = annotate(conn, annotator, warn)
    return replace(summary, annotated=done, annotation_ids=ids, no_span=missed, phoenix="on")


def _count(outcomes: Iterable[Outcome]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o.target] = counts.get(o.target, 0) + 1
    return counts


def _notes(outcomes: list[Outcome], ours: str | None) -> tuple[str, ...]:
    notes = []
    if not ours:
        notes.append("our team id is unknown (no BAZAAR_TEAM_ID, no /me snapshot): dealer and trade evals skipped")
    unpriced = [o.subject for o in outcomes if o.target == "duel" and o.score is None]
    if unpriced:
        notes.append(f"{len(unpriced)} duel deal(s) without a price yet: run `bazaar duel done` once")
    if not any(o.target == "market_test" for o in outcomes):
        notes.append("Market Test: no venue and no official efficiency yet (stub)")
    return tuple(notes)
