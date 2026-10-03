"""The ladder plan for a trading window: which dealer conversations to open, when, with which BidPlan.

RULES.md "Scoring": the ladder counts each level's best three deals (a missing one as zero), so the
first job of a window is three high-share deals per dealer we can trade with; later slots only serve the
album. Every slot is a `BidPlan` from the floor table (`ladder.plan_for`) under GUARDRAILS.md, with the
backtest's expected share, price and ticks next to it (`ladder_replay`), and it fits:
- one open conversation per dealer at a time (RULES.md), a slot every `slot_ticks`;
- each dealer's `deals_per_team_per_hour` and each pack's `per_team_per_hour` (`GET /api/dealers`);
- `max_spend_per_game_hour` and `cash_floor`, reserving each slot's max price (the worst case);
- the team's accept slot is untouched when the dealer takes our bid (most closes), so it is not budgeted.
A dealer we cannot plan for (no floor learned, or a cap below its market) is listed as blocked, with why.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bazaar_agent.guardrails import Guardrails
from bazaar_agent.ladder import Conversation, FloorRow, PlanChoice, main_rows, plan_for, rarity_of_class
from bazaar_agent.ladder_replay import Summary, backtest, fit
from bazaar_agent.strategy import dealer_command

UNLIMITED = 10_000  # a quota the payload does not state
SATURDAY_OPEN_T = 4.0  # game hours: Friday 19:00–23:00 is t 0–4, Saturday opens at t 4.0 (09:00 Madrid)


@dataclass(frozen=True)
class Window:
    """The trading window to plan, in game time. `wall_start` labels tick 0 (Madrid time, HH:MM)."""

    t_start: float = SATURDAY_OPEN_T
    minutes: int = 90
    tick_seconds: float = 30.0
    wall_start: str = "09:00"

    @property
    def ticks(self) -> int:
        return int(self.minutes * 60 // self.tick_seconds)

    @property
    def game_hours(self) -> int:
        """How many game hours the window touches (each has its own quotas and spend cap)."""
        return int(self.t_at(self.ticks - 1)) - int(self.t_start) + 1

    def t_at(self, tick: int) -> float:
        return self.t_start + tick * self.tick_seconds / 3600

    def wall(self, tick: int) -> str:
        h, m = (int(x) for x in self.wall_start.split(":"))
        seconds = (h * 60 + m) * 60 + int(tick * self.tick_seconds)
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


@dataclass(frozen=True)
class Grant:
    tick: int
    cash: int


@dataclass(frozen=True)
class DealerQuota:
    dealer: str
    level: int
    deals_per_hour: int
    packs_per_hour: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Target:
    """What to buy in one slot: a price class and, when known, the card ref (`LAV-07`) or pack id."""

    dealer: str
    price_class: str
    ref: str | None = None

    @property
    def topic(self) -> dict[str, Any]:
        if self.price_class.startswith("pack:"):
            return {"buy": {"pack": self.price_class.split(":", 1)[1]}}
        if self.ref:
            return {"buy": {"card": self.ref}}
        return {"buy": {"rarity": rarity_of_class(self.price_class)}}


@dataclass(frozen=True)
class Slot:
    tick: int
    wall: str
    game_hour: int
    target: Target
    start: int
    step: int
    max_price: int
    expected_share: float
    expected_price: float | None
    expected_ticks: float | None
    why: str

    def as_dict(self) -> dict[str, Any]:
        t = self.target
        item = t.price_class.split(":", 1)[1] if t.price_class.startswith("pack:") else t.ref or "<ref>"
        cmd = dealer_command(item, t.dealer, self.start, self.max_price, self.step)
        return {
            "tick": self.tick,
            "wall": self.wall,
            "game_hour": self.game_hour,
            "dealer": t.dealer,
            "price_class": t.price_class,
            "ref": t.ref,
            "topic": t.topic,
            "plan": {"start": self.start, "step": self.step, "max": self.max_price},
            "expected": {
                "share": round(self.expected_share, 3),
                "price": None if self.expected_price is None else round(self.expected_price, 1),
                "ticks": None if self.expected_ticks is None else round(self.expected_ticks, 1),
            },
            "command": f"{cmd} --live",
            "why": self.why,
        }


@dataclass(frozen=True)
class ClassPlan:
    dealer: str
    price_class: str
    choice: PlanChoice
    backtest: Summary | None


def class_plans(
    convs: Sequence[Conversation],
    rows: Iterable[FloorRow],
    rules: Guardrails,
    *,
    q: float = 0.5,
    caps: Mapping[tuple[str, str], int] | None = None,
    runs: int = 2000,
    seed: int = 0,
) -> dict[tuple[str, str], ClassPlan]:
    """A plan and its backtest per dealer × price class (buys only). `caps` overrides the guardrail cap
    for one dealer × class: a what-if for the report, never what the runtime enforces."""
    out = {}
    for key, row in main_rows(r for r in rows if r.price_class != "sell").items():
        cap = (caps or {}).get(key, rules.max_price_for(rarity_of_class(row.price_class)))
        choice = plan_for(row, cap, q=q)
        summary = None
        if choice.plan is not None:
            summary = backtest(choice.plan, fit(convs, *key, row.opening), runs=runs, seed=seed)
        out[key] = ClassPlan(key[0], key[1], choice, summary)
    return out


@dataclass(frozen=True)
class Schedule:
    slots: tuple[Slot, ...]
    blocked: tuple[tuple[str, str, str], ...]  # (dealer, price class, why)
    notes: tuple[str, ...]


def schedule(
    plans: Mapping[tuple[str, str], ClassPlan],
    targets: Sequence[Target],
    quotas: Mapping[str, DealerQuota],
    rules: Guardrails,
    *,
    window: Window | None = None,
    cash: int,
    grants: Sequence[Grant] = (),
    spent_this_hour: int = 0,
    slot_ticks: int | None = None,
) -> Schedule:
    """Place `targets` in order on each dealer's timeline. A target whose class has no plan is blocked;
    one that does not fit the budget or the quotas is skipped with a note (later targets may still fit)."""
    window = window or Window()
    blocked: list[tuple[str, str, str]] = []
    notes: list[str] = []
    slots: list[Slot] = []
    next_free: dict[str, int] = {}
    deals: dict[tuple[str, int], int] = {}
    packs: dict[tuple[str, int], int] = {}
    spend: dict[int, int] = {int(window.t_start): spent_this_hour}

    def cash_left(at: int) -> int:
        """Cash at tick `at` once every slot opened by then has spent its whole max price."""
        granted = sum(g.cash for g in grants if g.tick <= at)
        return cash + granted - sum(s.max_price for s in slots if s.tick <= at)

    unplaced: dict[tuple[str, str], int] = {}
    for target in targets:
        key = (target.dealer, target.price_class)
        cp = plans.get(key)
        quota = quotas.get(target.dealer)
        if cp is None or cp.choice.plan is None or quota is None:
            why = cp.choice.reason if cp is not None else "no conversation seen for this dealer and class"
            if quota is None:
                why = f"{target.dealer} is not in play for us (no quota known)"
            if (target.dealer, target.price_class, why) not in blocked:
                blocked.append((target.dealer, target.price_class, why))
            continue
        plan, bt = cp.choice.plan, cp.backtest
        ticks_needed = slot_ticks or max(4, math.ceil((bt.mean_ticks if bt and bt.mean_ticks else 6) + 2))
        tick = next_free.get(target.dealer, 0)
        placed = False
        while tick + ticks_needed <= window.ticks:
            hour = int(window.t_at(tick))
            # a slot placed now also spends at every later slot's tick: the floor must hold at each
            checks = [tick, *(s.tick for s in slots if s.tick > tick)]
            pack = target.price_class.split(":", 1)[1] if target.price_class.startswith("pack:") else None
            reason = None
            if deals.get((target.dealer, hour), 0) >= quota.deals_per_hour:
                reason = f"{target.dealer} quota {quota.deals_per_hour}/h"
            elif pack and packs.get((pack, hour), 0) >= quota.packs_per_hour.get(pack, UNLIMITED):
                reason = f"{pack} quota"
            elif pack and sum(n for (_, h), n in packs.items() if h == hour) >= rules.max_packs_per_game_hour:
                reason = f"max_packs_per_game_hour {rules.max_packs_per_game_hour}"  # every pack id together
            elif spend.get(hour, 0) + plan.max_price > rules.max_spend_per_game_hour:
                reason = f"max_spend_per_game_hour {rules.max_spend_per_game_hour}"
            elif min(cash_left(t) for t in checks) - plan.max_price < rules.cash_floor:
                reason = f"cash_floor {rules.cash_floor}"
            if reason is None:
                share = bt.mean_share if bt else 0.0
                why = f"{cp.choice.reason}; reserve {plan.max_price} P"
                slots.append(
                    Slot(
                        tick,
                        window.wall(tick),
                        hour,
                        target,
                        plan.start,
                        plan.step,
                        plan.max_price,
                        share,
                        bt.mean_price if bt else None,
                        bt.mean_ticks if bt else None,
                        why,
                    )
                )
                deals[(target.dealer, hour)] = deals.get((target.dealer, hour), 0) + 1
                if pack:
                    packs[(pack, hour)] = packs.get((pack, hour), 0) + 1
                spend[hour] = spend.get(hour, 0) + plan.max_price
                next_free[target.dealer] = tick + ticks_needed
                placed = True
                break
            if reason.startswith("cash_floor"):
                later = [g.tick for g in grants if g.tick > tick]
                if not later:
                    break
                tick = min(later)
                continue
            next_hour_tick = math.ceil((hour + 1 - window.t_start) * 3600 / window.tick_seconds)
            if next_hour_tick <= tick:
                break
            tick = next_hour_tick
        if not placed:
            unplaced[key] = unplaced.get(key, 0) + 1
    notes += [f"{n} more {d} {c} did not fit the window's budget or quotas" for (d, c), n in unplaced.items()]
    return Schedule(tuple(sorted(slots, key=lambda s: (s.tick, s.target.dealer))), tuple(blocked), tuple(notes))


def best_three_first(plans: Mapping[tuple[str, str], ClassPlan], dealer: str) -> list[str]:
    """A dealer's plannable price classes, the highest expected share first (packs last: they cost
    a pack slot): the ladder counts that dealer's best three deals."""
    mine = [cp for (d, _), cp in plans.items() if d == dealer and cp.choice.plan is not None and cp.backtest]
    mine.sort(key=lambda cp: (cp.price_class.startswith("pack:"), -(cp.backtest.mean_share if cp.backtest else 0)))
    return [cp.price_class for cp in mine]


def quotas_from_dealers(dealers: Iterable[Mapping[str, Any]]) -> dict[str, DealerQuota]:
    """`GET /api/dealers` → quotas: each dealer's deals per team per hour and its pack allotments. A
    missing number means no quota (as in `strategy.dealer_quotes`); a 0 means none this hour."""
    out = {}
    for d in dealers:
        menu = d.get("menu") or {}
        packs = {str(s["pack"]): _count(s.get("per_team_per_hour")) for s in menu.get("sells") or [] if s.get("pack")}
        deals = _count(menu.get("deals_per_team_per_hour"))
        out[str(d["id"])] = DealerQuota(str(d["id"]), int(d.get("level") or 0), deals, packs)
    return out


def _count(value: Any) -> int:
    return UNLIMITED if value is None else int(value)


# Quotas when no `GET /api/dealers` dump is given: Abuela from the real payload (tests/fixtures/api,
# Friday), El Chato from the #55 simulator's dealers.json (not yet seen in a real payload: check at 09:00).
DEFAULT_QUOTAS = {
    "abuela": DealerQuota("abuela", 1, 8, {"sobre_barrio": 3}),
    "chato": DealerQuota("chato", 2, 6, {"sobre_plata": 2, "sobre_oro": 1}),
}
RARITY_CLASS = {"common": "card:common", "uncommon": "card:uncommon", "rare": "card:rare"}


def default_targets(
    plans: Mapping[tuple[str, str], ClassPlan],
    quotas: Mapping[str, DealerQuota],
    *,
    hours: int = 1,
    refs: Sequence[tuple[str, str]] = (),
) -> list[Target]:
    """First every dealer's best three deals (highest level first: its deals also unlock the next
    level early), each in that dealer's highest-share class; then the album fill, dealers interleaved,
    each rotating through its classes up to its deals per hour × `hours`. `refs` are (ref, rarity)
    pairs handed out to card slots of that rarity, in order. Classes we cannot plan for are listed once
    so the schedule reports them as blocked."""
    pool: dict[str, list[str]] = {}
    for ref, rarity in refs:
        pool.setdefault(RARITY_CLASS.get(rarity, ""), []).append(ref)
    dealers = sorted(quotas, key=lambda d: -quotas[d].level)
    best: list[tuple[str, str]] = []
    fill: dict[str, list[str]] = {}
    for dealer in dealers:
        ranked = best_three_first(plans, dealer)
        if not ranked:
            continue
        best += [(dealer, ranked[0])] * 3
        room = quotas[dealer].deals_per_hour * hours - 3
        rotation = ranked[1:] + ranked[:1]
        fill[dealer] = [rotation[i % len(rotation)] for i in range(max(0, room))]
    rest = [
        (d, fill[d][i])
        for i in range(max((len(v) for v in fill.values()), default=0))
        for d in fill
        if i < len(fill[d])
    ]
    out = []
    for dealer, cls in best + rest:
        out.append(Target(dealer, cls, pool[cls].pop(0) if pool.get(cls) else None))
    out += [Target(d, c) for (d, c), cp in sorted(plans.items()) if cp.choice.plan is None]
    return out


def plan_document(
    convs: Sequence[Conversation],
    rows: Sequence[FloorRow],
    rules: Guardrails,
    *,
    cash: int,
    grants: Sequence[Grant] = (),
    window: Window | None = None,
    quotas: Mapping[str, DealerQuota] = DEFAULT_QUOTAS,
    refs: Sequence[tuple[str, str]] = (),
    caps: Mapping[tuple[str, str], int] | None = None,
    source: str = "",
    runs: int = 2000,
) -> dict[str, Any]:
    """Everything `ladder_plan.json` holds: the floor table, a plan and backtest per class, the window's
    schedule, what is blocked and why. With `caps`, a what-if: the runtime still enforces GUARDRAILS.md."""
    window = window or Window()
    plans = class_plans(convs, rows, rules, caps=caps, runs=runs)
    sched = schedule(
        plans,
        default_targets(plans, quotas, hours=window.game_hours, refs=refs),
        quotas,
        rules,
        window=window,
        cash=cash,
        grants=grants,
    )
    by_hour: dict[int, dict[str, Any]] = {}
    for s in sched.slots:
        h = by_hour.setdefault(s.game_hour, {"slots": 0, "reserved": 0, "expected_spend": 0.0})
        h["slots"] += 1
        h["reserved"] += s.max_price
        h["expected_spend"] = round(h["expected_spend"] + (s.expected_price or s.max_price), 1)
    return {
        "what_if_caps": {f"{d}:{c}": v for (d, c), v in (caps or {}).items()} or None,
        "source": source,
        "conversations": len(convs),
        "window": {
            "wall_start": window.wall_start,
            "minutes": window.minutes,
            "tick_seconds": window.tick_seconds,
            "ticks": window.ticks,
            "t_start": window.t_start,
        },
        "budget": {
            "cash_at_open": cash,
            "grants": [{"tick": g.tick, "wall": window.wall(g.tick), "cash": g.cash} for g in grants],
            "cash_floor": rules.cash_floor,
            "max_spend_per_game_hour": rules.max_spend_per_game_hour,
            "max_packs_per_game_hour": rules.max_packs_per_game_hour,
        },
        "quotas": {
            d: {"level": q.level, "deals_per_hour": q.deals_per_hour, "packs_per_hour": dict(q.packs_per_hour)}
            for d, q in quotas.items()
        },
        "floor_table": [r.as_dict() for r in rows],
        "plans": [
            {
                "dealer": d,
                "price_class": c,
                "opening": cp.choice.row.opening,
                "floor_p50": cp.choice.floor,
                "cap": cp.choice.cap,
                "plan": (
                    None
                    if cp.choice.plan is None
                    else [cp.choice.plan.start, cp.choice.plan.step, cp.choice.plan.max_price]
                ),
                "reason": cp.choice.reason,
                "backtest": None if cp.backtest is None else cp.backtest.as_dict(),
            }
            for (d, c), cp in sorted(plans.items())
        ],
        "schedule": [s.as_dict() for s in sched.slots],
        "per_game_hour": {str(h): v for h, v in sorted(by_hour.items())},
        "blocked": [{"dealer": d, "price_class": c, "why": w} for d, c, w in sched.blocked],
        "notes": list(sched.notes),
    }
