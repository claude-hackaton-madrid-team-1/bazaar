"""Reuse a Jev answer while the state it judged has not changed (SP1, Sunday's 15 s ticks).

The taker asks Jev the same question about the same state tick after tick: the pack gate's
`spend_pack_slot_now` while a pack waits for its slot, `offer_is_worth_accepting` while a board ask stands.
Each call costs ~0.3 s (up to GUARDRAILS.md `jev_timeout_s`) of the tick, plus the lessons recall in front
of it. The clock fields of a state (`tick`, `game_hour`) move every tick without changing the question, so
they are left out of the key; anything else that changes (cash, slots left, the album, the price) is a new
state and a new call. An answer the model gave is kept for `jev_cache_ticks` ticks; a failure (timeout, no
key, an HTTP error) is never kept, so it is asked again next tick. `jev_cache_ticks = 0` asks every time.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

CLOCK_KEYS = frozenset({"tick", "game_hour"})  # move every tick, never change what is asked
CACHED_REASONS = (None, "below_threshold")  # an answer the model gave; a failure is asked again next tick
CACHE_MAX = 512  # entries kept; the expired ones go first, then all of them


def state_key(question: str, state: Mapping[str, Any]) -> str:
    """The question plus the state without its clock fields, as canonical JSON."""
    body = {k: v for k, v in state.items() if k not in CLOCK_KEYS}
    return f"{question}:{json.dumps(body, sort_keys=True, default=str, separators=(',', ':'))}"


def state_tick(state: Mapping[str, Any]) -> int | None:
    tick = state.get("tick")
    return tick if isinstance(tick, int) and not isinstance(tick, bool) else None


class VerdictCache[T]:
    """Answers by state key, each valid for `ticks` ticks from the tick it was asked in."""

    def __init__(self, ticks: int, max_entries: int = CACHE_MAX) -> None:
        self.ticks, self.max_entries = max(0, ticks), max_entries
        self._entries: dict[str, tuple[int, T]] = {}
        self.hits = 0  # answers served without a call (the tick line and the tests read it)

    def get(self, key: str, tick: int | None) -> T | None:
        if self.ticks == 0 or tick is None:
            return None
        hit = self._entries.get(key)
        if hit is None:
            return None
        asked, value = hit
        if not asked <= tick < asked + self.ticks:
            del self._entries[key]
            return None
        self.hits += 1
        return value

    def put(self, key: str, tick: int | None, value: T) -> None:
        if self.ticks == 0 or tick is None:
            return
        if len(self._entries) >= self.max_entries:
            self._entries = {k: v for k, v in self._entries.items() if v[0] + self.ticks > tick}
            if len(self._entries) >= self.max_entries:
                self._entries = {}
        self._entries[key] = (tick, value)
