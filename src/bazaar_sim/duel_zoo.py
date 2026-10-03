"""The duel rival zoo: rival styles fitted to the real practice duels, and an offline engine that scores any policy.

Real duel rivals are other teams' agents (RULES.md: every team meets every other team), so one bot is not
enough to test a duel policy. The zoo has seven rival styles, six named by the night plan (W2a) plus the
jump-then-hold shape seen in duels 273 and 274 (`duel_replay.classify` labels all 26 real payloads):

    linear      concedes at a steady pace from an opening margin to an end margin, posting most ticks (duels 5, 6)
    convex      concedes slowly, then fast near the deadline (duel 201)
    one_shot    posts one or two offers early, then goes silent; may or may not still accept (95, 119, 120, 131...)
    tit_for_tat answers each new priced offer of ours with a concession proportional to ours (202, 268, 85...)
    no_show     never speaks, never accepts (duels 23, 24)
    sim         the simulator's own bot (`duels.py`: asks 0.9 of the pie, concedes to 0.35), replicated exactly
    holdout     opens far, jumps close in one or two steps, then holds its price to the end (duels 273, 274)

Every style except `sim` is a pure function of what a real rival knows: its own limit and days weight, the
decay, the clock and the public message history. Margins are fractions of the rival's OWN limit, like our v1
(`duel_anchor`). The `sim` replica reads both limits, as `duels._rival_price` does. No rival ever offers or
accepts outside its own limit, so an outside-limit close is always the policy's doing.

THE POLICY CONTRACT (what a duel policy plugs into, `bazaar_agent.agents.duelist.duel_move` as is):

    policy(duel: dict, tick: int, started_tick: int) -> move    # move.kind in accept | offer | hold
                                                                # move.price: int | None, move.days: int | None

`duel` has the exact key set of a real `GET /api/duels` row (tests/fixtures/evals/duels_done.json):
`result` is a float (P after decay) once closed, `days_meaning` is null, `rounds` follows the real rule.
`offer` sends a priced message (a two-issue duel without `days` is refused: `missing_days`, no round);
`accept` takes the rival's standing offer; `hold` sends nothing. One move per tick.

THE CLOCK (mirrors `duels.on_tick`): at tick t the rival moves first, seeing our messages up to t-1, then the
policy moves seeing the rival's messages up to t. The real order inside a tick is not known (practice rows show
both); `team_first=True` flips it, so the policy no longer sees the rival's tick-t message before it moves.
An accept at t settles at t+1. At `deadline_tick` an open duel closes with no deal. A duel runs `duel_ticks`
ticks from `started_tick`.

THE SCORE (verified on all 8 real practice deals, `test_duel_zoo`): `rounds = min(our priced messages, the
rival's priced messages)` and `result = surplus × (1 - decay) ** rounds`, in P. A rival that accepts by echoing
our price (duel 267: rounds 1) sends a priced message: `rival_accept_is_priced` (default True). `share` is the
surplus over the pie (both limits) and `score = share × kept`, the RULES.md "share of each deal's pie". A deal
outside our limit keeps its negative `result` and sets `outside_limit` (its real penalty is not published).

Two-issue duels: each side has a signed days weight. The rivals value days with the sign (the simulator's
semantics). Our truth is a scenario flag: `days_truth="signed"` (weight × days) or `"worst"` (-|weight| × days,
the stance of PR #60): the real sign is unverified.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

Role = Literal["seller", "buyer"]
DaysTruth = Literal["signed", "worst"]

US = "you"  # how the real API names our own messages
RIVAL_ALIAS = "Rival Azul"
ENDGAME_TICKS = 2  # the rivals' endgame (the sim bot's `ENDGAME_TICKS`)
PLAN_STYLES = ("linear", "convex", "one_shot", "tit_for_tat", "no_show", "sim")  # the night plan's six
STYLES = (*PLAN_STYLES, "holdout")
DECAYS = (0.06, 0.08, 0.10)  # Duels I, Duels II, Sunday
DUEL_TICKS = (12, 16)
LIMIT_MEANING = {"seller": "never sell below your cost", "buyer": "never pay above your value"}
SIM_START, SIM_END = 0.9, 0.35  # duels.RIVAL_START / RIVAL_END


class Move(Protocol):
    """What a policy returns: `bazaar_agent.agents.duelist.DuelMove` has this shape."""

    @property
    def kind(self) -> str: ...

    @property
    def price(self) -> int | None: ...

    @property
    def days(self) -> int | None: ...


Policy = Callable[[dict[str, Any], int, int], Move]


@dataclass(frozen=True)
class Act:
    """A move, for the reference policies and the rivals: accept | offer | hold."""

    kind: Literal["accept", "offer", "hold"]
    price: int | None = None
    days: int | None = None
    reason: str = ""


HOLD = Act("hold")


# ---------------------------------------------------------------- scenarios


@dataclass(frozen=True)
class Scenario:
    """One duel: our side, the rival's private side, the rival's style and the session's rules."""

    role: Role  # ours
    limit: int  # ours: a seller's cost or a buyer's value
    rival_limit: int
    style: str
    params: Mapping[str, float] = field(default_factory=dict)
    decay: float = 0.06
    duel_ticks: int = 12
    two_issues: bool = False
    days_weight: float | None = None  # ours, signed (P per delivery day)
    rival_days_weight: float | None = None
    days_truth: DaysTruth = "signed"
    started_tick: int = 100
    seed: int = 0
    duel: int = 1
    rival_accept_is_priced: bool = True
    team_first: bool = False  # within a tick we move before the rival (the simulator's order is rival first)

    @property
    def rival_role(self) -> Role:
        return "buyer" if self.role == "seller" else "seller"

    @property
    def deadline_tick(self) -> int:
        return self.started_tick + self.duel_ticks

    @property
    def pie(self) -> float:
        """The surplus a deal can split at the neutral days, both limits known (price-only: |value - cost|)."""
        return float(abs(self.rival_limit - self.limit))


def style_params(style: str, rng: random.Random) -> dict[str, float]:
    """One rival of `style`, its parameters drawn from the ranges fitted to the practice duels (docs/night)."""
    if style in ("linear", "convex"):
        return {
            "open": rng.uniform(0.25, 0.8),  # margin over its own limit at the start (fraction of its limit)
            "end": rng.uniform(0.02, 0.15),  # margin it concedes to by the deadline
            "shape": 1.0 if style == "linear" else rng.uniform(2.0, 3.5),
            "cadence": rng.uniform(0.6, 1.0),  # the chance it posts on a tick when its price moved
        }
    if style == "one_shot":
        return {
            "open": rng.uniform(0.2, 0.7),
            "shots": float(rng.choice((1, 2))),
            "gap": float(rng.randint(1, 3)),  # ticks between its two shots
            "listens": float(rng.random() < 0.5),  # it still reads and accepts our offers after going silent
            "accept": rng.uniform(0.02, 0.2),  # then any offer leaving it this margin (fraction of its limit)
        }
    if style == "tit_for_tat":
        return {
            "open": rng.uniform(0.25, 0.8),
            "end": rng.uniform(0.02, 0.15),
            "ratio": rng.uniform(0.4, 1.2),  # its concession per P of ours
            "drift": rng.uniform(0.0, 0.02),  # a small time-based concession per tick, fraction of its limit
        }
    if style == "holdout":
        return {
            "open": rng.uniform(0.3, 0.8),
            "hold": rng.uniform(0.08, 0.3),  # the margin it jumps to and keeps
            "steps": float(rng.choice((1, 2))),
        }
    if style in ("no_show", "sim"):
        return {}
    raise ValueError(f"unknown rival style {style!r} (one of {', '.join(STYLES)})")


def draw_scenario(
    style: str,
    role: Role,
    rng: random.Random,
    *,
    decay: float = 0.06,
    duel_ticks: int = 12,
    two_issues: bool = False,
    days_truth: DaysTruth = "signed",
    duel: int = 1,
) -> Scenario:
    """A random duel against `style`, limits drawn like the simulator's (`duels.start_session`):
    cost 30-110, value = cost + 20-80, signed days weights in -4..4 on a two-issue session."""
    cost = rng.randint(30, 110)
    value = cost + rng.randint(20, 80)
    ours, theirs = (cost, value) if role == "seller" else (value, cost)
    weights = (round(rng.uniform(-4, 4), 1), round(rng.uniform(-4, 4), 1)) if two_issues else (None, None)
    return Scenario(
        role=role,
        limit=ours,
        rival_limit=theirs,
        style=style,
        params=style_params(style, rng),
        decay=decay,
        duel_ticks=duel_ticks,
        two_issues=two_issues,
        days_weight=weights[0],
        rival_days_weight=weights[1],
        days_truth=days_truth,
        seed=rng.randrange(1 << 30),
        duel=duel,
    )


# ---------------------------------------------------------------- the rivals


@dataclass(frozen=True)
class Offer:
    price: int
    days: int
    tick: int


@dataclass(frozen=True)
class RivalView:
    """What the rival sees at the start of tick `tick`. `other_limit` is read only by the `sim` replica."""

    tick: int
    started_tick: int
    deadline_tick: int
    role: Role  # the rival's role
    limit: int  # the rival's own limit
    days_weight: float | None  # the rival's, signed
    two_issues: bool
    decay: float
    params: Mapping[str, float]
    messages: tuple[Mapping[str, Any], ...]  # every message so far, "from" US or the rival
    our_offer: Offer | None
    its_offer: Offer | None
    rng: random.Random
    other_limit: int

    @property
    def progress(self) -> float:
        total = max(1, self.deadline_tick - self.started_tick)
        return min(1.0, max(0.0, (self.tick - self.started_tick) / total))

    @property
    def endgame(self) -> bool:
        return self.deadline_tick - self.tick <= ENDGAME_TICKS

    def utility(self, price: int, days: int) -> float:
        """The rival's gain over its limit (signed days): a seller from the price, a buyer from its value."""
        base = price - self.limit if self.role == "seller" else self.limit - price
        return base + (self.days_weight or 0.0) * days if self.two_issues else base

    def days(self) -> int:
        """The rival's delivery day: the end of the range that suits it (`duels._rival_days`)."""
        if not self.two_issues:
            return 0
        weight = self.days_weight or 0.0
        return 10 if weight > 0 else 0 if weight < 0 else 5

    def price_for(self, margin: float, days: int) -> int:
        """The price that leaves the rival `margin` (fraction of its limit) over its limit at `days`."""
        want = margin * self.limit - ((self.days_weight or 0.0) * days if self.two_issues else 0.0)
        price = self.limit + want if self.role == "seller" else self.limit - want
        rounded = math.ceil(price) if self.role == "seller" else math.floor(price)  # never past its limit
        return max(1, rounded)

    def our_priced(self) -> list[Mapping[str, Any]]:
        return [m for m in self.messages if m["from"] == US and m.get("price") is not None]

    def fresh(self) -> bool:
        """Our latest priced message came after the rival's latest one: it has not answered it yet. By message
        order, not tick, so a rival that moves after us in the same tick does not answer the same offer twice."""
        last_ours = last_theirs = -1
        for i, m in enumerate(self.messages):
            if m.get("price") is not None:
                if m["from"] == US:
                    last_ours = i
                else:
                    last_theirs = i
        return last_ours > last_theirs


Rival = Callable[[RivalView], Act]


def _accepts(view: RivalView, next_price: int | None, floor: float = 0.0) -> bool:
    """The common rule: take our standing offer when it is newer than the rival's own, inside the rival's
    limit, and at least as good for it as its next price, or in the endgame when it beats `floor`. A rival
    with `listens` = 0 never accepts (a sensitivity case: drawn rivals of these styles always listen)."""
    ours = view.our_offer
    if ours is None or not view.fresh() or not view.params.get("listens", 1.0):  # a deaf rival never accepts
        return False
    mine = view.utility(ours.price, ours.days)
    if mine <= 0:
        return False
    if next_price is not None and mine >= view.utility(next_price, view.days()):
        return True
    return view.endgame and mine >= floor * view.limit


def _conceder(view: RivalView) -> Act:
    p = view.params
    margin = p["open"] - (p["open"] - p["end"]) * view.progress ** p["shape"]
    days = view.days()
    price = view.price_for(margin, days)
    if _accepts(view, price, p["end"]):
        return Act("accept", view.our_offer.price if view.our_offer else None)
    moved = view.its_offer is None or view.its_offer.price != price
    if moved and (view.its_offer is None or view.rng.random() < p["cadence"]):
        return Act("offer", price, days)
    return HOLD


def _one_shot(view: RivalView) -> Act:
    """One or two early offers, then silence. A listening one still takes any fresh offer of ours that leaves it
    its `accept` margin; a deaf one never closes."""
    p = view.params
    days = view.days()
    ours = view.our_offer
    if (
        p["listens"]
        and ours is not None
        and view.fresh()
        and view.utility(ours.price, ours.days) >= p["accept"] * view.limit
    ):
        return Act("accept", ours.price)
    shots = [view.started_tick, view.started_tick + int(p["gap"])][: int(p["shots"])]
    if view.tick in shots:
        second = view.its_offer is not None
        return Act("offer", view.price_for(p["open"] * (0.6 if second else 1.0), days), days)
    return HOLD


def _tit_for_tat(view: RivalView) -> Act:
    """Opens, then answers every new priced offer of ours: its concession is `ratio` times ours, plus drift."""
    p = view.params
    days = view.days()
    floor = view.price_for(p["end"], days)
    if view.its_offer is None:
        return Act("offer", view.price_for(p["open"], days), days)
    current = view.its_offer.price
    ours = view.our_priced()
    if _accepts(view, current, p["end"]):
        return Act("accept", view.our_offer.price if view.our_offer else None)
    if not view.fresh():
        return HOLD
    moved = 0
    if len(ours) >= 2:  # our last concession toward it: a seller comes down, a buyer goes up
        moved = max(
            0, ours[-2]["price"] - ours[-1]["price"] if view.role == "buyer" else ours[-1]["price"] - ours[-2]["price"]
        )
    step = p["ratio"] * moved + p["drift"] * view.limit
    price = current + step if view.role == "buyer" else current - step
    price = min(price, floor) if view.role == "buyer" else max(price, floor)
    rounded = math.floor(price) if view.role == "buyer" else math.ceil(price)
    return Act("offer", max(1, rounded), days)


def _holdout(view: RivalView) -> Act:
    p = view.params
    days = view.days()
    steps = int(p["steps"])
    elapsed = view.tick - view.started_tick
    margin = p["open"] if elapsed == 0 else p["hold"] + (p["open"] - p["hold"]) * max(0, steps - elapsed) / steps
    price = view.price_for(margin, days)
    if _accepts(view, view.price_for(p["hold"], days), p["hold"]):
        return Act("accept", view.our_offer.price if view.our_offer else None)
    if view.its_offer is None or view.its_offer.price != price or view.fresh():
        return Act("offer", price, days)  # it restates its price to every new offer of ours (duel 274)
    return HOLD


def _no_show(view: RivalView) -> Act:
    return HOLD


def _sim(view: RivalView) -> Act:
    """`duels._tick_duel` exactly: the pie from both limits, 0.9 → 0.35 of it, accept what beats its next ask."""
    pie = abs(view.limit - view.other_limit)
    keep = SIM_START - (SIM_START - SIM_END) * view.progress
    if view.role == "buyer":  # we sell: it bids from our cost upward
        target = max(1, round(view.other_limit + pie * (1 - keep)))
    else:
        target = max(1, round(view.other_limit - pie * (1 - keep)))
    days = view.days()
    ours = view.our_offer
    if ours is not None and (view.its_offer is None or ours.tick >= view.its_offer.tick):
        mine = view.utility(ours.price, ours.days)
        if mine >= 0 and (mine >= view.utility(target, days) or view.endgame):
            return Act("accept", ours.price)
    if view.its_offer is None or view.its_offer.price != target:
        return Act("offer", target, days)
    return HOLD


RIVALS: dict[str, Rival] = {
    "linear": _conceder,
    "convex": _conceder,
    "one_shot": _one_shot,
    "tit_for_tat": _tit_for_tat,
    "no_show": _no_show,
    "sim": _sim,
    "holdout": _holdout,
}


# ---------------------------------------------------------------- the engine


@dataclass
class _Duel:
    sc: Scenario
    messages: list[dict[str, Any]] = field(default_factory=list)
    your_offer: dict[str, Any] | None = None
    rival_offer: dict[str, Any] | None = None
    ours: int = 0  # our priced messages
    theirs: int = 0  # the rival's priced messages
    accepted: str | None = None
    accepted_tick: int | None = None
    status: str = "live"
    deal: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    next_id: int = 1
    closed_tick: int | None = None

    @property
    def rounds(self) -> int:
        return min(self.ours, self.theirs)

    def offer(self, price: int, days: int, tick: int) -> dict[str, Any]:
        self.next_id += 1
        return {"id": self.next_id, "price": price, "tick": tick, "days": days}

    def say(self, sender: str, price: int | None, days: int | None, tick: int) -> None:
        shown_days = days if self.sc.two_issues and price is not None else None
        text = f"{price} P?" if price is not None else "..."
        self.messages.append({"tick": tick, "from": sender, "text": text, "price": price, "days": shown_days})


@dataclass(frozen=True)
class Record:
    """One finished duel: the scenario's labels and what the policy got. `result` is in P after decay."""

    style: str
    role: Role
    decay: float
    duel_ticks: int
    two_issues: bool
    status: str  # deal | no_deal
    closer: str | None  # team | rival
    price: int | None
    days: int | None
    rounds: int
    our_messages: int
    gain: float  # surplus over our limit before decay (days at `days_truth`)
    result: float  # gain × (1 - decay) ** rounds
    pie: float
    share: float
    score: float  # share × kept: the RULES.md share of the pie, after decay
    outside_limit: bool
    close_tick: int | None
    errors: tuple[str, ...] = ()
    duel: int = 0

    @property
    def deal(self) -> bool:
        return self.status == "deal"


def our_gain(sc: Scenario, price: int, days: int) -> float:
    """Our surplus over our limit at `days`: signed (the simulator) or worst case (PR #60's stance)."""
    base = price - sc.limit if sc.role == "seller" else sc.limit - price
    if not sc.two_issues:
        return float(base)
    weight = sc.days_weight or 0.0
    return base + (weight * days if sc.days_truth == "signed" else -abs(weight) * days)


def payload(d: _Duel) -> dict[str, Any]:
    """The duel as the real `GET /api/duels` row, the fixture's key set exactly."""
    sc = d.sc
    closed = d.status != "live"
    return {
        "duel": sc.duel,
        "session": 2 if sc.two_issues else 1,
        "status": d.status,
        "role": sc.role,
        "item": "Zoo",
        "issues": ["price", "days"] if sc.two_issues else ["price"],
        "your_days_weight": sc.days_weight if sc.two_issues else None,
        "days_meaning": None,
        "your_limit": sc.limit,
        "limit_meaning": LIMIT_MEANING[sc.role],
        "rival": RIVAL_ALIAS,
        "deadline_tick": sc.deadline_tick,
        "decay_per_round": sc.decay,
        "rounds": d.rounds,
        "your_offer": dict(d.your_offer) if d.your_offer else None,
        "rival_offer": dict(d.rival_offer) if d.rival_offer else None,
        "messages": [dict(m) for m in d.messages],
        "result": d.deal["result"] if closed and d.deal else (0.0 if closed else None),
        "price": d.deal["price"] if d.deal else None,
        "days": d.deal["days"] if d.deal else None,
    }


def _as_offer(raw: Mapping[str, Any] | None) -> Offer | None:
    return None if raw is None else Offer(int(raw["price"]), int(raw.get("days") or 0), int(raw["tick"]))


def _rival_turn(d: _Duel, rival: Rival, tick: int, rng: random.Random) -> None:
    sc = d.sc
    view = RivalView(
        tick=tick,
        started_tick=sc.started_tick,
        deadline_tick=sc.deadline_tick,
        role=sc.rival_role,
        limit=sc.rival_limit,
        days_weight=sc.rival_days_weight,
        two_issues=sc.two_issues,
        decay=sc.decay,
        params=sc.params,
        messages=tuple(d.messages),
        our_offer=_as_offer(d.your_offer),
        its_offer=_as_offer(d.rival_offer),
        rng=rng,
        other_limit=sc.limit,
    )
    act = rival(view)
    if act.kind == "accept" and d.your_offer is not None:
        d.accepted, d.accepted_tick = "rival", tick
        if sc.rival_accept_is_priced:
            d.theirs += 1
            d.say(RIVAL_ALIAS, d.your_offer["price"], d.your_offer["days"], tick)
    elif act.kind == "offer" and act.price is not None:
        days = act.days if sc.two_issues and act.days is not None else 0
        d.rival_offer = d.offer(act.price, days, tick)
        d.theirs += 1
        d.say(RIVAL_ALIAS, act.price, days, tick)


def _team_turn(d: _Duel, policy: Policy, tick: int) -> None:
    sc = d.sc
    move = policy(payload(d), tick, sc.started_tick)
    kind = getattr(move, "kind", "hold")
    if kind == "accept":
        if d.rival_offer is None:
            d.errors.append("no_offer")
            return
        d.accepted, d.accepted_tick = "team", tick
    elif kind == "offer":
        price, days = getattr(move, "price", None), getattr(move, "days", None)
        if not isinstance(price, int) or isinstance(price, bool) or price < 1:
            d.errors.append("invalid_price")
            return
        if sc.two_issues and days is None:
            d.errors.append("missing_days")
            return
        if days is not None and (not isinstance(days, int) or not 0 <= days <= 10):
            d.errors.append("invalid_days")
            return
        d.your_offer = d.offer(price, days or 0, tick)
        d.ours += 1
        d.say(US, price, days, tick)


def _close(d: _Duel, tick: int) -> None:
    offer = d.rival_offer if d.accepted == "team" else d.your_offer
    assert offer is not None
    gain = our_gain(d.sc, offer["price"], offer["days"])
    kept = (1 - d.sc.decay) ** d.rounds
    days = offer["days"] if d.sc.two_issues else 0
    d.status, d.closed_tick = "deal", tick
    d.deal = {"price": offer["price"], "days": days, "gain": gain, "result": round(gain * kept, 2), "kept": kept}


def play(policy: Policy, sc: Scenario, rival: Rival | None = None) -> tuple[Record, dict[str, Any]]:
    """Run one duel to its close: the record, and the final payload (the real `?done=true` row)."""
    d = _Duel(sc)
    rival = rival or RIVALS[sc.style]
    rng = random.Random(f"{sc.seed}:{sc.duel}")
    for tick in range(sc.started_tick, sc.deadline_tick + 2):
        if d.accepted is not None and (d.accepted_tick or 0) < tick:
            _close(d, tick)
            break
        if tick >= sc.deadline_tick:
            d.status, d.closed_tick = "no_deal", tick
            break
        if sc.team_first:
            _team_turn(d, policy, tick)
            if d.accepted is None:
                _rival_turn(d, rival, tick, rng)
        else:
            _rival_turn(d, rival, tick, rng)
            if d.accepted is None:
                _team_turn(d, policy, tick)
    return _record(d), payload(d)


def _record(d: _Duel) -> Record:
    sc = d.sc
    deal = d.deal
    gain = deal["gain"] if deal else 0.0
    kept = deal["kept"] if deal else 1.0
    share = gain / sc.pie if deal and sc.pie else 0.0
    return Record(
        style=sc.style,
        role=sc.role,
        decay=sc.decay,
        duel_ticks=sc.duel_ticks,
        two_issues=sc.two_issues,
        status=d.status,
        closer=d.accepted if deal else None,
        price=deal["price"] if deal else None,
        days=deal["days"] if deal else None,
        rounds=d.rounds,
        our_messages=d.ours,
        gain=round(gain, 2),
        result=deal["result"] if deal else 0.0,
        pie=sc.pie,
        share=round(share, 4),
        score=round(share * kept, 4),
        outside_limit=bool(deal) and gain < 0,
        close_tick=d.closed_tick,
        errors=tuple(d.errors),
        duel=sc.duel,
    )


# ---------------------------------------------------------------- tournaments


def scenarios(
    styles: Sequence[str] = PLAN_STYLES,
    n: int = 200,
    roles: Sequence[Role] = ("seller", "buyer"),
    decays: Sequence[float] = (0.06, 0.08),
    duel_ticks: Sequence[int] = (12,),
    two_issues: bool = False,
    days_truth: DaysTruth = "signed",
    seed: int = 7,
) -> list[Scenario]:
    """The grid: `n` scenarios per style × role × decay × duel length, the same draws for every policy."""
    out: list[Scenario] = []
    for style in styles:
        for role in roles:
            for decay in decays:
                for ticks in duel_ticks:
                    rng = random.Random(f"{seed}:{style}:{role}:{decay}:{ticks}:{two_issues}")
                    for _ in range(n):
                        out.append(
                            draw_scenario(
                                style,
                                role,
                                rng,
                                decay=decay,
                                duel_ticks=ticks,
                                two_issues=two_issues,
                                days_truth=days_truth,
                                duel=len(out) + 1,
                            )
                        )
    return out


def with_params(grid: Iterable[Scenario], styles: Sequence[str], **fixed: float) -> list[Scenario]:
    """The same grid with some parameters of `styles` pinned (a sensitivity run): other duels unchanged."""
    return [replace(sc, params={**sc.params, **fixed}) if sc.style in styles else sc for sc in grid]


def run(policy: Policy, grid: Iterable[Scenario]) -> list[Record]:
    return [play(policy, sc)[0] for sc in grid]


@dataclass(frozen=True)
class Summary:
    """The go/no-go columns for one group of records."""

    n: int
    deals: int
    deal_rate: float
    mean_result: float  # P after decay, per duel (no deal = 0)
    mean_score: float  # share × kept, per duel
    mean_rounds: float  # over deals
    mean_messages: float  # our priced messages per duel
    outside_limit: int
    errors: int

    def row(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "deal_rate": round(self.deal_rate, 3),
            "mean_result": round(self.mean_result, 2),
            "mean_score": round(self.mean_score, 4),
            "mean_rounds": round(self.mean_rounds, 2),
            "mean_messages": round(self.mean_messages, 2),
            "outside_limit": self.outside_limit,
            "errors": self.errors,
        }


def summarize(records: Sequence[Record]) -> Summary:
    deals = [r for r in records if r.deal]
    n = len(records)
    return Summary(
        n=n,
        deals=len(deals),
        deal_rate=len(deals) / n if n else 0.0,
        mean_result=statistics.fmean(r.result for r in records) if n else 0.0,
        mean_score=statistics.fmean(r.score for r in records) if n else 0.0,
        mean_rounds=statistics.fmean(r.rounds for r in deals) if deals else 0.0,
        mean_messages=statistics.fmean(r.our_messages for r in records) if n else 0.0,
        outside_limit=sum(r.outside_limit for r in records),
        errors=sum(len(r.errors) for r in records),
    )


def by(records: Sequence[Record], *keys: str) -> dict[tuple[Any, ...], Summary]:
    """Summaries grouped by record fields, e.g. `by(records, "style", "role")`."""
    groups: dict[tuple[Any, ...], list[Record]] = defaultdict(list)
    for r in records:
        groups[tuple(getattr(r, k) for k in keys)].append(r)
    return {k: summarize(v) for k, v in sorted(groups.items(), key=lambda kv: tuple(map(str, kv[0])))}


def mix_mean(records: Sequence[Record], mix: Mapping[str, float], field_name: str = "result") -> float:
    """A per-duel mean with each style weighted by `mix` (e.g. `duel_replay.practice_mix()`), not by its count."""
    total = sum(w for s, w in mix.items() if any(r.style == s for r in records))
    if not total:
        return 0.0
    out = 0.0
    for style, weight in mix.items():
        picked = [getattr(r, field_name) for r in records if r.style == style]
        if picked:
            out += weight / total * statistics.fmean(float(v) for v in picked)
    return out


# ---------------------------------------------------------------- reference policies (baselines, no bazaar_agent)


def _rival_price(duel: Mapping[str, Any]) -> tuple[int, int] | None:
    offer = duel.get("rival_offer")
    if isinstance(offer, Mapping) and isinstance(offer.get("price"), int):
        return int(offer["price"]), int(offer.get("days") or 0)
    return None


def worst_case_gain(duel: Mapping[str, Any], price: int, days: int) -> float | None:
    """Our surplus on the rival's terms with every day costing |weight| (None: a two-issue duel without a weight).
    The same rule as `bazaar_agent.agents.duelist.worth` / `effective_price` (bazaar_sim does not import the
    agent): keep the two in step if the sign of `your_days_weight` is ever confirmed."""
    limit, role = duel["your_limit"], duel["role"]
    base = price - limit if role == "seller" else limit - price
    if "days" not in (duel.get("issues") or []):
        return float(base)
    weight = duel.get("your_days_weight")
    return None if not isinstance(weight, int | float) else base - abs(weight) * days


def accept_first_inside(duel: dict[str, Any], tick: int, started_tick: int) -> Act:
    """Never talk; accept the first rival offer strictly inside our limit (worst-case days)."""
    rival = _rival_price(duel)
    gain = worst_case_gain(duel, *rival) if rival else None
    return Act("accept", rival[0]) if rival and gain is not None and gain > 0 else HOLD


def endgame_accept(duel: dict[str, Any], tick: int, started_tick: int, endgame_ticks: int = ENDGAME_TICKS) -> Act:
    """Never talk; in the last `endgame_ticks` ticks accept the rival's standing offer if strictly inside."""
    if duel["deadline_tick"] - tick > endgame_ticks:
        return HOLD
    return accept_first_inside(duel, tick, started_tick)


def make_anchor_once(anchor: float = 0.3, endgame_ticks: int = ENDGAME_TICKS) -> Policy:
    """One priced anchor at our limit ± `anchor`, then silence; accept inside the limit in the endgame or once
    the rival's offer reaches half our anchor's surplus. A crude "silence is free" reference, not policy v2."""

    def policy(duel: dict[str, Any], tick: int, started_tick: int) -> Act:
        limit, role = duel["your_limit"], duel["role"]
        rival = _rival_price(duel)
        gain = worst_case_gain(duel, *rival) if rival else None
        good = gain is not None and (gain >= anchor * limit / 2 or duel["deadline_tick"] - tick <= endgame_ticks)
        if rival and good and gain is not None and gain > 0:
            return Act("accept", rival[0])
        if duel.get("your_offer") is None:
            price = math.ceil(limit * (1 + anchor)) if role == "seller" else math.floor(limit * (1 - anchor))
            days = 0 if "days" in (duel.get("issues") or []) else None
            return Act("offer", max(1, price), days)
        return HOLD

    return policy


REFERENCE_POLICIES: dict[str, Policy] = {
    "accept_first_inside": accept_first_inside,
    "endgame_accept": endgame_accept,
    "anchor_once": make_anchor_once(),
}
