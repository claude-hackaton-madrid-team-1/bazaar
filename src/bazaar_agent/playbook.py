"""The schedule playbook: for every known event, what each agent does BEFORE, DURING and AFTER it, with lead ticks.

Omar (Sat 3 Oct, 19:50): the whole system must read the schedule and know what to do before and during each event,
so nobody has to say it. The events are the news sentinel's own reads (no request here): `/api/schedule` (official:
benches, duels, the day closing and opening, a set release, a round start, the Sunday allowance, a dealer fever and
its end, a dealer opening to all), `/api/levels` (a level announced or turned on) and Radio Rastro (UNVERIFIED: a
rumour is never acted on until a price probe confirms it).

Deterministic and pure: a table per action (`RULES`) turns each event into instructions for `taker`, `maker`,
`duels` or `all`, each with a phase window in ticks and, for some, a constraint code an agent obeys (today the taker
obeys `no_new_dealer_thread`). Every instruction carries the context it was decided on (time, cash, album pages,
missing cards, duplicates, teams), is stored once as a learnings row (kind `schedule`, subject `playbook`) and is
logged once. A constraint only makes an agent MORE careful: it never moves a price, a cap or a guardrail, and every
write still passes `guardrails.check()`. GUARDRAILS `playbook_enabled` false: instructions are still stored and
logged, but no agent obeys a constraint.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bazaar_agent.learn.model import Learning

BEFORE_TICKS = 10  # how early the BEFORE phase starts (a dealer ladder runs 5-9 ticks)
AFTER_TICKS = 20  # how long the AFTER phase lasts
INSTANT_TICKS = 1  # a release, a grant or a round start happens on one tick
RUMOUR_TICKS = 120  # a rumour says no end: it stays a warning this long after it aired (an hour at 30 s ticks)
TEXT_MAX = 280
NO_NEW_DEALER_THREAD = "no_new_dealer_thread"
YIELD_ACCEPTS = "yield_accepts"
KEEP_BROKER_UP = "keep_broker_up"
NO_DEPLOY = "no_deploy"
PROTECT_NEW_PAGE = "protect_new_page"
UNVERIFIED = "unverified_rumour"


@dataclass(frozen=True)
class Step:
    agent: str  # taker | maker | duels | all
    phase: str  # before | during | after
    do: str  # what to do; `{subject}`, `{note}`, `{cash}`, `{missing}`, `{tick_seconds}` are filled in
    constraint: str | None = None


@dataclass(frozen=True)
class Instruction:
    event_id: str
    action: str
    subject: str | None
    at_hours: float | None
    lead_ticks: int | None  # > 0 before it, <= 0 once it started
    agent: str
    phase: str
    do: str
    constraint: str | None
    official: bool
    context: Mapping[str, Any] = field(default_factory=dict, compare=False)

    @property
    def key(self) -> str:
        return f"playbook:{self.event_id}:{self.agent}:{self.phase}"


S = Step
RULES: dict[str, tuple[Step, ...]] = {
    "bench": (
        S("taker", "before", "Market Test coming: open no new dealer thread, leave the request budget to the broker",
          NO_NEW_DEALER_THREAD),
        S("maker", "before", "Market Test coming: keep the maker and our venue's broker up", KEEP_BROKER_UP),
        S("all", "before", "Market Test coming: no deploy, no merge to main", NO_DEPLOY),
        S("maker", "during", "Market Test running: the broker matches every tick; keep it up", KEEP_BROKER_UP),
        S("taker", "during", "Market Test running: open no new dealer thread", NO_NEW_DEALER_THREAD),
    ),
    "duels": (
        S("duels", "before", "duel session coming: keep the duel runner up, no deploy", NO_DEPLOY),
        S("taker", "before", "duel session coming: open no new dealer thread (the duels need the accept slot)",
          NO_NEW_DEALER_THREAD),
        S("taker", "during", "duels running: open no new dealer thread; the duels take the team's accept first",
          NO_NEW_DEALER_THREAD),
        S("maker", "during", "duels running: the duels take the team's accept first", YIELD_ACCEPTS),
    ),
    "day_closes": (
        S("taker", "before", "the doors close soon: start no dealer ladder that cannot end before ({note})",
          NO_NEW_DEALER_THREAD),
        S("maker", "before", "the doors close soon: open offers stay overnight and settle when the doors open"),
    ),
    "day_opens": (S("all", "after", "the doors are open: re-read /me and the clock (tick every {tick_seconds} s)"),),
    "set_release": (
        S("taker", "before", "{subject} is released soon: plan its page cards (missing: {missing})"),
        S("taker", "after", "{subject} is out: buy its missing page cards first, every buy through the guardrails"),
        S("maker", "after", "{subject} is out: never list or sell our only copy of a {subject} card", PROTECT_NEW_PAGE),
    ),
    "round": (
        S("taker", "after", "{note}: the ladder restarts, 3 new scored deals per dealer level, highest level first"),
        S("maker", "after", "{note}: dealer sells are ladder deals too, the 3 best per level count again"),
    ),
    "grant_all": (
        S("taker", "after", "+{cash} P for every team: spend plan, missing page cards first ({missing}), then a "
          "negotiated deal per dealer level; never at a dealer's opening ask, every buy through the guardrails"),
    ),
    "persona_patch": (
        S("maker", "during", "official dealer change in force: {note}"),
        S("taker", "during", "official dealer change in force: {note}"),
    ),
    "persona_opens": (S("taker", "after", "{subject} opens to every team: the taker buys from its /api/dealers menu"),),
    "level_announced": (S("all", "before", "{note}: read /api/levels when it turns on (the sentinel does)"),),
    "end_round": (S("all", "before", "scores freeze soon ({note}): no risky trade, finish open ladders"),),
    "rumour": (
        S("taker", "during", "UNVERIFIED rumour ({note}): act only when a price probe confirms it", UNVERIFIED),
        S("maker", "during", "UNVERIFIED rumour ({note}): never price on it until a probe confirms it", UNVERIFIED),
    ),
}  # fmt: skip
FEVER_ENDS = ("fever breaks", "fever ends", "fever is over")


def _ticks(hours: float, tick_seconds: float) -> int:
    return math.ceil(round(hours * 3600.0 / tick_seconds, 6))


def duration_ticks(row: Mapping[str, Any]) -> int:
    """How many ticks the event runs: a bench's `ticks`, a duel session's rounds × `duel_ticks`, a rumour
    `RUMOUR_TICKS`, else one tick."""
    raw = row.get("params")
    params: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    ticks, rounds = params.get("ticks"), params.get("rounds")
    if row.get("action") == "bench" and isinstance(ticks, int) and 0 < ticks < 500:
        return ticks
    if row.get("action") == "rumour":
        return RUMOUR_TICKS
    duel = params.get("duel_ticks")
    if row.get("action") == "duels" and isinstance(duel, int) and 0 < duel < 200:
        return duel * (rounds if isinstance(rounds, int) and 0 < rounds < 10 else 1)
    return INSTANT_TICKS


def phase_of(lead: int, duration: int) -> str | None:
    if 0 < lead <= BEFORE_TICKS:
        return "before"
    if -duration < lead <= 0:
        return "during"
    if -duration - AFTER_TICKS < lead <= -duration:
        return "after"
    return None


def _clean(value: Any, cap: int = 120) -> str:
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(value or "")[: cap * 4]).split())[:cap]


def steps_for(row: Mapping[str, Any]) -> tuple[Step, ...]:
    """A fever's end is a `persona_patch` too: its maker/taker steps say to stop what the fever justified."""
    action = str(row.get("action") or "")
    note = str(row.get("note") or "").lower()
    if action == "persona_patch" and any(w in note for w in FEVER_ENDS):
        return (
            S("maker", "after", "the fever is over ({note}): stop planning sales that depended on it"),
            S("taker", "after", "the fever is over ({note}): its prices no longer hold"),
        )
    return RULES.get(action, ())


def instructions(
    rows: Iterable[Mapping[str, Any]], t_hours: float, tick_seconds: float, context: Mapping[str, Any]
) -> list[Instruction]:
    """The instructions in force now for every remembered event row (`event_id`, `action`, `note`, `at_hours`,
    `subject`, `params`, `official`)."""
    if tick_seconds <= 0:
        return []
    out: list[Instruction] = []
    for row in rows:
        at = row.get("at_hours")
        if not isinstance(at, int | float) or isinstance(at, bool) or not math.isfinite(at):
            continue
        lead = _ticks(float(at) - t_hours, tick_seconds)
        phase = phase_of(lead, duration_ticks(row))
        fill = {
            "subject": _clean(row.get("subject"), 40) or "it",
            "note": _clean(row.get("note")),
            "cash": _cash(row),
            "missing": ", ".join(context.get("missing") or []) or "none",
            "tick_seconds": f"{tick_seconds:g}",
        }
        for step in steps_for(row):
            if step.phase != phase:
                continue
            out.append(
                Instruction(
                    str(row.get("event_id")), str(row.get("action")), row.get("subject"), float(at), lead,
                    step.agent, step.phase, step.do.format(**fill)[:TEXT_MAX], step.constraint,
                    row.get("official") is not False, context,
                )
            )  # fmt: skip
    return out


def _cash(row: Mapping[str, Any]) -> str:
    raw = row.get("params")
    cash = raw.get("cash") if isinstance(raw, Mapping) else None
    return str(cash) if isinstance(cash, int) and not isinstance(cash, bool) else "?"


def context_of(tick: int, t_hours: float, market: Any = None, teams: int | None = None) -> dict[str, Any]:
    """What every instruction is decided on: the time, our cash, the album (pages have/of), the page cards we miss,
    our duplicates and how many teams the matrix knows (no request: the tick's `strategy.Market`)."""
    ctx: dict[str, Any] = {"tick": tick, "t_hours": round(t_hours, 3), "teams": teams}
    if market is None:
        return ctx
    held: Mapping[str, int] = getattr(market, "held", {}) or {}
    cards: Mapping[str, Any] = getattr(market, "cards", {}) or {}
    released = tuple(getattr(market, "released", ()) or ())
    pages: dict[str, str] = {}
    missing: list[str] = []
    for ref, c in sorted(cards.items()):
        if not getattr(c, "page", False) or c.set_code not in released:
            continue
        have, of = pages.get(c.set_code, "0/0").split("/")
        pages[c.set_code] = f"{int(have) + (held.get(ref, 0) > 0)}/{int(of) + 1}"
        if held.get(ref, 0) == 0:
            missing.append(ref)
    ctx.update(
        cash=getattr(market, "cash", None),
        pages=pages,
        missing=missing[:20],
        duplicates=sum(n - 1 for n in held.values() if n > 1),
    )
    return ctx


def rumour_rows(items: Iterable[Any], t_hours: float) -> list[dict[str, Any]]:
    """Radio Rastro items (`news.NewsItem`, not official) as events in force from their game hour: UNVERIFIED."""
    rows = []
    for item in items:
        if getattr(item, "official", True) or not isinstance(getattr(item, "at_hours", None), int | float):
            continue
        rows.append(
            {
                "event_id": str(item.news_id),
                "action": "rumour",
                "note": f"{item.headline}" + (f": {item.body}" if item.body else ""),
                "at_hours": float(item.at_hours),
                "subject": item.source,
                "official": False,
            }
        )
    return [r for r in rows if r["at_hours"] <= t_hours]


def learning_of(i: Instruction, tick: int) -> Learning:
    lead = "" if i.lead_ticks is None else (f" in {i.lead_ticks} ticks" if i.lead_ticks > 0 else " now")
    return Learning(
        subject_kind="organiser",
        subject="playbook",
        kind="schedule",
        tick=max(0, tick),
        confidence=1.0 if i.official else 0.5,
        text=f"{i.agent} {i.phase}{lead}: {i.do}"[:300],
        detail={
            "event_id": i.key,
            "action": i.action,
            "agent": i.agent,
            "phase": i.phase,
            "constraint": i.constraint,
            "official": i.official,
            "at_hours": i.at_hours,
            "lead_ticks": i.lead_ticks,
            "context": dict(i.context),
        },
    )


class Playbook:
    """Fed the sentinel's events every tick (`update`), asked by the agents (`constraints`, `for_agent`). It
    remembers each event past its start so DURING and AFTER still hold once `/api/schedule` stops listing it."""

    def __init__(self, record: Callable[[list[Learning]], object], log: Callable[[str], None]) -> None:
        self.record, self.log = record, log
        self.events: dict[str, dict[str, Any]] = {}
        self.current: list[Instruction] = []
        self._said: set[str] = set()

    def update(
        self,
        tick: int,
        t_hours: float,
        tick_seconds: float,
        upcoming: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any],
        extra: Sequence[Mapping[str, Any]] = (),
    ) -> list[Instruction]:
        for row in [*upcoming, *extra]:
            if isinstance(row, Mapping) and row.get("event_id"):
                self.events[str(row["event_id"])] = dict(row)
        horizon = (BEFORE_TICKS + AFTER_TICKS + 500) * tick_seconds / 3600.0
        self.events = {
            k: r for k, r in self.events.items() if not isinstance(r.get("at_hours"), int | float)
            or float(r["at_hours"]) > t_hours - horizon
        }  # fmt: skip
        self.current = instructions(self.events.values(), t_hours, tick_seconds, context)
        fresh = [i for i in self.current if i.key not in self._said]
        if fresh:
            self.record([learning_of(i, tick) for i in fresh])  # a store that raises: said again next tick
            for i in fresh:
                self._said.add(i.key)
                self.log(f"tick {tick} playbook: {i.agent} {i.phase}: {i.do}")
        return fresh

    def for_agent(self, agent: str) -> list[Instruction]:
        return [i for i in self.current if i.agent in (agent, "all")]

    def constraints(self, agent: str) -> frozenset[str]:
        return frozenset(i.constraint for i in self.for_agent(agent) if i.constraint and i.official)
