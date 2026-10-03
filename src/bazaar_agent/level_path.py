"""Levels: how the dealer ladder opened on Friday, read from the feed, and what the next early unlock needs.

RULES.md: a level opens "at once to the teams that earned it (for a dealer, a few good deals with the one
before: a deal at the dealer's opening price does not count, a negotiated one does) and to everyone
after a head start". The feed shows it happen: `level.announced` (a teaser), `level.activated` (how it
works, `opens_to_all_in_hours`), one `level.unlocked` per team (with a `why`: "4 deals with abuela", or
"open to everyone now") and `persona.open_to_all`. `GET /api/dealers` carries each dealer's rule in
`unlock` (`early_deals_with`, `early_min_deals`, `early_min_level`, `open_to_all_at`).

This module lists those events, tests candidate counting rules against every team's "N deals" (which
deals did the server count?), and counts our own deals toward the next level. Pure functions, no network.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bazaar_agent import intel

WHY = re.compile(r"(\d+) deals? with (\w+)")


@dataclass(frozen=True)
class Unlock:
    team: str
    tick: int
    dealer: str  # the dealer this unlock opened
    level: int | None
    why: str
    deals: int | None  # "N deals with <previous>": the count the server gave
    previous: str | None  # the dealer those deals were with
    open_to_all: bool  # unlocked by the head start running out, not by deals


def unlocks(events: Iterable[intel.Event]) -> list[Unlock]:
    out = []
    for e in events:
        if e.get("type") != "level.unlocked":
            continue
        p = e.get("payload") or {}
        why = str(p.get("why") or "")
        m = WHY.search(why)
        level = p.get("level")
        out.append(
            Unlock(
                str(p.get("team")),
                int(e.get("tick", 0)),
                str(p.get("persona") or (p.get("level") if isinstance(p.get("level"), str) else None) or "?"),
                int(level) if isinstance(level, int) else None,
                why,
                int(m[1]) if m else None,
                m[2] if m else None,
                "everyone" in why,
            )
        )
    return out


@dataclass(frozen=True)
class Timeline:
    """One level's Friday: when it was announced, activated and opened to everyone (ticks)."""

    dealer: str
    name: str
    teaser: str
    announced: int | None
    activated: int | None
    opened_to_all: int | None
    head_start_hours: float | None
    how: str | None
    early: int  # teams unlocked by deals
    late: int  # teams unlocked when it opened to everyone


def timelines(events: Sequence[intel.Event]) -> list[Timeline]:
    info: dict[str, dict[str, Any]] = {}
    for e in events:
        kind, p = e.get("type"), e.get("payload") or {}
        if kind in ("level.announced", "level.activated", "persona.open_to_all"):
            dealer = str(p.get("persona") or p.get("level") or "?")
            d = info.setdefault(dealer, {"name": p.get("name") or dealer, "teaser": p.get("teaser") or ""})
            key = {"level.announced": "announced", "level.activated": "activated"}.get(str(kind), "opened")
            d.setdefault(key, int(e.get("tick", 0)))
            if kind == "level.activated":
                d["how"], d["head"] = p.get("how"), p.get("opens_to_all_in_hours")
    found = unlocks(events)
    return [
        Timeline(
            dealer,
            str(d["name"]),
            str(d["teaser"]),
            d.get("announced"),
            d.get("activated"),
            d.get("opened"),
            d.get("head"),
            d.get("how"),
            sum(1 for u in found if u.dealer == dealer and not u.open_to_all),
            sum(1 for u in found if u.dealer == dealer and u.open_to_all),
        )
        for dealer, d in info.items()
    ]


Rule = Callable[[intel.DealerThread], bool]

# Candidate answers to "which deals did the server count?", each over one team's settled deals with the
# previous dealer up to its unlock tick.
RULES: dict[str, Rule] = {
    "every deal": lambda t: True,
    "buys": lambda t: t.side == "buy",
    "not at the opening price": lambda t: t.opening_ask is not None and t.fill_price != t.opening_ask,
    "buys not at the opening price": lambda t: (
        t.side == "buy" and t.opening_ask is not None and t.fill_price != t.opening_ask
    ),
    "buys where we bid": lambda t: t.side == "buy" and bool(t.team_prices),
}


@dataclass(frozen=True)
class RuleFit:
    rule: str
    exact: int  # teams whose count under this rule equals the server's "N deals"
    teams: int
    misses: tuple[tuple[str, int, int], ...]  # (team, server count, rule count)
    contradicted_by: tuple[str, ...]  # teams never unlocked by deals although this rule gives them the minimum


def deals_with(threads: Sequence[intel.DealerThread], team: str, dealer: str, up_to: int) -> list[intel.DealerThread]:
    return [
        t
        for t in threads
        if t.team == team and t.dealer == dealer and t.fill_price is not None and (t.fill_tick or 0) <= up_to
    ]


def fit_rules(events: Sequence[intel.Event], minimum: int = 3) -> list[RuleFit]:
    """Each candidate counting rule against every team's server count. A team that only got in when its
    level opened to everyone, while a rule credits it `minimum` deals with that level's previous dealer
    before then, contradicts the rule (latecomers were let in the tick their deals reached the minimum)."""
    threads = intel.dealer_threads(events)
    found = unlocks(events)
    counted = [u for u in found if u.deals is not None and u.previous]
    previous_of = {u.dealer: str(u.previous) for u in counted}  # each level's previous dealer
    late = [u for u in found if u.open_to_all and u.dealer in previous_of]
    opened = {t.dealer: t.opened_to_all for t in timelines(events)}
    counted_deals = {u: deals_with(threads, u.team, str(u.previous), u.tick) for u in counted}
    late_deals = {}
    for u in late:
        until = opened.get(u.dealer)
        late_deals[u] = deals_with(threads, u.team, previous_of[u.dealer], (until if until is not None else u.tick) - 1)
    out = []
    for name, rule in RULES.items():
        misses = []
        for u, deals in counted_deals.items():
            n = sum(1 for t in deals if rule(t))
            if n != u.deals:
                misses.append((u.team, int(u.deals or 0), n))
        contradicted = tuple(u.team for u, deals in late_deals.items() if sum(1 for t in deals if rule(t)) >= minimum)
        out.append(RuleFit(name, len(counted) - len(misses), len(counted), tuple(misses), contradicted))
    return sorted(out, key=lambda f: (len(f.contradicted_by), -f.exact))


class UnlockRule(BaseModel):
    """A dealer's `unlock` block in `GET /api/dealers`, validated at the boundary."""

    model_config = ConfigDict(extra="ignore")

    always: bool = False
    early_deals_with: str | None = None
    early_min_deals: int | None = Field(default=None, ge=0)
    early_min_level: int | None = Field(default=None, ge=0)
    open_to_all_at: Any = None


def dealers_from(body: Any) -> list[dict[str, Any]]:
    """Every shape a dealers payload comes in: a list, `{personas: [...]}` / `{dealers: [...]}`, one
    dealer (`GET /api/dealers/<id>`), or the simulator's `{id: dealer}`."""
    body = body.get("body", body) if isinstance(body, dict) and "body" in body else body
    if isinstance(body, list):
        return [d for d in body if isinstance(d, dict)]
    if not isinstance(body, dict):
        raise ValueError("not a dealers payload")
    for key in ("personas", "dealers"):
        if isinstance(body.get(key), list):
            return [d for d in body[key] if isinstance(d, dict)]
    if "id" in body:
        return [body]
    values = [v for v in body.values() if isinstance(v, dict) and "id" in v]
    if values:
        return values
    raise ValueError("not a dealers payload")


@dataclass(frozen=True)
class Requirement:
    """What `GET /api/dealers` says a dealer needs for an early unlock, and our progress."""

    dealer: str
    level: int | None
    status: str
    deals_with: str | None
    min_deals: int | None
    min_level: int | None
    open_to_all_at: Any
    ours: int | None  # our deals with `deals_with` since `since_tick` that count (buys not at the opening price)

    @property
    def missing(self) -> int | None:
        return None if self.min_deals is None or self.ours is None else max(0, self.min_deals - self.ours)


# What the plan counts toward an unlock: a lower bound, never relying on sales or opening-price deals, both
# unverified (t08 and t16 suggest a sale may count; our LAV-03 at Abuela's opening ask counted). "The opening"
# is the dealer's first price in the thread: her opening ask when she speaks first, and also when we bid first
# and she answers with her opening line.
COUNTED = RULES["buys not at the opening price"]


def requirements(
    dealers: Iterable[Mapping[str, Any]], events: Sequence[intel.Event], team: str, since_tick: int = 0
) -> list[Requirement]:
    """Each dealer's early-unlock rule and our deals toward it since `since_tick` (a round's first tick,
    if the count restarts per round). Our own level against `min_level` is the caller's to check."""
    threads = intel.dealer_threads(events)
    out = []
    for d in dealers:
        unlock = UnlockRule.model_validate(d.get("unlock") or {})
        ours = None
        if unlock.early_deals_with:
            deals = deals_with(threads, team, unlock.early_deals_with, 10**9)
            ours = sum(1 for t in deals if (t.fill_tick or 0) >= since_tick and COUNTED(t))
        level = d.get("level")
        out.append(
            Requirement(
                str(d.get("id")),
                level if isinstance(level, int) else None,
                str(d.get("status") or "?"),
                unlock.early_deals_with,
                unlock.early_min_deals,
                unlock.early_min_level,
                unlock.open_to_all_at,
                ours,
            )
        )
    return out
