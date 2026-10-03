"""A Market Test policy that maximises the chance of beating the free stall in each session (B1). Pure.

Why the chance and not the margin (W1a, PR #77): a session scores 0.5 for matching the free auto stall and
1.0 for the top-three mean, so against a field at the stall's level any edge earns the full point and a tie
earns half. The stall is deterministic and reads the same synthetic book, so a broker can replay it in
its head (the shadow stall) and only deviate when that is likely to win.

How it decides, every time it reads the book (a rollout over the base policy "cross like the stall"):
  1. what it has seen: each bench trader's arrival, quote history, and whether it left unmatched;
  2. `samples` futures consistent with that: each trader's hidden limit, firmness, relax rate and patience
     drawn from `BenchPrior` and kept only if they reproduce the quotes seen; unseen traders drawn whole;
  3. the candidate matchings among the pairs that cross at today's quotes (the stall's plan, every other
     matching, none), each followed by stall-like crossing to the end of the session;
  4. per future, our true gain against the shadow stall's: a win scores 1, a tie 0.5, a loss 0.5 × the
     ratio (`loss_curve="linear"`) or 0 (`"zero"`, the default: the safe reading of an unpublished curve);
  5. the candidate with the best mean, the stall's plan unless another beats it by `min_edge` after
     subtracting `z` standard errors of the paired difference (a deviation chosen on sampling noise loses).

The prior is W1a's own generative model of the bench (`bazaar_sim.bench`): arrivals, shades and patience are
unverified assumptions there, so this policy is only as good as that model is right. Matches use the quote
rule (`ask <= price`, `price + fee <= bid`, the SDK's docstring), so it never sends a refusable pair.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from bazaar_agent.agents.matcher import Fee, match_price

Pair = tuple[str, str, int]


@dataclass(frozen=True)
class BenchPrior:
    """What a bench trader is drawn from (W1a's `bazaar_sim.bench.NORMAL`; `HARD_PRIOR` for the hard test)."""

    traders: int = 10
    ticks: int = 16
    firm_share: float = 0.2
    impatient_share: float = 0.25
    impatient_life: tuple[int, int] = (1, 2)
    patient_life: tuple[int, int] = (3, 6)
    arrive_spread: int = 10
    relax: tuple[float, float] = (0.5, 1.0)
    cost: tuple[int, int] = (20, 60)
    value: tuple[int, int] = (40, 95)
    sell_shade: tuple[float, float] = (1.05, 1.3)
    buy_shade: tuple[float, float] = (0.75, 0.95)


NORMAL_PRIOR = BenchPrior()
HARD_PRIOR = BenchPrior(traders=12, firm_share=0.35, impatient_share=0.35)


@dataclass
class _Trader:
    """One trader of one sampled future: hidden fields drawn, observed ones copied."""

    id: str
    order: int  # the book's order, the stall's tie-break
    side: str
    limit: int
    quote: int
    arrive: int
    life: int
    relax: float
    shown: dict[int, int] = field(default_factory=dict)  # the quotes actually seen: replayed exactly

    def present(self, k: int) -> bool:
        return self.arrive <= k < self.arrive + self.life

    def quote_at(self, k: int) -> int:
        if k in self.shown:
            return self.shown[k]
        if self.relax <= 0 or self.life <= 1:
            return self.quote
        age = min(max(k - self.arrive, 0), self.life - 1)
        return round(self.limit + (self.quote - self.limit) * (1 - self.relax * age / (self.life - 1)))


@dataclass
class _Seen:
    id: str
    order: int
    side: str
    arrive: int
    quotes: dict[int, int] = field(default_factory=dict)  # run-relative tick -> quote shown
    gone: bool = False  # left our book without being matched by us: its whole life was seen


def _order(trader_id: str) -> int:
    tail = trader_id.rpartition("-")[2]
    return int(tail) if tail.isdigit() else 0


def _cross(asks: Sequence[tuple[int, str]], bids: Sequence[tuple[int, str]], fee: Fee) -> list[tuple[str, str]]:
    """The stall: lowest ask against highest bid while the bid covers ask + fee (lists already sorted)."""
    out = []
    for (ask, s), (bid, b) in zip(asks, bids, strict=False):
        if ask + fee.of(ask) > bid:
            break
        out.append((s, b))
    return out


def _stall_step(traders: Sequence[_Trader], used: set[str], k: int, fee: Fee) -> list[tuple[str, str]]:
    open_ = [t for t in traders if t.id not in used and t.present(k)]
    asks = sorted(((t.quote_at(k), t.id) for t in open_ if t.side == "sell"), key=lambda a: a[0])
    bids = sorted(((t.quote_at(k), t.id) for t in open_ if t.side == "buy"), key=lambda b: -b[0])
    return _cross(asks, bids, fee)


def _gain(by_id: dict[str, _Trader], pairs: Iterable[tuple[str, str]]) -> int:
    return sum(max(0, by_id[b].limit - by_id[s].limit) for s, b in pairs)


def _matchings(edges: Sequence[tuple[str, str]], cap: int) -> list[list[tuple[str, str]]]:
    """Matchings of the crossing graph, at most `cap` of them, largest first (on a big graph the cap can drop the
    empty one: the caller always adds it and the stall's plan)."""
    out: list[list[tuple[str, str]]] = []

    def walk(i: int, chosen: list[tuple[str, str]], used: frozenset[str]) -> None:
        if len(out) >= cap * 4:
            return
        if i == len(edges):
            out.append(list(chosen))
            return
        s, b = edges[i]
        if s not in used and b not in used:
            walk(i + 1, [*chosen, (s, b)], used | {s, b})
        walk(i + 1, chosen, used)

    walk(0, [], frozenset())
    out.sort(key=len, reverse=True)
    return out[:cap]


class WinRatePolicy:
    """`policy(book) -> [(sell id, buy id, price)]`, stateful across the reads of one session (and the next)."""

    def __init__(
        self,
        prior: BenchPrior = NORMAL_PRIOR,
        *,
        samples: int = 128,
        min_edge: float = 0.0,
        z: float = 1.0,
        loss_curve: Literal["linear", "zero"] = "zero",
        max_candidates: int = 24,
        seed: int = 0,
    ) -> None:
        self.prior = prior
        self.samples = samples
        self.min_edge = min_edge
        self.z = z
        self.loss_curve = loss_curve
        self.max_candidates = max_candidates
        self.rng = random.Random(seed)
        self.run: str | None = None
        self.start = 0
        self.seen: dict[str, _Seen] = {}
        self.ours: list[tuple[str, str]] = []  # pairs we sent this session
        self.deviations = 0  # reads where we chose something else than the stall's plan

    # ---------------------------------------------------------------- what the book shows

    def _observe(self, book: dict[str, Any]) -> tuple[int, list[tuple[str, str, int]]]:
        offers, runs = [], set()
        for o in book.get("bench_offers") or []:
            oid = str(o.get("id", ""))
            want, give = o.get("want") or {}, o.get("give") or {}
            runs.add(str(o["run"]) if o.get("run") is not None else oid.partition("-")[0])
            if want.get("cash"):
                offers.append((oid, "sell", int(want["cash"])))
            elif give.get("cash"):
                offers.append((oid, "buy", int(give["cash"])))
        tick = int(book.get("tick") or 0)
        run = min(runs) if runs else self.run
        if run != self.run:
            self.run, self.start, self.seen, self.ours = run, tick, {}, []
        k = tick - self.start
        present = {oid for oid, _, _ in offers}
        matched = {i for pair in self.ours for i in pair}
        for t in self.seen.values():
            if t.id not in present and t.id not in matched and max(t.quotes) < k:
                t.gone = True
        for oid, side, quote in offers:
            t = self.seen.setdefault(oid, _Seen(oid, _order(oid), side, k))
            t.quotes[k] = quote
            if oid in matched:  # a match of ours was refused: the trader is still there
                self.ours = [p for p in self.ours if oid not in p]
        return k, offers

    def refused(self, sell: str, buy: str) -> None:
        """The broker loop tells us a match was refused: both traders are open again. Without this call a refusal
        is only noticed when one of them shows up in the next book (if both leave first, it stays counted)."""
        self.ours = [p for p in self.ours if p != (sell, buy)]

    # ---------------------------------------------------------------- one sampled future

    def _life(self, at_least: int) -> int | None:
        p, rng = self.prior, self.rng
        for _ in range(20):
            lo, hi = p.impatient_life if rng.random() < p.impatient_share else p.patient_life
            life = rng.randint(lo, hi)
            if life >= at_least:
                return life
        return None

    def _sample_seen(self, t: _Seen, k: int) -> _Trader | None:
        p, rng = self.prior, self.rng
        ticks = sorted(t.quotes)
        q0 = t.quotes[ticks[0]]
        lo_lim, hi_lim = p.cost if t.side == "sell" else p.value
        lo_sh, hi_sh = p.sell_shade if t.side == "sell" else p.buy_shade
        a, b = max(lo_lim, math.ceil(q0 / hi_sh)), min(hi_lim, math.floor(q0 / lo_sh))
        if a > b:
            a, b = (lo_lim, hi_lim) if t.side == "sell" else (lo_lim, hi_lim)
        moved = len(set(t.quotes.values())) > 1
        last_age = ticks[-1] - t.arrive
        for _ in range(30):
            limit = rng.randint(a, b)
            if rng.random() > a / max(limit, 1):  # the posterior of the limit given the quote goes as 1/limit
                continue
            life = last_age + 1 if t.gone else self._life(last_age + 1)
            if life is None:
                continue
            firm = not moved and (len(ticks) > 1 or rng.random() < p.firm_share)
            if firm:
                relax = 0.0
            elif moved and life > 1:
                q_last, shade = t.quotes[ticks[-1]], q0 - limit
                if shade == 0 or last_age == 0:
                    continue
                relax = (1 - (q_last - limit) / shade) * (life - 1) / last_age
                if not p.relax[0] - 0.1 <= relax <= p.relax[1] + 0.05:
                    continue
                relax = min(1.0, max(0.0, relax))
            else:
                relax = rng.uniform(*p.relax)
            cand = _Trader(t.id, t.order, t.side, limit, q0, t.arrive, life, relax)
            if all(abs(cand.quote_at(tk) - q) <= 1 for tk, q in t.quotes.items()):
                cand.shown = dict(t.quotes)
                return cand
        return None

    def _sample_new(self, idx: int, side: str, k: int) -> _Trader:
        p, rng = self.prior, self.rng
        limit = rng.randint(*(p.cost if side == "sell" else p.value))
        quote = round(limit * rng.uniform(*(p.sell_shade if side == "sell" else p.buy_shade)))
        arrive = rng.randint(k + 1, max(k + 1, p.arrive_spread))
        life = self._life(1) or 1
        relax = 0.0 if rng.random() < p.firm_share else rng.uniform(*p.relax)
        return _Trader(f"~{idx}", 1000 + idx, side, limit, quote, arrive, life, relax)

    def _future(self, k: int) -> list[_Trader] | None:
        traders = []
        for t in self.seen.values():
            s = self._sample_seen(t, k)
            if s is None:
                return None
            traders.append(s)
        for side in ("sell", "buy"):
            missing = self.prior.traders // 2 - sum(t.side == side for t in self.seen.values())
            traders += [self._sample_new(len(traders) + i, side, k) for i in range(max(0, missing))]
        return traders

    # ---------------------------------------------------------------- the decision

    def _score(self, ours: int, stall: int) -> float:
        if ours > stall:
            return 1.0
        if ours == stall:
            return 0.5
        return 0.5 * ours / stall if self.loss_curve == "linear" and stall > 0 else 0.0

    def _rollout(self, traders: list[_Trader], k: int, now: list[tuple[str, str]], fee: Fee) -> int:
        """Our true gain if we match `now` at tick k, then cross like the stall to the end."""
        used = {i for pair in self.ours for i in pair} | {i for pair in now for i in pair}
        pairs = [*self.ours, *now]
        for tk in range(k + 1, self.prior.ticks):
            step = _stall_step(traders, used, tk, fee)
            pairs += step
            used |= {i for pair in step for i in pair}
        return _gain({t.id: t for t in traders}, pairs)

    def __call__(self, book: dict[str, Any]) -> list[Pair]:
        fee = Fee(int(book.get("fee_bps") or 0), int(book.get("fee_per_card") or 0))
        k, offers = self._observe(book)
        matched = {i for pair in self.ours for i in pair}
        asks = sorted(
            ((q, oid) for oid, side, q in offers if side == "sell" and oid not in matched), key=lambda a: a[0]
        )
        bids = sorted(
            ((q, oid) for oid, side, q in offers if side == "buy" and oid not in matched), key=lambda b: -b[0]
        )
        stall_plan = _cross(asks, bids, fee)
        edges = [(s, b) for ask, s in asks for bid, b in bids if ask + fee.of(ask) <= bid]
        if not edges:
            return []
        candidates = _matchings(edges, self.max_candidates)
        for must in (stall_plan, []):  # the stall's plan and waiting are always on the table
            if must not in candidates:
                candidates.append(must)
        scores: list[list[float]] = [[] for _ in candidates]
        drawn = 0
        for _ in range(self.samples * 2):
            if drawn == self.samples:
                break
            traders = self._future(k)
            if traders is None:
                continue
            drawn += 1
            stall_used: set[str] = set()
            stall_pairs: list[tuple[str, str]] = []
            for tk in range(self.prior.ticks):
                step = _stall_step(traders, stall_used, tk, Fee())
                stall_pairs += step
                stall_used |= {i for pair in step for i in pair}
            stall_gain = _gain({t.id: t for t in traders}, stall_pairs)
            for i, cand in enumerate(candidates):
                scores[i].append(self._score(self._rollout(traders, k, cand, fee), stall_gain))
        choice = stall_plan
        if drawn > 1:
            base = scores[candidates.index(stall_plan)]
            best = max(range(len(candidates)), key=lambda i: sum(scores[i]))
            diff = [a - b for a, b in zip(scores[best], base, strict=True)]
            mean = sum(diff) / drawn
            se = math.sqrt(sum((d - mean) ** 2 for d in diff) / (drawn - 1) / drawn)
            if mean - self.z * se > self.min_edge:
                choice = candidates[best]
        if choice != stall_plan:
            self.deviations += 1
        quote = {oid: q for oid, _, q in offers}
        self.ours += choice
        return [(s, b, match_price(quote[s], quote[b], fee)) for s, b in choice]
