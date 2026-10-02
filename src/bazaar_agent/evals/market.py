"""Market Test outcomes. A STUB until Team 1 runs a venue (RULES.md "Your own market").

The Market Test scores the share of the possible gains a venue's broker realises on a synthetic
book; value created between other teams on our venue scores too. Neither is computable from our
side: the organisers report the efficiency in `/me` → `score.bench_efficiency` (null while we have
no venue), so this scorer records that official number once per day and nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bazaar_agent.evals.model import Outcome, clamp01, label_for


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def score_market_test(score: Mapping[str, Any] | None, tick: int, day: str | None) -> Outcome | None:
    """The day's Market Test from a `/me` score block, or None while there is nothing to score."""
    efficiency = _num((score or {}).get("bench_efficiency"))
    if efficiency is None:
        return None
    s = score or {}
    details = {
        "bench_efficiency": efficiency,
        "bench_points": _num(s.get("bench_points")),
        "bench_venue": s.get("bench_venue"),
        "venue": s.get("venue"),
        "mm_points": _num(s.get("mm_points")),
        "source": "official /me score",
    }
    value = clamp01(efficiency)
    venue = s.get("bench_venue") or s.get("venue")
    text = f"Market Test efficiency {value:.0%} (official, /me at tick {tick}) on venue {venue}."
    return Outcome(
        "market_test",
        f"market_test:{day or 'unknown'}",
        round(value, 4),
        label_for(value),
        text,
        tick,
        day,
        details=details,
    )
