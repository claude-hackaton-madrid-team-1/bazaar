"""What the broker believes about each Market Test trader: its hidden limit and whether it is about to leave. Pure.

The bench (RULES.md "Your own market", starter_broker.py) shows quotes, never limits: "Bench traders shade their
quotes away from a limit they keep hidden. Some are patient and some leave soon; most relax their quotes as their
patience runs out, and the firm ones never do." The score counts the gains between the true limits, so a broker
that estimates those limits, and who is about to leave, can beat the stall that crosses by quote.

`TraderModel` is one bench trader seen through the book, read after read:
  - its limit band: a seller's cost lies in `[q0 / markup_hi, q0 / markup_lo]` (q0 = its first ask) and never above
    an ask it has shown; a buyer's value lies in `[q0 / shade_hi, q0 / shade_lo]` and never below a bid it has shown.
    The estimate is the band's midpoint;
  - its leave hazard: the chance it leaves after this tick given how many ticks it has been in the book, from the
    preset's patience prior (impatient traders stay 1–2 ticks, patient ones 3–6, #12). Firm traders need no model
    of their own: a quote that never moves keeps the band where its first quote put it.

The priors (`BenchPrior`) are the simulator's (#55 `_start_bench`) and #12's presets. They are guesses until a real
Market Test is seen: every one is a parameter, and a trader that breaks its prior (older than any patience, a quote
outside its band) degrades to "critical" and "band = what it has shown", never to an error.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal


@dataclass(frozen=True)
class BenchPrior:
    """The bench's hidden parameters as we believe them (#55's generator and #12's presets)."""

    seller_markup: tuple[float, float] = (1.05, 1.30)  # first ask = cost × markup
    buyer_shade: tuple[float, float] = (0.75, 0.95)  # first bid = value × shade
    impatient_share: float = 0.25
    impatient_ticks: tuple[int, int] = (1, 2)
    patient_ticks: tuple[int, int] = (3, 6)
    session_ticks: int = 16

    def patience_pmf(self, ticks: int) -> float:
        """P(a trader stays exactly `ticks` ticks in the book)."""
        p = 0.0
        for share, (lo, hi) in (
            (self.impatient_share, self.impatient_ticks),
            (1 - self.impatient_share, self.patient_ticks),
        ):
            if lo <= ticks <= hi:
                p += share / (hi - lo + 1)
        return p

    def hazard(self, age: int) -> float:
        """P(leaves after this tick | it has been in the book for `age` ticks, this one included)."""
        survival = sum(self.patience_pmf(t) for t in range(age, self.longest() + 1))
        if survival <= 1e-12:
            return 1.0  # older than any patience we know: the prior is wrong, treat it as leaving now
        return self.patience_pmf(age) / survival

    def longest(self) -> int:
        return max(self.impatient_ticks[1], self.patient_ticks[1])

    def widened(self) -> BenchPrior:
        """Wide limit bands (asks up to 1.6 × cost, bids down to half the value): what a limit probe prices on
        (`BenchEdge.probe_prior`), so that a bench shading more than #55 does is still probed."""
        return replace(self, seller_markup=(1.0, 1.6), buyer_shade=(0.5, 1.0))


PRIORS: dict[str, BenchPrior] = {
    "normal": BenchPrior(),
    "hard": BenchPrior(impatient_share=0.35),
}


# Fields a bench offer might carry about when it leaves. None is documented (openapi BenchOffer has only id, give,
# want); the broker logs every key it sees in a bench offer once per session, so the first real Market Test tells.
EXPIRY_ABSOLUTE = ("expires_tick", "expires_at_tick", "leaves_tick", "until_tick", "last_tick")
EXPIRY_RELATIVE = ("ticks_left", "ttl_ticks", "patience_left")


def expiry_of(offer: Mapping[str, object], tick: int) -> int | None:
    """The last tick a bench offer stays in the book, if the offer says (absolute tick, or ticks left after this)."""
    for key in EXPIRY_ABSOLUTE:
        value = offer.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    for key in EXPIRY_RELATIVE:
        value = offer.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return tick + value
    return None


@dataclass
class TraderModel:
    """One bench trader, from its quotes in successive book reads (a seller's ask or a buyer's bid)."""

    id: str
    side: Literal["sell", "buy"]
    first_tick: int
    prior: BenchPrior
    quotes: list[tuple[int, int]] = field(default_factory=list)  # (tick, quote), one per tick it was seen
    expires: int | None = None  # its last tick in the book, when the bench offer says so (`expiry_of`)

    def observe(self, tick: int, quote: int) -> None:
        if self.quotes and self.quotes[-1][0] == tick:
            self.quotes[-1] = (tick, quote)  # a second read in one tick: the latest quote wins
        else:
            self.quotes.append((tick, quote))

    @property
    def quote(self) -> int:
        return self.quotes[-1][1]

    def age(self, tick: int) -> int:
        """Ticks in the book, this one included (1 on the tick it first shows)."""
        return tick - self.first_tick + 1

    def hazard(self, tick: int) -> float:
        if self.expires is not None:
            return 1.0 if tick >= self.expires else 0.0
        return self.prior.hazard(self.age(tick))

    def band(self, prior: BenchPrior | None = None) -> tuple[float, float]:
        """Where its hidden limit lies: (low, high), under its own prior or the one given."""
        prior = prior or self.prior
        first = self.quotes[0][1]
        if self.side == "sell":  # cost ≤ every ask shown
            shown = min(q for _, q in self.quotes)
            lo, hi = first / prior.seller_markup[1], min(first / prior.seller_markup[0], shown)
        else:  # value ≥ every bid shown
            shown = max(q for _, q in self.quotes)
            lo, hi = max(first / prior.buyer_shade[1], shown), first / prior.buyer_shade[0]
        if lo > hi:  # the prior is wrong for this trader: trust only what it has shown
            return (float(shown), float(shown))
        return (lo, hi)

    def limit(self, prior: BenchPrior | None = None) -> float:
        lo, hi = self.band(prior)
        return (lo + hi) / 2

    def p_limit_below(self, price: float, prior: BenchPrior | None = None) -> float:
        """P(limit ≤ price) under a uniform band: a seller accepts `price` with this chance."""
        lo, hi = self.band(prior)
        if hi - lo < 1e-9:
            return 1.0 if lo <= price else 0.0
        return min(1.0, max(0.0, (price - lo) / (hi - lo)))

    def p_limit_above(self, price: float, prior: BenchPrior | None = None) -> float:
        """P(limit ≥ price): a buyer pays `price` (fee included) with this chance."""
        lo, hi = self.band(prior)
        if hi - lo < 1e-9:
            return 1.0 if hi >= price else 0.0
        return min(1.0, max(0.0, (hi - price) / (hi - lo)))
