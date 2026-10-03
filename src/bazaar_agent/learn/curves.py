"""How each dealer haggles, per price class, from every team's public threads: the concession curve.

The feed carries the structure of every dealer conversation (`intel.dealer_threads`): the dealer's
asks, the team's bids, the `final` flag and the fill. From those, per (dealer, price class):
- fills: what conversations closed at (each conversation has its own secret limit, at or below it);
- the opening ask and how much the dealer gives up per bid we make;
- patience: how many of the team's bids it takes before the dealer names a final offer;
- silence: a bid so low the dealer never answers it.
Numbers only, never the dealer's words. `learn.lessons` quotes them and `learn.evolve` turns them
into ladder parameters.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import median

from bazaar_agent.evals.dealers import price_class
from bazaar_agent.intel import DealerThread

# The only price classes we learn about. A thread's topic is chosen by the team that opened it and the feed
# publishes it as is, so a made-up "pack" name must never become a class, a lesson or a ladder.
KNOWN_CLASS = re.compile(r"^(card:(common|uncommon|rare|epic|legendary)|pack:sobre_[a-z0-9_]{1,24}|sell)$")
DEALER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def known_class(item: str) -> str | None:
    """`price_class(item)` when it is one we learn about, else None (an unknown or forged topic)."""
    cls = price_class(item)
    return cls if cls is not None and KNOWN_CLASS.fullmatch(cls) else None


def quantile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated quantile (q in [0, 1]); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * min(1.0, max(0.0, q))
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


@dataclass(frozen=True)
class CurveStats:
    """One dealer's behaviour for one price class (`card:uncommon`, `pack:sobre_barrio`, `sell`)."""

    dealer: str
    price_class: str
    threads: int
    fills: tuple[int, ...]  # sorted
    openings: tuple[int, ...]  # the dealer's first ask per conversation
    finals: int  # conversations where the dealer named a final offer
    patience: float | None  # median team bids before the final (conversations with a final)
    concession: float | None  # median drop of the dealer's ask per team bid, after its opening ask
    silent_below: int | None  # highest first bid the dealer never answered (no ask at all)
    thread_ids: tuple[int, ...]  # the evidence

    @property
    def opening(self) -> float | None:
        return median(self.openings) if self.openings else None

    def fill_q(self, q: float) -> float | None:
        return quantile(self.fills, q)

    @property
    def floor(self) -> int | None:
        """The lowest fill anyone got: the cheapest the dealer has ever been."""
        return self.fills[0] if self.fills else None

    def describe(self) -> str:
        """One line a human (and an embedding) can read; numbers only."""
        parts = [f"{self.dealer} {self.price_class}: {self.threads} threads"]
        if self.fills:
            p50 = self.fill_q(0.5)
            parts.append(f"{len(self.fills)} fills {self.fills[0]}-{self.fills[-1]} (median {p50:g})")
        else:
            parts.append("no fill yet")
        if self.opening is not None:
            parts.append(f"opens at {self.opening:g}")
        if self.patience is not None:
            parts.append(f"names a final after ~{self.patience:g} bids ({self.finals} finals)")
        if self.concession is not None:
            parts.append(f"gives ~{self.concession:g} per bid after the opening")
        if self.silent_below is not None:
            parts.append(f"ignored a first bid of {self.silent_below}")
        return "; ".join(parts)


def _drops(prices: Sequence[int]) -> list[int]:
    return [a - b for a, b in zip(prices, prices[1:], strict=False) if a >= b]


def curve_stats(threads: Iterable[DealerThread]) -> dict[tuple[str, str], CurveStats]:
    """Per (dealer, price class), from every dealer thread in the feed (ours and other teams')."""
    groups: dict[tuple[str, str], list[DealerThread]] = defaultdict(list)
    for t in threads:
        cls = known_class(t.item)
        if cls is not None and t.side in ("buy", "sell") and DEALER_ID.fullmatch(t.dealer):
            groups[(t.dealer, cls)].append(t)
    out: dict[tuple[str, str], CurveStats] = {}
    for (dealer, cls), members in groups.items():
        fills = tuple(sorted(t.fill_price for t in members if t.fill_price is not None))
        if cls.startswith("pack:") and not fills:
            continue  # a pack name is the opener's free choice until a dealer actually sells it (a fill)
        openings = tuple(t.dealer_prices[0] for t in members if t.dealer_prices)
        with_final = [t for t in members if t.final_price is not None]
        patience = median(len(t.team_prices) for t in with_final) if with_final else None
        drops = [d for t in members for d in _drops(t.dealer_prices[1:])]
        silent = [
            t.team_prices[0]
            for t in members
            if t.side == "buy" and t.team_prices and not t.dealer_prices and t.fill_price is None
        ]
        out[(dealer, cls)] = CurveStats(
            dealer=dealer,
            price_class=cls,
            threads=len(members),
            fills=fills,
            openings=openings,
            finals=len(with_final),
            patience=float(patience) if patience is not None else None,
            concession=float(median(drops)) if drops else None,
            silent_below=max(silent) if silent else None,
            thread_ids=tuple(sorted(t.thread for t in members)),
        )
    return out
