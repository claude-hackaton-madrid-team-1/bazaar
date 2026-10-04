"""The Market Test broker that looks ahead: which quote-crossing bench pairs to match now, and which to hold. Pure.

The server matches a bench pair only when its QUOTES cross (the one live probe, tick 1692, sell ask 40 × buy bid 39:
400 bad_match "price must sit between the ask 40 and the bid 39"), and the score counts the gains between the hidden
limits. So a broker beats the free stall only by WHICH crossing traders it matches and WHEN. On books fitted to the
recorded sessions, a broker that knew every departure would realise 4–14 % more than the stall; one that only picks
better partners among today's crossing pairs, under 1 % (docs/research/2026-10-04/bench-sim.md).

Each read of the bench, this planner
  1. remembers every trader's quote path (one quote a tick) and places the run on its 16 ticks from the offers'
     `expires_tick` (the run's end on the real server), so a restart mid-run keeps the clock;
  2. lists the options: the exact plan (the stall's traders), then every other matching of pairs that cross by quote
     now (hold a pair, re-pair, add a pair), most pairs first, at most `max_options`;
  3. draws `samples` futures from `LookaheadPrior` (fitted to runs b120 and b137): each present trader's hidden
     limit, life and relax share given its path, plus the traders not seen yet arriving later;
  4. plays every option in every future (common draws), followed by the stall crossing by quote until the run ends,
     and keeps the option with the most true gains in total; ties keep the exact plan.

Wired by BAZAAR_BENCH_POLICY=lookahead on the maker (default exact): `agents/broker.py`. Any exception there sends
the exact plan for the tick.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from bazaar_agent.agents.matcher import Fee, Match, Quote, feasible, match_price

Pair = tuple[str, str]  # (sell id, buy id)


@dataclass(frozen=True)
class LookaheadPrior:
    """The bench's hidden parameters, fitted to the recorded runs b120 (session 7) and b137 (session 8)."""

    per_side: int = 10  # traders a side (/api/schedule `traders`; raised to the most seen on a side)
    ticks: int = 16
    arrive_last: int = 11  # the last run tick a trader shows up at (b120: 11, b137: 10)
    cheap_cost: tuple[int, int] = (25, 42)  # sellers come in two bumps: asks 35-48 or 77-134, none between
    dear_cost: tuple[int, int] = (65, 110)
    cheap_share: float = 0.5
    value: tuple[int, int] = (40, 105)
    sell_markup: tuple[float, float] = (1.05, 1.45)  # first ask = cost × markup
    buy_shade: tuple[float, float] = (0.6, 0.95)  # first bid = value × shade
    firm_share: float = 0.2
    impatient_share: float = 0.25
    impatient_life: tuple[int, int] = (1, 2)
    patient_life: tuple[int, int] = (3, 6)
    relax: tuple[float, float] = (0.5, 1.0)  # share of the shade given up by the last tick, linear in age


@dataclass(frozen=True)
class LookaheadConfig:
    samples: int = 128  # 128 rollouts: ≤ 0.1 s on the worst tick; fewer decide noisier (16-512 measured)
    max_options: int = 48
    tries: int = 200  # rejection draws for one trader before falling back to its path
    prior: LookaheadPrior = field(default_factory=LookaheadPrior)


@dataclass(frozen=True)
class _Trader:
    id: str
    side: str
    limit: int
    quote: int  # the first quote
    arrive: int  # run tick
    life: int
    relax: float

    def quote_at(self, tick: int) -> int:
        if self.relax <= 0 or self.life <= 1:
            return self.quote
        age = min(max(tick - self.arrive, 0), self.life - 1)
        return round(self.limit + (self.quote - self.limit) * (1 - self.relax * age / (self.life - 1)))

    def present(self, tick: int) -> bool:
        return self.arrive <= tick < self.arrive + self.life


@dataclass
class _Path:
    side: str
    first: int  # run tick first seen
    quotes: list[int]


class BenchLookahead:
    """One per broker: it keeps the quote paths of the current run and forgets them when a new run starts."""

    def __init__(self, config: LookaheadConfig | None = None, seed: int = 0) -> None:
        self.config = config or LookaheadConfig()
        self.rng = random.Random(seed)
        self.run: str | None = None
        self.start: int | None = None
        self.paths: dict[str, _Path] = {}

    # ------------------------------------------------------------ observing

    def _clock(self, run: str, tick: int, expires: int | None) -> int:
        if run != self.run:
            self.run, self.start, self.paths = run, None, {}
        if expires is not None:
            self.start = expires - self.config.prior.ticks
        elif self.start is None:
            self.start = tick
        return tick - self.start

    def observe(self, bench: Sequence[Quote], tick: int, expires: int | None = None) -> int:
        """Record this tick's quotes; the run tick (0 = the run's first)."""
        if not bench:
            return tick - (self.start or tick)
        t = self._clock(bench[0].item, tick, expires)
        for q in bench:
            path = self.paths.setdefault(str(q.id), _Path(q.side, t, []))
            if len(path.quotes) < t - path.first + 1:
                path.quotes.append(q.price)
        return t

    # ------------------------------------------------------------ drawing futures

    def _life(self, rng: random.Random) -> int:
        p = self.config.prior
        lo, hi = p.impatient_life if rng.random() < p.impatient_share else p.patient_life
        return rng.randint(lo, hi)

    def _posterior(self, tid: str, path: _Path, rng: random.Random) -> _Trader:
        """A hidden trader that shows this path and is still in the book: limit beyond every quote, life at least as
        long as seen, relax share that reproduces the path's mean step."""
        p, q0, n = self.config.prior, path.quotes[0], len(path.quotes)
        step = (path.quotes[-1] - q0) / (n - 1) if n > 1 else None
        for _ in range(self.config.tries):
            if path.side == "sell":
                limit = round(q0 / rng.uniform(*p.sell_markup))
                if limit > min(path.quotes) or limit <= 0:
                    continue
            else:
                limit = round(q0 / rng.uniform(*p.buy_shade))
                if limit < max(path.quotes):
                    continue
            life = self._life(rng)
            if life < n:
                continue
            if step is None:
                relax = 0.0 if rng.random() < p.firm_share else rng.uniform(*p.relax)
            elif step == 0:
                relax = 0.0
            else:
                if life <= 1 or limit == q0:
                    continue
                relax = step * (life - 1) / (limit - q0)
                if not 0.0 < relax <= 1.0:
                    continue
            return _Trader(tid, path.side, limit, q0, path.first, life, relax)
        return _Trader(tid, path.side, path.quotes[-1], q0, path.first, n + 1, 0.0)

    def _newcomer(self, tid: str, side: str, arrive: int, rng: random.Random) -> _Trader:
        p = self.config.prior
        if side == "sell":
            limit = rng.randint(*(p.cheap_cost if rng.random() < p.cheap_share else p.dear_cost))
            quote = round(limit * rng.uniform(*p.sell_markup))
        else:
            limit = rng.randint(*p.value)
            quote = round(limit * rng.uniform(*p.buy_shade))
        relax = 0.0 if rng.random() < p.firm_share else rng.uniform(*p.relax)
        return _Trader(tid, side, limit, quote, arrive, self._life(rng), relax)

    def _future(self, present: Sequence[str], t: int, rng: random.Random) -> list[_Trader]:
        p = self.config.prior
        traders = [self._posterior(tid, self.paths[tid], rng) for tid in present]
        seen = {"sell": 0, "buy": 0}
        for path in self.paths.values():
            seen[path.side] += 1
        per_side = max(p.per_side, *seen.values())
        if t + 1 <= p.arrive_last:
            for side in ("sell", "buy"):
                for k in range(per_side - seen[side]):
                    traders.append(self._newcomer(f"new-{side}-{k}", side, rng.randint(t + 1, p.arrive_last), rng))
        return traders

    # ------------------------------------------------------------ playing an option

    def _rollout(self, traders: Sequence[_Trader], t: int, now: Sequence[Pair], fee: Fee) -> int:
        """True gains of `now`, then of the stall crossing by quote on every later tick of the run."""
        by = {x.id: x for x in traders}
        used: set[str] = set()
        gain = 0
        for sell_id, buy_id in now:
            used.update((sell_id, buy_id))
            gain += max(0, by[buy_id].limit - by[sell_id].limit)
        for k in range(t + 1, self.config.prior.ticks):
            open_ = [x for x in traders if x.id not in used and x.present(k)]
            asks = sorted((x for x in open_ if x.side == "sell"), key=lambda x: x.quote_at(k))
            bids = sorted((x for x in open_ if x.side == "buy"), key=lambda x: -x.quote_at(k))
            for s, b in zip(asks, bids, strict=False):
                ask = s.quote_at(k)
                if ask + fee.of(ask) > b.quote_at(k):
                    break
                used.update((s.id, b.id))
                gain += max(0, b.limit - s.limit)
        return gain

    def _options(self, bench: Sequence[Quote], fee: Fee, exact: Sequence[Pair]) -> list[tuple[Pair, ...]]:
        sells = [q for q in bench if q.side == "sell"]
        buys = [q for q in bench if q.side == "buy"]
        cross = [(str(s.id), str(b.id)) for s in sells for b in buys if feasible(s, b, fee)]
        found: list[tuple[Pair, ...]] = []
        cap = 4 * self.config.max_options

        def grow(i: int, cur: tuple[Pair, ...], used: frozenset[str]) -> None:
            if len(found) >= cap:
                return
            if i == len(cross):
                found.append(cur)
                return
            s, b = cross[i]
            if s not in used and b not in used:
                grow(i + 1, (*cur, (s, b)), used | {s, b})
            grow(i + 1, cur, used)

        grow(0, (), frozenset())
        base = set(exact)
        others = sorted((m for m in found if set(m) != base), key=lambda m: (-len(m), -len(set(m) & base)))
        return [tuple(exact), *others[: self.config.max_options]]

    # ------------------------------------------------------------ the plan

    def plan(
        self,
        bench: Sequence[Quote],
        fee: Fee,
        tick: int,
        exact: Sequence[Match],
        expires: int | None = None,
        log: Callable[[str], None] | None = None,
    ) -> list[Match]:
        """This tick's bench matches: `exact` unless another matching of crossing pairs earns more in expectation."""
        t = self.observe(bench, tick, expires)
        if not bench or t >= self.config.prior.ticks - 1:
            return list(exact)
        exact_pairs = [(str(m.sell.id), str(m.buy.id)) for m in exact]
        options = self._options(bench, fee, exact_pairs)
        if len(options) == 1:
            return list(exact)
        present = [str(q.id) for q in bench]
        totals = [0] * len(options)
        for _ in range(self.config.samples):
            traders = self._future(present, t, self.rng)
            for i, option in enumerate(options):
                totals[i] += self._rollout(traders, t, option, fee)
        best = max(range(len(options)), key=lambda i: (totals[i], -i))
        if best == 0:
            return list(exact)
        if log:
            log(
                f"bench lookahead: {len(options[best])} pair(s) instead of exact's {len(exact)}, "
                f"+{(totals[best] - totals[0]) / self.config.samples:.1f} expected P over {len(options)} options"
            )
        by = {str(q.id): q for q in bench}
        out = []
        for s, b in options[best]:
            sell, buy = by[s], by[b]
            price = match_price(sell.price, buy.price, fee)
            out.append(Match(sell, buy, price, fee.of(price)))
        return out
