"""Move impact: what selling, swapping away or buying one copy could do to our score, before it is sent.

The incident (Sat 3 Oct): at tick 947 we sold SAL-07 (asset 438) to dealer Pilar for 29. That copy came from team
t02 at tick 320 for 23 and completed Salamanca (/me `your_value` 118.6). /me `neg_points` fell 134.2 -> 44.6 at
tick 948 (-89.6 = 29 - 118.6) and the board's `negotiating` 20.75 -> 16.48 at its next update (tick 950). Buying
SAL-07 back from Abuela (21) restored the page, not the points: a dealer deal is not a team trade.

The model, fitted to that incident (`neg_points` is the value gained in team trades at private values):
- a copy we got from a team counts its gain while we hold it; once it leaves, to anyone, it counts the price we
  got instead: delta neg_points = price - the copy's `your_value` (from /me, page bonus included);
- a sale to a team is a team trade at private values: the same price - `your_value`, whatever the copy's origin;
- a buy from a team: delta neg_points = the new copy's value to us - the price (fee included);
- any other dealer deal moves no neg_points; every dealer deal adds a small ladder estimate (configurable).
delta score = delta neg_points x k, where k is the board's `negotiating` per neg_point measured on our own /me
snapshots (`slope`), or the fallback when they show no change big enough to measure.

Pure: no network, no database. `impact_board.py` reads the tape and the snapshots once per tick.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from statistics import median
from typing import Any, Literal

from bazaar_agent.intel import TEAM_ID

OriginKind = Literal["team", "dealer", "pack", "start", "unknown"]
Side = Literal["sell", "buy"]
OFF_PAGE_RARITIES = ("epic", "legendary")  # RULES.md: on top of the page (as guardrails.OFF_PAGE_RARITIES)
ANY_TEAM = "*"  # an offer anyone may take (guardrails.ANY_TEAM): a team trade
STARTING_COPIES = 15  # team k was dealt asset ids 15k-14 .. 15k (Friday's card scan, `bazaar supply`)
MIN_JUMP = 5.0  # neg_points: a smaller change hides in the board's own drift
LAG_TICKS = 12  # the board's `negotiating` follows neg_points at its next update (every 10 ticks)
RECENT_EVENTS = 3  # the slope is the median of this many most recent measured changes
MAX_SLOPE = 1.0  # a measured slope above this is noise (another component moved too), never used


@dataclass(frozen=True)
class Origin:
    """How we got one copy: from a team, a dealer, a pack (or a gift, a craft), our starting stock, or unknown."""

    kind: OriginKind
    frm: str | None = None
    price: int | None = None
    tick: int | None = None

    def describe(self) -> str:
        if self.kind in ("team", "dealer"):
            return f"bought from {self.frm} for {self.price} at tick {self.tick}"
        return {"pack": "from a pack, a gift or a craft", "start": "starting stock"}.get(self.kind, "origin unknown")


UNKNOWN = Origin("unknown")


def is_team(who: str | None) -> bool:
    return who == ANY_TEAM or bool(who and TEAM_ID.match(who))


def starting_copy(team: str, asset: int) -> bool:
    number = int(team[1:]) if TEAM_ID.match(team) else 0
    return number > 0 and STARTING_COPIES * (number - 1) < asset <= STARTING_COPIES * number


def origins(settlements: Iterable[Mapping[str, Any]], team: str) -> dict[int, Origin]:
    """Asset id -> how we got each copy we still hold, from settlement payloads in feed order: the last one that
    moved it to us. A copy that left us afterwards is dropped. The giver is a team when it is a team id."""
    out: dict[int, Origin] = {}
    for p in settlements:
        for item in p.get("items") or []:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), int):
                continue
            asset, frm = int(item["id"]), str(item.get("frm") or "")
            if item.get("to") == team:
                kind: OriginKind = "team" if TEAM_ID.match(frm) else "dealer"
                price = p.get("price")
                tick = p.get("tick")
                out[asset] = Origin(
                    kind,
                    frm,
                    price if isinstance(price, int) else None,
                    tick if isinstance(tick, int) else None,
                )
            elif frm == team:
                out.pop(asset, None)
    return out


def origin_of(asset: int | None, known: Mapping[int, Origin] | None, team: str | None) -> Origin:
    """`known` None: the tape was not read (unknown). A copy no settlement brought us is our starting stock or
    came from a pack, a gift or a craft: not a team trade."""
    if asset is None or known is None:
        return UNKNOWN
    if asset in known:
        return known[asset]
    return Origin("start") if team and starting_copy(team, asset) else Origin("pack")


# ---------------------------------------------------------------- k: score per neg_point


@dataclass(frozen=True)
class ScorePoint:
    tick: int
    neg_points: float
    negotiating: float


@dataclass(frozen=True)
class Slope:
    """Board `negotiating` per neg_point, measured apart for losses and gains: the board is relative to the
    other teams, so a gain while we lead in neg_points moved it by ~0 (ticks 376-386) and a loss by 0.048."""

    loss: float
    gain: float
    loss_events: int = 0
    gain_events: int = 0

    def k(self, delta: float) -> float:
        return self.loss if delta < 0 else self.gain

    def describe(self, delta: float) -> str:
        n = self.loss_events if delta < 0 else self.gain_events
        return f"k {self.k(delta):.3f} ({f'{n} measured change(s)' if n else 'fallback'})"


def fallback_slope(fallback: float) -> Slope:
    return Slope(fallback, fallback)


def slope(points: Sequence[ScorePoint], fallback: float) -> Slope:
    """k from our /me snapshots: each neg_points change of at least `MIN_JUMP`, against the board's
    `negotiating` change at its next update (within `LAG_TICKS`; changes inside one update are merged), the
    median of the `RECENT_EVENTS` latest per sign. No measured change of a sign: `fallback`."""
    pts = sorted({p.tick: p for p in points}.values(), key=lambda p: p.tick)
    measured: list[tuple[float, float]] = []  # (delta neg_points, delta negotiating)
    i = 1
    while i < len(pts):
        before, now = pts[i - 1], pts[i]
        if now.neg_points == before.neg_points:
            i += 1
            continue
        g0, t0 = before.negotiating, now.tick
        moved = next((j for j in range(i, len(pts)) if pts[j].tick - t0 > LAG_TICKS or pts[j].negotiating != g0), None)
        if moved is None or pts[moved].tick - now.tick > LAG_TICKS:
            i += 1
            continue
        dn = pts[moved].neg_points - before.neg_points
        if abs(dn) >= MIN_JUMP:
            measured.append((dn, pts[moved].negotiating - before.negotiating))
        i = moved + 1

    def k_of(sign: int) -> tuple[float, int]:
        ks = [dg / dn for dn, dg in measured if dn * sign > 0 and 0 <= dg / dn <= MAX_SLOPE]
        recent = ks[-RECENT_EVENTS:]
        return (round(median(recent), 4), len(recent)) if recent else (fallback, 0)

    (loss, n_loss), (gain, n_gain) = k_of(-1), k_of(1)
    return Slope(loss, gain, n_loss, n_gain)


# ---------------------------------------------------------------- our cards (from /me)


@dataclass(frozen=True)
class Copy:
    asset: int
    ref: str
    rarity: str | None
    your_value: float | None


@dataclass(frozen=True)
class OurCards:
    """What /me says about our copies and pages: each copy's `your_value` (what we lose by giving it away, page
    bonus included) and the sets whose page is complete."""

    team: str | None
    copies: tuple[Copy, ...] = ()
    complete: frozenset[str] = field(default_factory=frozenset)

    def of(self, ref: str) -> list[Copy]:
        return [c for c in self.copies if c.ref == ref]

    def copy(self, asset: int) -> Copy | None:
        return next((c for c in self.copies if c.asset == asset), None)


def our_cards(me: Mapping[str, Any]) -> OurCards:
    team = me.get("id")
    copies = tuple(
        Copy(
            int(a["id"]),
            str(a.get("ref")),
            a.get("rarity") if isinstance(a.get("rarity"), str) else None,
            float(a["your_value"]) if _number(a.get("your_value")) else None,
        )
        for a in me.get("assets") or []
        if isinstance(a, Mapping) and a.get("kind", "card") == "card" and isinstance(a.get("id"), int)
    )
    album = me.get("album")
    pages = (album.get("pages") or []) if isinstance(album, Mapping) else []
    complete = frozenset(str(p.get("set")) for p in pages if isinstance(p, Mapping) and p.get("complete") is True)
    return OurCards(team if isinstance(team, str) and TEAM_ID.match(team) else None, copies, complete)


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def breaks_page(ref: str, rarity: str | None, copies: int, complete: Collection[str]) -> bool:
    """Our only copy of a page card of a complete page: selling it opens a hole in the album."""
    set_code = ref.split("-", 1)[0] if "-" in ref else ""
    return copies <= 1 and set_code in complete and str(rarity or "").lower() not in OFF_PAGE_RARITIES


# ---------------------------------------------------------------- the estimate


@dataclass(frozen=True)
class Facts:
    """What the guard reads once per tick (`impact_board`): how we got each copy, and our score history (k)."""

    team: str
    origins: Mapping[int, Origin]
    points: tuple[ScorePoint, ...] = ()

    def slope(self, fallback: float) -> Slope:
        return slope(self.points, fallback)


@dataclass(frozen=True)
class Impact:
    side: Side
    ref: str
    price: float
    counterparty: str | None
    neg_points: float | None  # None: it cannot be estimated (no value for the copy)
    score: float | None
    k: float
    origin: Origin
    asset: int | None = None
    value: float | None = None
    team_trade: bool = False
    breaks_page: bool = False
    assumed: bool = False  # the origin was unknown: priced as a copy bought from a team (the worst case)
    reason: str = ""

    def as_state(self) -> dict[str, Any]:
        """For a decider's state (judge input): the estimate and why."""
        return {
            "score_delta": None if self.score is None else round(self.score, 3),
            "neg_points_delta": None if self.neg_points is None else round(self.neg_points, 1),
            "k": round(self.k, 4),
            "copy_origin": self.origin.kind,
            "copy_from": self.origin.frm,
            "team_trade": self.team_trade,
            "breaks_complete_page": self.breaks_page,
            "origin_assumed": self.assumed,
            "reason": self.reason,
        }


def estimate(
    side: Side,
    ref: str,
    price: float,
    value: float | None,
    counterparty: str | None,
    slope_: Slope,
    ladder: float,
    origin: Origin = UNKNOWN,
    asset: int | None = None,
    breaks: bool = False,
) -> Impact:
    """The impact of one move. `value`: a sell's copy `your_value`, or a buy's value of one more copy to us.
    `counterparty`: a team id (or "*", anyone), else a dealer. An unknown origin is priced as a team copy."""
    team_trade = is_team(counterparty)
    base = Impact(side, ref, price, counterparty, None, None, slope_.k(-1), origin, asset, value, team_trade, breaks)
    if value is None:
        return _with(base, reason=f"{ref}: no value for the copy, the impact cannot be estimated")
    assumed = side == "sell" and origin.kind == "unknown" and not team_trade
    if side == "sell":
        counts = team_trade or origin.kind in ("team", "unknown")
        dn = price - value if counts else 0.0
    else:
        dn = value - price if team_trade else 0.0
    k = slope_.k(dn)
    score = dn * k + (0.0 if team_trade else ladder)
    return _with(
        base,
        neg_points=dn,
        score=score,
        k=k,
        assumed=assumed,
        reason=_reason(side, ref, price, value, counterparty, team_trade, origin, assumed, dn, slope_, ladder, breaks),
    )


def _with(base: Impact, **changes: Any) -> Impact:
    return replace(base, **changes)


def _reason(
    side: Side,
    ref: str,
    price: float,
    value: float,
    who: str | None,
    team_trade: bool,
    origin: Origin,
    assumed: bool,
    dn: float,
    slope_: Slope,
    ladder: float,
    breaks: bool,
) -> str:
    to = f"{'to' if side == 'sell' else 'from'} {'team ' if team_trade else 'dealer '}{who or '?'}"
    if side == "buy":
        why = "a team trade at private values" if team_trade else "a dealer deal: no neg_points"
        what = f"buy {ref} at {price:g} {to}: {why}; neg_points {dn:+.1f} (value {value:g} - price)"
    else:
        what = f"sell {ref} at {price:g} {to}: {_sell_why(team_trade, origin, assumed)}; neg_points {dn:+.1f} "
        what += f"(price - your_value {value:g})"
    extra = "" if team_trade else f" + dealer ladder {ladder:g}"
    page = "; BREAKS a complete page" if breaks else ""
    return f"{what} × {slope_.describe(dn)}{extra}{page}"


def _sell_why(team_trade: bool, origin: Origin, assumed: bool) -> str:
    if team_trade:
        return "a team trade at private values"
    if assumed:
        return "a copy of unknown origin, priced as one bought from a team"
    if origin.kind == "team":
        return f"the copy was {origin.describe()}"
    return f"a dealer deal of a copy not bought from a team ({origin.describe()})"


def sell_impact(
    cards: OurCards | None,
    ref: str,
    rarity: str | None,
    price: float,
    counterparty: str | None,
    facts: Facts | None,
    fallback: float,
    ladder: float,
    asset: int | None = None,
    value: float | None = None,
) -> Impact:
    """The impact of selling (or swapping away) `asset`, else the copy of `ref` that costs us most. The copy's
    value is /me's (`cards`), else `value` (the caller's); its origin is the tape's (`facts`). `facts` None
    (unread) or naming another team: every origin is unknown and k is the fallback (the worst case)."""
    team = cards.team if cards is not None else None
    if facts is not None and team is not None and facts.team != team:
        facts = None
    slope_ = facts.slope(fallback) if facts is not None else fallback_slope(fallback)
    known = facts.origins if facts is not None else None
    held = cards.of(ref) if cards is not None else []
    candidates = [c for c in held if c.asset == asset] if asset is not None else held
    breaks = breaks_page(ref, rarity, len(held), cards.complete) if cards is not None else False
    if not candidates:  # /me does not show the copy: the caller's value, its asset's origin if any
        origin = origin_of(asset, known, team or (facts.team if facts else None))
        return estimate("sell", ref, price, value, counterparty, slope_, ladder, origin, asset, breaks)
    impacts = [
        estimate(
            "sell",
            ref,
            price,
            c.your_value if c.your_value is not None else value,
            counterparty,
            slope_,
            ladder,
            origin_of(c.asset, known, team),
            c.asset,
            breaks,
        )
        for c in candidates
    ]
    return min(impacts, key=lambda i: float("-inf") if i.score is None else i.score)
