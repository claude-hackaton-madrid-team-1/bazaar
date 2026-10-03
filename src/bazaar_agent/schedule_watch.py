"""What the organisers will do and when: `/api/schedule` and `/api/levels`, with a lead time in ticks.

The schedule is official (it happens): the Market Test benches every two game hours, the duel sessions, a
dealer opening to everyone, a set fever, a set release, the day closing. Each event is stored once as a
learning (kind `schedule`) and refreshed with its lead time ("Market Test in 8 ticks") when it crosses
`ALERT_TICKS`, so every runner and Jev can see it coming: a bench needs the maker and the broker up and no
deploy. Pure reading: the payloads come from the news sentinel's own keyless reads (`news.NewsSentinel`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bazaar_agent.learn.model import Learning

ALERT_TICKS = (20, 10, 3)  # a lead time crossing one of these is logged and stored again
CONFIDENCE = 1.0  # the organisers' schedule happens
FIELD_MAX = 200
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
# What each action asks of us, in a few words (quoted in the learning; never an instruction to a model).
ADVICE = {
    "bench": "keep the maker and our venue's broker up, no deploy",
    "duels": "keep the duel runner up, no deploy",
    "persona_opens": "a dealer opens to every team: the taker reads it from /api/dealers",
    "persona_patch": "a dealer's prices change",
    "set_release": "a new album page: buy its cards, never sell our only copy",
    "grant_all": "cash for every team",
    "day_closes": "doors close: nothing settles until they open",
    "end_round": "scores freeze",
}


@dataclass(frozen=True)
class ScheduledEvent:
    event_id: str  # "<action>:<at_hours>" for the schedule, "level:<id>" for a level opening
    action: str
    note: str
    at_hours: float | None  # None: announced, time not said
    subject: str | None = None  # the persona, set or level it is about


def _clean(value: Any, cap: int = FIELD_MAX) -> str:
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(value or "")).split())
    return text[:cap]


def _hours(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return round(float(value), 3)
    return None


def events_from_schedule(payload: Mapping[str, Any]) -> list[ScheduledEvent]:
    out = []
    for s in payload.get("upcoming") or []:
        if not isinstance(s, dict) or not isinstance(s.get("action"), str):
            continue
        raw = s.get("params")
        params: dict[str, Any] = raw if isinstance(raw, dict) else {}
        subject = params.get("persona") or params.get("id") or params.get("set") or params.get("name")
        at = _hours(s.get("at_hours"))
        action = _clean(s["action"], 32)
        about = _clean(subject, 64) or None
        event_id = f"{action}:{at}" + (f":{about}" if about else "")  # one time may close three stalls
        out.append(ScheduledEvent(event_id, action, _clean(s.get("note")), at, about))
    return out


def events_from_levels(payload: Mapping[str, Any]) -> list[ScheduledEvent]:
    """A level not open to all yet (a dealer opening later) or only announced (time not said)."""
    out = []
    for lv in payload.get("levels") or []:
        if (
            not isinstance(lv, dict)
            or not isinstance(lv.get("id"), str)
            or not SAFE_ID.match(lv["id"])
            or lv.get("open_to_all") is True
        ):
            continue
        name = _clean(lv.get("name") or lv["id"], 64)
        if lv.get("state") == "announced":
            out.append(
                ScheduledEvent(f"level:{lv['id']}:announced", "level_announced", f"{name} announced", None, lv["id"])
            )
        elif (at := _hours(lv.get("opens_to_all_at_hours"))) is not None:
            out.append(
                ScheduledEvent(f"level:{lv['id']}:open", "persona_opens", f"{name} opens for everyone", at, lv["id"])
            )
    return out


def ticks_until(at_hours: float, t_hours: float, tick_seconds: float) -> int:
    """Whole ticks until `at_hours` (rounded first: 1.55 h - 1.5 h at 60 s is 3 ticks, not 3.0000000000000027)."""
    return math.ceil(round((at_hours - t_hours) * 3600.0 / tick_seconds, 6))


def lead_ticks(ev: ScheduledEvent, t_hours: float, tick_seconds: float) -> int | None:
    """Ticks until the event at today's pace (None: time not said, or no pace)."""
    if ev.at_hours is None or tick_seconds <= 0:
        return None
    return max(0, ticks_until(ev.at_hours, t_hours, tick_seconds))


def describe(ev: ScheduledEvent, lead: int | None) -> str:
    when = f"at game hour {ev.at_hours:g}" if ev.at_hours is not None else "time not said"
    soon = f" (in {lead} ticks)" if lead is not None else ""
    advice = ADVICE.get(ev.action)
    return f"{ev.note or ev.action} {when}{soon}" + (f": {advice}" if advice else "")


def learning_of(ev: ScheduledEvent, tick: int, lead: int | None) -> Learning:
    return Learning(
        subject_kind="organiser",
        subject="schedule",
        kind="schedule",
        tick=max(0, tick),
        confidence=CONFIDENCE,
        text=describe(ev, lead) or "-",
        detail={
            "event_id": ev.event_id,
            "action": ev.action,
            "at_hours": ev.at_hours,
            "lead_ticks": lead,
            "subject": ev.subject,
        },
    )


class ScheduleWatch:
    """`update` with fresh payloads (every read window), `on_tick` every tick: no request of its own."""

    def __init__(
        self,
        record: Callable[[list[Learning]], object],
        log: Callable[[str], None],
        alerts: tuple[int, ...] = ALERT_TICKS,
    ) -> None:
        self.record, self.log, self.alerts = record, log, tuple(sorted(alerts, reverse=True))
        self.events: dict[str, ScheduledEvent] = {}
        self._said: dict[str, int | None] = {}  # event -> the smallest alert threshold already said (None: seen)

    def update(self, schedule: Mapping[str, Any] | None, levels: Mapping[str, Any] | None) -> None:
        planned = events_from_schedule(schedule or {})
        opening = {ev.subject for ev in planned if ev.action == "persona_opens"}  # the level says it again
        extra = [
            ev
            for ev in events_from_levels(levels or {})
            if not (ev.action == "persona_opens" and ev.subject in opening)
        ]
        self.events.update({ev.event_id: ev for ev in [*planned, *extra]})

    def on_tick(self, tick: int, t_hours: float, tick_seconds: float) -> list[Learning]:
        """New events and lead times that crossed a threshold: logged and stored (one row per event, refreshed).
        An event is dropped once its lead reaches 0 (it is happening)."""
        due: list[Learning] = []
        for ev in sorted(self.events.values(), key=lambda e: (e.at_hours is None, e.at_hours or 0.0)):
            lead = lead_ticks(ev, t_hours, tick_seconds)
            if self._new(ev.event_id, lead):
                due.append(learning_of(ev, tick, lead))
        if due:
            self._say(tick, due)
        self.events = {k: v for k, v in self.events.items() if lead_ticks(v, t_hours, tick_seconds) != 0}
        return due

    def _say(self, tick: int, due: list[Learning]) -> None:
        self.record(due)  # a store that raises: nothing is marked as said, so the next tick tries again
        for lr in due:
            self._said[str(lr.detail["event_id"])] = self._threshold(lr.detail["lead_ticks"])
            self.log(f"tick {tick} schedule: {lr.text}")

    def _threshold(self, lead: Any) -> int | None:
        if not isinstance(lead, int):
            return None
        crossed = [a for a in self.alerts if lead <= a]
        return min(crossed) if crossed else None

    def _new(self, event_id: str, lead: int | None) -> bool:
        """The event is new, or its lead time crossed a smaller threshold than the last one said."""
        if event_id not in self._said:
            return True
        said, now = self._said[event_id], self._threshold(lead)
        return now is not None and (said is None or now < said)

    def upcoming(self, t_hours: float, tick_seconds: float) -> list[dict[str, Any]]:
        """Every known future event with its lead time, soonest first (for `market_events.json`)."""
        rows = []
        for ev in self.events.values():
            lead = lead_ticks(ev, t_hours, tick_seconds)
            rows.append({"event_id": ev.event_id, "action": ev.action, "note": ev.note, "at_hours": ev.at_hours,
                         "lead_ticks": lead, "subject": ev.subject})  # fmt: skip
        return sorted(rows, key=lambda r: (r["at_hours"] is None, r["at_hours"] or 0.0))


GUARD_ACTIONS = ("bench", "duels")


def _at_or_last(row: Mapping[str, Any]) -> float:
    at = row.get("at_hours")
    return (
        float(at) if isinstance(at, int | float) else math.inf
    )  # a dealer ladder should not still be running when these start


def ladder_ticks(ladder: tuple[int, int, int] | None, max_ticks: int) -> int:
    """Ticks a dealer ladder may run: one bid per tick, every distinct bid (the last one clamped to the top, so a
    step that does not divide the range still ends on it), at most the thread's tick limit, plus the tick on which
    she answers our last bid or we accept."""
    if ladder is None:
        return max_ticks + 1
    start, top, step = ladder
    bids = math.ceil(max(0, top - start) / max(1, step)) + 1
    return min(max_ticks, bids) + 1


def crossing(
    upcoming: Sequence[Mapping[str, Any]],
    t_hours: float,
    tick_seconds: float,
    ticks: int,
    actions: tuple[str, ...] = GUARD_ACTIONS,
) -> dict[str, Any] | None:
    """The first upcoming `actions` event that starts within the next `ticks` ticks (None: the way is clear)."""
    for u in sorted(upcoming, key=_at_or_last):
        at = u.get("at_hours")
        if u.get("action") not in actions or not isinstance(at, int | float) or tick_seconds <= 0:
            continue
        lead = ticks_until(float(at), t_hours, tick_seconds)
        if 0 < lead <= ticks:
            return {**u, "lead_ticks": lead}
    return None
