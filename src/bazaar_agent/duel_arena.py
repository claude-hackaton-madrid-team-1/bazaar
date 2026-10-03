"""Duel arena: score a duel policy offline against a zoo of rival styles and against the real practice payloads.

Nothing here talks to the game. The engine follows what the practice session showed (2026-10-02, shared DB
`duels`, tests/fixtures/evals/duels_done.json):
  - `result = surplus × (1 − decay)^rounds`, `rounds = min(our priced messages, the rival's priced messages)`;
    an accept adds no round (duels 268, 273).
  - an accept settles at the next tick; a duel closes with no deal at its deadline tick;
  - the team accepts ONE offer per tick across every duel (RULES.md), one message per duel per tick.
The order inside a tick is the simulator's (#55): settle last tick's accept, close at the deadline, the rival
answers what we sent last tick, then we read the duel and move.

Rival styles (each with its own limit, never shown to us): `linear` and `convex` concede over the clock;
`oneshot` opens once or twice and then only answers our offers; `titfortat` mirrors our concessions;
`noshow` never speaks but takes an offer inside its floor; `simbot` is #55's bot (0.9 → 0.35 of the pie,
priced off OUR limit). Two-issue duels draw signed day weights for both sides (as the simulator does) and the
truth is scored both ways: signed (the simulator's `days_meaning`) and worst case (every day costs |weight|).
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bazaar_agent import guardrails as gr
from bazaar_agent.agents.duel_v2 import DEFAULTS, OUR_SENDER, V2Params, plan_moves
from bazaar_agent.agents.duelist import DuelMove, duel_action, duel_id, duel_move

STYLES = ("linear", "convex", "oneshot", "titfortat", "noshow", "simbot")
ROBUSTNESS = ("late", "stubborn")  # reported apart, outside the go/no-go: v2's worst cases
ALIASES = ("Rival Azul", "Rival Verde", "Rival Oro", "Rival Rojo", "Rival Noche", "Rival Plata")
RIVAL_ENDGAME = 2  # the simulator's bot takes any deal inside its limit in the last 2 ticks

Policy = Callable[[list[dict[str, Any]], int, Mapping[int, int]], dict[int, DuelMove]]


# ---------------------------------------------------------------- scenarios and rivals


@dataclass
class Scenario:
    role: str  # ours: "seller" | "buyer"
    limit: int  # ours
    rival_limit: int
    style: str
    decay: float = 0.06
    ticks: int = 12
    two_issue: bool = False
    weight: float | None = None  # ours, signed (the simulator's meaning)
    rival_weight: float | None = None
    rival_open: float = 0.4  # how far beyond its limit the rival opens (fraction of its limit)
    rival_floor: float = 0.1  # how close to its limit it goes (fraction of its limit)
    rival_step: float = 0.35  # oneshot: share of its remaining gap it gives per answer; titfortat: mirror ratio
    rival_every: int = 1  # time-based rivals reprice every N ticks

    @property
    def pie(self) -> int:
        return abs(self.rival_limit - self.limit)


def draw(rng: random.Random, style: str, role: str, decay: float, ticks: int, two_issue: bool) -> Scenario:
    """One scenario as the simulator draws them: a cost 30–110, a value 20–80 above it."""
    cost = rng.randint(30, 110)
    value = cost + rng.randint(20, 80)
    limit, rival_limit = (cost, value) if role == "seller" else (value, cost)
    weights = (round(rng.uniform(-4, 4), 1), round(rng.uniform(-4, 4), 1)) if two_issue else (None, None)
    return Scenario(
        role,
        limit,
        rival_limit,
        style,
        decay,
        ticks,
        two_issue,
        weights[0],
        weights[1],
        rival_open=rng.uniform(0.2, 0.6),
        rival_floor=rng.uniform(0.03, 0.25),
        rival_step=rng.uniform(0.2, 0.6) if style == "oneshot" else rng.uniform(0.6, 1.2),
        rival_every=rng.choice((1, 1, 2)),
    )


class Rival:
    """One rival bot. Prices are the rival's: a rival buyer when we sell, a rival seller when we buy."""

    def __init__(self, s: Scenario, start: int) -> None:
        self.s, self.start = s, start
        self.buyer = s.role == "seller"
        rl = s.rival_limit
        self.open = round(rl * (1 - s.rival_open)) if self.buyer else round(rl * (1 + s.rival_open))
        self.floor = round(rl * (1 - s.rival_floor)) if self.buyer else round(rl * (1 + s.rival_floor))
        self.days = 0 if not s.two_issue else (10 if (s.rival_weight or 0) > 0 else 0)
        self.price: int | None = None  # its standing offer
        self.answered_ours = 0  # how many of our offers it has answered

    def utility(self, price: int, days: int) -> float:
        base = self.s.rival_limit - price if self.buyer else price - self.s.rival_limit
        return base + (self.s.rival_weight or 0.0) * days

    def progress(self, tick: int) -> float:
        return min(1.0, max(0.0, (tick - self.start) / max(1, self.s.ticks)))

    def _toward(self, frm: float, to: float, share: float) -> int:
        return round(frm + (to - frm) * min(1.0, max(0.0, share)))

    def target(self, tick: int) -> int | None:
        """Its price this tick, or None when it stays silent (keeps its standing offer)."""
        s, p = self.s, self.progress(tick)
        if s.style == "simbot":
            keep = 0.9 - (0.9 - 0.35) * p
            pie = s.pie
            return max(1, round(s.limit + pie * (1 - keep) if self.buyer else s.limit - pie * (1 - keep)))
        if s.style == "late":  # silent for 3–5 ticks, then concedes over what is left (worst case for free offers)
            wake = 3 + s.rival_every + (1 if s.rival_step > 0.9 else 0)
            if tick - self.start < wake:
                return None
            return self._toward(self.open, self.floor, (tick - self.start - wake) / max(1, s.ticks - wake))
        if s.style == "stubborn":  # one price, said again every tick (duel 274's 103)
            return self._toward(self.open, self.floor, 0.5)
        if s.style in ("linear", "convex"):
            if (tick - self.start) % s.rival_every:
                return None
            shape = p if s.style == "linear" else p**3
            return self._toward(self.open, self.floor, shape)
        return None

    def act(self, tick: int, ours: tuple[int, int] | None, ours_new: bool, our_step: float) -> tuple[str, Any]:
        """('accept', None) | ('offer', price) | ('none', None). `ours` = our standing (price, days)."""
        s = self.s
        left = self.start + s.ticks - tick
        target = self.target(tick)
        if ours is not None and ours_new:
            mine = self.utility(*ours)
            standing = target if target is not None else self.price
            wanted = self.utility(standing, self.days) if standing is not None else math.inf
            floor_ok = mine >= 0 if s.style == "simbot" else mine >= self.utility(self.floor, self.days)
            if floor_ok and (mine >= wanted or left <= RIVAL_ENDGAME):
                return "accept", None
        if s.style == "noshow":
            return "none", None
        if s.style in ("oneshot", "titfortat"):
            if self.price is None:
                return "offer", self.open
            if s.style == "oneshot" and self.answered_ours == 0 and tick - self.start == 2 and s.rival_every == 2:
                return "offer", self._toward(self.open, self.floor, 0.3)  # some open twice (duels 131, 147)
            if ours is not None and ours_new:
                self.answered_ours += 1
                if s.style == "oneshot":
                    return "offer", self._toward(self.price, self.floor, s.rival_step)
                step = max(1.0, our_step * s.rival_step)
                nxt = self.price + step if self.buyer else self.price - step
                nxt = min(nxt, self.floor) if self.buyer else max(nxt, self.floor)
                return "offer", round(nxt)
            return "none", None
        if target is not None and (target != self.price or s.style == "stubborn"):
            return "offer", target
        return "none", None


# ---------------------------------------------------------------- the engine


@dataclass
class Live:
    did: int
    s: Scenario
    rival: Rival
    alias: str
    start: int
    deadline: int
    messages: list[dict[str, Any]] = field(default_factory=list)
    ours: tuple[int, int] | None = None
    ours_tick: int = -1
    ours_prev_price: int | None = None
    rival_offer_id: int = 0
    rival_tick: int = -1
    accepted_by: str | None = None
    accepted_at: int = -1
    accepted_terms: tuple[int, int] | None = None
    status: str = "live"
    price: int | None = None
    days: int = 0
    closed_tick: int | None = None

    def n_priced(self, ours: bool) -> int:
        return sum(1 for m in self.messages if m["price"] is not None and (m["from"] == OUR_SENDER) == ours)

    @property
    def rounds(self) -> int:
        return min(self.n_priced(True), self.n_priced(False))

    def payload(self) -> dict[str, Any]:
        """What GET /api/duels shows us (the practice session's shape)."""
        s = self.s
        rival = self.rival
        rival_offer = None
        if rival.price is not None:
            rival_offer = {"id": self.rival_offer_id, "price": rival.price, "days": rival.days, "tick": None}
        return {
            "duel": self.did,
            "session": 2,
            "status": "live",
            "role": s.role,
            "item": "Arena Card",
            "issues": ["price", "days"] if s.two_issue else ["price"],
            "your_days_weight": s.weight,
            "days_meaning": None,
            "your_limit": s.limit,
            "limit_meaning": "never sell below your cost" if s.role == "seller" else "never pay above your value",
            "rival": self.alias,
            "deadline_tick": self.deadline,
            "decay_per_round": s.decay,
            "rounds": self.rounds,
            "your_offer": None if self.ours is None else {"price": self.ours[0], "days": self.ours[1]},
            "rival_offer": rival_offer,
            "messages": [dict(m) for m in self.messages],
            "result": None,
            "price": None,
            "days": None,
        }


@dataclass(frozen=True)
class Outcome:
    did: int
    style: str
    role: str
    decay: float
    ticks: int
    two_issue: bool
    status: str  # deal | no_deal
    rounds: int
    gain_signed: float  # our surplus at the deal, days at the signed weight (0 without a deal)
    gain_worst: float  # days at |weight| against us
    pie: int
    denied: int  # moves the guardrail refused (should be 0: the policy stays inside the limit)

    @property
    def kept(self) -> float:
        return (1 - self.decay) ** self.rounds

    @property
    def result(self) -> float:
        return self.gain_signed * self.kept if self.status == "deal" else 0.0

    @property
    def result_worst(self) -> float:
        return self.gain_worst * self.kept if self.status == "deal" else 0.0

    @property
    def outside(self) -> bool:
        return self.status == "deal" and min(self.gain_signed, self.gain_worst) <= 0


def _gains(s: Scenario, price: int, days: int) -> tuple[float, float]:
    base = price - s.limit if s.role == "seller" else s.limit - price
    w = s.weight or 0.0
    return base + w * days, base - abs(w) * days


def run_session(
    scenarios: list[Scenario],
    policy: Policy,
    start: int = 100,
    first_id: int = 1,
    rules: gr.Guardrails | None = None,
) -> list[Outcome]:
    """Every scenario as one live duel in the same session (same start and deadline per duration).
    Every move goes through `guardrails.check` first, as `duel run --play` sends it."""
    rules = rules or gr.Guardrails()
    duels = [
        Live(first_id + i, s, Rival(s, start), ALIASES[i % len(ALIASES)], start, start + s.ticks)
        for i, s in enumerate(scenarios)
    ]
    denied = {d.did: 0 for d in duels}
    first_seen = {d.did: start for d in duels}
    end = max(d.deadline for d in duels)
    for tick in range(start, end + 1):
        for d in duels:
            if d.status != "live":
                continue
            if d.accepted_by is not None and d.accepted_at < tick:
                d.status, d.closed_tick = "deal", tick
                d.price, d.days = d.accepted_terms or (0, 0)
                continue
            if tick >= d.deadline:
                d.status, d.closed_tick = "no_deal", tick
                continue
            if d.accepted_by is not None:
                continue
            new = d.ours is not None and d.ours_tick >= d.rival_tick  # it has not answered our offer yet
            step = 0.0
            if new and d.ours is not None and d.ours_prev_price is not None:
                step = abs(d.ours_prev_price - d.ours[0])
            kind, price = d.rival.act(tick, d.ours, new, step)
            if kind == "accept" and d.ours is not None:
                d.accepted_by, d.accepted_at, d.accepted_terms = "rival", tick, d.ours
                d.messages.append({"tick": tick, "from": d.alias, "text": "Deal.", "price": None, "days": None})
            elif kind == "offer":
                d.rival.price, d.rival_tick = int(price), tick
                d.rival_offer_id += 1
                days = d.rival.days if d.s.two_issue else None
                d.messages.append({"tick": tick, "from": d.alias, "text": "", "price": int(price), "days": days})
        live = [d for d in duels if d.status == "live" and d.accepted_by is None]
        if not live or tick >= end:
            continue
        moves = policy([d.payload() for d in live], tick, first_seen)
        accepted = 0
        for d in live:
            move = moves.get(d.did, DuelMove("hold"))
            if move.kind == "hold":
                continue
            payload = d.payload()
            ctx = gr.Context(cash=0, held={}, tick=tick, t_hours=0.0, accepts_this_tick=accepted)
            verdict = gr.check(duel_action(payload, move), ctx, rules)
            if not verdict.allowed:
                denied[d.did] += 1
                continue
            if move.kind == "accept":
                if d.rival.price is None:
                    continue
                accepted += 1
                d.accepted_by, d.accepted_at = "us", tick
                d.accepted_terms = (d.rival.price, d.rival.days if d.s.two_issue else 0)
            elif move.price is not None:
                d.ours_prev_price = d.ours[0] if d.ours is not None else None
                d.ours, d.ours_tick = (move.price, move.days or 0), tick
                d.messages.append(
                    {"tick": tick, "from": OUR_SENDER, "text": "", "price": move.price, "days": move.days}
                )
    out = []
    for d in duels:
        gs, gw = _gains(d.s, d.price, d.days) if d.status == "deal" and d.price is not None else (0.0, 0.0)
        s = d.s
        out.append(
            Outcome(
                d.did, s.style, s.role, s.decay, s.ticks, s.two_issue, d.status, d.rounds, gs, gw, s.pie, denied[d.did]
            )
        )
    return out


# ---------------------------------------------------------------- the policies


def v1_policy(anchor: float = 0.6, floor: float = 0.05, endgame_ticks: int = 2) -> Policy:
    """Today's player as `duel run --play` drives it without Jev: `duel_move` per duel, the first accept
    in list order takes the team's slot (`ledger.reserve_accept`), the others are refused that tick."""

    def play(duels: list[dict[str, Any]], tick: int, first_seen: Mapping[int, int]) -> dict[int, DuelMove]:
        moves, taken = {}, False
        for d in duels:
            did = duel_id(d)
            if did is None:
                continue
            move = duel_move(
                d, tick, first_seen.get(did, tick), anchor=anchor, floor=floor, endgame_ticks=endgame_ticks
            )
            if move.kind == "accept":
                if taken:
                    move = DuelMove("hold", reason="another process took the team's accept this tick")
                taken = True
            moves[did] = move
        return moves

    return play


def v2_policy(params: V2Params = DEFAULTS) -> Policy:
    return lambda duels, tick, first_seen: plan_moves(duels, tick, first_seen, params)


def silent_policy() -> Policy:
    """A reference, not a candidate: never speak, accept the best standing offer in time."""
    return v2_policy(V2Params(max_own_offers=1, open_wait_ticks=10_000))


# ---------------------------------------------------------------- tournament


def tournament(
    policies: Mapping[str, Policy],
    *,
    styles: Iterable[str] = STYLES,
    scenarios: int = 200,
    decays: Iterable[float] = (0.06, 0.08),
    ticks: Iterable[int] = (12,),
    two_issue: bool = False,
    per_session: int = 6,
    seed: int = 7,
    rules: Mapping[str, gr.Guardrails] | None = None,
) -> dict[str, list[Outcome]]:
    """Each policy on the same scenarios: style × scenario × role × decay × duration, `per_session` duels
    sharing one accept per tick (they start and end together, the hard case for the accept slot).
    `rules` per policy name: the guardrails its moves meet (default: GUARDRAILS.md's defaults)."""
    cases: list[Scenario] = []
    for style in styles:
        for decay in decays:
            for n_ticks in ticks:
                rng = random.Random(f"{seed}:{style}:{decay}:{n_ticks}:{two_issue}")
                for _ in range(scenarios):
                    for role in ("seller", "buyer"):
                        cases.append(draw(rng, style, role, decay, n_ticks, two_issue))
    results: dict[str, list[Outcome]] = {}
    for name, policy in policies.items():
        out: list[Outcome] = []
        for i in range(0, len(cases), per_session):
            batch = cases[i : i + per_session]
            out.extend(run_session(batch, policy, first_id=i + 1, rules=(rules or {}).get(name)))
        results[name] = out
    return results


@dataclass(frozen=True)
class Summary:
    n: int
    mean_result: float
    deal_rate: float
    mean_rounds: float
    mean_share: float
    outside: int
    denied: int


def summarize(outcomes: list[Outcome], worst: bool = False) -> Summary:
    n = len(outcomes) or 1
    deals = [o for o in outcomes if o.status == "deal"]
    results = [o.result_worst if worst else o.result for o in outcomes]
    return Summary(
        len(outcomes),
        sum(results) / n,
        len(deals) / n,
        sum(o.rounds for o in deals) / (len(deals) or 1),
        sum(r / o.pie for r, o in zip(results, outcomes, strict=True) if o.pie) / n,
        sum(o.outside for o in outcomes),
        sum(o.denied for o in outcomes),
    )


# ---------------------------------------------------------------- replay on the real practice payloads


def replay(
    duels: list[Mapping[str, Any]], policy: Policy, ticks: int = 12, only_unanswered: bool = True
) -> list[dict[str, Any]]:
    """Replay finished practice duels: the rival sends exactly its recorded priced messages, whatever we do
    (its moves were unilateral when we never answered); it takes our offer only when that offer is at least
    as good for it as its own standing price. Every duel runs on one clock and shares the team's one accept
    per tick. Returns one row per duel: what the policy realised and what the duel really scored."""
    rows = []
    picked = [d for d in duels if d.get("status") in ("deal", "no_deal")]
    if only_unanswered:
        picked = [d for d in picked if not any(m.get("from") == OUR_SENDER for m in d.get("messages") or [])]
    state = {int(d["duel"]): _ReplayDuel(d, ticks) for d in picked}
    begin = min(r.start for r in state.values())
    end = max(r.deadline for r in state.values())
    rules = gr.Guardrails()
    first_seen = {did: r.start for did, r in state.items()}
    for tick in range(begin, end + 1):
        for r in state.values():
            r.server(tick)
        live = [r for r in state.values() if r.live(tick)]
        if not live:
            continue
        moves = policy([r.payload(tick) for r in live], tick, first_seen)
        accepted = 0
        for r in live:
            move = moves.get(r.did, DuelMove("hold"))
            if move.kind == "hold":
                continue
            ctx = gr.Context(cash=0, held={}, tick=tick, t_hours=0.0, accepts_this_tick=accepted)
            if not gr.check(duel_action(r.payload(tick), move), ctx, rules).allowed:
                r.denied += 1
                continue
            if move.kind == "accept" and r.standing is not None:
                accepted += 1
                r.accepted_at, r.terms = tick, r.standing
            elif move.kind == "offer" and move.price is not None:
                r.sent.append((tick, move.price))
    for r in state.values():
        rows.append(r.row())
    return rows


class _ReplayDuel:
    def __init__(self, d: Mapping[str, Any], ticks: int) -> None:
        self.d = d
        self.did = int(d["duel"])
        self.deadline = int(d["deadline_tick"])
        self.start = self.deadline - ticks
        self.rival = str(d.get("rival"))
        self.recorded = sorted(
            (int(m["tick"]), int(m["price"]))
            for m in d.get("messages") or []
            if m.get("from") == self.rival and isinstance(m.get("price"), int | float)
        )
        self.sent: list[tuple[int, int]] = []
        self.standing: int | None = None
        self.rival_sent = 0
        self.accepted_at: int | None = None
        self.rival_took: int | None = None
        self.terms: int | None = None
        self.closed: str | None = None
        self.denied = 0

    def live(self, tick: int) -> bool:
        return self.closed is None and self.accepted_at is None and self.start <= tick < self.deadline

    def server(self, tick: int) -> None:
        if self.closed is not None:
            return
        if self.accepted_at is not None and self.accepted_at < tick:
            self.closed = "deal"
            return
        if tick >= self.deadline:
            self.closed = "no_deal"
            return
        if self.accepted_at is not None:
            return
        last = self.sent[-1] if self.sent else None
        if last is not None and last[0] == tick - 1 and self.standing is not None:
            seller = self.d.get("role") == "seller"  # we sell: the rival buys, it likes low prices
            if (last[1] <= self.standing) if seller else (last[1] >= self.standing):
                self.accepted_at, self.terms = tick, last[1]
                return
        for t, price in self.recorded:
            if t == tick:
                self.standing = price
                self.rival_sent += 1

    def payload(self, tick: int) -> dict[str, Any]:
        said = [(t, self.rival, p) for t, p in self.recorded if t <= tick] + [(t, OUR_SENDER, p) for t, p in self.sent]
        msgs = [{"tick": t, "from": who, "text": "", "price": p, "days": None} for t, who, p in sorted(said)]
        d = dict(self.d)
        d.update(
            status="live",
            result=None,
            price=None,
            rounds=min(len(self.sent), self.rival_sent),
            messages=msgs,
            your_offer=None if not self.sent else {"price": self.sent[-1][1], "days": 0},
            rival_offer=None if self.standing is None else {"price": self.standing, "days": 0},
        )
        return d

    def row(self) -> dict[str, Any]:
        rounds = min(
            len([s for s in self.sent if self.accepted_at is None or s[0] <= self.accepted_at]), self.rival_sent
        )
        limit, seller = int(self.d["your_limit"]), self.d.get("role") == "seller"
        gain = 0.0
        if self.closed == "deal" and self.terms is not None:
            gain = (self.terms - limit) if seller else (limit - self.terms)
        decay = float(self.d.get("decay_per_round") or 0.06)
        return {
            "duel": self.did,
            "role": self.d.get("role"),
            "limit": limit,
            "status": self.closed,
            "price": self.terms if self.closed == "deal" else None,
            "rounds": rounds,
            "result": round(gain * (1 - decay) ** rounds, 2) if gain else 0.0,
            "real_result": self.d.get("result"),
            "denied": self.denied,
        }


def load_practice(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [d for d in data.get("duels", data) if isinstance(d, dict)]
