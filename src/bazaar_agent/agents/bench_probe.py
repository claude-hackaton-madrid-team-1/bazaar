"""The Market Test probe: the exact (stall-equal) plan first, then a few pairs whose quotes do not cross. Pure.

The stall crosses by quote, and a quote is not a limit (starter_broker.py's docstring): bench traders shade their
quotes away from a hidden limit, and the score counts the gains between the true limits. Every bench pair we have
ever proposed crossed by quote, so nobody knows whether `POST /api/broker/matches` checks the quotes (openapi:
"ask ≤ price and price + fee ≤ bid") or the hidden limits. The probe finds out at no cost to the exact plan:

  - each tick the exact plan goes out first, unchanged (`plan_matches`): the stall's traders, the stall's pairs;
  - then, among the bench traders the exact plan leaves out, the lowest ask against the highest bid, the next
    against the next, ..., while the gap `ask + fee(ask) − bid` is at most `max_gap`, at most `per_tick` pairs, at
    the price that centres `[price, price + fee]` between the two quotes (both limits sit beyond their quotes, so
    the middle has the best chance of sitting inside both);
  - a pair is never proposed twice at one price, and at most `tries_per_pair` times; a session (bench run) stops
    probing after `give_up_after` refusals with nothing accepted: then the server checks quotes, and the broker
    is the exact broker again for the rest of that run and, while the process lives, for every later run;
  - the server answers a match `queued` and settles it next tick (h11: `settles_at_tick` = tick + 1), so a queued
    probe is only a probe sent: it counts as accepted when the next tick's book has neither trader, and as
    refused ("dropped") when either is back. Its offers are never marked done, so a dropped pair's traders stay
    open to the exact plan.

If the server checks quotes, every probe is refused and the matches are exactly the exact plan's: the same score.
If it checks limits, a probe that lands pairs two traders whose limits cross and who would otherwise have left
unmatched: gains the stall can never take. Refusal codes are kept (`codes`) so the logs settle the question.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from bazaar_agent.agents.matcher import Fee, Match, Quote

DEFAULT_MAX_GAP = 20  # P between a leftover ask (+ fee) and a leftover bid
DEFAULT_PER_TICK = 4  # probes per tick, sent after every exact match
DEFAULT_TRIES_PER_PAIR = 3
DEFAULT_GIVE_UP_AFTER = 8  # refusals in one run with nothing accepted


@dataclass(frozen=True)
class ProbeConfig:
    max_gap: int = DEFAULT_MAX_GAP
    per_tick: int = DEFAULT_PER_TICK
    tries_per_pair: int = DEFAULT_TRIES_PER_PAIR
    give_up_after: int = DEFAULT_GIVE_UP_AFTER


def probe_price(ask: int, bid: int, fee: Fee) -> int:
    """The price whose `[price, price + fee(price)]` sits in the middle of `[bid, ask]` (the midpoint at 0 fee)."""
    centre2 = ask + bid  # twice the centre: integers only
    price = centre2 // 2
    while price > 0 and 2 * price + fee.of(price) > centre2:
        price -= 1
    return price


@dataclass
class _Run:
    accepted: int = 0
    refused: int = 0
    tries: Counter[tuple[str, str]] = field(default_factory=Counter)
    prices: dict[tuple[str, str], set[int]] = field(default_factory=lambda: defaultdict(set))

    def given_up(self, config: ProbeConfig) -> bool:
        return not self.accepted and self.refused >= config.give_up_after


DROPPED = "dropped"  # a queued probe whose traders are back in the next tick's book


class BenchProbe:
    """Per-run memory of the probes: what was tried, what was refused, and whether the run still probes."""

    def __init__(self, config: ProbeConfig | None = None) -> None:
        self.config = config or ProbeConfig()
        self.runs: dict[str, _Run] = {}
        self.codes: Counter[str] = Counter()  # refusal codes over every run: the server's answer to the question
        self.pending: list[Match] = []  # probes the server queued, settled (or dropped) by the next read
        self.quote_rule = False  # a whole run refused every probe: no more probes while this process lives

    def _run(self, item: str) -> _Run:
        return self.runs.setdefault(item.removeprefix("bench:"), _Run())

    def active(self, item: str) -> bool:
        return not self.quote_rule and not self._run(item).given_up(self.config)

    def resolve(self, bench: Iterable[Quote]) -> list[tuple[Match, bool]]:
        """Settle last tick's queued probes against this tick's book: accepted when both traders are gone."""
        present = {str(q.id) for q in bench}
        settled = [(m, str(m.sell.id) not in present and str(m.buy.id) not in present) for m in self.pending]
        self.pending = []
        for m, accepted in settled:
            self.record(m, accepted, None if accepted else DROPPED)
        return settled

    def plan(self, bench: Sequence[Quote], exact: Iterable[Match], fee: Fee, room: int) -> list[Match]:
        """At most `min(room, per_tick)` probes among the bench quotes the exact plan leaves out, smallest gap
        first. Pairs that cross by quote are never probes (the exact plan already took every one worth taking).
        Call `resolve` with this tick's book first."""
        taken = {str(q.id) for m in exact for q in (m.sell, m.buy)}
        left = [q for q in bench if q.bench and str(q.id) not in taken]
        by_run: dict[str, tuple[list[Quote], list[Quote]]] = defaultdict(lambda: ([], []))
        for q in left:
            sells, buys = by_run[q.item]
            (sells if q.side == "sell" else buys).append(q)
        candidates: list[tuple[int, int, Match]] = []
        for item, (sells, buys) in by_run.items():
            if not self.active(item):
                continue
            run = self._run(item)
            asks = sorted(sells, key=lambda q: q.price)  # stable: the book's order among equal quotes
            bids = sorted(buys, key=lambda q: -q.price)
            for rank, (s, b) in enumerate(zip(asks, bids, strict=False)):
                gap = s.price + fee.of(s.price) - b.price
                if gap > self.config.max_gap:
                    break
                if gap <= 0:  # crosses by quote: the exact plan's business, never a probe
                    continue
                key = (str(s.id), str(b.id))
                price = probe_price(s.price, b.price, fee)
                if run.tries[key] >= self.config.tries_per_pair or price in run.prices[key]:
                    continue
                candidates.append((gap, rank, Match(s, b, price, fee.of(price))))
        candidates.sort(key=lambda c: (c[0], c[1], str(c[2].sell.id)))
        return [m for _, _, m in candidates[: max(0, min(room, self.config.per_tick))]]

    def sent(self, m: Match) -> None:
        """The server queued this probe: whether it settles shows in the next tick's book (`resolve`)."""
        self.pending.append(m)

    def record(self, m: Match, accepted: bool, code: str | None = None) -> None:
        """What the server said to one probe (refused at once), or what the next book showed (`resolve`)."""
        run = self._run(m.sell.item)
        key = (str(m.sell.id), str(m.buy.id))
        run.tries[key] += 1
        run.prices[key].add(m.price)
        if accepted:
            run.accepted += 1
        else:
            run.refused += 1
            self.codes[code or "unknown"] += 1
            self.quote_rule = self.quote_rule or run.given_up(self.config)

    def summary(self, item: str) -> str:
        run = self._run(item)
        state = "given up" if run.given_up(self.config) else "stopped" if self.quote_rule else "probing"
        return f"probes {run.accepted} accepted, {run.refused} refused ({state})"

    def forget(self, run: str) -> None:
        self.runs.pop(run, None)
