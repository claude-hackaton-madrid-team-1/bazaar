"""Rival affinity map: which set each team chases, with a probability, from the public feed alone.

Every team holds the same six set multipliers, shuffled (RULES.md). So a team's affinities are one of the
6! = 720 assignments of the multiset to the six sets, and the evidence picks among them:

  - interest: what a team buys, bids for or asks a dealer for counts for that set; what it sells or lists
    counts against it (`Signal.weight`, signed). A set's interest S raises the assignments that give it a
    high multiplier: log-weight `beta × S × z(multiplier)`, z = the multiplier standardised over the six;
  - prices: a team that paid (or bids) p for a card of book b values it at least p, and a card's value is
    at most `book × multiplier × (1 + page bonus)`; so a p above `book × a × 1.25` makes multiplier a
    unlikely (a soft step, with `noise` for teams that overpay).

The posterior over the 720 assignments (uniform prior) gives, per team and set, P(the set holds the top
multiplier) and the expected multiplier. A set nobody has seen yet (not released) keeps the prior: its
mass is what the evidence leaves. Pure functions over feed events; the multiset comes from our own
`/api/me` (`multipliers_from`), never from this file.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from bazaar_agent import intel

Event = dict[str, Any]
TEAM = intel.TEAM_ID

SignalKind = Literal["buy", "bid", "topic", "sell", "ask"]
# How much one observation says about a team's interest in a set (signed in `Signal.weight`). Buying (with
# cash, from a team or a dealer) is the strongest; a standing bid or a dealer topic is a stated wish;
# selling says the set matters less to them, a listing (often a duplicate) says it weakly.
WEIGHTS: dict[str, float] = {"buy": 1.0, "bid": 0.6, "topic": 0.5, "sell": -0.6, "ask": -0.3}


@dataclass(frozen=True)
class Signal:
    team: str
    set_code: str
    kind: SignalKind
    ref: str
    tick: int
    price: int | None = None  # cash paid or bid for this one card (None: a bundle, a swap or a topic)
    book: float | None = None

    @property
    def weight(self) -> float:
        return WEIGHTS[self.kind]


def _cards(side: dict[str, Any] | None) -> list[str]:
    side = side or {}
    refs = [str(t).split(":", 1)[-1] for t in (side.get("types") or []) + (side.get("cards") or [])]
    return [r for r in refs if intel.set_of(r)]


def _assets(side: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [a for a in (side or {}).get("assets") or [] if isinstance(a, dict) and intel.set_of(a.get("ref"))]


def signals(events: Iterable[Event], catalog: dict[str, Any] | None = None) -> list[Signal]:
    """Every team's public trading signals, de-duplicated: one per settlement item, one bid per (team, card)
    at its highest price, one ask per (team, copy) at its lowest, one dealer topic per (team, card or set).
    A reprice is the same wish, so it does not count twice."""
    book = intel.book_values(catalog)
    out: list[Signal] = []
    bids: dict[tuple[str, str], Signal] = {}
    asks: dict[tuple[str, int | str], Signal] = {}
    topics: dict[tuple[str, str], Signal] = {}
    asset_ref: dict[int, str] = {}

    def remember(assets: Iterable[dict[str, Any]]) -> None:
        for a in assets:
            if isinstance(a.get("id"), int):
                asset_ref[int(a["id"])] = str(a.get("ref"))

    for e in events:
        kind, p, tick = e.get("type"), e.get("payload") or {}, int(e.get("tick") or 0)
        if kind == "settlement":
            items = [i for i in p.get("items") or [] if intel.set_of(i.get("ref"))]
            remember(items)
            cash = int(p.get("price") or 0)
            # One card for cash: the price is that card's. A bundle or a swap: interest only.
            one = len(p.get("items") or []) == 1 and cash > 0
            for i in items:
                ref, s = str(i.get("ref")), str(intel.set_of(i.get("ref")))
                price = cash if one else None
                if TEAM.match(str(i.get("to"))):
                    out.append(Signal(str(i["to"]), s, "buy", ref, tick, price, book.get(ref)))
                if TEAM.match(str(i.get("frm"))):
                    out.append(Signal(str(i["frm"]), s, "sell", ref, tick, price, book.get(ref)))
        elif kind == "offer.listed" and TEAM.match(str(e.get("actor") or "")):
            team, offer = str(e["actor"]), p.get("offer") or {}
            give, want = offer.get("give") or {}, offer.get("want") or {}
            given, wanted = _assets(give), _cards(want)
            remember(given)
            cash_for = int(give.get("cash") or 0) if len(wanted) == 1 and not given else 0
            for ref in wanted:
                sig = Signal(team, str(intel.set_of(ref)), "bid", ref, tick, cash_for or None, book.get(ref))
                old = bids.get((team, ref))
                if old is None or (sig.price or 0) > (old.price or 0):
                    bids[(team, ref)] = sig
            ask_for = int(want.get("cash") or 0) if len(given) == 1 and not wanted else 0
            for a in given:
                ref = str(a.get("ref"))
                key: int | str = int(a["id"]) if isinstance(a.get("id"), int) else ref
                sig = Signal(team, str(intel.set_of(ref)), "ask", ref, tick, ask_for or None, book.get(ref))
                old_ask = asks.get((team, key))
                if old_ask is None or (sig.price or 10**9) < (old_ask.price or 10**9):
                    asks[(team, key)] = sig
        elif kind == "thread.opened" and TEAM.match(str(p.get("team") or "")):
            team, topic = str(p["team"]), p.get("topic") or {}
            buy, sell = topic.get("buy") or {}, topic.get("sell") or {}
            ref = str(buy.get("card") or "")
            wished = intel.set_of(ref) or (str(buy["set"]) if buy.get("set") else None)
            if wished:
                topics.setdefault((team, ref or wished), Signal(team, wished, "topic", ref or wished, tick))
            for asset_id in sell.get("assets") or []:
                if isinstance(asset_id, int) and asset_id in asset_ref:
                    sold = asset_ref[asset_id]
                    asks.setdefault((team, asset_id), Signal(team, str(intel.set_of(sold)), "ask", sold, tick))
    return out + list(bids.values()) + list(asks.values()) + list(topics.values())


# ---------------------------------------------------------------- the posterior


@dataclass(frozen=True)
class ModelParams:
    beta: float = 0.5  # log-weight per unit of signed interest per standard deviation of the multiplier
    noise: float = 0.2  # share of buys and bids above value (bots, page bonus hunts): a price never rules out
    bonus: float = 0.25  # a card's value can carry up to this page bonus share on top of book × multiplier
    softness: float = 0.1  # width of the price step, as a share of the card's book
    damp: bool = True  # interest counts as sign × log(1 + |S|): a bot repeating one policy is one decision


@dataclass(frozen=True)
class TeamAffinity:
    team: str
    sets: tuple[str, ...]
    p_top: dict[str, float]  # P(the set holds the highest multiplier)
    expected: dict[str, float]  # E[multiplier]
    distribution: dict[str, dict[float, float]]  # set -> multiplier -> probability
    evidence: dict[str, float]  # signed interest per set
    signals: int

    @property
    def top_set(self) -> str:
        return max(self.p_top, key=lambda s: (self.p_top[s], s))

    @property
    def confidence(self) -> float:
        return self.p_top[self.top_set]

    def p_at_least(self, set_code: str, multiplier: float) -> float:
        return sum(p for a, p in self.distribution.get(set_code, {}).items() if a >= multiplier - 1e-9)


def multipliers_from(me: dict[str, Any]) -> tuple[float, ...]:
    """The shared multiset, read from our own `/api/me` (every team holds the same six, shuffled)."""
    return tuple(sorted(float(v) for v in (me.get("affinity") or {}).values()))


def _price_loglik(s: Signal, a: float, mp: ModelParams) -> float:
    if s.price is None or not s.book or s.kind not in ("buy", "bid"):
        return 0.0
    ceiling = s.book * a * (1 + mp.bonus)
    width = max(1e-6, mp.softness * s.book)
    x = max(-50.0, min(50.0, (ceiling - s.price) / width))
    return math.log(mp.noise + (1 - mp.noise) / (1 + math.exp(-x)))


def team_affinity(
    team: str,
    sigs: Sequence[Signal],
    sets: Sequence[str],
    multipliers: Sequence[float],
    params: ModelParams | None = None,
) -> TeamAffinity:
    """The posterior over the assignments of `multipliers` to `sets` for one team (uniform prior)."""
    mp = params or ModelParams()
    sets, mult = tuple(sets), tuple(multipliers)
    if len(sets) != len(mult):
        raise ValueError(f"{len(sets)} sets but {len(mult)} multipliers")
    mean = sum(mult) / len(mult)
    sd = math.sqrt(sum((a - mean) ** 2 for a in mult) / len(mult)) or 1.0
    interest: dict[str, float] = defaultdict(float)
    price_sigs: dict[str, list[Signal]] = defaultdict(list)
    for s in sigs:
        if s.team != team or s.set_code not in sets:
            continue
        interest[s.set_code] += s.weight
        if s.price is not None and s.kind in ("buy", "bid"):
            price_sigs[s.set_code].append(s)
    level = {st: math.copysign(math.log1p(abs(v)), v) if mp.damp else v for st, v in interest.items()}
    # Per (set, multiplier) log-weight: the assignment's log-weight is the sum over its six pairs.
    pair = {
        (st, a): mp.beta * level.get(st, 0.0) * (a - mean) / sd + sum(_price_loglik(s, a, mp) for s in price_sigs[st])
        for st in sets
        for a in set(mult)
    }
    logw: list[tuple[float, tuple[float, ...]]] = []
    for perm in set(itertools.permutations(mult)):
        logw.append((sum(pair[(st, a)] for st, a in zip(sets, perm, strict=True)), perm))
    top = max(w for w, _ in logw)
    total = sum(math.exp(w - top) for w, _ in logw)
    dist: dict[str, dict[float, float]] = {st: defaultdict(float) for st in sets}
    for w, perm in logw:
        p = math.exp(w - top) / total
        for st, a in zip(sets, perm, strict=True):
            dist[st][a] += p
    best = max(mult)
    return TeamAffinity(
        team,
        sets,
        {st: round(dist[st].get(best, 0.0), 4) for st in sets},
        {st: round(sum(a * p for a, p in dist[st].items()), 4) for st in sets},
        {st: {a: round(p, 4) for a, p in sorted(dist[st].items())} for st in sets},
        {st: round(interest[st], 2) for st in sets},
        sum(1 for s in sigs if s.team == team),
    )


@dataclass(frozen=True)
class AffinityMap:
    teams: dict[str, TeamAffinity] = field(default_factory=dict)

    def chasers(self, set_code: str, min_p: float = 0.5) -> list[str]:
        """Teams whose top set is `set_code` with probability at least `min_p`, likeliest first."""
        rows = [(t.p_top.get(set_code, 0.0), t.team) for t in self.teams.values()]
        return [team for p, team in sorted(rows, reverse=True) if p >= min_p]

    def expected(self, team: str, set_code: str, default: float = 1.0) -> float:
        t = self.teams.get(team)
        return t.expected.get(set_code, default) if t else default


def affinity_map(
    events: Iterable[Event],
    sets: Sequence[str],
    multipliers: Sequence[float],
    catalog: dict[str, Any] | None = None,
    params: ModelParams | None = None,
    exclude: Iterable[str] = (),
    teams: Iterable[str] = (),
) -> AffinityMap:
    """One `TeamAffinity` per team that joined or traded (and every id in `teams`), except `exclude`. A team
    with no signal keeps the uniform prior."""
    events = list(events)
    sigs = signals(events, catalog)
    joined = {str((e.get("payload") or {}).get("team")) for e in events if e.get("type") == "team.joined"}
    skip = set(exclude)
    seen = {s.team for s in sigs} | {t for t in joined if TEAM.match(t)} | set(teams)
    ordered = sorted(seen - skip, key=lambda t: (len(t), t))
    return AffinityMap({t: team_affinity(t, sigs, sets, multipliers, params) for t in ordered})


def catalog_sets(catalog: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(s.get("id")) for s in catalog.get("sets") or [] if s.get("id"))
