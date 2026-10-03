"""Where learnings live and how agents recall them: the shared `learnings` table, with this process's
memory as the always-on fallback.

Every write is an idempotent upsert on the learning's dedupe key, so two processes (or a restart)
reading the same feed write one row. `recall()` merges Postgres and memory and returns only what is
still in force at the tick asked. Nothing here raises into a tick: a database error is logged once,
Postgres is retried at most once per tick, and memory keeps serving recall meanwhile.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Iterable, Sequence
from typing import Any

import psycopg

from bazaar_agent.db import jsonb_safe
from bazaar_agent.learn.model import Learning

STATEMENT_TIMEOUT_MS = 1500  # a recall or a write must never eat the tick
RETRY_EVERY = 5  # ticks between Postgres retries once it failed (a connect may take seconds)
MEMORY_MAX = 5000
SCOPES = {"dealer": "trader", "team": "trader", "venue": "market", "organiser": "market", "rival": "duel"}
COLUMNS = "subject_kind, subject, kind, created_tick, until_tick, team, evidence, confidence, claim, source, stats"


def _row(learning: Learning) -> tuple[Any, ...]:
    return (
        SCOPES[learning.subject_kind],
        learning.subject_kind,
        learning.subject,
        learning.kind,
        learning.tick,
        learning.until_tick,
        learning.team,
        list(learning.evidence),
        len(learning.evidence),
        learning.confidence,
        learning.text,
        learning.source,
        json.dumps(jsonb_safe(learning.detail), default=str, ensure_ascii=False, allow_nan=False),
        learning.key(),
    )


UPSERT = (
    "insert into learnings (scope, subject_kind, subject, kind, created_tick, until_tick, team, evidence, support_n, "
    "confidence, claim, source, stats, dedupe_key, updated_at) "
    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, now()) "
    "on conflict (dedupe_key) do update set claim = excluded.claim, stats = excluded.stats, source = excluded.source, "
    "evidence = excluded.evidence, support_n = excluded.support_n, "
    "confidence = greatest(learnings.confidence, excluded.confidence), "
    "created_tick = greatest(learnings.created_tick, excluded.created_tick), updated_at = now() "
    # an LLM row never rewrites another source's row; a rules fact takes back only a row an older LLM reading held
    "where learnings.source is not distinct from excluded.source "
    "or (excluded.source = 'rules' and learnings.source = 'llm')"
)


def _from_row(row: tuple[Any, ...]) -> Learning | None:
    subject_kind, subject, kind, tick, until, team, evidence, confidence, claim, source, stats = row
    try:
        return Learning(
            subject_kind=subject_kind,
            subject=subject,
            kind=kind,
            tick=tick or 0,
            until_tick=until,
            team=team,
            evidence=tuple(evidence or ()),
            confidence=float(confidence if confidence is not None else 0.0),
            text=claim or "-",
            source=source or "rules",
            detail=stats if isinstance(stats, dict) else {},
        )
    except ValueError:  # a row another writer wrote in an older shape: skipped, never fatal
        return None


def matches(
    learning: Learning,
    *,
    subject: str | None,
    kinds: Collection[str] | None,
    subject_kind: str | None,
    tick: int | None,
    team: str | None,
    source: str | None = None,
) -> bool:
    """`team`: the learnings that bind this team or everyone (None = every learning)."""
    return (
        (source is None or learning.source == source)
        and (subject is None or learning.subject == subject)
        and (kinds is None or learning.kind in kinds)
        and (subject_kind is None or learning.subject_kind == subject_kind)
        and (tick is None or learning.active(tick))
        and (team is None or learning.team in (None, team))
    )


class LearningStore:
    """`record()` and `recall()` over Postgres (when `connect` answers) plus this process's memory."""

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection] | None = None,
        log: Callable[[str], None] = lambda message: None,
        init_schema: Callable[[psycopg.Connection], object] | None = None,
    ) -> None:
        self._connect, self._log, self._init = connect, log, init_schema
        self._conn: psycopg.Connection | None = None
        self._down = False
        self._tick: int | None = None
        self._tried_tick: int | None = None
        self._init_tried = False
        self._disabled = False  # the table lacks a column we need: memory only for this process
        self._has_vectors: bool | None = None  # learnings.embedding exists (pgvector), read once per connection
        self.memory: dict[str, Learning] = {}

    # ---------------------------------------------------------------- connection (DecisionLog's pattern)

    def begin_tick(self, tick: int) -> None:
        """While Postgres is down, try it again at most once per tick (a connect may take seconds)."""
        self._tick = tick

    @property
    def where(self) -> str:
        return "postgres learnings + memory" if self._db() is not None else "memory only"

    def _db(self) -> psycopg.Connection | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        if self._connect is None or self._disabled:
            return None
        tick, tried = self._tick, self._tried_tick
        if self._down and tick is not None and tried is not None and tick - tried < RETRY_EVERY:
            return None
        self._tried_tick = self._tick
        try:
            conn = self._connect()
            self._init_once(conn)
            conn.autocommit = True
        except Exception as e:
            if not self._down:
                self._log(f"learnings: Postgres unavailable ({type(e).__name__}); memory only")
            self._down = True
            return None
        if self._down:
            self._log("learnings: Postgres back")
        self._conn, self._down, self._has_vectors = conn, False, None
        return conn

    def open(self) -> str:
        """Connect (and apply the schema) now, at process start, so no tick ever waits on it."""
        return self.where

    def _init_once(self, conn: psycopg.Connection) -> None:
        """The learnings columns (idempotent `init_schema`), tried once per process: on failure the table is
        used as it is, and a missing column only sends writes to memory."""
        if self._init is None or self._init_tried:
            return
        self._init_tried = True
        try:
            self._init(conn)
        except Exception as e:
            self._log(f"learnings: schema init failed ({type(e).__name__}); using the table as it is")
            conn.rollback()

    def _failed(self, what: str, error: Exception) -> None:
        if isinstance(error, ValueError):  # a bad value in this batch: the connection is fine, skip the batch
            self._log(f"learnings: {what} skipped a batch ({type(error).__name__}); memory keeps it")
            return
        if isinstance(error, psycopg.errors.UndefinedColumn | psycopg.errors.UndefinedTable):
            self._disabled = True  # run `bazaar db init`; retrying every tick would only log the same error
            self._log(f"learnings: the learnings table is not migrated ({type(error).__name__}); memory only")
        else:
            self._log(f"learnings: {what} failed in Postgres ({type(error).__name__}); memory meanwhile")
        if self._conn is not None:
            self._conn.close()
        self._conn, self._down, self._tried_tick = None, True, self._tick

    # ---------------------------------------------------------------- write

    def remember(self, learnings: Iterable[Learning]) -> dict[str, Learning]:
        """Into this process's memory only (no I/O), trimmed to the newest MEMORY_MAX facts."""
        batch = {lr.key(): lr for lr in learnings}
        self.memory.update(batch)
        if len(self.memory) > MEMORY_MAX:
            self.memory = dict(sorted(self.memory.items(), key=lambda kv: kv[1].tick)[-MEMORY_MAX:])
        return batch

    def record(self, learnings: Iterable[Learning]) -> int:
        """Remember and upsert; returns how many were given (memory always takes them)."""
        batch = self.remember(learnings)
        if not batch:
            return 0
        conn = self._db()
        if conn is not None:
            try:
                with conn.transaction():
                    conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                    with conn.cursor() as cur:
                        cur.executemany(UPSERT, [_row(lr) for lr in sorted(batch.values(), key=Learning.key)])
            except (psycopg.Error, ValueError) as e:  # ValueError: a value jsonb refuses (NaN, an encoding)
                self._failed("upsert", e)
        return len(batch)

    # ---------------------------------------------------------------- read

    def recall(
        self,
        subject: str | None = None,
        kinds: Collection[str] | None = None,
        tick: int | None = None,
        *,
        subject_kind: str | None = None,
        team: str | None = None,
        limit: int = 50,
        use_db: bool = True,
        source: str | None = None,
    ) -> list[Learning]:
        """The learnings in force at `tick` (all ticks when None), newest first, deduped by key.
        `use_db=False`: memory only, no I/O (what a tick reads before its sends)."""
        found = {k: lr for k, lr in self.memory.items()}
        for lr in self._recall_db(subject, kinds, tick, subject_kind, team, limit, source) if use_db else ():
            found.setdefault(lr.key(), lr)
        hits = [
            lr
            for lr in found.values()
            if matches(lr, subject=subject, kinds=kinds, subject_kind=subject_kind, tick=tick, team=team, source=source)
        ]
        return sorted(hits, key=lambda lr: (-lr.tick, lr.key()))[:limit]

    def _recall_db(
        self,
        subject: str | None,
        kinds: Collection[str] | None,
        tick: int | None,
        subject_kind: str | None,
        team: str | None,
        limit: int,
        source: str | None = None,
    ) -> list[Learning]:
        conn = self._db()
        if conn is None:
            return []
        query = (
            f"select {COLUMNS} from learnings where dedupe_key is not null and superseded_by is null "
            "and (%(subject)s::text is null or subject = %(subject)s) "
            "and (%(kinds)s::text[] is null or kind = any(%(kinds)s)) "
            "and (%(sk)s::text is null or subject_kind = %(sk)s) "
            "and (%(tick)s::int is null or until_tick is null or until_tick > %(tick)s) "
            "and (%(team)s::text is null or team is null or team = %(team)s) "
            "and (%(source)s::text is null or source = %(source)s) "
            "order by created_tick desc nulls last, id desc limit %(limit)s"
        )
        params = {
            "subject": subject,
            "kinds": sorted(kinds) if kinds is not None else None,
            "sk": subject_kind,
            "tick": tick,
            "team": team,
            "limit": limit,
            "source": source,
        }
        try:
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                rows = conn.execute(query, params).fetchall()  # type: ignore[arg-type]
        except psycopg.Error as e:
            self._failed("recall", e)
            return []
        return [lr for row in rows if (lr := _from_row(row)) is not None]

    # ---------------------------------------------------------------- candidates (N3: the hybrid recall)

    def candidates(
        self,
        *,
        kinds: Collection[str] | None,
        subjects: Collection[str] | None,
        sources: Collection[str] | None,
        subject_kind: str | None,
        team: str | None,
        tick: int | None,
        where: Sequence[tuple[str, str]] = (),
        limit: int,
    ) -> list[Learning]:
        """Every learning that passes ALL the hard filters, newest first: the filters run in SQL before the
        limit, so many rows about other subjects can never push the relevant ones out of the pool."""

        def wanted(lr: Learning) -> bool:
            return (
                matches(lr, subject=None, kinds=kinds, subject_kind=subject_kind, tick=tick, team=team)
                and (subjects is None or lr.subject in subjects)
                and (sources is None or lr.source in sources)
                and all(str(lr.detail.get(k)) == v for k, v in where)
            )

        found = {k: lr for k, lr in list(self.memory.items()) if wanted(lr)}
        conn = self._db()
        if conn is not None:
            query = (
                f"select {COLUMNS} from learnings where dedupe_key is not null and superseded_by is null "
                f"{FILTERS} order by created_tick desc nulls last, id desc limit %(limit)s"
            )
            params = _filters(kinds, subjects, sources, subject_kind, team, tick, where) | {"limit": limit}
            try:
                with conn.transaction():
                    conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                    rows = conn.execute(query, params).fetchall()  # type: ignore[arg-type]
            except psycopg.Error as e:
                self._failed("candidates", e)
                rows = []
            for row in rows:
                lr = _from_row(row)
                if lr is not None and wanted(lr):
                    found.setdefault(lr.key(), lr)
        return sorted(found.values(), key=lambda lr: (-lr.tick, lr.key()))[:limit]

    # ---------------------------------------------------------------- vectors (N3: the hybrid recall)

    def _vector_off(self, conn: psycopg.Connection, error: Exception) -> None:
        """A vector column or type is missing (no pgvector, an unmigrated table): the vector leg and the
        embeddings stop for this connection, everything else in the store goes on."""
        if self._has_vectors is not False:
            self._log(f"learnings: no vector search here ({type(error).__name__}); BM25 only")
        self._has_vectors = False
        if not conn.closed and conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()

    def _vectors_on(self, conn: psycopg.Connection) -> bool:
        """Whether `learnings.embedding` exists (it does only where pgvector is installed)."""
        if self._has_vectors is None:
            row = conn.execute(
                "select 1 from information_schema.columns where table_schema = current_schema() "
                "and table_name = 'learnings' and column_name = 'embedding'"
            ).fetchone()
            self._has_vectors = row is not None
        return self._has_vectors

    def vector_rank(
        self,
        vector: Sequence[float],
        *,
        kinds: Collection[str] | None,
        tick: int | None,
        subject_kind: str | None,
        team: str | None,
        subjects: Collection[str] | None,
        limit: int,
        sources: Collection[str] | None = None,
        where: Sequence[tuple[str, str]] = (),
    ) -> list[tuple[str, float]]:
        """(dedupe key, cosine similarity) of the embedded learnings nearest `vector`, under the same hard
        filters as `recall()`. Empty without pgvector, without a connection, or on any error."""
        conn = self._db()
        if conn is None:
            return []
        query = (
            "select dedupe_key, 1 - (embedding <=> %(v)s::vector) from learnings "
            f"where embedding is not null and dedupe_key is not null and superseded_by is null {FILTERS} "
            "order by embedding <=> %(v)s::vector limit %(limit)s"
        )
        params = _filters(kinds, subjects, sources, subject_kind, team, tick, where) | {
            "v": vector_literal(vector),
            "limit": limit,
        }
        try:
            if not self._vectors_on(conn):
                return []
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                rows = conn.execute(query, params).fetchall()  # type: ignore[arg-type]
        except (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedObject) as e:
            self._vector_off(conn, e)
            return []
        except psycopg.Error as e:
            self._failed("vector search", e)
            return []
        return [(str(key), float(cos)) for key, cos in rows if key is not None and cos is not None]

    def embed_missing(self, embed: Callable[[list[str]], list[list[float]] | None], limit: int = 64) -> int:
        """Embed the learnings whose text changed since they were embedded (or never were). Returns how
        many were written; 0 without pgvector, without models or on any error (retried next pass)."""
        conn = self._db()
        if conn is None:
            return 0
        try:
            if not self._vectors_on(conn):
                return 0
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                rows = conn.execute(
                    "select id, claim from learnings where claim is not null and dedupe_key is not null "
                    "and (embedding is null or embedded_hash is distinct from md5(claim)) order by id limit %s",
                    (limit,),
                ).fetchall()
            if not rows:
                return 0
            vectors = embed([str(claim) for _, claim in rows])
            if not vectors or len(vectors) != len(rows):
                return 0
            with conn.transaction():
                conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                with conn.cursor() as cur:
                    cur.executemany(  # a claim edited meanwhile keeps its old hash: embedded again next pass
                        "update learnings set embedding = %s::vector, embedded_hash = md5(claim) "
                        "where id = %s and md5(claim) = md5(%s)",
                        [(vector_literal(v), rid, claim) for (rid, claim), v in zip(rows, vectors, strict=True)],
                    )
        except (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedObject) as e:
            self._vector_off(conn, e)
            return 0
        except psycopg.Error as e:
            self._failed("embedding write", e)
            return 0
        return len(rows)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()


FILTERS = (
    "and (%(kinds)s::text[] is null or kind = any(%(kinds)s)) "
    "and (%(subjects)s::text[] is null or subject = any(%(subjects)s)) "
    "and (%(sources)s::text[] is null or source = any(%(sources)s)) "
    "and (%(sk)s::text is null or subject_kind = %(sk)s) "
    "and (%(tick)s::int is null or until_tick is null or until_tick > %(tick)s) "
    "and (%(team)s::text is null or team is null or team = %(team)s) "
    # each feature compared as text, as memory compares it (a number 115 matches "115")
    "and (%(where)s::jsonb is null or not exists (select 1 from jsonb_each_text(%(where)s::jsonb) w "
    "where (stats ->> w.key) is distinct from w.value))"
)


def _filters(
    kinds: Collection[str] | None,
    subjects: Collection[str] | None,
    sources: Collection[str] | None,
    subject_kind: str | None,
    team: str | None,
    tick: int | None,
    where: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    return {
        "kinds": sorted(kinds) if kinds is not None else None,
        "subjects": sorted(subjects) if subjects is not None else None,
        "sources": sorted(sources) if sources is not None else None,
        "sk": subject_kind,
        "tick": tick,
        "team": team,
        "where": json.dumps(dict(where)) if where else None,
    }


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text form: '[0.1,0.2,...]' (no pgvector Python adapter needed)."""
    return "[" + ",".join(f"{float(x):.6g}" for x in vector) + "]"
