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

import json
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any

import psycopg

from bazaar_agent.evals.model import Outcome
from bazaar_agent.feed import Event
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.intel import DealerThread
from bazaar_agent.learn.behaviours import INSERT as BEHAVIOUR_INSERT
from bazaar_agent.learn.behaviours import BehaviourRow, behaviour_rows
from bazaar_agent.learn.curves import CurveStats, curve_stats
from bazaar_agent.learn.embed import Models
from bazaar_agent.learn.evolve import Key, LadderPolicy, cap_for, evolve, policies_from
from bazaar_agent.learn.lessons import lessons_from
from bazaar_agent.learn.model import Learning
from bazaar_agent.learn.replay import compare, today_ladder
from bazaar_agent.learn.store import STATEMENT_TIMEOUT_MS, LearningStore

LEARN_EVERY_TICKS = 5
FAIL_LOG_EVERY = 20
READ_PAGE = 5000  # dealer events per query: a long feed is read in pages, each its own short statement
REREAD_TAIL = 500  # ids below the last one read again each pass: the archive may insert a late event
EMBED_BATCH = 16  # small batches: a recall waiting on the model lock waits ~70 ms at most
EMBED_ROUNDS = 16  # at most this many batches per pass: a backlog drains over a few passes


@dataclass(frozen=True)
class PassResult:
    tick: int
    outcomes: int = 0
    lessons: int = 0
    behaviours: int = 0  # trader_behaviors rows offered (inserted only when new)
    written: int = 0  # learnings new or changed this pass (sent to the store)
    embedded: int = 0
    curves: dict[tuple[str, str], CurveStats] = field(default_factory=dict)
    policies: dict[Key, LadderPolicy] = field(default_factory=dict)
    learned: tuple[Learning, ...] = ()
    elapsed_s: float = 0.0
    error: str | None = None


def rival_aliases(conn: psycopg.Connection) -> dict[int, str]:
    rows = conn.execute("select duel, rival from duels where rival is not null").fetchall()
    conn.commit()
    return {int(d): str(r) for d, r in rows if d is not None and r}


@dataclass
class PassState:
    """What one learner keeps between passes so each pass costs only what is new: the dealer events read
    so far (paged, `id > last_id` plus a re-read tail for late inserts), the dealer moves already inserted,
    what each learning said when it was last written, and the derived threads/curves while nothing is new."""

    events: list[Event] = field(default_factory=list)
    seen: set[int] = field(default_factory=set)
    last_id: int = 0
    moves: set[str] = field(default_factory=set)
    recorded: dict[str, tuple[str, float, str]] = field(default_factory=dict)
    derived: tuple[list[DealerThread], dict[tuple[str, str], CurveStats], list[BehaviourRow]] | None = None

    def read(self, conn: psycopg.Connection) -> bool:
        """Read the new dealer events page by page (progress is kept page by page, so a slow first read of
        a long feed resumes next pass instead of restarting). True when anything new arrived."""
        from bazaar_agent.evals.inputs import DEALER_EVENT_TYPES

        new = False
        after = max(0, self.last_id - REREAD_TAIL)  # an event archived late with a lower id is still read
        while True:
            rows = conn.execute(
                "select id, tick, type, actor, payload from feed_events where type = any(%s) and id > %s "
                "order by id limit %s",
                (list(DEALER_EVENT_TYPES), after, READ_PAGE),
            ).fetchall()
            conn.commit()
            for i, t, k, a, payload in rows:
                if int(i) in self.seen:
                    continue
                self.seen.add(int(i))
                slim = {key: v for key, v in (payload or {}).items() if key != "text"}  # words are never used
                self.events.append({"id": int(i), "tick": t, "type": k, "actor": a, "payload": slim})
                new = True
            if rows:
                after = int(rows[-1][0])
                self.last_id = max(self.last_id, after)
            if len(rows) < READ_PAGE:
                break
        if new:
            self.events.sort(key=lambda e: int(e["id"]))
            self.derived = None
        return new

    def changed(self, learned: list[Learning]) -> list[Learning]:
        """The learnings whose text, confidence or numbers moved since they were last written."""
        return [lr for lr in learned if self.recorded.get(lr.key()) != _signature(lr)]

    def written(self, learned: list[Learning]) -> None:
        """Mark them written: only once the store reached Postgres (a pass during an outage retries them)."""
        for lr in learned:
            self.recorded[lr.key()] = _signature(lr)


def _signature(lr: Learning) -> tuple[str, float, str]:
    return (lr.text, lr.confidence, json.dumps(lr.detail, sort_keys=True, default=str))


def scored(conn: psycopg.Connection, threads: list[DealerThread], us: str, log: Callable[[str], None]) -> list[Outcome]:
    """The evals' outcomes (duels, dealer threads, team trades), the dealer part from the cached threads."""
    from bazaar_agent.evals import inputs
    from bazaar_agent.evals.dealers import learned_ranges, score_thread
    from bazaar_agent.evals.run import duel_outcomes, trade_outcomes

    ranges = learned_ranges(inputs.curve_rows(conn))
    levels = inputs.dealer_levels(conn)
    ours = [score_thread(t, ranges, levels) for t in threads if t.ours]
    out = duel_outcomes(conn, None, log) + ours + trade_outcomes(conn, us, None)
    conn.commit()
    return out


def learn_once(
    connect: Callable[[], psycopg.Connection],
    store: LearningStore,
    models: Models | None,
    us: str,
    tick: int,
    log: Callable[[str], None] = lambda message: None,
    *,
    save_moves: bool = True,
    state: PassState | None = None,
    rules: Guardrails | None = None,
) -> PassResult:
    """One pass (synchronous: the CLI calls it; agents go through `OutcomeLearner`, which keeps `state`).
    Lessons go where `store` writes (memory only for a store without a connection); `save_moves=False`
    (a CLI dry run) also keeps the dealer moves out of `trader_behaviors`. With `rules`, the pass also
    evolves the dealer ladders (`evolve.py`) from the previous policies in `store` and records the new ones."""
    from bazaar_agent.intel import dealer_threads

    state = state if state is not None else PassState()
    started = time.monotonic()
    with connect() as conn:
        conn.execute(f"set statement_timeout = {STATEMENT_TIMEOUT_MS * 4}")
        state.read(conn)
        if state.derived is None:  # nothing new since the last pass: reuse the threads, curves and moves
            threads = dealer_threads(state.events, us)
            state.derived = (threads, curve_stats(threads), behaviour_rows(state.events, us))
        threads, curves, rows = state.derived
        outcomes = scored(conn, threads, us, log)
        learned = lessons_from(outcomes, curves, rival_aliases(conn), us, tick)
        fresh = [r for r in rows if r.dedupe_key not in state.moves]
        if save_moves and fresh:
            with conn.cursor() as cur:
                cur.executemany(BEHAVIOUR_INSERT, [r.as_tuple() for r in fresh])
        conn.commit()
        if save_moves:  # only once committed: a failed commit inserts them again next pass
            state.moves.update(r.dedupe_key for r in fresh)
    store.begin_tick(tick)
    policies: dict[Key, LadderPolicy] = {}
    if rules is not None:
        stored = store.recall(None, {"policy"}, None, subject_kind="dealer", team=us, limit=200)
        previous = policies_from(stored, us)
        policies = evolve_ladders(curves, previous, rules, tick, threads)
        learned = learned + [p.to_learning(us) for p in policies.values()]
    pending = state.changed(learned)
    store.record(pending)
    if store.where != "memory only":
        state.written(pending)
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
        written=len(pending),
        behaviours=len(rows),
        embedded=embedded,
        curves=curves,
        policies=policies,
        learned=tuple(learned),
        elapsed_s=round(time.monotonic() - started, 2),
    )


def evolve_ladders(
    curves: dict[Key, CurveStats],
    previous: dict[Key, LadderPolicy],
    rules: Guardrails,
    tick: int,
    threads: list[DealerThread],
) -> dict[Key, LadderPolicy]:
    """This pass's ladders, each with its replay against today's ladder on the same real threads (evidence)."""
    policies = evolve(curves, previous, rules, tick, threads=threads)
    out: dict[Key, LadderPolicy] = {}
    for key, policy in policies.items():
        stats = curves[key]
        old = today_ladder(stats.fills, cap_for(key[1], rules), rules.dealer_max_ticks_per_thread)
        found = None
        if old is not None and stats.floor is not None:
            found = compare(threads, key[0], key[1], old, policy.ladder, stats.floor, policy.patience)
        out[key] = replace(policy, replay=found.as_dict()) if found is not None else policy
    return out


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
        rules: Guardrails | None = None,
    ) -> None:
        self.connect, self.store, self.models, self.log = connect, store, models, log
        self.every, self.on_pass, self.rules = max(1, every), on_pass, rules
        self.policies: dict[Key, LadderPolicy] = {}  # replaced whole after each pass: the taker reads it
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bazaar-learner")
        self._running: Future[PassResult] | None = None
        self._last_tick: int | None = None
        self.last: PassResult | None = None
        self.state = PassState()
        self._failures: dict[str, int] = {}

    def maybe_run(self, tick: int, us: str | None) -> bool:
        """Start a pass when one is due and none is running. Never blocks, never raises."""
        try:
            if not us or (self._running is not None and not self._running.done()):
                return False
            if self._last_tick is not None and tick < self._last_tick:
                self._last_tick = None  # the clock went back (a simulator reset): start over
            if self._last_tick is not None and tick - self._last_tick < self.every:
                return False
            warm = getattr(self.models, "warm", None)
            if callable(warm):
                warm()  # a model load that failed at boot is retried every few passes
            self._last_tick = tick
            self._running = self._pool.submit(self._run, tick, us)
            return True
        except Exception as e:
            self._fail("schedule", e)
            return False

    def _run(self, tick: int, us: str) -> PassResult:
        try:
            result = learn_once(
                self.connect, self.store, self.models, us, tick, self.log, state=self.state, rules=self.rules
            )
            if self.rules is not None:
                self._log_changes(result.policies)
                self.policies = result.policies
            if self.on_pass is not None:
                self.on_pass(result)
        except Exception as e:
            self._fail("pass", e)
            result = PassResult(tick, error=type(e).__name__)  # never str(e): a connect error may echo the URL
        self.last = result
        if result.error is None and (result.written or result.embedded):  # quiet when nothing changed
            self.log(
                f"learner: tick {tick}: {result.outcomes} outcomes → {result.lessons} lessons "
                f"({result.written} written), {len(result.curves)} dealer curves, {result.embedded} embedded "
                f"({result.elapsed_s:g} s)"
            )
        return result

    def _log_changes(self, policies: dict[Key, LadderPolicy]) -> None:
        for key, policy in sorted(policies.items()):
            old = self.policies.get(key)
            if old is None or old.ladder != policy.ladder:
                self.log(f"learner: policy {key[0]} {key[1]}: {policy.text()}")

    def _fail(self, what: str, error: Exception) -> None:
        """Logged the first time and then every FAIL_LOG_EVERY-th time: a learner that keeps failing says so."""
        n = self._failures.get(what, 0) + 1
        self._failures[what] = n
        if n == 1 or n % FAIL_LOG_EVERY == 0:
            self.log(f"learner: {what} failed ({type(error).__name__}, {n}x); trading as before")

    def wait(self, timeout: float | None = None) -> PassResult | None:
        """Block until the running pass ends (tests and the CLI)."""
        if self._running is not None:
            return self._running.result(timeout=timeout)
        return self.last

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
