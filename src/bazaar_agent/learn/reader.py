"""The deterministic feed reader: feed events, our closed threads and our refused requests → learnings.

It reads the live feed the way a person reads the "On air · Live feed" panel of the game's homepage
(that panel is `GET /api/feed` + the public SSE stream, rendered per event type): who was sent away
until which tick, which venue will charge what from which tick, which dealer opened, when the clock
paused, how duels over each item ended. Structure only: a field the server set (`until_tick`,
`reason`, `fee_bps`, ...) is a fact; free text (organiser notices, dealer words) is kept as quoted
`text` and interpreted later by the LLM pass, never acted on here. One exception: a dealer's fixed etiquette
phrase ("no me llame amigo") becomes a `behaviour` learning that only forbids a word in our messages
(`etiquette.py`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from bazaar_agent.learn.etiquette import etiquette_learnings
from bazaar_agent.learn.model import SUBJECT_PATTERN, Kind, Learning, SubjectKind

Event = dict[str, Any]

COOLOFF_DEFAULT_TICKS = 10  # a cooloff without `until_tick`: retry after this many ticks
LOCKED_RECHECK_TICKS = 10  # a `locked` dealer: retry after this many ticks (an unlock event clears it sooner)
# A refusal costs nothing and a blocker that lasts too long loses trades: every blocker is capped, and an
# hourly one (quota, sold out) is retried after at most this many ticks even if the hour has not ended.
HOURLY_CAP_TICKS = 60
COOLOFF_CAP_TICKS = 400  # even a server-set `until_tick` is not believed beyond this
# closed_reason / refusal code → learning kind. Anything else (idle, walked, deal) blocks nothing.
REASON_KINDS: Mapping[str, Kind] = {
    "cooloff": "cooloff",
    "persona_quota": "quota",
    "sold_out": "sold_out",
    "locked": "blocker",
}
TOPICS_MAX = 5000
_UNTIL = re.compile(r"until tick (\d{1,9})")
_SUBJECT = re.compile(SUBJECT_PATTERN)


@dataclass(frozen=True)
class GameHour:
    """Where the current game hour ends, from one clock reading (a dealer's quota resets there).

    `hours_per_tick` is the pace observed between two clock readings (Δt_hours / Δtick): it holds whether
    `t_hours` follows wall-clock time or counts ticks. Without it, `tick_seconds / 3600` is assumed."""

    tick: int
    t_hours: float
    tick_seconds: float = 60.0
    hours_per_tick: float | None = None

    @property
    def _per_tick(self) -> float:
        observed = self.hours_per_tick
        if observed is not None and 0 < observed < 1:
            return observed
        return max(self.tick_seconds, 1.0) / 3600

    @property
    def start_tick(self) -> int:
        into = (self.t_hours - math.floor(self.t_hours)) / self._per_tick
        return self.tick - int(math.floor(into + 1e-6))

    @property
    def end_tick(self) -> int:
        """The first tick of the next game hour."""
        left = (math.floor(self.t_hours) + 1 - self.t_hours) / self._per_tick
        return self.tick + max(1, math.ceil(left - 1e-6))

    def end_for(self, tick: int) -> int:
        """The hour end for an event at `tick`: this hour's end when it falls in this hour; an older
        event's hour is already over (its blocker is history)."""
        return self.end_tick if tick >= self.start_tick else tick + 1


def _subject(value: object) -> str | None:
    text = str(value or "")
    return text if _SUBJECT.match(text) else None


def _int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except (ValueError, OverflowError):
        return None


def _make(**fields: Any) -> Learning | None:
    """A learning, or None when the event's fields do not validate (a malformed event is skipped)."""
    try:
        return Learning(**fields)
    except ValidationError:
        return None


def _who(team: str | None, us: str | None) -> str:
    return "us" if team is not None and team == us else str(team or "a team")


def item_of(topic: object) -> str | None:
    """'LAV-03' or 'sobre_barrio' for a buy topic."""
    if not isinstance(topic, dict) or not isinstance(topic.get("buy"), dict):
        return None
    spec = topic["buy"]
    return _subject(spec.get("card") or spec.get("pack"))


# ---------------------------------------------------------------- blockers (structure set by the server)


def blocker(
    dealer: str,
    reason: str,
    *,
    team: str | None,
    us: str | None,
    tick: int,
    hour: GameHour | None,
    until_tick: int | None = None,
    item: str | None = None,
    message: str = "",
    evidence: Iterable[int] = (),
    origin: str = "feed",
) -> Learning | None:
    """One blocker from a `closed_reason` or a refusal code; None for a reason that blocks nothing."""
    kind = REASON_KINDS.get(reason)
    if kind is None or _subject(dealer) is None or _subject(team) is None:
        return None  # a blocker binds a known team, or nobody
    confidence = 1.0
    detail: dict[str, Any] = {"code": reason, "origin": origin}
    if kind == "cooloff":
        found = _UNTIL.search(message or "")
        until_tick = until_tick or (_int(found.group(1)) if found else None)
        if until_tick is None:
            until_tick, confidence = tick + COOLOFF_DEFAULT_TICKS, 0.6
        until_tick = min(until_tick, tick + COOLOFF_CAP_TICKS)
    elif kind == "blocker":
        until_tick = tick + LOCKED_RECHECK_TICKS
    else:  # quota / sold out: until the game hour ends (an older event's hour is over), retried within the cap
        end = hour.end_for(tick) if hour is not None else tick + 1
        if kind == "quota" and origin == "refusal" and hour is not None:
            # A server refusal uses the game clock, not the old 60-tick retry shortcut (15 s ticks need 240).
            until_tick = until_tick if until_tick is not None and until_tick > tick else end
            detail["quota_until_tick"] = until_tick
            limit = re.fullmatch(r"at most ([1-9][0-9]*) conversations per hour with " + re.escape(dealer), message)
            if limit:
                detail["conversations_per_hour"] = int(limit.group(1))
                item = None  # this quota covers every topic with the dealer, not only the attempted pack
        else:
            until_tick = min(end, tick + HOURLY_CAP_TICKS)
        # a pack's hourly allotment, or one item sold out, blocks that item only; a card's quota the dealer
        if item is not None and (kind == "sold_out" or "-" not in item):
            detail["item"] = item
    whom = _who(team, us)
    what = f" for {detail['item']}" if "item" in detail else ""
    text = f"{dealer} {reason.replace('_', ' ')} with {whom}{what} until T{until_tick}"
    return _make(
        subject_kind="dealer",
        subject=dealer,
        kind=kind,
        tick=tick,
        until_tick=until_tick,
        team=_subject(team),
        evidence=tuple(evidence),
        confidence=confidence,
        text=text,
        detail=detail,
    )


def from_thread(thread: Mapping[str, Any], us: str | None, hour: GameHour) -> Learning | None:
    """Our own dealer thread that closed with a blocking `closed_reason` (`GET /api/threads/{id}`)."""
    reason = str(thread.get("closed_reason") or thread.get("status") or "")
    dealer = str(thread.get("with") or "")
    return blocker(
        dealer,
        reason,
        team=us,
        us=us,
        tick=hour.tick,
        hour=hour,
        until_tick=_int(thread.get("until_tick")),
        item=item_of(thread.get("topic")),
        origin=f"thread:{_int(thread.get('id'))}",
    )


def from_refusal(
    dealer: str, code: str, message: str, extra: Mapping[str, Any], us: str | None, hour: GameHour, item: str | None
) -> Learning | None:
    """An `open_thread` the server refused (`cooloff` with `until_tick`, `persona_quota`, `sold_out`, `locked`)."""
    return blocker(
        dealer,
        code,
        team=us,
        us=us,
        tick=hour.tick,
        hour=hour,
        until_tick=_int(extra.get("until_tick")),
        item=item,
        message=message,
        origin="refusal",
    )


# ---------------------------------------------------------------- the feed, event by event


def _payload(e: Event) -> dict[str, Any]:
    p = e.get("payload")
    return p if isinstance(p, dict) else {}


def _notice(
    e: Event, subject_kind: SubjectKind, subject: object, kind: Kind, text: object, **detail: Any
) -> Learning | None:
    who = _subject(subject)
    if who is None or not str(text or "").strip():
        return None
    return _make(
        subject_kind=subject_kind,
        subject=who,
        kind=kind,
        tick=_int(e.get("tick")) or 0,
        evidence=(int(e["id"]),),
        confidence=1.0,
        text=str(text),
        detail={k: v for k, v in detail.items() if v is not None},
    )


def _pct(bps: object) -> str:
    value = _int(bps)
    return f"{value / 100:g}%" if value is not None else "?"


def _cooloff(e: Event, us: str | None, hour: GameHour | None) -> Learning | None:
    p = _payload(e)
    return blocker(
        str(p.get("persona") or e.get("actor") or ""),
        "cooloff",
        team=_subject(p.get("team")),
        us=us,
        tick=_int(e.get("tick")) or 0,
        hour=hour,
        until_tick=_int(p.get("until_tick")),
        evidence=(int(e["id"]),),
    )


def _strike(e: Event, us: str | None) -> Learning | None:
    p = _payload(e)
    raw_kinds = p.get("kinds")
    kinds = [k for k in raw_kinds if isinstance(k, str)][:5] if isinstance(raw_kinds, list) else []
    who = _who(_subject(p.get("team")), us)
    text = f"{p.get('persona')} gave {who} a strike ({', '.join(kinds) or '?'}), {p.get('strikes')} so far"
    learned = _notice(e, "dealer", p.get("persona"), "behaviour", text, strikes=_int(p.get("strikes")), kinds=kinds)
    return learned.model_copy(update={"team": _subject(p.get("team"))}) if learned else None


def _closed(e: Event, us: str | None, hour: GameHour | None, topics: Mapping[int, str]) -> Learning | None:
    p = _payload(e)
    if p.get("kind") != "persona":  # a dealer conversation only; a team thread's end blocks nothing
        return None
    thread = _int(p.get("thread"))
    return blocker(
        str(p.get("with") or ""),
        str(p.get("reason") or ""),
        team=_subject(p.get("team")),
        us=us,
        tick=_int(e.get("tick")) or 0,
        hour=hour,
        until_tick=_int(p.get("until_tick")),
        item=topics.get(thread) if thread is not None else None,
        evidence=(int(e["id"]),),
    )


def _unlocked(e: Event, us: str | None) -> Learning | None:
    p = _payload(e)
    team, dealer = _subject(p.get("team")), p.get("persona")
    if team is not None and team == us:
        text = f"we unlocked {p.get('persona_name') or dealer} (level {p.get('level')})"
        learned = _notice(e, "dealer", dealer, "announcement", text, unlocks=True, level=_int(p.get("level")))
        return learned.model_copy(update={"team": team}) if learned else None
    text = f"{team} unlocked {dealer} (level {p.get('level')}): {p.get('why') or ''}"
    return _notice(e, "team", team, "behaviour", text, dealer=_subject(dealer), level=_int(p.get("level")))


def _fee(e: Event) -> Learning | None:
    p = _payload(e)
    venue, effective = _subject(p.get("venue")), _int(p.get("effective_tick"))
    if e.get("type") == "venue.fee_changed":
        effective = _int(e.get("tick"))
        text = f"{venue} now charges {_pct(p.get('fee_bps'))} + {p.get('fee_per_card') or 0} P/card"
    else:
        text = f"{venue} will charge {_pct(p.get('fee_bps'))} + {p.get('fee_per_card') or 0} P/card from T{effective}"
    return _notice(
        e,
        "venue",
        venue,
        "fee_change",
        text,
        venue=venue,
        fee_bps=_int(p.get("fee_bps")),
        fee_per_card=_int(p.get("fee_per_card")),
        effective_tick=effective,
    )


def _venue(e: Event) -> Learning | None:
    p, kind = _payload(e), str(e.get("type"))
    venue, state = p.get("venue"), kind.split(".", 1)[1]
    if kind == "venue.announcement":  # a team writes it: one row per venue (the newest), however many it posts
        text = f"{venue} says: “{p.get('text') or ''}”"
        return _notice(e, "venue", venue, "announcement", text, venue=venue, aggregate="venue_notice")
    if kind == "venue.opened":
        text = f"{p.get('owner')} opened {venue} “{p.get('name') or ''}” at {_pct(p.get('fee_bps'))}"
        return _notice(
            e, "venue", venue, "announcement", text, venue=venue, state="open", fee_bps=_int(p.get("fee_bps"))
        )
    text = f"{venue} {state}" + (f": {p.get('reason')}" if p.get("reason") else "")
    return _notice(e, "venue", venue, "announcement", text, venue=venue, state=state)


def _clock(e: Event) -> Learning | None:
    p = _payload(e)
    text = "the clock is paused" if p.get("paused") else f"the clock ticks every {p.get('tick_seconds')} s"
    return _notice(e, "organiser", "organiser", "rule_change", text, paused=bool(p.get("paused")))


def _day(e: Event) -> Learning | None:
    p = _payload(e)
    if e.get("type") == "day.opened":
        text = f"{p.get('name') or p.get('day')} is open: one tick every {p.get('tick_seconds')} s"
    else:
        text = f"closed until {p.get('reopens')}" if p.get("reopens") else "the Bazaar has closed"
    return _notice(e, "organiser", "organiser", "rule_change", text, reopens=p.get("reopens"))


def _level(e: Event) -> Learning | None:
    p, kind = _payload(e), str(e.get("type"))
    name = p.get("name") or p.get("level") or p.get("persona")
    if kind == "level.announced":
        text = f"coming soon: {name}: “{p.get('teaser') or ''}”"
    elif kind == "level.activated":
        text = f"{name} is open: {p.get('how') or ''} (everyone in {p.get('opens_to_all_in_hours')} h)"
    elif kind == "persona.open_to_all":
        text = f"{name} (level {p.get('level')}) now trades with everyone"
    else:  # persona.updated
        text = f"{name} updated to v{p.get('version')}" + (f": “{p.get('note')}”" if p.get("note") else "")
    unlocks = kind == "persona.open_to_all" or None
    dealer = p.get("persona") if kind.startswith("persona.") else p.get("level")
    return _notice(e, "dealer", dealer, "announcement", text, unlocks=unlocks)


def _organiser(e: Event) -> Learning | None:
    p, kind = _payload(e), str(e.get("type"))
    text = p.get("text") or p.get("note") or p.get("reason") or p.get("error") or p.get("name")
    if kind.startswith("round."):
        text = f"round {p.get('round')} “{p.get('name') or ''}” {kind.split('.', 1)[1]} (weight {p.get('weight')})"
        return _notice(e, "organiser", "organiser", "rule_change", text)
    if kind == "duels.scheduled":
        text = (
            f"duels “{p.get('name')}”: {p.get('duels')} duels, {p.get('duel_ticks')} ticks each, decay {p.get('decay')}"
        )
    return _notice(e, "organiser", "organiser", "announcement", f"{kind}: {text or ''}".strip())


def _team_notice(e: Event, us: str | None) -> Learning | None:
    p, kind = _payload(e), str(e.get("type"))
    team = _subject(p.get("team"))
    if team is None or team != us:
        return None
    text = f"{kind}: {p.get('kind') or ''} {p.get('amount') or ''} {p.get('component') or ''} {p.get('reason') or ''}"
    learned = _notice(e, "team", team, "rule_change", text)
    return learned.model_copy(update={"team": team}) if learned else None


ORGANISER_TYPES = (
    "announcement",
    "schedule.fired",
    "schedule.failed",
    "set.released",
    "duels.scheduled",
    "duels.finished",
    "settlement.failed",
    "engine.error",
    "round.",
    "calendar.",
)


def read_event(e: Event, us: str | None, hour: GameHour | None, topics: Mapping[int, str]) -> Learning | None:
    """One feed event → at most one learning (deterministic; free text kept as quoted data)."""
    kind = str(e.get("type") or "")
    handlers: dict[str, Callable[[], Learning | None]] = {
        "persona.cooloff": lambda: _cooloff(e, us, hour),
        "persona.strike": lambda: _strike(e, us),
        "thread.closed": lambda: _closed(e, us, hour, topics),
        "level.unlocked": lambda: _unlocked(e, us),
        "venue.fee_announced": lambda: _fee(e),
        "venue.fee_changed": lambda: _fee(e),
        "clock.changed": lambda: _clock(e),
        "day.opened": lambda: _day(e),
        "day.closed": lambda: _day(e),
    }
    if kind in handlers:
        return handlers[kind]()
    if kind in ("level.announced", "level.activated", "persona.open_to_all", "persona.updated"):
        return _level(e)
    if kind.startswith("venue."):
        return _venue(e)
    if kind.startswith(("admin.", "team.granted")):
        return _team_notice(e, us)
    if kind.startswith(ORGANISER_TYPES):
        return _organiser(e)
    return None


# ---------------------------------------------------------------- the stateful reader (one per process)


@dataclass
class DuelTally:
    tick: int = 0
    deals: int = 0
    no_deals: int = 0
    evidence: list[int] = field(default_factory=list)


@dataclass
class FeedReader:
    """Reads each feed event once (by id) and keeps what spans events: thread topics and duel tallies."""

    us: str | None
    newest: int = 0
    topics: dict[int, str] = field(default_factory=dict)
    duels: dict[str, DuelTally] = field(default_factory=dict)
    etiquette: set[str] = field(default_factory=set)  # the etiquette learnings already read (dedupe keys)

    def read(self, events: Iterable[Event], hour: GameHour | None = None) -> list[Learning]:
        out: list[Learning] = []
        touched: set[str] = set()
        for e in sorted((e for e in events if _int(e.get("id")) is not None), key=lambda e: int(e["id"])):
            if int(e["id"]) <= self.newest:
                continue
            self.newest = int(e["id"])
            try:  # one malformed event is skipped; it never costs the learnings read around it
                self._remember(e, touched)
                learned = read_event(e, self.us, hour, self.topics)
                out += self._etiquette(e)
            except Exception:
                continue
            if learned is not None:
                out.append(learned)
        if len(self.topics) > TOPICS_MAX:  # the newest threads only: an old thread's close is history
            self.topics = {t: self.topics[t] for t in sorted(self.topics)[-TOPICS_MAX // 2 :]}
        return out + [d for item in sorted(touched) if (d := self._duel_learning(item)) is not None]

    def _etiquette(self, e: Event) -> list[Learning]:
        """How a dealer asked to be addressed ("no me llame amigo"), once per dealer and address."""
        fresh = [lr for lr in etiquette_learnings(e) if lr.key() not in self.etiquette]
        if len(self.etiquette) > TOPICS_MAX:
            self.etiquette.clear()  # a repeat after this is one more idempotent upsert, never a second row
        self.etiquette.update(lr.key() for lr in fresh)
        return fresh

    def _remember(self, e: Event, touched: set[str]) -> None:
        p = _payload(e)
        if e.get("type") == "thread.opened" and (thread := _int(p.get("thread"))) is not None:
            item = item_of(p.get("topic"))
            if item is not None:
                self.topics[thread] = item
        elif e.get("type") == "duel.closed" and isinstance(p.get("item"), str):
            item = p["item"][:80]
            tally = self.duels.setdefault(item, DuelTally())
            tally.tick = max(tally.tick, _int(e.get("tick")) or 0)
            tally.deals += p.get("status") == "deal"
            tally.no_deals += p.get("status") != "deal"
            tally.evidence = [*tally.evidence, int(e["id"])][-20:]
            touched.add(item)

    def _duel_learning(self, item: str) -> Learning | None:
        tally = self.duels[item]
        total = tally.deals + tally.no_deals
        text = f"duels over “{item}”: {tally.deals} of {total} closed with a deal"
        return _make(
            subject_kind="organiser",
            subject="duels",
            kind="behaviour",
            tick=tally.tick,
            evidence=tuple(tally.evidence),
            confidence=min(1.0, total / 10),
            text=text,
            detail={"aggregate": "duels", "item": item, "deals": tally.deals, "no_deals": tally.no_deals},
        )
