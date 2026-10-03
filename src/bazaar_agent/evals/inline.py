"""Evals inside the agents: each tick-driven agent scores its own settled decisions (Omar, 2026-10-03).

No Railway evals service. The duel player, the taker and the maker call `TickEvals.after_tick(tick)` once
their tick has sent everything. Every `every_ticks` ticks that starts ONE pass on a daemon thread, for
that agent's targets only:

- the duel player: duels;
- the taker: the dealer ladder and every team trade (its own accepts and the maker's fills);
- the maker: the Market Test.

A pass reads Postgres and writes `outcomes` and Phoenix annotations: no game call, so the key's 5 req/s
budget is untouched. It never delays a move: it starts after the tick's sends, runs off the tick thread,
is skipped while the previous pass still runs, and any error is logged and dropped (fail open).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection
from typing import Any

import psycopg

from bazaar_agent.evals.phoenix import PhoenixAnnotator
from bazaar_agent.evals.run import run_once

TARGETS_BY_AGENT: dict[str, frozenset[str]] = {
    "duels": frozenset({"duel"}),
    "taker": frozenset({"dealer", "trade"}),
    "maker": frozenset({"market_test"}),
}


def _daemon(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="bazaar-evals", daemon=True).start()


class TickEvals:
    """The agent's eval pass, every `every_ticks` ticks counted from the first tick it sees (0 = off)."""

    def __init__(
        self,
        agent: str,
        every_ticks: int,
        connect: Callable[[], psycopg.Connection],
        team: Callable[[psycopg.Connection], str | None],
        annotator: Callable[[], PhoenixAnnotator | None],
        log: Callable[[str], None],
        start: Callable[[Callable[[], None]], Any] = _daemon,
    ) -> None:
        self.agent, self.every_ticks, self.targets = agent, every_ticks, TARGETS_BY_AGENT.get(agent, frozenset())
        self._connect, self._team, self._annotator, self._log, self._start = connect, team, annotator, log, start
        self._last: int | None = None
        self._running = threading.Event()

    def after_tick(self, tick: int) -> bool:
        """Call at the end of a tick. True when a pass started (it runs in the background)."""
        if self.every_ticks <= 0 or not self.targets:
            return False
        if self._last is None:
            self._last = tick  # the first pass comes `every_ticks` ticks after start, not at boot
            return False
        if tick - self._last < self.every_ticks or self._running.is_set():
            return False
        self._last = tick
        self._running.set()
        try:
            self._start(self._pass)
        except Exception as e:  # noqa: BLE001 - a thread that cannot start costs this pass, never the tick
            self._running.clear()
            self._log(f"evals ({self.agent}): could not start a pass ({type(e).__name__})")
            return False
        return True

    def _pass(self) -> None:
        try:
            self._score(self.targets)
        except Exception as e:  # noqa: BLE001 - evals are a side record: they never stop an agent
            self._log(f"evals ({self.agent}): pass failed ({type(e).__name__}); next one in {self.every_ticks} ticks")
        finally:
            self._running.clear()

    def _score(self, targets: Collection[str]) -> None:
        annotator = self._annotator()
        try:
            with self._connect() as conn:
                summary = run_once(conn, self._team(conn), annotator=annotator, warn=self._log, targets=targets)
        finally:
            if annotator is not None:
                annotator.close()
        scored = ", ".join(f"{k} {v}" for k, v in sorted(summary.scored.items())) or "nothing settled yet"
        self._log(
            f"evals ({self.agent}): {scored} · {summary.changed} new/changed · phoenix {summary.phoenix}: "
            f"{summary.annotated} annotated"
        )
