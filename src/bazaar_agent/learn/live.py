"""The live learner: what an agent's tick loop calls (the taker on Railway owns one).

Per tick, BEFORE the sends: read the new feed events (the ones the agent already holds: no extra game
call) and answer which dealers are blocked for us, from memory only (no database I/O). AFTER the sends
(`flush`): write what was learned and pull what other processes learned, for the next tick. Our own
closed threads and refused `open_thread` calls are learned the moment they happen. Every method fails
open: an error is logged and the agent carries on exactly as it would without learnings.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from bazaar_agent.learn.blockers import Blocks, blocks_for
from bazaar_agent.learn.model import BLOCKING_KINDS, Learning
from bazaar_agent.learn.reader import FeedReader, GameHour, from_refusal, from_thread
from bazaar_agent.learn.store import LearningStore

RECALL_LIMIT = 500


def game_hour(clock: Any, hours_per_tick: float | None = None) -> GameHour:
    return GameHour(int(clock.tick), float(clock.t_hours), float(clock.tick_seconds), hours_per_tick)


class LiveLearner:
    def __init__(self, store: LearningStore, log: Callable[[str], None] = lambda message: None) -> None:
        self.store, self.log = store, log
        self.reader: FeedReader | None = None
        self.pending: list[Learning] = []
        self._failed: set[str] = set()
        self._last: tuple[int, float] | None = None  # the previous clock reading: (tick, t_hours)
        self._per_tick: float | None = None  # the game-hour pace observed between two readings
        self._us: str | None = None
        self._tick: int | None = None

    def _hour(self, clock: Any) -> GameHour:
        """This tick's game hour, at the pace observed between clock readings (it follows a pace change)."""
        tick, t_hours = int(clock.tick), float(clock.t_hours)
        if self._last is not None and tick > self._last[0] and t_hours > self._last[1]:
            self._per_tick = (t_hours - self._last[1]) / (tick - self._last[0])
        if self._last is None or tick != self._last[0]:
            self._last = (tick, t_hours)
        return game_hour(clock, self._per_tick)

    def _fail(self, what: str, error: Exception) -> None:
        if what not in self._failed:  # once per kind of failure: a broken learner must not flood the log
            self.log(f"learnings: {what} failed ({type(error).__name__}: {str(error)[:80]}); trading as before")
        self._failed.add(what)

    def blocks(self, events: Iterable[dict[str, Any]], us: str, clock: Any) -> Blocks:
        """Read the new events, then the blockers in force for us at this tick. Empty on any error."""
        try:
            tick = int(clock.tick)
            self.store.begin_tick(tick)
            self._us, self._tick = us, tick
            if self.reader is None or self.reader.us != us:
                self.reader = FeedReader(us)
            self.pending += self.reader.read(events, self._hour(clock))
            self.store.remember(self.pending)  # in force before the write
            facts = self.store.recall(tick=tick, subject_kind="dealer", team=us, limit=RECALL_LIMIT, use_db=False)
            return blocks_for(facts, us, tick)
        except Exception as e:
            self._fail("recall", e)
            return Blocks()

    def thread_closed(self, thread: Mapping[str, Any], us: str, clock: Any) -> Learning | None:
        return self._learn("thread", lambda: from_thread(thread, us, self._hour(clock)))

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
                self._hour(clock),
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
            self.store.remember([learned])
            if learned.kind in BLOCKING_KINDS:
                self.log(f"learned: {learned.text}")
        return learned

    def flush(self) -> int:
        """After the tick's sends: write what this tick learned, and pull what is stored about dealers for us
        (another taker process, or `bazaar learnings --save` on a laptop) into memory for the next tick."""
        batch, self.pending = self.pending, []
        try:
            written = self.store.record(batch)
            if self._us is not None:
                self.store.remember(
                    self.store.recall(tick=self._tick, subject_kind="dealer", team=self._us, limit=RECALL_LIMIT)
                )
            return written
        except Exception as e:
            self._fail("write", e)
            return 0
