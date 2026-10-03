"""The single `recall()` the agents use: hybrid retrieval over the shared `learnings` table.

1. Hard filters first (Postgres + this process's memory, via `LearningStore.recall`): kind, subject,
   whom it binds (our team or everyone) and validity at the tick (`until_tick` exclusive).
2. Two rankings of what survived: BM25 over the text and key fields (`bm25.py`), and pgvector cosine
   between the query embedding and each learning's embedding (same filters, in SQL).
3. Reciprocal rank fusion (k = 60) of the two rankings.
4. A local cross-encoder reranks the fused top N; only learnings at or above `min_score` are kept.

It answers inside a deadline (the tick budget: Saturday ticks are 30 s, Sunday 15 s) on a worker
thread with its own connection, and it fails open: models still loading, a database error, a timeout
or anything unexpected returns NO lessons, never a stale or irrelevant one. Callers treat the hits as
quoted data (`as_quoted`), never as instructions.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace
from typing import Any

from bazaar_agent.learn.bm25 import BM25
from bazaar_agent.learn.embed import Models
from bazaar_agent.learn.model import Learning
from bazaar_agent.learn.store import LearningStore

RRF_K = 60
CANDIDATES = 600  # hard-filtered learnings the lexical leg ranks
LEG_TOP = 40  # each leg's ranking depth
RERANK_TOP = 12  # fused learnings the cross-encoder reads
BM25_FLOOR = 5.0  # BM25-only fallback: Friday's 34 lessons scored 7.5-23.5 for relevant queries, <= 4.5 otherwise
MIN_SCORE = 0.0  # cross-encoder relevance floor (ms-marco logit; calibrated on real lessons, see README)
DEFAULT_BUDGET_S = 0.8  # far inside a 15 s tick; a slower answer is dropped
QUOTE_MAX = 5


@dataclass(frozen=True)
class Query:
    """What an agent is about to do, in words, plus the hard filters."""

    text: str
    subjects: tuple[str, ...] | None = None  # dealer ids, team ids, rival slugs; None = any
    kinds: tuple[str, ...] | None = ("lesson", "behaviour", "policy")
    subject_kind: str | None = None
    team: str | None = None  # learnings that bind this team or everyone
    tick: int | None = None  # only learnings still valid at this tick
    # Whose rows: by default only what the outcome learner wrote from our own scored outcomes (prices, never
    # words). The feed reader's rows (N12) are opt-in: some quote feed text another team chose.
    sources: tuple[str, ...] | None = ("outcome",)
    where: tuple[tuple[str, str], ...] = ()  # situation features that must match, e.g. (("mechanic", "duel"),)
    k: int = 3
    min_score: float = MIN_SCORE
    budget_s: float = DEFAULT_BUDGET_S


@dataclass(frozen=True)
class Hit:
    learning: Learning
    score: float  # cross-encoder relevance
    fused: float  # reciprocal rank fusion score
    bm25_rank: int | None  # 1-based; None = not in that leg's top
    vector_rank: int | None
    cosine: float | None = None


@dataclass(frozen=True)
class Recalled:
    hits: tuple[Hit, ...] = ()
    status: str = "ok"  # ok | bm25_only (no reranker yet) | no_candidates | timeout | error:<Type>
    elapsed_ms: float = 0.0
    candidates: int = 0
    legs: dict[str, int] = field(default_factory=dict)  # how many each leg ranked

    @property
    def learnings(self) -> list[Learning]:
        return [h.learning for h in self.hits]

    def as_quoted(self, limit: int = QUOTE_MAX) -> list[dict[str, Any]]:
        """What Jev's state and the words context get: quoted data, never instructions."""
        return [
            {
                "quoted_lesson": h.learning.text,
                "about": h.learning.subject,
                "kind": h.learning.kind,
                "source": h.learning.source,
                "tick": h.learning.tick,
                "relevance": round(h.score, 2),
                "confidence": h.learning.confidence,
            }
            for h in self.hits[:limit]
        ]


def doc_text(lr: Learning) -> str:
    """The searchable text: the sentence plus the fields a query names (subject, kind, item, class)."""
    keys = " ".join(str(lr.detail[k]) for k in ("item", "price_class", "role", "target") if lr.detail.get(k))
    return f"{lr.subject} {lr.kind} {keys}: {lr.text}"


def fuse(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal rank fusion: each list adds 1 / (k + rank) for every key it ranks (rank from 1)."""
    out: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            out[key] = out.get(key, 0.0) + 1.0 / (k + rank)
    return out


class HybridRecall:
    """Owns a worker thread and its own `LearningStore` (own connection): a slow recall never blocks
    the tick loop's connection, and a timed-out one just finishes in the background."""

    def __init__(self, store: LearningStore, models: Models, log: Callable[[str], None] = lambda message: None):
        self.store, self.models, self.log = store, models, log
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bazaar-recall")
        self._warned: set[str] = set()
        self._bm25: tuple[tuple[str, ...], BM25] | None = None  # the last index, reused while the pool holds

    def _index(self, keys: list[str], by_key: dict[str, Learning]) -> BM25:
        """BM25 over the candidates; rebuilt only when the candidate set changed (most ticks it does not)."""
        ident = tuple(f"{k}:{hash(by_key[k].text)}" for k in keys)
        if self._bm25 is None or self._bm25[0] != ident:
            self._bm25 = (ident, BM25.build([doc_text(by_key[k]) for k in keys]))
        return self._bm25[1]

    def recall(self, query: Query) -> Recalled:
        """`search()` under the query's deadline. Never raises; any failure is no lessons."""
        started = time.monotonic()
        try:
            future = self._pool.submit(self.search, query)
            found = future.result(timeout=max(0.01, query.budget_s))
        except FutureTimeout:
            found = Recalled(status="timeout")
        except Exception as e:
            found = Recalled(status=f"error:{type(e).__name__}")
        if found.status not in ("ok", "no_candidates") and found.status not in self._warned:
            self._warned.add(found.status)
            if found.status == "bm25_only":
                self.log("learnings: the reranker is not ready; recall is BM25-only (lexical floor) until it is")
            else:
                self.log(f"learnings: recall {found.status}; deciding without lessons")
        return replace(found, elapsed_ms=round((time.monotonic() - started) * 1000, 1))

    def search(self, query: Query) -> Recalled:
        """The pipeline itself (synchronous; tests and the CLI call it directly)."""
        started = time.monotonic()
        self.store.begin_tick(query.tick if query.tick is not None else -1)
        pool = self.store.candidates(
            kinds=query.kinds,
            subjects=query.subjects,
            sources=query.sources,
            subject_kind=query.subject_kind,
            team=query.team,
            tick=query.tick,
            where=query.where,
            limit=CANDIDATES,
        )
        if not pool or not query.text.strip():
            return Recalled(status="no_candidates", candidates=len(pool))
        by_key = {lr.key(): lr for lr in pool}
        keys = list(by_key)
        index = self._index(keys, by_key)
        lexical = [keys[i] for i in index.ranked(query.text, LEG_TOP)]
        vector = self.models.embed_query(query.text)
        cosines: dict[str, float] = {}
        if vector is not None:
            for key, cos in self.store.vector_rank(
                vector,
                kinds=query.kinds,
                tick=query.tick,
                subject_kind=query.subject_kind,
                team=query.team,
                subjects=query.subjects,
                limit=LEG_TOP,
                sources=query.sources,
                where=query.where,
            ):
                if key in by_key:
                    cosines[key] = cos
        semantic = sorted(cosines, key=lambda k: -cosines[k])
        fused = fuse([lexical, semantic])
        top = sorted(fused, key=lambda k: (-fused[k], -by_key[k].tick, k))[:RERANK_TOP]
        legs = {"bm25": len(lexical), "vector": len(semantic), "fused": len(top)}
        scores = self.models.rerank(query.text, [doc_text(by_key[k]) for k in top])
        if scores is None:  # no reranker (loading, or the download failed): BM25 alone, above a lexical floor
            raw = dict(zip(keys, index.scores(query.text), strict=True))
            lex = [Hit(by_key[k], raw[k], 0.0, i, None) for i, k in enumerate(lexical, start=1) if raw[k] >= BM25_FLOOR]
            return Recalled(tuple(lex[: query.k]), "bm25_only", 0.0, len(pool), legs)
        lex_rank = {k: i for i, k in enumerate(lexical, start=1)}
        vec_rank = {k: i for i, k in enumerate(semantic, start=1)}
        hits = [
            Hit(by_key[k], s, round(fused[k], 5), lex_rank.get(k), vec_rank.get(k), cosines.get(k))
            for k, s in zip(top, scores, strict=True)
            if s >= query.min_score
        ]
        hits.sort(key=lambda h: (-h.score, -h.learning.confidence, -h.learning.tick))
        elapsed = round((time.monotonic() - started) * 1000, 1)
        return Recalled(tuple(hits[: query.k]), "ok", elapsed, len(pool), legs)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.store.close()


LESSONS_K = 3
LESSONS_BUDGET_S = 0.5  # inside the agent's tick: Jev itself gets up to `jev_timeout_s` after this
LESSONS_CACHE_TICKS = 5  # lessons change every few ticks (the outcome learner's pace): reuse within that


class Lessons:
    """What an agent calls before Jev or the words model: the top lessons for a situation, as quoted data.

    Cached per (situation, filters) for `LESSONS_CACHE_TICKS` ticks; answers `[]` while the models load and
    on any failure (the recall already fails open). Never raises."""

    def __init__(self, recall: HybridRecall, k: int = LESSONS_K, budget_s: float = LESSONS_BUDGET_S) -> None:
        self.recall, self.k, self.budget_s = recall, k, budget_s
        self._cache: dict[tuple[object, ...], list[dict[str, Any]]] = {}

    def __call__(
        self,
        text: str,
        *,
        subjects: tuple[str, ...] | None = None,
        subject_kind: str | None = None,
        tick: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            key = None if tick is None else (text, subjects, subject_kind, tick // LESSONS_CACHE_TICKS)
            if key is not None and key in self._cache:  # no tick (a duel state): never cached, always fresh
                return self._cache[key]
            query = Query(
                text, subjects=subjects, subject_kind=subject_kind, tick=tick, k=self.k, budget_s=self.budget_s
            )
            found = self.recall.recall(query)
            quoted = found.as_quoted(self.k) if found.status in ("ok", "bm25_only") else []
            if key is not None and found.status in ("ok", "no_candidates", "bm25_only"):  # timeouts, errors: retried
                if len(self._cache) > 256:
                    self._cache.clear()
                self._cache[key] = quoted
            return quoted
        except Exception:
            return []
