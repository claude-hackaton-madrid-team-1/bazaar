"""The outcome learner: settled decisions in, lessons + dealer patterns + embeddings out.

One pass reads Postgres only (no game call, so it adds nothing to the key's 5 req/s): the evals score
every settled outcome (`evals.run.score_all`), the public feed gives every dealer's concession curve
(`curves.py`), and each outcome becomes a lesson (`lessons.py`). Lessons are upserted into `learnings`
by their dedupe key (a re-run changes nothing), dealer moves go to `trader_behaviors`, and new or edited
claims are embedded for the hybrid recall.

In an agent the pass is tick-driven (`maybe_run(tick)` every `every` ticks, never wall-clock) and runs
on one background worker, so it never holds a tick; a pass still running when the next one is due is
skipped. Every failure is logged once per kind and the agent trades exactly as it would without it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import psycopg

from bazaar_agent.learn.behaviours import INSERT as BEHAVIOUR_INSERT
from bazaar_agent.learn.behaviours import behaviour_rows
from bazaar_agent.learn.curves import CurveStats, curve_stats
from bazaar_agent.learn.embed import Models
from bazaar_agent.learn.lessons import lessons_from
from bazaar_agent.learn.model import Learning
from bazaar_agent.learn.store import STATEMENT_TIMEOUT_MS, LearningStore

LEARN_EVERY_TICKS = 5
EMBED_BATCH = 64
EMBED_ROUNDS = 8  # at most this many batches per pass: a backlog drains over a few passes


@dataclass(frozen=True)
class PassResult:
    tick: int
    outcomes: int = 0
    lessons: int = 0
    behaviours: int = 0  # trader_behaviors rows offered (inserted only when new)
    embedded: int = 0
    curves: dict[tuple[str, str], CurveStats] = field(default_factory=dict)
    learned: tuple[Learning, ...] = ()
    elapsed_s: float = 0.0
    error: str | None = None


def rival_aliases(conn: psycopg.Connection) -> dict[int, str]:
    rows = conn.execute("select duel, rival from duels where rival is not null").fetchall()
    conn.commit()
    return {int(d): str(r) for d, r in rows if d is not None and r}


def learn_once(
    connect: Callable[[], psycopg.Connection],
    store: LearningStore,
    models: Models | None,
    us: str,
    tick: int,
    log: Callable[[str], None] = lambda message: None,
    *,
    save_moves: bool = True,
) -> PassResult:
    """One full pass (synchronous: the CLI calls it; agents go through `OutcomeLearner`). Lessons go where
    `store` writes (memory only for a store without a connection); `save_moves=False` (a CLI dry run) also
    keeps the dealer moves out of `trader_behaviors`."""
    from bazaar_agent.evals import inputs
    from bazaar_agent.evals.run import score_all
    from bazaar_agent.intel import dealer_threads

    started = time.monotonic()
    with connect() as conn:
        conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS * 4}")
        outcomes = score_all(conn, us, warn=log)
        events = inputs.dealer_events(conn)
        curves = curve_stats(dealer_threads(events, us))
        learned = lessons_from(outcomes, curves, rival_aliases(conn), us, tick)
        rows = behaviour_rows(events, us)
        if save_moves:
            with conn.cursor() as cur:
                cur.executemany(BEHAVIOUR_INSERT, [r.as_tuple() for r in rows])
        conn.commit()
    store.begin_tick(tick)
    store.record(learned)
    embedded = 0
    if models is not None and models.ready:
        for _ in range(EMBED_ROUNDS):
            done = store.embed_missing(models.embed, EMBED_BATCH)
            embedded += done
            if done < EMBED_BATCH:
                break
    return PassResult(
        tick,
        outcomes=len(outcomes),
        lessons=sum(1 for lr in learned if lr.kind == "lesson"),
        behaviours=len(rows),
        embedded=embedded,
        curves=curves,
        learned=tuple(learned),
        elapsed_s=round(time.monotonic() - started, 2),
    )


class OutcomeLearner:
    """What an agent's tick loop owns: `maybe_run(tick, us)` after the tick's sends."""

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection],
        store: LearningStore,
        models: Models | None,
        log: Callable[[str], None] = lambda message: None,
        every: int = LEARN_EVERY_TICKS,
        on_pass: Callable[[PassResult], Any] | None = None,
    ) -> None:
        self.connect, self.store, self.models, self.log = connect, store, models, log
        self.every, self.on_pass = max(1, every), on_pass
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bazaar-learner")
        self._running: Future[PassResult] | None = None
        self._last_tick: int | None = None
        self.last: PassResult | None = None
        self._failed: set[str] = set()

    def maybe_run(self, tick: int, us: str | None) -> bool:
        """Start a pass when one is due and none is running. Never blocks, never raises."""
        try:
            if not us or (self._running is not None and not self._running.done()):
                return False
            if self._last_tick is not None and tick - self._last_tick < self.every:
                return False
            self._last_tick = tick
            self._running = self._pool.submit(self._run, tick, us)
            return True
        except Exception as e:
            self._fail("schedule", e)
            return False

    def _run(self, tick: int, us: str) -> PassResult:
        try:
            result = learn_once(self.connect, self.store, self.models, us, tick, self.log)
            if self.on_pass is not None:
                self.on_pass(result)
        except Exception as e:
            self._fail("pass", e)
            result = PassResult(tick, error=f"{type(e).__name__}: {str(e)[:120]}")
        self.last = result
        if result.error is None and (result.lessons or result.embedded):
            self.log(
                f"learner: tick {tick}: {result.outcomes} outcomes → {result.lessons} lessons, "
                f"{len(result.curves)} dealer curves, {result.embedded} embedded ({result.elapsed_s:g} s)"
            )
        return result

    def _fail(self, what: str, error: Exception) -> None:
        if what not in self._failed:
            self.log(f"learner: {what} failed ({type(error).__name__}: {str(error)[:80]}); trading as before")
        self._failed.add(what)

    def wait(self, timeout: float | None = None) -> PassResult | None:
        """Block until the running pass ends (tests and the CLI)."""
        if self._running is not None:
            return self._running.result(timeout=timeout)
        return self.last

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
