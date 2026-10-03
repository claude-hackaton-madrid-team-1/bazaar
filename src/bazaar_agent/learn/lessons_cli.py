"""`bazaar learnings --lessons` and `bazaar learnings --query "..."`: the outcome learner and the hybrid recall.

`--lessons` runs one outcome-learner pass over Postgres (the evals' outcomes + every dealer's public
threads) and prints the lessons and dealer patterns; with `--save` it upserts them (and embeds them)
into the shared `learnings` table, exactly what the taker does every few ticks.
`--query` asks the hybrid recall what an agent would get for that situation from the shared table
(with `--lessons` and no `--save`: from the pass just run, in memory, without the vector leg): each
hit with its cross-encoder score, its BM25 and vector ranks, and the latency. No key, no game call.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import psycopg
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from bazaar_agent.learn.embed import LocalModels
from bazaar_agent.learn.model import Learning
from bazaar_agent.learn.outcomes import PassResult, learn_once
from bazaar_agent.learn.recall import HybridRecall, Query, Recalled
from bazaar_agent.learn.store import LearningStore

console = Console()
err_console = Console(stderr=True)


def _lesson_table(rows: list[Learning]) -> Table:
    table = Table(title="lessons and dealer patterns (source: outcomes)")
    for col in ("tick", "subject", "kind", "conf", "lesson"):
        table.add_column(col, overflow="fold")
    for lr in rows:
        table.add_row(str(lr.tick), f"{lr.subject_kind}:{lr.subject}", lr.kind, f"{lr.confidence:.2f}", escape(lr.text))
    return table


def _hits_table(found: Recalled, query: str) -> Table:
    table = Table(title=f"recall: {query}")
    for col in ("score", "bm25", "vector", "cos", "kind", "lesson"):
        table.add_column(col, overflow="fold")
    for h in found.hits:
        cos = "-" if h.cosine is None else f"{h.cosine:.3f}"
        table.add_row(
            f"{h.score:+.2f}",
            str(h.bm25_rank or "-"),
            str(h.vector_rank or "-"),
            cos,
            h.learning.kind,
            escape(h.learning.text),
        )
    return table


def show(
    connect: Callable[[], psycopg.Connection] | None,
    us: str | None,
    tick: int,
    *,
    lessons: bool,
    query: str | None,
    save: bool,
    subject: str | None,
    limit: int,
    min_score: float,
    as_json: bool,
    init_schema: Callable[[psycopg.Connection], object] | None,
) -> None:
    if connect is None or not us:
        err_console.print("[red]lessons need Postgres (DATABASE_URL) and our team id (BAZAAR_TEAM_ID or /me)[/red]")
        raise SystemExit(1)
    models = LocalModels(log=err_console.print)
    started = time.monotonic()
    models.load()
    err_console.print(f"[dim]models: {models.status} ({time.monotonic() - started:.1f} s)[/dim]")
    store = LearningStore(connect if save else None, err_console.print, init_schema)
    out: dict[str, Any] = {"tick": tick, "us": us}
    result: PassResult | None = None
    if lessons:
        result = learn_once(connect, store, models if save else None, us, tick, err_console.print, save_moves=save)
        out["pass"] = {
            "outcomes": result.outcomes,
            "lessons": result.lessons,
            "behaviours": result.behaviours,
            "embedded": result.embedded,
            "elapsed_s": result.elapsed_s,
            "where": store.where,
        }
    if query:
        # --lessons without --save: query the pass just run (memory: BM25 + rerank, no vectors);
        # otherwise the shared table, which is what the agents recall.
        reader = store if lessons and not save else LearningStore(connect, err_console.print)
        recall = HybridRecall(reader, models, err_console.print)
        subjects = (subject,) if subject else None
        asked = Query(query, subjects=subjects, team=us, tick=tick, k=limit, min_score=min_score, budget_s=10)
        found = recall.recall(asked)
        out["query"] = {
            "text": query,
            "status": found.status,
            "elapsed_ms": found.elapsed_ms,
            "candidates": found.candidates,
            "legs": found.legs,
            "hits": [
                {
                    "score": round(h.score, 3),
                    "bm25_rank": h.bm25_rank,
                    "vector_rank": h.vector_rank,
                    "cosine": h.cosine,
                    "kind": h.learning.kind,
                    "subject": h.learning.subject,
                    "text": h.learning.text,
                }
                for h in found.hits
            ],
        }
        recall.close()
    if as_json:
        if result is not None and lessons:
            out["lessons"] = [lr.model_dump() for lr in result.learned][: max(limit, 50)]
        console.print_json(json.dumps(out, default=str))
        return
    if result is not None:
        p = out["pass"]
        console.print(
            f"pass: {p['outcomes']} outcomes → {p['lessons']} lessons, {len(result.curves)} dealer curves, "
            f"{p['behaviours']} dealer moves, {p['embedded']} embedded in {p['elapsed_s']} s ({p['where']})"
        )
        if lessons:
            rows = [lr for lr in result.learned if subject is None or lr.subject == subject]
            console.print(_lesson_table(rows[: max(limit, 40)]))
    if query:
        q = out["query"]
        console.print(f"recall {q['status']} in {q['elapsed_ms']} ms · {q['candidates']} candidates · legs {q['legs']}")
        console.print(_hits_table(found, query))
