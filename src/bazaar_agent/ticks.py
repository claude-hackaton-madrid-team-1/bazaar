"""Tick discipline: every loop sleeps by the game clock, never by wall-clock time.

The server ticks every 60 s (Fri), 30 s (Sat) or 15 s (Sun) and the organisers may change it
(5-60 s), pause the clock, or close the doors. `GET /api/clock` is the only source of truth.
The one exception: with the doors closed, `next_opens` is a wall-clock time, so the sleep until it uses ours,
bounded to 1-300 s; a skewed clock moves the wake-up by the skew (or a 5 s poll), never by minutes.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from bazaar_agent.jev.decider import note_tick_seconds

AFTER_TICK_S = 0.3  # settle margin after a tick lands, so reads see the settled state
PAUSED_POLL_S = 5.0
CLOSED_POLL_MAX_S = 300.0
CLOSED_POLL_MIN_S = 1.0
MIN_SLEEP_S = 0.05
# Opt-in stagger (`bazaar budget --stagger`): each service wakes this many seconds later than the tick,
# so not every loop spends the key's burst of 20 at the same instant. Unset or 0: today's timing.
TICK_OFFSET_ENV = "BAZAAR_TICK_OFFSET_S"
MAX_OFFSET_SHARE = 0.4  # never wake later than 40 % into a tick: a 15 s tick keeps 9 s for the work
MAX_OFFSET_S = 10.0  # and never later than 10 s, whatever the pace (a 60 s tick still starts its work early)


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
    max_tick_seconds: float = 60.0  # the slowest pace the organisers may set (RULES.md: 5-60 s)
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


def tick_offset_from_env(environ: Mapping[str, str] | None = None) -> float:
    """`BAZAAR_TICK_OFFSET_S` in seconds (environment, then `.env`; 0 when unset). Not a number ≥ 0: fails fast."""
    if environ is None:
        from bazaar_agent.config import REPO_ROOT, read_env_file

        environ = {**read_env_file(REPO_ROOT / ".env"), **os.environ}
    raw = environ.get(TICK_OFFSET_ENV, "").strip()
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{TICK_OFFSET_ENV}={raw!r}: expected seconds ≥ 0 (unset for today's timing)")
    return value


def _epoch(stamp: Any) -> float | None:
    """An ISO timestamp with a zone as epoch seconds; None when missing, malformed or naive."""
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return parsed.timestamp() if parsed.tzinfo is not None else None


def _until_opening(clock: Clock, now: float) -> float:
    """Doors closed: sleep until just after the next opening, polling at least every CLOSED_POLL_MAX_S.

    The next opening is the earliest of `next_opens` and the future `opens` in `days`. Once the announced
    opening has passed (our clock ahead of the server's, a late opening), or while `now` is inside one of
    the day windows (the doors should be open; `next_opens` may already point to the next day), poll like
    a pause. Without a usable timestamp, poll every CLOSED_POLL_MAX_S.
    """
    days = (clock.model_extra or {}).get("days")
    upcoming = []
    for day in days if isinstance(days, list) else []:
        if not isinstance(day, dict):
            continue
        opens, closes = _epoch(day.get("opens")), _epoch(day.get("closes"))
        if opens is not None and closes is not None and opens <= now < closes:
            return PAUSED_POLL_S
        if opens is not None and opens > now:
            upcoming.append(opens)
    next_opens = _epoch(clock.next_opens)
    if next_opens is not None and next_opens <= now:
        return PAUSED_POLL_S
    if next_opens is not None:
        upcoming.append(next_opens)
    if not upcoming:
        return CLOSED_POLL_MAX_S
    return min(CLOSED_POLL_MAX_S, max(CLOSED_POLL_MIN_S, min(upcoming) - now + AFTER_TICK_S))


def seconds_until_next_tick(clock: Clock, offset_s: float = 0.0, *, now: float | None = None) -> float:
    """How long to sleep so the next read lands just after the next tick (or the next poll).

    `offset_s` delays the wake-up after a tick further (the stagger), never past 40 % of the tick nor 10 s.
    `now` (epoch seconds, default the wall clock) only matters while the doors are closed."""
    if clock.doors != "open":
        try:
            return _until_opening(clock, time.time() if now is None else now)
        except Exception:  # a bad timestamp must never stop a loop: fall back to the slow poll
            return CLOSED_POLL_MAX_S
    if clock.paused:
        return PAUSED_POLL_S
    offset = min(max(0.0, offset_s), MAX_OFFSET_S, clock.tick_seconds * MAX_OFFSET_SHARE)
    return max(MIN_SLEEP_S, clock.next_tick_in) + AFTER_TICK_S + offset


def action_budget_s(clock: Clock, safety_margin_s: float = 2.0) -> float:
    """Time left in this tick for a decision. 0 means: too late, drop it and wait for the next tick.

    The margin scales down on fast ticks so a 15 s Sunday tick still leaves room to act.
    """
    if not clock.is_live:
        return 0.0
    margin = min(safety_margin_s, clock.tick_seconds * 0.15)
    return max(0.0, clock.next_tick_in - margin)


ERROR_BACKOFF_MAX_S = 60.0


def _report(stage: str, error: BaseException) -> None:
    """Default error sink: one line plus the traceback on stderr (Railway and terminals keep it)."""
    import sys
    import traceback

    print(f"tick loop: {stage} failed ({type(error).__name__}: {error}); continuing", file=sys.stderr)
    traceback.print_exception(error, file=sys.stderr)


def run_per_tick(
    read_clock: Callable[[], dict[str, Any]],
    on_tick: Callable[[Clock], None],
    *,
    max_ticks: int | None = None,
    stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_error: Callable[[str, BaseException], None] = _report,
    start_offset_s: float | None = None,
) -> int:
    """Call `on_tick` once per new live tick until `max_ticks` or `stop()`. Returns ticks handled.

    One call owns the tick bookkeeping: never call this in a loop with max_ticks=1, or the same
    tick is handled again on every call.

    Unattended loops must survive the network: a failed clock read is reported and retried with
    exponential backoff (1 s → 60 s), and a failed tick is reported and counted as handled so it is
    never retried in a burst. Only KeyboardInterrupt / SystemExit stop the loop.

    `start_offset_s` (else `BAZAAR_TICK_OFFSET_S`, else 0) wakes the loop that much later after each
    tick: one value per service staggers their calls (`rate_budget.PROPOSED_STAGGER`).
    """
    offset = tick_offset_from_env() if start_offset_s is None else start_offset_s
    handled, last_tick, failures = 0, None, 0
    while (max_ticks is None or handled < max_ticks) and not (stop and stop()):
        try:
            clock = Clock.model_validate(read_clock())
        except Exception as error:  # DNS, Wi-Fi, a server restart, a malformed body
            failures += 1
            on_error("clock read", error)
            sleep(min(ERROR_BACKOFF_MAX_S, 2.0 ** (failures - 1)))
            continue
        failures = 0
        note_tick_seconds(clock.tick_seconds if clock.is_live else None)
        started = time.monotonic()
        if clock.is_live and clock.tick != last_tick:
            try:
                on_tick(clock)
            except Exception as error:
                on_error(f"tick {clock.tick}", error)
            last_tick, handled = clock.tick, handled + 1
            if (max_ticks is not None and handled >= max_ticks) or (stop and stop()):
                break
        # The clock was read before on_tick did its work: subtract that time, or we oversleep.
        worked = time.monotonic() - started if clock.is_live else 0.0
        sleep(max(MIN_SLEEP_S, seconds_until_next_tick(clock, offset) - worked))
    return handled
