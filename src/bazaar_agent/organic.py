"""Organic market-making (#13): which venues other teams actually use, and what our venue's share could score.

RULES.md only says market-making counts "value created between other teams on your venue". The formula
used here comes from issue #17's reverse-engineering of the site's front-end ([quote], unverified against a
real score): "√ of the value that other teams create on our venue, capped per pair". Both the √ and the
per-pair cap are parameters, and so is the normalisation (W5's score model: a component scores
`min(1, raw / top-3 mean of raw)`). Pure: feed events in, numbers out.

The flow read off the public feed:
  - `offer.listed` → the venue a maker posted on (a venue's owner can never post on it: `self_venue`);
  - `settlement` → the venue a trade settled on, its two parties, price and fee;
  - `venue.opened` → each team venue's owner, so the owner's own trades are never counted as organic.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

HOUSE = "rastro"


@dataclass
class VenueFlow:
    venue: str
    owner: str | None = None
    listed: int = 0
    sells: int = 0
    bids: int = 0
    makers: set[str] = field(default_factory=set)
    trades: int = 0
    volume: int = 0
    fees: int = 0
    pairs: dict[tuple[str, str], int] = field(default_factory=dict)  # sorted (team, team) -> trades

    @property
    def fee_share(self) -> float:
        """Fees paid as a share of the traded price (El Rastro: 5 % + 1 P per card)."""
        return round(self.fees / self.volume, 4) if self.volume else 0.0


def venue_flows(events: Iterable[Mapping[str, Any]]) -> dict[str, VenueFlow]:
    """Per venue: offers posted, distinct makers, trades between teams other than its owner, distinct pairs.
    Direct-thread trades (venue None) and dealer deals are not venue flow and are skipped."""
    flows: dict[str, VenueFlow] = {}

    def flow(vid: str) -> VenueFlow:
        return flows.setdefault(vid, VenueFlow(vid))

    for e in events:
        kind, p = e.get("type"), e.get("payload") or {}
        if kind == "venue.opened" and p.get("venue"):
            flow(str(p["venue"])).owner = p.get("owner")
        elif kind == "offer.listed":
            offer = p.get("offer") or {}
            vid = p.get("venue") or offer.get("venue")
            if not vid or offer.get("thread") is not None:
                continue
            f = flow(str(vid))
            f.listed += 1
            f.sells += bool((offer.get("give") or {}).get("assets"))
            f.bids += not (offer.get("give") or {}).get("assets")
            if offer.get("maker"):
                f.makers.add(str(offer["maker"]))
        elif kind == "settlement" and p.get("venue") and p.get("kind") in (None, "trade", "match"):
            parties = [str(t) for t in p.get("parties") or []]
            f = flow(str(p["venue"]))
            if len(parties) != 2 or f.owner in parties:
                continue
            a, b = sorted(parties)
            f.trades += 1
            f.volume += int(p.get("price") or 0)
            f.fees += int(p.get("fee") or 0)
            f.pairs[(a, b)] = f.pairs.get((a, b), 0) + 1
    return flows


def organic_raw(
    pair_values: Mapping[tuple[str, str], float], *, sqrt: bool = True, pair_cap: float | None = None
) -> float:
    """One venue's organic raw score: the value created per pair of other teams, each pair capped at
    `pair_cap` (None: no cap), summed, and √ of it (`sqrt=False`: linear)."""
    total = sum(max(0.0, v) if pair_cap is None else min(max(0.0, v), pair_cap) for v in pair_values.values())
    return math.sqrt(total) if sqrt else total


def organic_fraction(ours: float, rivals: Sequence[float]) -> float:
    """Our share of the organic points, normalised to the mean of the top three raws (ours included; every
    team has a raw, most of them 0, so the mean always divides by three). Nobody with any flow scores 0."""
    top = sorted([ours, *rivals, 0.0, 0.0], reverse=True)[:3]
    mean = sum(top) / 3
    return 0.0 if mean <= 0 else round(min(1.0, ours / mean), 4)


def scenario_table(
    ours: Mapping[tuple[str, str], float],
    rivals: Sequence[Mapping[tuple[str, str], float]],
    *,
    pair_cap: float,
) -> dict[str, float]:
    """Our organic fraction under each reading of the formula: √ or linear, with or without the pair cap."""
    out = {}
    for sqrt in (True, False):
        for cap in (pair_cap, None):
            raw = organic_raw(ours, sqrt=sqrt, pair_cap=cap)
            field_ = [organic_raw(r, sqrt=sqrt, pair_cap=cap) for r in rivals]
            out[f"{'sqrt' if sqrt else 'linear'}{'' if cap is None else ', capped'}"] = organic_fraction(raw, field_)
    return out
