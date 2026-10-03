"""Local CPU models for the learnings RAG: a 384-d text embedder and a cross-encoder reranker (fastembed).

Models (verified in fastembed 0.8.1's `list_supported_models()`, 2026-10-03):
- `BAAI/bge-small-en-v1.5`: 384-d embeddings, 0.067 GB. Our lessons and queries are English text we
  write ourselves, so the small English model beats the multilingual one on speed and size.
- `Xenova/ms-marco-MiniLM-L-6-v2`: a cross-encoder reranker, 0.08 GB.
Measured on an M-series laptop, one thread: a query embedding ~3 ms, reranking 20 lessons ~45 ms.

Nothing here may hold a tick: the first use downloads the models (~150 MB, seconds), so `warm()` loads
them on a background thread and every call answers None until they are ready. Any failure is logged
once and answers None: the caller then works without lessons (fail open), never with a wrong one.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
DIM = 384
THREADS_ENV = "BAZAAR_MODEL_THREADS"  # onnxruntime threads per model (default 1: agents share the CPU)
MODEL_DIR_ENV = "BAZAAR_MODEL_DIR"  # where the models are cached (default <data_dir>/models)
QUERY_CACHE = 256


class Models(Protocol):
    """What the recall and the learner need from the models (a fake in tests)."""

    @property
    def ready(self) -> bool: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None: ...

    def embed_query(self, text: str) -> list[float] | None: ...

    def rerank(self, query: str, docs: Sequence[str]) -> list[float] | None: ...


def model_dir(data_dir: Path | None = None) -> Path:
    configured = os.environ.get(MODEL_DIR_ENV)
    if configured:
        return Path(configured)
    if data_dir is None:
        from bazaar_agent.config import load_settings

        data_dir = load_settings().data_dir
    return data_dir / "models"


RETRY_AFTER_WARMS = 20  # a failed load is tried again after this many warm() calls (the learner warms per pass)


def _threads_from_env() -> int:
    raw = os.environ.get(THREADS_ENV, "").strip()
    return min(8, max(1, int(raw))) if raw.isdigit() else 1


class LocalModels:
    """fastembed models, loaded once per process on a background thread."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        log: Callable[[str], None] = lambda message: None,
        threads: int | None = None,
    ) -> None:
        self._cache_dir, self._log = cache_dir, log
        self._threads = threads or _threads_from_env()
        self._embedder: Any = None
        self._reranker: Any = None
        self._lock = threading.Lock()  # onnxruntime sessions are not shared across concurrent calls here
        self._loading: threading.Thread | None = None
        self._failed: str | None = None
        self._warms_since_failure = 0
        self._queries: OrderedDict[str, list[float]] = OrderedDict()

    @property
    def ready(self) -> bool:
        return self._embedder is not None and self._reranker is not None

    @property
    def status(self) -> str:
        if self.ready:
            return f"ready ({EMBED_MODEL} + {RERANK_MODEL})"
        if self._failed:
            return f"off ({self._failed})"
        return "loading" if self._loading is not None else "not loaded"

    def warm(self) -> None:
        """Start loading the models in the background (idempotent). After a failed load (no network at
        boot), every RETRY_AFTER_WARMS-th call tries again."""
        if self._failed is not None and not self.ready and (self._loading is None or not self._loading.is_alive()):
            self._warms_since_failure += 1
            if self._warms_since_failure < RETRY_AFTER_WARMS:
                return
            self._failed, self._loading, self._warms_since_failure = None, None, 0
        if self.ready or self._failed or self._loading is not None:
            return
        self._loading = threading.Thread(target=self.load, name="bazaar-models", daemon=True)
        self._loading.start()

    def load(self) -> bool:
        """Load both models now (blocking: the CLI and the background warm-up use it)."""
        if self.ready:
            return True
        try:
            from fastembed import TextEmbedding
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            cache = str(self._cache_dir or model_dir())
            embedder = TextEmbedding(EMBED_MODEL, cache_dir=cache, threads=self._threads)
            reranker = TextCrossEncoder(RERANK_MODEL, cache_dir=cache, threads=self._threads)
        except Exception as e:  # no network, no disk, a missing wheel: lessons are off, trading is not
            self._failed = f"{type(e).__name__}: {str(e)[:120]}"
            self._log(f"learnings: local models unavailable ({self._failed}); recall without lessons")
            return False
        with self._lock:
            self._embedder, self._reranker = embedder, reranker
        self._log(f"learnings: models ready ({EMBED_MODEL}, {RERANK_MODEL})")
        return True

    def _fail(self, what: str, error: Exception) -> None:
        if self._failed is None:
            self._log(f"learnings: {what} failed ({type(error).__name__}); recall without lessons")
        self._failed = f"{what}: {type(error).__name__}"

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        if not self.ready or not texts:
            return None if not self.ready else []
        try:
            with self._lock:
                return [[float(x) for x in v] for v in self._embedder.embed(list(texts), batch_size=32)]
        except Exception as e:
            self._fail("embedding", e)
            return None

    def embed_query(self, text: str) -> list[float] | None:
        if not self.ready:
            return None
        cached = self._queries.get(text)
        if cached is not None:
            self._queries.move_to_end(text)
            return cached
        try:
            with self._lock:
                vector = [float(x) for x in next(iter(self._embedder.query_embed(text)))]
        except Exception as e:
            self._fail("query embedding", e)
            return None
        self._queries[text] = vector
        if len(self._queries) > QUERY_CACHE:
            self._queries.popitem(last=False)
        return vector

    def rerank(self, query: str, docs: Sequence[str]) -> list[float] | None:
        if not self.ready:
            return None
        if not docs:
            return []
        try:
            with self._lock:
                return [float(s) for s in self._reranker.rerank(query, list(docs), batch_size=32)]
        except Exception as e:
            self._fail("reranking", e)
            return None


_shared: LocalModels | None = None
_shared_lock = threading.Lock()


def shared_models(log: Callable[[str], None] = lambda message: None) -> LocalModels:
    """One model pair per process (the taker, the duel player and the maker share the CPU)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = LocalModels(log=log)
        return _shared
