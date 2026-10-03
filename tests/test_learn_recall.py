"""The hybrid recall (N3): hard filters, BM25 + vector legs, RRF, the cross-encoder floor, fail-open.

Fake models keep it offline and deterministic: a bag-of-words vector (hashed into 384 dims) and a
reranker that scores shared words minus two, so an unrelated lesson scores below the 0 floor.
"""

import hashlib
import math
import threading
import time

import pytest

from bazaar_agent.learn.bm25 import BM25, tokens
from bazaar_agent.learn.embed import DIM, LocalModels
from bazaar_agent.learn.model import Learning
from bazaar_agent.learn.recall import HybridRecall, Query, doc_text, fuse
from bazaar_agent.learn.store import LearningStore, vector_literal
from tests.test_db import database_url, schema  # noqa: F401  (fixtures for the Postgres test)

US = "t01"


class FakeModels:
    def __init__(self, ready=True, fail=False, delay=0.0):
        self._ready, self.fail, self.delay = ready, fail, delay
        self.reranked: list[int] = []

    @property
    def ready(self) -> bool:
        return self._ready

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for t in tokens(text):
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, texts):
        return [self._vec(t) for t in texts] if self._ready else None

    def embed_query(self, text):
        return self._vec(text) if self._ready else None

    def rerank(self, query, docs):
        if self.fail:
            raise RuntimeError("onnx exploded")
        time.sleep(self.delay)
        if not self._ready:
            return None
        self.reranked.append(len(docs))
        q = set(tokens(query))
        return [float(len(q & set(tokens(d))) - 2) for d in docs]


def lesson(subject, text, tick=100, kind="lesson", until=None, team=US, item=None, subject_kind="dealer"):
    detail = {"outcome": f"x:{abs(hash(text)) % 10_000}"} | ({"item": item} if item else {})
    return Learning(
        subject_kind=subject_kind,
        subject=subject,
        kind=kind,
        tick=tick,
        until_tick=until,
        team=team,
        confidence=0.8,
        text=text,
        source="outcome",
        detail=detail,
    )


CHATO = lesson(
    "chato", "chato uncommon LAV-08: we bid 17 to 24, asks 33 to 31; no deal. Fills 28-32: skip", item="LAV-08"
)
ABUELA = lesson("abuela", "abuela uncommon LAV-06: we bid 15 to 22 vs asks 29 to 23; deal at 22", item="LAV-06")
OPENING = lesson("abuela", "abuela common LAV-03: took her opening ask 7; counter at least once first", item="LAV-03")
DUEL = lesson("rival_verde", "Duel 85 as seller vs Rival Verde: deal after 7 rounds", subject_kind="rival")
OTHER_TEAM = lesson("abuela", "abuela uncommon LAV-06 lesson that binds another team", team="t09")
EXPIRED = lesson("abuela", "abuela cooloff with us", kind="cooloff", until=90)


def recall_over(*learned, models=None):
    store = LearningStore()
    store.record(learned)
    return HybridRecall(store, models or FakeModels())


def test_tokens_keep_card_refs_and_their_parts():
    assert tokens("Buy LAV-06 from the dealer, card:uncommon") == [
        "buy",
        "lav-06",
        "lav",
        "06",
        "dealer",
        "card:uncommon",
        "card",
        "uncommon",
    ]


def test_bm25_ranks_the_lesson_that_shares_rare_terms_first():
    docs = [doc_text(x) for x in (CHATO, ABUELA, OPENING)]
    index = BM25.build(docs)
    assert index.ranked("chato LAV-08 uncommon", 3)[0] == 0
    assert index.ranked("opening ask", 3) == [2]
    assert index.ranked("nothing matches zzz", 3) == []
    assert BM25.build([]).ranked("x", 3) == []


def test_reciprocal_rank_fusion_rewards_agreement():
    fused = fuse([["a", "b", "c"], ["b", "a"]])
    assert fused["a"] == fused["b"] > fused["c"]
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62)


def test_recall_returns_the_relevant_lesson_first_and_only_relevant_ones():
    r = recall_over(CHATO, ABUELA, OPENING, DUEL)
    found = r.search(Query("buy LAV-08 uncommon from chato, his ask 33", team=US, tick=120, k=3))
    assert found.status == "ok" and found.hits[0].learning == CHATO
    assert all(h.score >= 0 for h in found.hits)
    assert found.hits[0].bm25_rank == 1 and found.legs["bm25"] >= 1
    unrelated = r.search(Query("weather in Paris tomorrow", team=US, tick=120))
    assert unrelated.hits == ()


def test_hard_filters_drop_other_teams_expired_and_unwanted_kinds_and_subjects():
    r = recall_over(CHATO, ABUELA, OTHER_TEAM, EXPIRED, DUEL)
    found = r.search(Query("abuela uncommon LAV-06 cooloff", team=US, tick=120, kinds=None, k=10, min_score=-99))
    texts = {h.learning.text for h in found.hits}
    assert OTHER_TEAM.text not in texts and EXPIRED.text not in texts and ABUELA.text in texts
    only = r.search(Query("abuela uncommon LAV-06", subjects=("chato",), team=US, tick=120, k=10, min_score=-99))
    assert {h.learning.subject for h in only.hits} == {"chato"}
    duels = r.search(Query("duel seller rounds", kinds=("lesson",), subject_kind="rival", team=US, tick=120))
    assert [h.learning for h in duels.hits] == [DUEL]


def test_no_lessons_while_the_models_load_and_on_any_failure():
    loading = recall_over(CHATO, models=FakeModels(ready=False))
    assert loading.search(Query("chato LAV-08", team=US)).status == "bm25_only"
    assert loading.recall(Query("chato LAV-08", team=US)).hits == ()
    broken = recall_over(CHATO, models=FakeModels(fail=True))
    out = broken.recall(Query("chato LAV-08", team=US))
    assert out.hits == () and out.status == "error:RuntimeError"
    assert recall_over().recall(Query("anything", team=US)).status == "no_candidates"


def test_a_slow_recall_times_out_inside_the_budget_and_returns_nothing():
    logged: list[str] = []
    r = recall_over(CHATO, models=FakeModels(delay=0.5))
    r.log = logged.append
    started = time.monotonic()
    out = r.recall(Query("chato LAV-08", team=US, budget_s=0.05))
    assert out.status == "timeout" and out.hits == () and time.monotonic() - started < 0.4
    r.recall(Query("chato LAV-08", team=US, budget_s=0.05))
    assert logged == ["learnings: recall timeout; deciding without lessons"]  # once per kind of failure
    r.close()


def test_hits_are_quoted_data_with_relevance():
    r = recall_over(CHATO, ABUELA)
    quoted = r.search(Query("chato LAV-08 uncommon", team=US, tick=120)).as_quoted()
    assert quoted[0]["quoted_lesson"] == CHATO.text and quoted[0]["about"] == "chato"
    assert set(quoted[0]) == {"quoted_lesson", "about", "kind", "source", "tick", "relevance", "confidence"}
    assert quoted[0]["source"] == "outcome"


def test_only_the_fused_top_reaches_the_reranker():
    many = [lesson("abuela", f"abuela uncommon lesson number {i} LAV-06", tick=i) for i in range(40)]
    models = FakeModels()
    recall_over(*many, models=models).search(Query("abuela uncommon LAV-06", team=US, tick=120))
    assert models.reranked == [12]


def test_local_models_fail_open_when_fastembed_cannot_load(monkeypatch, tmp_path):
    monkeypatch.delenv("BAZAAR_MODELS", raising=False)  # this test loads (fastembed faked)
    import builtins

    real_import = builtins.__import__

    def no_fastembed(name, *args, **kwargs):
        if name.startswith("fastembed"):
            raise ImportError("no wheel")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_fastembed)
    logged: list[str] = []
    models = LocalModels(tmp_path, logged.append)
    assert models.load() is False and not models.ready and models.status.startswith("off")
    assert models.embed(["x"]) is None and models.embed_query("x") is None and models.rerank("q", ["d"]) is None
    assert "unavailable" in logged[0]
    models.warm()  # a failed load is never retried in a loop
    assert models._loading is None


def test_local_models_warm_in_the_background_without_blocking(monkeypatch, tmp_path):
    monkeypatch.delenv("BAZAAR_MODELS", raising=False)  # this test loads (fastembed faked)
    gate = threading.Event()
    models = LocalModels(tmp_path)

    def slow_load() -> bool:
        gate.wait(2)
        return False

    monkeypatch.setattr(models, "load", slow_load)
    started = time.monotonic()
    models.warm()
    models.warm()  # idempotent
    assert time.monotonic() - started < 0.2 and models.status == "loading"
    gate.set()


def test_vector_literal_is_pgvector_text():
    assert vector_literal([0.5, 1, -0.25]) == "[0.5,1,-0.25]"


# ---------------------------------------------------------------- Postgres (throwaway schema, pgvector)


@pytest.mark.integration
def test_embeddings_and_the_vector_leg_through_postgres(database_url, schema):  # noqa: F811
    from bazaar_agent import db
    from tests.test_db import open_in

    store = LearningStore(lambda: open_in(database_url, schema), init_schema=db.init_schema)
    store.record([CHATO, ABUELA, OPENING, OTHER_TEAM])
    with open_in(database_url, schema) as conn:
        if db.pgvector_version(conn) is None:
            pytest.skip("pgvector not installed on this Postgres")
    models = FakeModels()
    assert store.embed_missing(models.embed) == 4
    assert store.embed_missing(models.embed) == 0  # nothing changed: nothing re-embedded
    ranked = store.vector_rank(
        models.embed_query("chato LAV-08 uncommon"),
        kinds=("lesson",),
        tick=120,
        subject_kind=None,
        team=US,
        subjects=None,
        limit=10,
    )
    assert ranked[0][0] == CHATO.key() and OTHER_TEAM.key() not in {k for k, _ in ranked}
    reader = HybridRecall(LearningStore(lambda: open_in(database_url, schema)), models)
    found = reader.search(Query("chato LAV-08 uncommon", team=US, tick=120))
    assert found.hits[0].learning.key() == CHATO.key() and found.hits[0].vector_rank == 1
    assert found.legs["vector"] == 3
    edited = CHATO.model_copy(update={"text": CHATO.text + " (edited)"})
    store.record([edited])
    assert store.embed_missing(models.embed) == 1  # the claim changed: embedded again
    reader.close()
    store.close()


class _FakeEmbedding:
    calls = 0

    def __init__(self, model_name, cache_dir=None, threads=None):
        self.model_name = model_name

    def embed(self, texts, batch_size=32):
        return ([float(len(t)), 1.0] for t in texts)

    def query_embed(self, text):
        _FakeEmbedding.calls += 1
        return iter([[float(len(text)), 0.0]])


class _FakeCrossEncoder:
    def __init__(self, model_name, cache_dir=None, threads=None):
        self.model_name = model_name

    def rerank(self, query, docs, batch_size=32):
        if query == "boom":
            raise RuntimeError("onnx")
        return (float(len(d)) for d in docs)


def test_local_models_load_embed_cache_and_rerank_with_fastembed_faked(monkeypatch, tmp_path):
    monkeypatch.delenv("BAZAAR_MODELS", raising=False)  # this test loads (fastembed faked)
    import fastembed
    import fastembed.rerank.cross_encoder as xenc

    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeEmbedding)
    monkeypatch.setattr(xenc, "TextCrossEncoder", _FakeCrossEncoder)
    logged: list[str] = []
    models = LocalModels(tmp_path, logged.append, threads=1)
    assert models.status == "not loaded" and models.embed(["x"]) is None
    assert models.load() is True and models.ready and models.load() is True
    assert models.status.startswith("ready")
    assert models.embed(["ab", "abc"]) == [[2.0, 1.0], [3.0, 1.0]] and models.embed([]) == []
    _FakeEmbedding.calls = 0
    assert models.embed_query("abuela") == [6.0, 0.0] and models.embed_query("abuela") == [6.0, 0.0]
    assert _FakeEmbedding.calls == 1  # the second identical query came from the cache
    assert models.rerank("q", ["a", "bbb"]) == [1.0, 3.0] and models.rerank("q", []) == []
    assert models.rerank("boom", ["a"]) is None and "reranking failed" in logged[-1]


def test_model_dir_comes_from_the_env_or_the_data_dir(monkeypatch, tmp_path):
    from bazaar_agent.learn.embed import MODEL_DIR_ENV, model_dir, shared_models

    monkeypatch.setenv(MODEL_DIR_ENV, str(tmp_path / "m"))
    assert model_dir() == tmp_path / "m"
    monkeypatch.delenv(MODEL_DIR_ENV)
    assert model_dir(tmp_path) == tmp_path / "models"
    assert shared_models() is shared_models()


# ---------------------------------------------------------------- review fixes (PR #96)


def test_by_default_only_our_outcome_rows_are_recalled_feed_rows_are_opt_in():
    feed_row = Learning(
        subject_kind="dealer",
        subject="chato",
        kind="behaviour",
        tick=110,
        confidence=0.9,
        source="rules",
        text="chato LAV-08 uncommon behaviour quoted from the feed",
        detail={"aggregate": "x"},
    )
    r = recall_over(CHATO, feed_row)
    hits = r.search(Query("chato LAV-08 uncommon", team=US, tick=120, k=5, min_score=-99)).hits
    assert [h.learning for h in hits] == [CHATO]
    both = r.search(Query("chato LAV-08 uncommon", team=US, tick=120, k=5, min_score=-99, sources=None)).hits
    assert {h.learning.source for h in both} == {"outcome", "rules"}


def test_many_rows_about_other_subjects_never_push_the_relevant_lesson_out():
    junk = [lesson("abuela", f"abuela common filler lesson {i}", tick=1000 + i) for i in range(700)]
    r = recall_over(CHATO, *junk)
    found = r.search(Query("buy LAV-08 uncommon from chato", subjects=("chato",), team=US, tick=2000))
    assert found.hits and found.hits[0].learning == CHATO and found.candidates == 1


def test_a_failed_model_load_is_retried_after_some_warms(monkeypatch, tmp_path):
    monkeypatch.delenv("BAZAAR_MODELS", raising=False)  # this test loads (fastembed faked)
    from bazaar_agent.learn import embed

    models = LocalModels(tmp_path)
    loads: list[int] = []

    def load() -> bool:
        loads.append(1)
        models._failed = "no network"
        return False

    monkeypatch.setattr(models, "load", load)
    models.load()
    for _ in range(embed.RETRY_AFTER_WARMS - 1):
        models.warm()
    assert len(loads) == 1  # not yet
    models.warm()
    models._loading.join(2)  # type: ignore[union-attr]
    assert len(loads) == 2


def test_model_threads_env_is_parsed_safely(monkeypatch):
    from bazaar_agent.learn.embed import THREADS_ENV, _threads_from_env

    for raw, want in (("", 1), ("abc", 1), ("2", 2), ("99", 8), ("-1", 1)):
        monkeypatch.setenv(THREADS_ENV, raw)
        assert _threads_from_env() == want


@pytest.mark.integration
def test_the_sql_candidates_apply_every_filter_before_the_limit(database_url, schema):  # noqa: F811
    from bazaar_agent import db
    from tests.test_db import open_in

    store = LearningStore(lambda: open_in(database_url, schema), init_schema=db.init_schema)
    junk = [lesson("abuela", f"abuela common filler lesson {i}", tick=1000 + i) for i in range(50)]
    store.record([CHATO, *junk])
    reader = LearningStore(lambda: open_in(database_url, schema))
    pool = reader.candidates(
        kinds=("lesson",),
        subjects=("chato",),
        sources=("outcome",),
        subject_kind=None,
        team=US,
        tick=2000,
        where=(("item", "LAV-08"),),
        limit=5,
    )
    assert [lr.key() for lr in pool] == [CHATO.key()]
    assert (
        reader.candidates(kinds=None, subjects=None, sources=("llm",), subject_kind=None, team=None, tick=None, limit=5)
        == []
    )
    store.close()
    reader.close()


@pytest.mark.integration
def test_a_claim_edited_while_it_was_embedded_is_embedded_again(database_url, schema):  # noqa: F811
    from bazaar_agent import db
    from tests.test_db import open_in

    store = LearningStore(lambda: open_in(database_url, schema), init_schema=db.init_schema)
    store.record([CHATO])
    with open_in(database_url, schema) as conn:
        if db.pgvector_version(conn) is None:
            pytest.skip("pgvector not installed on this Postgres")

    def embed_while_edited(texts):
        with open_in(database_url, schema) as other:
            other.execute("update learnings set claim = claim || ' (edited)'")
        return FakeModels().embed(texts)

    assert store.embed_missing(embed_while_edited) == 1
    with open_in(database_url, schema) as conn:
        row = conn.execute("select embedded_hash is null, embedding is null from learnings").fetchone()
    assert row == (True, True)  # the stale vector was not written
    assert store.embed_missing(FakeModels().embed) == 1
    store.close()


def test_without_the_reranker_recall_is_bm25_only_above_a_lexical_floor():
    filler = [lesson("abuela", f"abuela common filler lesson number {i} for SAL-0{i % 5}", tick=i) for i in range(40)]
    logged: list[str] = []
    r = recall_over(CHATO, *filler, models=FakeModels(ready=False))
    r.log = logged.append
    found = r.recall(Query("buy LAV-08 uncommon from chato, his ask 33", team=US, tick=120))
    assert found.status == "bm25_only" and found.hits[0].learning == CHATO and found.hits[0].score >= 5.0
    assert r.recall(Query("weather in Paris tomorrow", team=US, tick=120)).hits == ()
    r.recall(Query("chato LAV-08", team=US, tick=120))
    assert logged == ["learnings: the reranker is not ready; recall is BM25-only (lexical floor) until it is"]
    from bazaar_agent.learn.recall import Lessons

    lessons = Lessons(r)
    assert lessons("buy LAV-08 uncommon from chato", tick=120)[0]["about"] == "chato"
    assert len(lessons._cache) == 1  # cached for its tick bucket only: the reranker may be ready a few ticks on


def test_bazaar_models_off_never_loads_nor_warms(monkeypatch, tmp_path):
    import fastembed

    def boom(*args, **kwargs):
        raise AssertionError("fastembed must not be touched")

    monkeypatch.setattr(fastembed, "TextEmbedding", boom)
    models = LocalModels(tmp_path)
    assert models.load() is False and models.status.startswith("off")
    models.warm()
    assert models._loading is None and not models.ready
