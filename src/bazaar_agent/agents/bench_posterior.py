"""The Market Test broker that plays to finish above the stall: posterior sampling and one-step lookahead. Pure.

BAZAAR_BENCH_POLICY=lookahead_safe (`below` = 0) or lookahead_bold (`below` = 0.5); not #292's `lookahead`
(`agents/bench_lookahead.py`, which maximises expected true surplus).

The points rule makes the bench a race against one number: a venue alone above the free stall's efficiency gets the
full points, one at the stall half of them, one below it 0.5 x E/Es (a few hundredths under half). So the objective
is not the expected efficiency but P(our session ends above the stall's), and every quote-crossing set of traders
we could match this tick (holding the rest) is scored on it:

  1. **Posterior.** Each trader seen so far is a path of quotes. Under the bench's generator (`PosteriorPrior`: a
     limit, a shade, a life, a relax share; quotes relax linearly toward the limit with age, as the simulator
     draws them) every (limit, life, relax) whose quotes reproduce the path within a prima keeps its prior weight,
     the rest are ruled out. A trader that left unmatched lived exactly as long as it was seen.
  2. **Samples.** `samples` worlds: every seen trader drawn from its posterior, every trader not seen yet drawn
     from the prior (the run's traders a side are known: `side_traders`), arriving after this tick.
  3. **Score.** In each world the stall is replayed from the run's first tick (on the quotes we saw, the model's
     quotes only where we saw none), and each candidate (a set of crossing pairs now, the rest held, then the
     stall's own rule for us until the end) ends with its true gains. A candidate scores the real points rule
     against the stall in that world; the mean over the same worlds picks it.

A candidate only beats the stall's own set by `min_edge` points: on a tie (most ticks, every tick of a quiet run)
the stall's own pairs go out, so this broker is the stall unless the samples say otherwise.
Prices are the midpoint of the quotes, as the stall's; the server refuses any pair whose quotes do not cross.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

Pair = tuple[str, str, int]  # (sell id, buy id, price)


@dataclass(frozen=True)
class PosteriorPrior:
    """The bench's generator as we believe it (fitted to sessions 7 and 8's recorded books, bench_tournament.py)."""

    side_traders: int = 10  # traders a side (the schedule's `traders`; session 9: 10)
    ticks: int = 16
    arrive_spread: int = 11  # arrivals uniform on run ticks 0..spread
    cheap_cost: tuple[int, int] = (25, 42)  # sellers' costs: two bumps
    dear_cost: tuple[int, int] = (65, 110)
    cheap_share: float = 0.5
    value: tuple[int, int] = (40, 105)
    sell_shade: tuple[float, float] = (1.05, 1.45)  # opening ask = cost x shade
    buy_shade: tuple[float, float] = (0.6, 0.95)  # opening bid = value x shade
    firm_share: float = 0.2
    impatient_share: float = 0.25
    impatient_life: tuple[int, int] = (1, 2)
    patient_life: tuple[int, int] = (3, 6)
    relax: tuple[float, float] = (0.5, 1.0)

    def life_pmf(self) -> dict[int, float]:
        out: dict[int, float] = {}
        for share, (lo, hi) in (
            (self.impatient_share, self.impatient_life),
            (1 - self.impatient_share, self.patient_life),
        ):
            for n in range(lo, hi + 1):
                out[n] = out.get(n, 0.0) + share / (hi - lo + 1)
        return out

    def limit_pmf(self, side: str, limit: int) -> float:
        if side == "buy":
            lo, hi = self.value
            return 1 / (hi - lo + 1) if lo <= limit <= hi else 0.0
        p = 0.0
        for share, (lo, hi) in ((self.cheap_share, self.cheap_cost), (1 - self.cheap_share, self.dear_cost)):
            if lo <= limit <= hi:
                p += share / (hi - lo + 1)
        return p

    def draw_limit(self, side: str, rng: random.Random) -> int:
        if side == "buy":
            return rng.randint(*self.value)
        return rng.randint(*(self.cheap_cost if rng.random() < self.cheap_share else self.dear_cost))

    def draw_life(self, rng: random.Random) -> int:
        lo, hi = self.impatient_life if rng.random() < self.impatient_share else self.patient_life
        return rng.randint(lo, hi)

    def draw_relax(self, rng: random.Random) -> float:
        return 0.0 if rng.random() < self.firm_share else rng.uniform(*self.relax)


@dataclass(frozen=True)
class Hidden:
    """One trader as a sample world holds it (run-relative ticks)."""

    id: str
    side: str
    limit: int
    q0: int
    arrive: int
    life: int
    relax: float

    def quote(self, tick: int) -> int:
        if self.relax <= 0 or self.life <= 1:
            return self.q0
        age = min(max(tick - self.arrive, 0), self.life - 1)
        return round(self.limit + (self.q0 - self.limit) * (1 - self.relax * age / (self.life - 1)))

    def present(self, tick: int) -> bool:
        return self.arrive <= tick < self.arrive + self.life


@dataclass
class Seen:
    """One trader as the book showed it."""

    id: str
    side: str
    first: int  # run-relative tick of its first quote
    quotes: list[int] = field(default_factory=list)  # one per tick from `first`
    matched_at: int | None = None  # the run tick we matched it

    @property
    def last(self) -> int:
        return self.first + len(self.quotes) - 1


RELAX_GRID = 6  # relax shares tried per (limit, life) between the prior's bounds, besides firm


def posterior(s: Seen, prior: PosteriorPrior, now: int, ticks: int) -> list[tuple[float, int, int, float]]:
    """[(weight, limit, life, relax)] reproducing `s`'s quotes within a prima. Its life is exactly what was seen when
    it left unmatched before `now`; at least that otherwise."""
    q0, n = s.quotes[0], len(s.quotes)
    gone = s.matched_at is None and s.last < now
    shade = prior.sell_shade if s.side == "sell" else prior.buy_shade
    lo, hi = sorted((q0 / shade[1], q0 / shade[0]))
    limits = range(max(1, math.floor(lo)), math.ceil(hi) + 1)
    lives = prior.life_pmf()
    relaxes = [(prior.firm_share, 0.0)] + [
        ((1 - prior.firm_share) / RELAX_GRID, prior.relax[0] + (prior.relax[1] - prior.relax[0]) * k / (RELAX_GRID - 1))
        for k in range(RELAX_GRID)
    ]
    out = []
    for limit in limits:
        if (s.side == "sell" and limit > min(s.quotes)) or (s.side == "buy" and limit < max(s.quotes)):
            continue
        w_limit = prior.limit_pmf(s.side, limit) / limit  # P(round(limit x shade) = q0) ~ 1 / (limit x width)
        if w_limit <= 0:
            continue
        for life, w_life in lives.items():
            if gone and life != n and s.first + n < ticks:
                continue
            if life < n:
                continue
            for w_relax, relax in relaxes:
                h = Hidden(s.id, s.side, limit, q0, s.first, life, relax)
                if all(abs(h.quote(s.first + k) - q) <= 1 for k, q in enumerate(s.quotes)):
                    out.append((w_limit * w_life * w_relax, limit, min(life, ticks - s.first), relax))
    if not out:  # the prior cannot explain this path: its last quote is its limit, it stays as seen
        out.append((1.0, s.quotes[-1], max(n, 1), 0.0))
    return out


def _stall_pairs(open_: Sequence[Hidden], quote: Mapping[str, int]) -> list[tuple[Hidden, Hidden]]:
    asks = sorted((h for h in open_ if h.side == "sell"), key=lambda h: quote[h.id])
    bids = sorted((h for h in open_ if h.side == "buy"), key=lambda h: -quote[h.id])
    out = []
    for s, b in zip(asks, bids, strict=False):
        if quote[s.id] > quote[b.id]:
            break
        out.append((s, b))
    return out


@dataclass
class World:
    """One sample: every trader of the run, the quotes we saw pinned over the model's."""

    traders: list[Hidden]
    seen_quotes: dict[tuple[str, int], int]
    ticks: int

    def quote(self, h: Hidden, tick: int) -> int:
        return self.seen_quotes.get((h.id, tick), h.quote(tick))

    def stall(self) -> int:
        used: set[str] = set()
        total = 0
        by = {h.id: h for h in self.traders}
        for tick in range(self.ticks):
            open_ = [h for h in self.traders if h.id not in used and h.present(tick)]
            quote = {h.id: self.quote(h, tick) for h in open_}
            for s, b in _stall_pairs(open_, quote):
                used.update((s.id, b.id))
                total += max(0, by[b.id].limit - by[s.id].limit)
        return total

    def possible(self) -> int:
        sells = sorted(h.limit for h in self.traders if h.side == "sell")
        buys = sorted((h.limit for h in self.traders if h.side == "buy"), reverse=True)
        return sum(max(0, b - s) for s, b in zip(sells, buys, strict=False))

    def finish(self, used: set[str], tick: int) -> int:
        """What the stall's rule realises for us from `tick` on, given who is matched already."""
        used = set(used)
        total = 0
        for k in range(tick, self.ticks):
            open_ = [h for h in self.traders if h.id not in used and h.present(k)]
            quote = {h.id: self.quote(h, k) for h in open_}
            for s, b in _stall_pairs(open_, quote):
                used.update((s.id, b.id))
                total += max(0, b.limit - s.limit)
        return total


def points(ours: int, stall: int, below: float = 1.0) -> float:
    """The real points rule against a field at the stall (bench_tournament.real_points): 1 above it, 0.5 at it,
    `below` x 0.5 x E/Es under it (1: Saturday's fit; 0: a rule that gives nothing under the stall)."""
    if ours > stall:
        return 1.0
    if ours < stall:
        return below * 0.5 * ours / stall if stall > 0 else 0.5
    return 0.5


def candidate_sets(
    asks: Sequence[tuple[str, int]], bids: Sequence[tuple[str, int]], cap: int
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Every (sellers, buyers) pair of equal-size sets that can all be matched by quote at once (sorted asks against
    sorted bids, each crossing), the empty one included; at most `cap`, the largest sets first."""
    crossing_asks = [a for a in asks if any(a[1] <= b[1] for b in bids)]
    crossing_bids = [b for b in bids if any(a[1] <= b[1] for a in asks)]
    out: list[tuple[tuple[str, ...], tuple[str, ...]]] = [((), ())]
    for k in range(1, min(len(crossing_asks), len(crossing_bids)) + 1):
        for ss in combinations(sorted(crossing_asks, key=lambda a: a[1]), k):
            for bs in combinations(sorted(crossing_bids, key=lambda b: b[1]), k):
                if all(a[1] <= b[1] for a, b in zip(ss, bs, strict=True)):
                    out.append((tuple(a[0] for a in ss), tuple(b[0] for b in bs)))
    out.sort(key=lambda c: -len(c[0]))
    return out[:cap]


def pair_up(asks: Mapping[str, int], bids: Mapping[str, int], sells: Sequence[str], buys: Sequence[str]) -> list[Pair]:
    ss = sorted(sells, key=lambda i: asks[i])
    bs = sorted(buys, key=lambda i: bids[i])
    return [(s, b, (asks[s] + bids[b]) // 2) for s, b in zip(ss, bs, strict=True)]


@dataclass
class PosteriorPolicy:
    """policy(book) -> [(sell, buy, price)]: one object per broker process; bench runs are told apart by id."""

    prior: PosteriorPrior = field(default_factory=PosteriorPrior)
    samples: int = 96
    min_edge: float = 0.01  # points a candidate must gain over the stall's own set
    below: float = 1.0  # the points rule under the stall the samples score with (`points`)
    max_candidates: int = 64
    seed: int = 0
    runs: dict[str, dict[str, Seen]] = field(default_factory=dict)
    starts: dict[str, int] = field(default_factory=dict)
    last_choice: dict[str, Any] = field(default_factory=dict)
    _posts: dict[tuple[Any, ...], list[tuple[float, int, int, float]]] = field(default_factory=dict)

    def __call__(self, book: Mapping[str, Any]) -> list[Pair]:
        tick = int(book.get("tick") or 0)
        by_run: dict[str, list[Mapping[str, Any]]] = {}
        for o in book.get("bench_offers") or []:
            by_run.setdefault(str(o["id"]).split("-")[0], []).append(o)
        out: list[Pair] = []
        for run, offers in sorted(by_run.items()):
            out += self._plan_run(run, offers, tick)
        return out

    def start_of(self, run: str, offers: Iterable[Mapping[str, Any]], tick: int) -> int:
        if run not in self.starts:
            ends = [int(e) for o in offers if isinstance(e := o.get("expires_tick"), int)]
            self.starts[run] = (min(ends) - self.prior.ticks) if ends else tick  # b120, b137: expires = start + 16
        return self.starts[run]

    def observe(self, run: str, offers: Sequence[Mapping[str, Any]], rel: int) -> dict[str, Seen]:
        seen = self.runs.setdefault(run, {})
        for o in offers:
            oid = str(o["id"])
            ask, bid = (o.get("want") or {}).get("cash"), (o.get("give") or {}).get("cash")
            sell = bool(ask)
            q = int((ask if sell else bid) or 0)
            s = seen.get(oid)
            if s is None:
                s = seen[oid] = Seen(oid, "sell" if sell else "buy", rel)
            k = rel - s.first
            if s.matched_at is not None and s.matched_at < rel:
                s.matched_at = None  # still in the book after we matched it: the match never settled
            if k < len(s.quotes):
                s.quotes[k] = q  # a second read in one tick: the latest quote wins
            else:
                while len(s.quotes) < k:  # a missed read: carry the last quote
                    s.quotes.append(s.quotes[-1])
                s.quotes.append(q)
        return seen

    def _plan_run(self, run: str, offers: Sequence[Mapping[str, Any]], tick: int) -> list[Pair]:
        rel = tick - self.start_of(run, offers, tick)
        seen = self.observe(run, offers, rel)
        live = {str(o["id"]) for o in offers}
        asks = {i: s.quotes[-1] for i, s in seen.items() if i in live and s.side == "sell"}
        bids = {i: s.quotes[-1] for i, s in seen.items() if i in live and s.side == "buy"}
        stall = [(a, b, (asks[a] + bids[b]) // 2) for a, b in zip(*_stall_sets(asks, bids), strict=True)]
        if not stall:
            return []
        cands = candidate_sets(list(asks.items()), list(bids.items()), self.max_candidates)
        stall_key = (tuple(sorted(s for s, _, _ in stall)), tuple(sorted(b for _, b, _ in stall)))
        keys = [(tuple(sorted(c[0])), tuple(sorted(c[1]))) for c in cands]
        if stall_key not in keys:
            keys.append(stall_key)
        scores = self.score(seen, rel, keys)
        best = max(range(len(keys)), key=lambda k: scores[k])
        pick = keys.index(stall_key)
        if scores[best] > scores[pick] + self.min_edge:
            pick = best
        self.last_choice[run] = {
            "rel": rel,
            "stall": scores[keys.index(stall_key)],
            "pick": scores[pick],
            "deviates": keys[pick] != stall_key,
            "tick": tick,
        }
        plan = stall if keys[pick] == stall_key else pair_up(asks, bids, *keys[pick])
        for s, b, _ in plan:
            seen[s].matched_at = seen[b].matched_at = rel
        return plan

    def score(
        self, seen: Mapping[str, Seen], rel: int, keys: Sequence[tuple[tuple[str, ...], tuple[str, ...]]]
    ) -> list[float]:
        rng = random.Random(f"{self.seed}:{sorted(seen)}:{rel}")
        p = self.prior
        posts = {}
        for i, s in seen.items():
            key = (i, tuple(s.quotes), s.matched_at is None and s.last < rel)
            if key not in self._posts:
                self._posts[key] = posterior(s, p, rel, p.ticks)
            posts[i] = self._posts[key]
        cum = {i: _cumulative(ws) for i, ws in posts.items()}
        pinned = {(i, s.first + k): q for i, s in seen.items() for k, q in enumerate(s.quotes)}
        used = {i for i, s in seen.items() if s.matched_at is not None}
        totals = [0.0] * len(keys)
        for _ in range(self.samples):
            traders = []
            for i, s in seen.items():
                w, limit, life, relax = _draw(posts[i], cum[i], rng)
                traders.append(Hidden(i, s.side, limit, s.quotes[0], s.first, life, relax))
            for side in ("sell", "buy"):
                missing = p.side_traders - sum(1 for s in seen.values() if s.side == side)
                for k in range(max(0, missing)):
                    if rel >= p.arrive_spread:
                        break
                    arrive = rng.randint(rel + 1, p.arrive_spread)
                    limit = p.draw_limit(side, rng)
                    shade = rng.uniform(*(p.sell_shade if side == "sell" else p.buy_shade))
                    traders.append(
                        Hidden(
                            f"?{side}{k}",
                            side,
                            limit,
                            round(limit * shade),
                            arrive,
                            p.draw_life(rng),
                            p.draw_relax(rng),
                        )
                    )
            world = World(traders, pinned, p.ticks)
            by = {h.id: h for h in traders}
            stall = world.stall()
            done = _realised(seen, by)
            for k, (sells, buys) in enumerate(keys):
                now = sum(by[b].limit for b in buys) - sum(by[s].limit for s in sells)
                after = world.finish(used | set(sells) | set(buys), rel + 1)
                totals[k] += points(done + now + after, stall, self.below)
        return [t / self.samples for t in totals]


def _stall_sets(asks: Mapping[str, int], bids: Mapping[str, int]) -> tuple[list[str], list[str]]:
    a = sorted(asks, key=lambda i: asks[i])
    b = sorted(bids, key=lambda i: -bids[i])
    sells, buys = [], []
    for s, x in zip(a, b, strict=False):
        if asks[s] > bids[x]:
            break
        sells.append(s)
        buys.append(x)
    return sells, buys


def _realised(seen: Mapping[str, Seen], by: Mapping[str, Hidden]) -> int:
    """Our matches so far, in this sample: the buyers' values minus the sellers' costs."""
    return sum((by[i].limit if s.side == "buy" else -by[i].limit) for i, s in seen.items() if s.matched_at is not None)


def _cumulative(ws: Sequence[tuple[float, int, int, float]]) -> list[float]:
    out, total = [], 0.0
    for w, *_ in ws:
        total += w
        out.append(total)
    return out


def _draw(
    ws: Sequence[tuple[float, int, int, float]], cum: Sequence[float], rng: random.Random
) -> tuple[float, int, int, float]:
    import bisect

    x = rng.random() * cum[-1]
    return ws[min(bisect.bisect_left(cum, x), len(ws) - 1)]
