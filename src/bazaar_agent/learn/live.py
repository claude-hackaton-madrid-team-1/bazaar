"""The live learner: what an agent's tick loop calls (the taker on Railway owns one).

Per tick: read the new feed events (the ones the agent already holds: no extra game call), recall
the blockers in force for us, and, after the tick's sends, write what was learned. Our own closed
threads and refused `open_thread` calls are learned the moment they happen. Every method fails open:
an error is logged and the agent carries on exactly as it would without learnings.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from bazaar_agent.learn.blockers import Blocks, blocks_for
from bazaar_agent.learn.model import BLOCKING_KINDS, Learning
from bazaar_agent.learn.reader import FeedReader, GameHour, from_refusal, from_thread
from bazaar_agent.learn.store import LearningStore

RECALL_LIMIT = 500


def game_hour(clock: Any) -> GameHour:
    return GameHour(int(clock.tick), float(clock.t_hours), float(clock.tick_seconds))


class LiveLearner:
    def __init__(self, store: LearningStore, log: Callable[[str], None] = lambda message: None) -> None:
        self.store, self.log = store, log
        self.reader: FeedReader | None = None
        self.pending: list[Learning] = []
        self._failed: set[str] = set()

    def _fail(self, what: str, error: Exception) -> None:
        if what not in self._failed:  # once per kind of failure: a broken learner must not flood the log
            self.log(f"learnings: {what} failed ({type(error).__name__}: {str(error)[:80]}); trading as before")
        self._failed.add(what)

    def blocks(self, events: Iterable[dict[str, Any]], us: str, clock: Any) -> Blocks:
        """Read the new events, then the blockers in force for us at this tick. Empty on any error."""
        try:
            self.store.begin_tick(int(clock.tick))
            if self.reader is None or self.reader.us != us:
                self.reader = FeedReader(us)
            self.pending += self.reader.read(events, game_hour(clock))
            self.store.memory.update({lr.key(): lr for lr in self.pending})  # in force before the write
            dealer_facts = self.store.recall(tick=int(clock.tick), subject_kind="dealer", team=us, limit=RECALL_LIMIT)
            return blocks_for(dealer_facts, us, int(clock.tick))
        except Exception as e:
            self._fail("recall", e)
            return Blocks()

    def thread_closed(self, thread: Mapping[str, Any], us: str, clock: Any) -> Learning | None:
        return self._learn("thread", lambda: from_thread(thread, us, game_hour(clock)))

    def refused(self, dealer: str, error: Any, us: str, clock: Any, item: str | None) -> Learning | None:
        """An `open_thread` refusal (a `BazaarError`: code, message, extra)."""

        def read() -> Learning | None:
            extra = getattr(error, "extra", None)
            return from_refusal(
                dealer,
                str(getattr(error, "code", "")),
                str(getattr(error, "message", "")),
                extra if isinstance(extra, dict) else {},
                us,
                game_hour(clock),
                item,
            )

        return self._learn("refusal", read)

    def _learn(self, what: str, read: Callable[[], Learning | None]) -> Learning | None:
        try:
            learned = read()
        except Exception as e:
            self._fail(what, e)
            return None
        if learned is not None:
            self.pending.append(learned)
            self.store.memory[learned.key()] = learned
            if learned.kind in BLOCKING_KINDS:
                self.log(f"learned: {learned.text}")
        return learned

    def flush(self) -> int:
        """Write what this tick learned (after the tick's sends)."""
        batch, self.pending = self.pending, []
        try:
            return self.store.record(batch)
        except Exception as e:
            self._fail("write", e)
            return 0
