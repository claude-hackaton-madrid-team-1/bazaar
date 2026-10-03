"""What every scorer returns: one settled decision, its score and a sentence a human can check.

Evals measure what the game scores (RULES.md "Scoring"): duel pie share, the dealer ladder's share
of each dealer's price range, surplus at our private values in team trades, and the Market Test.
Never the number of trades, fees or luck. A score is 0..1 where the rules score a share, and the
primas behind it travel in `surplus` and `details` so the explanation can be re-derived.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

Target = Literal["duel", "dealer", "trade", "market_test"]
Label = Literal["good", "ok", "bad"]

TARGETS: tuple[Target, ...] = ("duel", "dealer", "trade", "market_test")
EVERY_TICKS = 6  # an agent's eval pass: every 6 ticks (3 min at Saturday's 30 s ticks, 90 s at Sunday's 15 s)
GOOD = 0.6  # a score at or above this is "good"
OK = 0.3  # at or above this (and below GOOD) is "ok"; below is "bad"
# One Phoenix annotation name per target, so a trace view can filter on it.
ANNOTATION_NAMES: dict[str, str] = {
    "duel": "duel_pie_share",
    "dealer": "ladder_share",
    "trade": "trade_surplus",
    "market_test": "market_test_efficiency",
}


def jev_question(agent: str | None, kind: str | None) -> str:
    """The Jev question behind a decision's verdict (a `decisions.jev` row does not name it): the duel
    player asks `duel_move`, the maker `list_price_choice` for a post and `reprice_or_hold` for a stale
    offer, the taker `offer_is_worth_accepting` (questions/*.json)."""
    if agent == "duels":
        return "duel_move"
    if agent == "maker":
        return "list_price_choice" if (kind or "").startswith("post") else "reprice_or_hold"
    return "offer_is_worth_accepting"


def label_for(score: float) -> Label:
    return "good" if score >= GOOD else "ok" if score >= OK else "bad"


def clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class JevCheck:
    """A Jev verdict a decision used, and whether the settled outcome proved it right."""

    question: str
    verdict: str  # yes | no | undecided
    right: bool | None  # None: undecided, or the outcome cannot tell

    @property
    def outcome(self) -> str:
        """The Jev log vocabulary (`jev.log.JEV_OUTCOMES`)."""
        return "unknown" if self.right is None else "right" if self.right else "wrong"


@dataclass(frozen=True)
class Outcome:
    target: Target
    subject: str  # "duel:85", "thread:101", "settlement:67", "market_test:sat"
    score: float | None  # 0..1; None = settled but not scorable yet (the explanation says why)
    label: Label
    explanation: str
    tick: int | None  # the tick the outcome settled
    day: str | None = None  # the game day it settled in: fri | sat | sun (a round)
    surplus: float | None = None  # primas we gained (duels: after decay; trades: at our values)
    ladder_share: float | None = None
    decision_id: int | None = None
    jev: JevCheck | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "subject": self.subject,
            "score": self.score,
            "label": self.label,
            "explanation": self.explanation,
            "tick": self.tick,
            "day": self.day,
            "surplus": self.surplus,
            "ladder_share": self.ladder_share,
            "decision_id": self.decision_id,
            "jev": None if self.jev is None else {**self.jev.__dict__, "outcome": self.jev.outcome},
            "details": dict(self.details),
        }


def day_of(tick: int | None, openings: Iterable[tuple[int, str]]) -> str | None:
    """The game day of `tick`: the latest `day.opened` at or before it. `openings` = (tick, day)."""
    if tick is None:
        return None
    best: tuple[int, str] | None = None
    for opened_tick, day in openings:
        if opened_tick <= tick and (best is None or opened_tick >= best[0]):
            best = (opened_tick, day)
    return best[1] if best else None
