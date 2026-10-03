"""The Market Test broker that tries to beat the stall: which bench pairs to cross now, and which to hold. Pure.

The stall (and the starter broker) crosses the best ask against the best bid every tick, by quote. Two things a
broker can do better (starter_broker.py's docstring, #12):
  - **who**: the score counts the gains between the hidden limits, not between the quotes. Among the pairs that
    cross, the maximum-weight matching on the *estimated* surplus (`bench_model.TraderModel`: limit bands from the
    quotes) picks the traders worth most, where the stall picks the best quotes;
  - **when**: a pair that crosses now and whose traders are both likely to stay can wait a tick, so that a trader
    who arrives or relaxes later finds the partner it needs. A pair with a trader about to leave (its leave hazard
    reaches `hold_below`) or in the session's last tick is crossed at once.

`cross = "quote"` (the default) only ever proposes pairs whose quotes cross (`ask + fee(ask) ≤ bid`), as the
simulator and RULES.md's "pairs crossing offers" require. `cross = "limit"` also proposes pairs whose quotes do not
cross but whose estimated limits do, at the price most likely to sit inside both limits: it is only worth anything
if the real `POST /api/broker/matches` checks hidden limits rather than quotes, which nobody has verified. It is a
probe, off by default; a refused pair is not proposed again until one of its quotes moves.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Literal

from bazaar_agent.agents.bench_model import BenchPrior, TraderModel, expiry_of
from bazaar_agent.agents.matcher import Fee, Match, Quote, feasible, match_price, max_weight_assignment


@dataclass(frozen=True)
class EdgeConfig:
    hold_below: float = 0.0  # cross a pair at once when either trader's leave hazard reaches this (0: never hold)
    endgame_ticks: int = 1  # cross everything in the session's last tick(s)
    # When both traders' bench offers say when they leave, hold their pair until one of them is on its last tick
    # (the prescient bound in the tournament: a pair held is never lost, and a later trader may need one of them).
    hold_known: bool = True
    # Ticks taken off a stated expiry: the field's meaning is a guess (`bench_model.expiry_of`), and a pair held one
    # tick too long is lost, so the edge crosses one tick early.
    expiry_margin: int = 1
    cross: Literal["quote", "limit"] = "quote"  # "limit": also propose non-crossing pairs (a probe, see above)
    min_accept: float = 0.2  # "limit": propose a non-crossing pair only with at least this chance of acceptance
    tries_per_pair: int = 3  # "limit": refused prices remembered per pair; a pair refused this often is dropped
    # "limit": stop probing once no probe was ever accepted and that had less than this chance if the server
    # checked limits (the product of each refused probe's predicted refusal chance): the server checks quotes.
    give_up_below: float = 0.01
    give_up_after: int = 8  # ... and never before this many refusals


@dataclass(frozen=True)
class Candidate:
    sell: TraderModel
    buy: TraderModel
    price: int
    weight: float  # estimated true surplus (× the acceptance chance for a non-crossing pair)
    crossing: bool
    chance: float = 1.0  # predicted acceptance (1 for a crossing pair)


@dataclass
class ProbeStats:
    """What the limit probes have shown so far, over every bench run one broker process sees."""

    sent: int = 0
    refused: int = 0
    accepted: int = 0
    all_refused: float = 1.0  # P(every probe so far refused | the server checks limits), while none was accepted

    def record(self, accepted: bool, chance: float) -> None:
        self.sent += 1
        if accepted:
            self.accepted += 1
        else:
            self.refused += 1
            self.all_refused *= 1 - min(max(chance, 0.0), 1.0)


def _run(quote: Quote) -> str:
    return quote.item.removeprefix("bench:")


def is_probe(m: Match) -> bool:
    """A match whose price is not inside the quotes (`ask ≤ price`, `price + fee ≤ bid`): a limit probe."""
    return not (m.sell.price <= m.price and m.price + m.fee <= m.buy.price)


def expiries_in(bench_offers: Iterable[Mapping[str, Any]], tick: int) -> dict[str, int]:
    """Offer id -> last tick, for the bench offers that say when they leave."""
    out = {}
    for o in bench_offers:
        if isinstance(o, Mapping) and (last := expiry_of(o, tick)) is not None:
            out[str(o.get("id"))] = last
    return out


def _assign(table: Sequence[Sequence[Candidate | None]]) -> list[tuple[int, int]]:
    """The maximum-weight matching of the candidates (more pairs among equal weights)."""
    return max_weight_assignment([[0 if c is None else int(c.weight * 100) + 1 for c in row] for row in table])


@dataclass
class BenchEdge:
    prior: BenchPrior
    config: EdgeConfig = field(default_factory=EdgeConfig)
    # A probe prices on wide bands (asks up to 1.6 × cost, bids down to half the value), so that a bench that shades
    # more than the prior still gets probed; crossing pairs are weighed on `prior`.
    probe_prior: BenchPrior | None = None
    models: dict[str, TraderModel] = field(default_factory=dict)
    first_tick: dict[str, int] = field(default_factory=dict)  # bench run -> the first tick it showed
    refused: dict[tuple[str, str], list[int]] = field(default_factory=dict)  # non-crossing pair -> prices refused
    probes: ProbeStats = field(default_factory=ProbeStats)
    chances: dict[tuple[str, str, int], float] = field(default_factory=dict)  # a proposed probe -> its predicted chance

    def __post_init__(self) -> None:
        if self.probe_prior is None:
            self.probe_prior = self.prior.widened()

    @property
    def probing(self) -> bool:
        """Non-crossing pairs are proposed: `cross = "limit"`, and the server has not refused all of them so far."""
        p = self.probes
        settled = p.refused >= self.config.give_up_after and p.all_refused < self.config.give_up_below
        return self.config.cross == "limit" and (p.accepted > 0 or not settled)

    def observe(self, quotes: Iterable[Quote], tick: int, expiries: Mapping[str, int] | None = None) -> None:
        """One book read: every bench quote updates (or starts) its trader's model. `expiries` (offer id -> last
        tick) is what the bench offers say about when they leave, if anything (`bench_model.expiry_of`)."""
        for q in quotes:
            if not q.bench:
                continue
            self.first_tick.setdefault(_run(q), tick)
            model = self.models.get(str(q.id))
            if model is None:
                model = self.models[str(q.id)] = TraderModel(str(q.id), q.side, tick, self.prior)
            model.observe(tick, q.price)
            if expiries and str(q.id) in expiries:
                model.expires = expiries[str(q.id)] - self.config.expiry_margin

    def last_tick(self, run: str, session_ticks: int | None = None) -> int:
        return self.first_tick.get(run, 0) + (session_ticks or self.prior.session_ticks) - 1

    def forget(self, run: str) -> None:
        """A bench run is over: drop its traders, its start and its refused pairs."""
        prefix = f"{run}-"
        self.models = {k: v for k, v in self.models.items() if not k.startswith(prefix)}
        self.refused = {k: v for k, v in self.refused.items() if not k[0].startswith(prefix)}
        self.first_tick.pop(run, None)

    def note_sent(self, m: Match, accepted: bool) -> None:
        """The server's answer to a match. A refused non-crossing pair remembers its price: the next try for that
        pair is priced on what is left of the limit bands (`_best_price`)."""
        if not is_probe(m):
            return  # a crossing pair: a refusal only means it was taken or gone, nothing to learn
        self.probes.record(accepted, self.chances.pop((str(m.sell.id), str(m.buy.id), m.price), self.config.min_accept))
        if not accepted:
            self.refused.setdefault((str(m.sell.id), str(m.buy.id)), []).append(m.price)

    def plan(
        self,
        quotes: Sequence[Quote],
        fee: Fee,
        tick: int,
        *,
        limit: int | None = None,
        session_ticks: Mapping[str, int] | None = None,
        session_starts: Mapping[str, int] | None = None,
    ) -> list[Match]:
        """The bench matches to send now (the read's quotes must have been `observe`d first): crossing pairs before
        probes, each most urgent first. `session_starts` (run -> its first tick, from `bench.started`) fixes the
        session's last tick; a pair is held for a known expiry only in a run whose start is known, so a late
        guess of the last tick never strands a held pair."""
        self.chances.clear()  # what the last plan's probes predicted is only needed until they are answered
        for run, start in (session_starts or {}).items():
            self.first_tick[run] = start
        by_run: dict[str, tuple[list[Quote], list[Quote]]] = defaultdict(lambda: ([], []))
        for q in quotes:
            if q.bench and str(q.id) in self.models:
                sells, buys = by_run[_run(q)]
                (sells if q.side == "sell" else buys).append(q)
        now: list[tuple[float, Match]] = []
        for run, (sells, buys) in sorted(by_run.items()):
            endgame = tick > self.last_tick(run, (session_ticks or {}).get(run)) - self.config.endgame_ticks
            may_hold = run in (session_starts or {})
            for urgency, m in self._plan_run(sells, buys, fee, tick, endgame, may_hold):
                now.append((urgency, m))
        now.sort(key=lambda e: (is_probe(e[1]), -e[0], -e[1].surplus, str(e[1].sell.id)))
        picked = [m for _, m in now]
        return picked if limit is None else picked[:limit]

    def _plan_run(
        self, sells: Sequence[Quote], buys: Sequence[Quote], fee: Fee, tick: int, endgame: bool, may_hold: bool
    ) -> list[tuple[float, Match]]:
        """Crossing pairs first (a sure match is never displaced by a probe), then probes among who is left."""
        table = [[self._candidate(s, b, fee) for b in buys] for s in sells]
        pairs = _assign([[c if c is not None and c.crossing else None for c in row] for row in table])
        rows, cols = {r for r, _ in pairs}, {c for _, c in pairs}
        left = [
            [
                c if c is not None and not c.crossing and r not in rows and j not in cols else None
                for j, c in enumerate(row)
            ]
            for r, row in enumerate(table)
        ]
        pairs += _assign(left)
        out = []
        for r, c in pairs:
            cand = table[r][c]
            assert cand is not None
            urgency = max(cand.sell.hazard(tick), cand.buy.hazard(tick))
            known = cand.sell.expires is not None and cand.buy.expires is not None
            hold = urgency < self.config.hold_below or (known and may_hold and urgency < 1.0 and self.config.hold_known)
            if endgame or not hold or not cand.crossing:  # a probe is never held
                out.append((1.0 if endgame else urgency, Match(sells[r], buys[c], cand.price, fee.of(cand.price))))
                if not cand.crossing:
                    self.chances[(str(sells[r].id), str(buys[c].id), cand.price)] = cand.chance
        return out

    def _candidate(self, s: Quote, b: Quote, fee: Fee) -> Candidate | None:
        seller, buyer = self.models[str(s.id)], self.models[str(b.id)]
        if feasible(s, b, fee):
            gain = max(float(b.price - s.price), buyer.limit() - seller.limit())
            return Candidate(seller, buyer, match_price(s.price, b.price, fee), gain, True)
        if not self.probing or s.item != b.item or s.maker == b.maker:
            return None
        tried = self.refused.get((str(s.id), str(b.id)), [])
        if len(tried) >= self.config.tries_per_pair:
            return None
        price, chance = self._best_price(seller, buyer, fee, tried, self.probe_prior)
        if chance < self.config.min_accept:
            return None
        gain = buyer.limit(self.probe_prior) - seller.limit(self.probe_prior)
        if gain <= 0:
            return None
        # what giving up weighs: the lower of the wide and the preset prior's chance, so that a wide prior's optimism
        # on a narrowly shaded bench never makes a few refusals look like proof that the server checks quotes
        cautious = min(chance, _acceptance(seller, buyer, fee, tried, self.prior)(price))
        return Candidate(seller, buyer, price, chance * gain, False, cautious)

    @staticmethod
    def _best_price(
        seller: TraderModel, buyer: TraderModel, fee: Fee, tried: Sequence[int] = (), prior: BenchPrior | None = None
    ) -> tuple[int, float]:
        """The whole price most likely to be at least the seller's cost and, fee included, at most the buyer's value,
        given that every price in `tried` was refused (each refusal rules out "cost ≤ p and value ≥ p + fee(p))."""
        chance = _acceptance(seller, buyer, fee, tried, prior)
        lo, hi = int(seller.band(prior)[0]), int(buyer.band(prior)[1]) + 1
        best = (lo, 0.0)
        for price in range(max(1, lo), max(lo, hi) + 1):
            if price not in tried and (c := chance(price)) > best[1]:
                best = (price, c)
        return best


def _acceptance(
    seller: TraderModel, buyer: TraderModel, fee: Fee, tried: Sequence[int], prior: BenchPrior | None
) -> Callable[[int], float]:
    """P(a match at `price` is accepted | every price in `tried` was refused), as a function of the price.

    Cost and value are independent and uniform on their bands, so P(accept at p) is a product and the refused
    region is a union of such products: inclusion–exclusion over the (few) refused prices is exact."""

    def mass(prices: Sequence[int]) -> float:  # P(cost ≤ min p and value ≥ max(p + fee(p))): all accept
        top = max(p + fee.of(p) for p in prices)
        return seller.p_limit_below(min(prices), prior) * buyer.p_limit_above(top, prior)

    def free(extra: Sequence[int]) -> float:  # P(every price in `extra` accepts and no refused one would)
        total = 0.0
        for k in range(len(tried) + 1):
            for subset in combinations(tried, k):
                total += (-1) ** k * (mass([*extra, *subset]) if extra or subset else 1.0)
        return total

    left = free(())
    return lambda price: 0.0 if left <= 1e-9 else free((price,)) / left
