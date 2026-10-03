"""Which decider answers `jev.judge()`: Jev (TypeSafe) or an LLM (Claude), switched by BAZAAR_DECIDER.

`judge()` is the one place every verdict comes from (taker, maker, duels, team desk, pack gate, dealer
buy, the CLI and the model chooser), so the switch lives there and no caller changes. `jev` (the
default, also for an unset or unknown value) is TypeSafe's Jev. `llm` sends the same masked state and
questions to Claude (`bazaar_agent.llm.decider`) and returns the same verdict shape, under the same bars.
Either way a verdict informs a move; guardrails, the ledger and the breakers still gate every send.

Read from the process environment on every call (Railway sets it per service), like TYPESAFE_API_KEY.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from typing import Literal

Decider = Literal["jev", "llm"]

DECIDER_VARIABLE = "BAZAAR_DECIDER"
TIMEOUT_VARIABLE = "BAZAAR_DECIDER_TIMEOUT_S"
DEFAULT_DECIDER: Decider = "jev"
DEFAULT_TIMEOUT_S = 12.0  # an Opus answer through the Claude Code CLI took 6.2-9.1 s (3 live calls, 2026-10-03)
TIMEOUT_RANGE_S = (1.0, 60.0)
BUDGET_MARGIN_S = 1.0  # time left in the tick after the answer, to still send the move
MIN_TICK_VARIABLE = "BAZAAR_DECIDER_MIN_TICK_S"
DEFAULT_MIN_TICK_S = 30.0  # the LLM decider needs a tick this long: its answer takes 6-9 s, the window is ~tick - 4.5 s
MIN_TICK_RANGE_S = (0.0, 3600.0)

_log = logging.getLogger(__name__)
_warned: set[str] = set()
_tick_seconds: float | None = None  # the length of the tick this process runs in (ticks.run_per_tick sets it)


def note_tick_seconds(seconds: float | None) -> None:
    """The tick length the loop is running at; the LLM decider steps aside on ticks too short for it."""
    global _tick_seconds
    _tick_seconds = seconds if seconds is not None and math.isfinite(seconds) and seconds > 0 else None


def decider(environ: Mapping[str, str] | None = None) -> Decider:
    """`llm` only when BAZAAR_DECIDER says so; anything else is Jev, the behaviour before the switch.

    Also Jev when the loop's ticks are shorter than BAZAAR_DECIDER_MIN_TICK_S (default 30 s): on a 15 s tick an
    LLM call (6-9 s, budget 12 s + margin) never fits the window, so every gated move was dropped as "no tick
    budget for jev". Jev answers in ~0.3 s under the same bars. An unknown tick length (a CLI call) keeps the LLM."""
    raw = (os.environ if environ is None else environ).get(DECIDER_VARIABLE, "")
    value = raw.strip().lower()
    if value not in ("", "jev", "llm") and value not in _warned:
        _warned.add(value)  # once per value: a typo must not read as "llm is on"
        _log.warning("%s=%r is neither jev nor llm: Jev decides", DECIDER_VARIABLE, value[:20])
    if value != "llm":
        return DEFAULT_DECIDER
    floor = env_float(MIN_TICK_VARIABLE, DEFAULT_MIN_TICK_S, *MIN_TICK_RANGE_S, environ=environ)
    if _tick_seconds is not None and _tick_seconds < floor:
        if "short-tick" not in _warned:
            _warned.add("short-tick")
            _log.warning(
                "%.0f s ticks are shorter than %s=%.0f: Jev decides instead of the LLM",
                _tick_seconds,
                MIN_TICK_VARIABLE,
                floor,
            )
        return DEFAULT_DECIDER
    return "llm"


def env_float(name: str, default: float, low: float, high: float, environ: Mapping[str, str] | None = None) -> float:
    """A number from the environment, clamped to [low, high]; unset or unreadable is the default."""
    raw = (os.environ if environ is None else environ).get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return min(max(value, low), high) if math.isfinite(value) else default


def llm_timeout_s(environ: Mapping[str, str] | None = None) -> float:
    """The whole budget of one LLM decision (BAZAAR_DECIDER_TIMEOUT_S, default 12 s)."""
    return env_float(TIMEOUT_VARIABLE, DEFAULT_TIMEOUT_S, *TIMEOUT_RANGE_S, environ=environ)


def needed_budget_s(jev_min_budget_s: float, environ: Mapping[str, str] | None = None) -> float:
    """The tick time a caller must have left before asking: its Jev value, or the LLM's timeout + margin."""
    if decider(environ) == "jev":
        return jev_min_budget_s
    return max(jev_min_budget_s, llm_timeout_s(environ) + BUDGET_MARGIN_S)
