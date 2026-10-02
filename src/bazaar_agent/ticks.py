"""Tick discipline: every loop sleeps by the game clock, never by wall-clock time.

The server ticks every 60 s (Fri), 30 s (Sat) or 15 s (Sun) and the organisers may change it
(5-60 s), pause the clock, or close the doors. `GET /api/clock` is the only source of truth.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

AFTER_TICK_S = 0.3  # settle margin after a tick lands, so reads see the settled state
PAUSED_POLL_S = 5.0
CLOSED_POLL_MAX_S = 300.0
MIN_SLEEP_S = 0.05


class Limits(BaseModel):
    model_config = ConfigDict(extra="allow")
    accepts_per_team_per_tick: int = 1
    messages_per_side_per_tick: int = 1
    max_open_threads_per_team: int = 6
    max_open_offers_per_team: int = 30
    offers_per_team_per_tick: int = 12


class Clock(BaseModel):
    model_config = ConfigDict(extra="allow")
    tick: int
    tick_seconds: float = 60.0
    next_tick_in: float = 1.0
    paused: bool = False
    doors: str = "open"
    t_hours: float = 0.0
    round: int | None = None
    round_name: str | None = None
    closes: str | None = None
    next_opens: str | None = None
    limits: Limits = Limits()

    @property
    def is_live(self) -> bool:
        return self.doors == "open" and not self.paused


def seconds_until_next_tick(clock: Clock) -> float:
    """How long to sleep so the next read lands just after the next tick (or the next poll)."""
    if clock.doors != "open":
        return CLOSED_POLL_MAX_S
    if clock.paused:
        return PAUSED_POLL_S
    return max(MIN_SLEEP_S, clock.next_tick_in) + AFTER_TICK_S


def action_budget_s(clock: Clock, safety_margin_s: float = 2.0) -> float:
    """Time left in this tick for a decision. 0 means: too late, drop it and wait for the next tick.

    The margin scales down on fast ticks so a 15 s Sunday tick still leaves room to act.
    """
    if not clock.is_live:
        return 0.0
    margin = min(safety_margin_s, clock.tick_seconds * 0.15)
    return max(0.0, clock.next_tick_in - margin)


def run_per_tick(
    read_clock: Callable[[], dict[str, Any]],
    on_tick: Callable[[Clock], None],
    *,
    max_ticks: int | None = None,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Call `on_tick` once per new live tick until `max_ticks` or `stop()`. Returns ticks handled.

    One call owns the tick bookkeeping: never call this in a loop with max_ticks=1, or the same
    tick is handled again on every call.
    """
    handled, last_tick = 0, None
    while (max_ticks is None or handled < max_ticks) and not (stop and stop()):
        clock = Clock.model_validate(read_clock())
        started = time.monotonic()
        if clock.is_live and clock.tick != last_tick:
            on_tick(clock)
            last_tick, handled = clock.tick, handled + 1
            if (max_ticks is not None and handled >= max_ticks) or (stop and stop()):
                break
        # The clock was read before on_tick did its work: subtract that time, or we oversleep.
        worked = time.monotonic() - started if clock.is_live else 0.0
        sleep(max(MIN_SLEEP_S, seconds_until_next_tick(clock) - worked))
    return handled
