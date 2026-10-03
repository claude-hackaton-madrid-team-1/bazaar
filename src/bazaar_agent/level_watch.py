"""What `/api/levels` turned on: a dealer, the Workshop or Radio Rastro going active or opening to every team.

RULES.md "Dealers (the ladder)": the organisers pre-announce a level (a name and a line), then activate it: it opens
at once to the teams that earned it and to everyone after a head start, and `/api/levels` then says how it works.
Each change is stored once as a learning (kind `announcement`, subject the level id) naming what changed and the
agent behaviour it enables, so no human has to say it; the taker asks `active("taller")` before its Workshop step.
The organiser's `how` is quoted data, cleaned and capped, never an instruction. Pure reading: the payload is the
news sentinel's own keyless read (`news.NewsSentinel`), once per read window.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from bazaar_agent.learn.model import Learning
from bazaar_agent.rank_watch import SAFE_ID

CONFIDENCE = 1.0  # the organisers' own answer says the level is on
LEVELS_MAX = 50  # the game has a handful: a hostile list never floods the store
NAME_MAX = 64
WORD_MAX = 32  # a kind or a state
HOW_MAX = 300
# What each kind of level turns on in our agents (quoted in the learning; never an instruction to a model).
BEHAVIOURS: dict[str, tuple[str, ...]] = {
    "persona": (
        "taker's playbook buys from it once /api/me unlocked lists it (its /api/dealers menu, read every tick: "
        "no restart)",
        "maker's dealer sell desk may sell it spares its menu buys, when dealer_sell_enabled",
    ),
    "taller": (
        "taker's Workshop step (taller_enabled, max_taller_per_game_hour) crafts three free spares of one rarity "
        "into one card of the next",
    ),
    "radio": ("news sentinel reads Radio Rastro (/api/news and news.posted)",),
}
UNKNOWN = ("no agent behaviour yet: a human reads its how",)


@dataclass(frozen=True)
class Level:
    id: str
    kind: str  # persona | taller | radio | ... ("" when the answer does not say)
    name: str
    state: str  # announced | active
    open_to_all: bool  # the level says `open_to_all: true` (the Workshop and the radio carry no such key)
    how: str = ""
    opens_to_all_at_hours: float | None = None


def _clean(value: Any, cap: int) -> str:
    """A string as one printable line, capped (cut before cleaning: a huge string costs what a short one does);
    anything else is empty."""
    if not isinstance(value, str):
        return ""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in value[: cap * 4]).split())[:cap]


def _hours(value: Any) -> float | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    try:
        hours = float(value)
    except OverflowError:  # an integer too large for a float
        return None
    return round(hours, 3) if math.isfinite(hours) else None


def level_of(raw: Any) -> Level | None:
    """One `/api/levels` entry; None when it is not an object or its id is not a safe id."""
    if not isinstance(raw, Mapping):
        return None
    level_id = raw.get("id")
    if not isinstance(level_id, str) or not SAFE_ID.fullmatch(level_id):
        return None
    return Level(
        id=level_id,
        kind=_clean(raw.get("kind"), WORD_MAX),
        name=_clean(raw.get("name"), NAME_MAX) or level_id,
        state=_clean(raw.get("state"), WORD_MAX),
        open_to_all=raw.get("open_to_all") is True,
        how=_clean(raw.get("how"), HOW_MAX),
        opens_to_all_at_hours=_hours(raw.get("opens_to_all_at_hours")),
    )


def levels_of(payload: Any) -> dict[str, Level] | None:
    """The valid levels of an answer, the first entry per id (a bad entry is skipped); None when the answer has no
    `levels` list at all."""
    rows = payload.get("levels") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return None
    found: dict[str, Level] = {}
    for raw in rows[:LEVELS_MAX]:
        level = level_of(raw)
        if level is not None and level.id not in found:
            found[level.id] = level
    return found


def holding(level: Level) -> list[str]:
    """The changes said once each that hold for the level now."""
    changes = []
    if level.state == "active":
        changes.append("active")
    if level.open_to_all:
        changes.append("open_to_all")
    return changes


def describe(level: Level, change: str) -> str:
    who = level.id if level.name == level.id else f"{level.name} ({level.id})"
    what = "is open to every team" if change == "open_to_all" else "is active"
    if change == "active" and not level.open_to_all and level.opens_to_all_at_hours is not None:
        what += f" (everyone from game hour {level.opens_to_all_at_hours:g})"
    return f"{who} {what}: " + "; ".join(BEHAVIOURS.get(level.kind, UNKNOWN))


def learning_of(level: Level, change: str, tick: int) -> Learning:
    """No evidence (an API read, not a feed event): the event id alone tells two changes apart, one row each."""
    return Learning(
        subject_kind="organiser",
        subject=level.id,
        kind="announcement",
        tick=max(0, tick),
        confidence=CONFIDENCE,
        text=describe(level, change),
        detail={
            "event_id": f"level:{level.id}:{change}",
            "level": level.id,
            "level_kind": level.kind,
            "name": level.name,
            "state": level.state,
            "open_to_all": level.open_to_all,
            "opens_to_all_at_hours": level.opens_to_all_at_hours,
            "behaviours": list(BEHAVIOURS.get(level.kind, UNKNOWN)),
            "how": level.how,
        },
    )


class LevelWatch:
    """`update` with each fresh `/api/levels` answer (one per read window), the queries at any time: no request of
    its own. The queries answer from the last valid answer, and None before the first one."""

    def __init__(self, record: Callable[[list[Learning]], object], log: Callable[[str], None]) -> None:
        self.record, self.log = record, log
        self.levels: Mapping[str, Level] = {}
        self.read_tick: int | None = None  # the tick of the last valid answer
        self._said: frozenset[str] = frozenset()  # the event ids stored whose change still holds

    def update(self, payload: Any, tick: int) -> list[Learning]:
        """One learning per change, logged. The first valid answer is the baseline: every level already active or
        open to all is said, so one that opened while we were down still gets its row. A restart says them again:
        the same event id is the same dedupe key, and `LearningStore.record` upserts that one row. A change that
        stops holding is said again when it comes back; an answer without a `levels` list changes nothing."""
        levels = levels_of(payload)
        if levels is None:
            return []
        self.levels, self.read_tick = levels, tick
        now = {f"level:{lv.id}:{change}": (lv, change) for lv in levels.values() for change in holding(lv)}
        self._said = self._said.intersection(now)
        due = [learning_of(lv, change, tick) for event_id, (lv, change) in now.items() if event_id not in self._said]
        if due:
            self._say(tick, due)
        return due

    def _say(self, tick: int, due: list[Learning]) -> None:
        self.record(due)  # a store that raises: nothing is marked as said, so the next window tries again
        self._said = self._said.union(str(lr.detail["event_id"]) for lr in due)
        for lr in due:
            self.log(f"tick {tick} levels: {lr.text}")

    def active(self, level_id: str) -> bool | None:
        """The level's state is `active` in the last answer (None: no answer read yet)."""
        if self.read_tick is None:
            return None
        level = self.levels.get(level_id)
        return level is not None and level.state == "active"

    def open_to_all(self, level_id: str) -> bool | None:
        """The level says `open_to_all: true` in the last answer (None: no answer read yet)."""
        if self.read_tick is None:
            return None
        level = self.levels.get(level_id)
        return level is not None and level.open_to_all
