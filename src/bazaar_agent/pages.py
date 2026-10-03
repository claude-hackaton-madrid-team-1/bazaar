"""Page economics: what finishing a page is worth to us, what it costs, and where the cash goes.

Completing a page does not score by itself (RULES.md "Scoring" has no album term). A complete page raises
the private value of its cards (the catalog's `page_bonus`, 25 % of the page's book × our affinity), and
private value scores only as the surplus of a team-to-team trade. A dealer purchase scores through the
ladder (the share of the dealer's price range), never through what the card is worth to us. So every
source here carries its scoring channel: `ladder` (a dealer) or `trade` (another team on a venue).

Pure functions over the same payloads as `strategy.py` (`/api/me`, `/api/catalog`, `/api/dealers`, the
feed, `/api/schedule`), reusing its market, page-bonus shares, supply and holders. No network, no writes:
`bazaar plan pages` prints this plan; every buy still goes through GUARDRAILS.md when someone runs it.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from statistics import median
from typing import Any, Literal

from bazaar_agent import intel, strategy
from bazaar_agent.evals.dealers import price_class
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.strategy import Card, Market, StrategyParams

Channel = Literal["ladder", "trade"]
Multipliers = Mapping[str, Mapping[str, float]]  # team -> set -> expected multiplier
Verdict = Literal["finish", "blocked", "skip", "complete"]

RASTRO_FEE_RATE = 0.05  # RULES.md: El Rastro charges 5 % plus 1 P per card
RASTRO_FEE_PER_CARD = 1
VENUE_BOND = 250  # RULES.md: a refundable bond of 250 P plus 20 P
VENUE_FEE = 20
SATURDAY_OPENS = 4  # game hours: Friday 19-23 is h0-4, Saturday 09-23 is h4-18, Sunday 09-15 is h18-24
SUNDAY_OPENS = 18
GAME_ENDS = 24


# ---------------------------------------------------------------- supply, refreshed from the feed


def serials_seen(events: Iterable[intel.Event]) -> dict[str, int]:
    """card ref -> the highest serial any public event showed: a lower bound on its minted copies."""
    top: dict[str, int] = defaultdict(int)
    for e in events:
        p = e.get("payload") or {}
        kind = e.get("type")
        items: list[dict[str, Any]] = []
        if kind == "settlement":
            items = p.get("items") or []
        elif kind == "pack.opened" and isinstance(p.get("best"), dict):
            items = [p["best"]]
        elif kind == "offer.listed":
            items = ((p.get("offer") or {}).get("give") or {}).get("assets") or []
        for item in items:
            ref, serial = item.get("ref"), item.get("serial")
            if isinstance(ref, str) and isinstance(serial, int):
                top[ref] = max(top[ref], serial)
    return dict(top)


def refresh_minted(m: Market, events: Iterable[intel.Event], assets: Iterable[dict[str, Any]] = ()) -> Market:
    """The catalog's minted counts, raised to the highest serial seen in the feed or in our own hand
    (a catalog read before a mint is stale; serials only grow)."""
    seen = serials_seen(events)
    for a in assets:
        ref, serial = a.get("ref"), a.get("serial")
        if isinstance(ref, str) and isinstance(serial, int):
            seen[ref] = max(seen.get(ref, 0), serial)
    cards = {ref: replace(c, minted=max(c.minted, seen.get(ref, 0))) for ref, c in m.cards.items()}
    return replace(m, cards=cards)


# ---------------------------------------------------------------- prices by source


@dataclass(frozen=True)
class Ask:
    offer: int
    ref: str
    price: int
    maker: str
    expires_tick: int | None


def open_asks(events: Sequence[intel.Event], tick: int | None) -> list[Ask]:
    """One-card cash asks still open at `tick`: listed, not cancelled, not expired, the copy not settled since."""
    asks: dict[int, tuple[Ask, int, int]] = {}  # offer id -> (ask, asset id, tick listed)
    gone: set[int] = set()
    settled: dict[int, int] = {}  # asset id -> last tick it changed hands
    for e in events:
        kind, p = e.get("type"), e.get("payload") or {}
        if kind == "offer.listed" and e.get("actor"):
            offer = p.get("offer") or {}
            assets = (offer.get("give") or {}).get("assets") or []
            cash = int((offer.get("want") or {}).get("cash") or 0)
            if len(assets) == 1 and cash and isinstance(offer.get("id"), int) and not offer.get("to"):
                a = assets[0]
                ask = Ask(int(offer["id"]), str(a.get("ref")), cash, str(e["actor"]), offer.get("expires_tick"))
                asks[ask.offer] = (ask, int(a.get("id") or -1), int(e.get("tick") or 0))
        elif kind == "offer.cancelled" and isinstance(p.get("offer"), int):
            gone.add(int(p["offer"]))
        elif kind == "settlement":
            for item in p.get("items") or []:
                if isinstance(item.get("id"), int):
                    settled[int(item["id"])] = int(e.get("tick") or 0)
    now = tick if tick is not None else 0
    out = []
    for oid, (ask, asset, listed) in asks.items():
        expired = ask.expires_tick is not None and ask.expires_tick < now
        if oid in gone or expired or settled.get(asset, -1) >= listed:  # the copy changed hands since it was listed
            continue
        out.append(ask)
    return sorted(out, key=lambda a: (a.ref, a.price))


@dataclass(frozen=True)
class DealerFills:
    """Every fill a dealer gave any team for one price class ("card:rare"), from the public threads."""

    dealer: str
    price_class: str
    fills: tuple[int, ...]
    openings: tuple[int, ...]

    @property
    def low(self) -> int | None:
        return min(self.fills) if self.fills else None

    @property
    def mid(self) -> float | None:
        return float(median(self.fills)) if self.fills else None


def dealer_fill_table(events: Iterable[intel.Event]) -> dict[tuple[str, str], DealerFills]:
    """(dealer, price class) -> fills and opening asks, for purchases from the dealer (sales to it excluded)."""
    fills: dict[tuple[str, str], list[int]] = defaultdict(list)
    openings: dict[tuple[str, str], list[int]] = defaultdict(list)
    for t in intel.dealer_threads(events):
        cls = price_class(t.item)
        if t.side != "buy" or cls is None or not cls.startswith("card:"):
            continue
        if t.fill_price is not None:
            fills[(t.dealer, cls)].append(t.fill_price)
        if t.opening_ask is not None:
            openings[(t.dealer, cls)].append(t.opening_ask)
    keys = set(fills) | set(openings)
    return {k: DealerFills(k[0], k[1], tuple(sorted(fills[k])), tuple(sorted(openings[k]))) for k in keys}


def cost_basis(m: Market, ref: str, teams: Iterable[str]) -> dict[str, int]:
    """team -> the price it last paid for this card (a dealer or another team), when the tape shows it.
    A holder rarely sells below what it paid; a starting-hand copy has no basis."""
    wanted = set(teams)
    out: dict[str, int] = {}
    for p in sorted(m.prints, key=lambda p: p.tick):
        if p.ref == ref and p.buyer in wanted:
            out[p.buyer] = p.price
    return out


def rastro_fee(price: float, cards: int = 1) -> int:
    return math.ceil(price * RASTRO_FEE_RATE) + RASTRO_FEE_PER_CARD * cards


@dataclass(frozen=True)
class Source:
    """One way to get a missing card, priced, with the channel through which buying it scores."""

    source: str  # a dealer id, or "teams"
    channel: Channel
    price: float  # what we expect to pay, all in (a team trade includes the Rastro fee)
    low: float | None  # the cheapest we have seen it go for
    basis: str
    max_price: int  # the most we would pay: our value minus the minimum surplus, under the guardrail cap
    sellers: tuple[str, ...]  # the dealer, or the teams that hold or list a copy
    blocked: str | None = None  # why this source cannot fill under today's guardrails
    note: str = ""


DealerCaps = Mapping[tuple[str, str], int]  # (dealer, rarity) -> cap


def dealer_cap(rules: Guardrails, rarity: str, dealer: str, what_if: DealerCaps | None = None) -> int | None:
    """The guardrail cap a dealer buy meets: a what-if, else W3's `dealer_price_caps` (#81, read when the
    loaded guardrails have it), else `max_price_<rarity>`."""
    own = dict(getattr(rules, "dealer_caps", None) or {})
    own.update(what_if or {})
    cap = own.get((dealer, rarity))
    return cap if cap is not None else rules.max_price_for(rarity)


def parse_caps(text: str) -> dict[tuple[str, str], int]:
    """ "chato:rare=93,chato:uncommon=31" -> {("chato", "rare"): 93, ...}; a bad entry raises ValueError."""
    out: dict[tuple[str, str], int] = {}
    for entry in filter(None, (e.strip() for e in text.split(","))):
        who, _, price = entry.partition("=")
        dealer, _, rarity = who.partition(":")
        if not (dealer and rarity and price.isdigit() and int(price) >= 1):
            raise ValueError(f"cap {entry!r}: use dealer:rarity=price, e.g. chato:rare=93")
        out[(dealer.strip(), rarity.strip())] = int(price)
    return out


def dealer_sources(
    m: Market,
    card: Card,
    value: float,
    table: Mapping[tuple[str, str], DealerFills],
    params: StrategyParams,
    rules: Guardrails,
    what_if: DealerCaps | None = None,
) -> list[Source]:
    """Each unlocked dealer that sells this card's rarity for its set, priced at the median fill any team got."""
    out = []
    for q in m.quotes:
        if q.item != card.rarity or (q.sets is not None and card.set_code not in q.sets):
            continue
        if card.set_code not in m.released or card.minted >= card.print_run:
            continue
        seen = table.get((q.dealer, f"card:{card.rarity}"))
        low, mid = (seen.low, seen.mid) if seen else (None, None)
        price = mid if mid is not None else float(q.list_price)
        basis = f"{q.dealer} fills ×{len(seen.fills)} {low}–{max(seen.fills)}" if seen and seen.fills else "list"
        cap = dealer_cap(rules, card.rarity, q.dealer, what_if)
        top = math.floor(value - params.min_buy_surplus)
        top = min(top, cap) if cap is not None else top
        blocked = None
        if cap is not None and low is not None and cap < low:
            blocked = f"max_price_{card.rarity} {cap} < lowest {q.dealer} fill {low}"
        elif cap is not None and cap < price:
            blocked = f"max_price_{card.rarity} {cap} < median {q.dealer} fill {price:g}: fills only at its luckiest"
        elif top < price:
            blocked = f"worth {value:.0f}, {q.dealer} fills ~{price:g}: surplus below min_buy_surplus"
        out.append(Source(q.dealer, "ladder", price, low, basis, top, (q.dealer,), blocked))
    return out


def reservation(
    m: Market, card: Card, team: str, paid: Mapping[str, int], expected: Multipliers | None = None
) -> float:
    """The least a holder plausibly sells for: what it paid, and at least the card's worth to it. With
    `expected` (team -> set -> expected multiplier, W4's affinity map) that is book × its expected multiplier.
    Otherwise every team holds the same multipliers (ours, shuffled): a team that chases the set holds the
    top one, any other team is priced at their mean."""
    mults = list(m.affinity.values()) or [1.0]
    mult = max(mults) if team in m.chasers.get(card.set_code, ()) else sum(mults) / len(mults)
    if expected is not None and card.set_code in expected.get(team, {}):
        mult = expected[team][card.set_code]
    return float(max(paid.get(team, 0), math.ceil(card.book * mult)))


@dataclass(frozen=True)
class TeamTape:
    """Team-to-team prints and each card's rarity: read once per plan, used for every card."""

    peers: tuple[intel.Print, ...]
    rarity_of: dict[str, str]

    @classmethod
    def of(cls, m: Market) -> TeamTape:
        return cls(tuple(strategy.peer_prints(m)), {ref: c.rarity for ref, c in m.cards.items()})


def team_source(
    m: Market,
    card: Card,
    value: float,
    asks: Sequence[Ask],
    params: StrategyParams,
    rules: Guardrails,
    expected: Multipliers | None = None,
    tape: TeamTape | None = None,
) -> Source | None:
    """Another team on El Rastro, plus the fee. An open ask is a team willing to sell at its price. Without
    one, each likely holder is priced at the higher of the tape and its `reservation`, and the cheapest
    holder sets the price (a holder that chases the set wants book × the top multiplier). None when no
    team holds a copy."""
    mine = [a for a in asks if a.ref == card.ref and a.maker != m.us]
    holders = m.holders.get(card.ref, ())
    if not mine and card.minted <= m.held.get(card.ref, 0):
        return None
    chasers = set(m.chasers.get(card.set_code, ()))
    paid = cost_basis(m, card.ref, holders)
    rivals = [h for h in holders if h in chasers]
    sellers: tuple[str, ...]
    if mine:
        best = mine[0]
        price, basis, sellers = float(best.price), f"open ask #{best.offer} by {best.maker}", (best.maker,)
        low: float | None = float(best.price)
    else:
        tape = tape or TeamTape.of(m)
        fallback = params.rare_fallback_price if card.rarity == "rare" else card.book
        est = strategy.estimate_price(card.ref, card.rarity, tape.peers, tape.rarity_of, None, fallback)
        same = [p.price for p in tape.peers if p.ref == card.ref] or [
            p.price for p in tape.peers if tape.rarity_of.get(p.ref) == card.rarity
        ]
        low = float(min(same)) if same else None
        price, basis, sellers = est.price, est.basis, holders
        if holders:
            asking = {h: max(est.price, reservation(m, card, h, paid, expected)) for h in holders}
            price = min(asking.values())
            sellers = tuple(h for h in holders if asking[h] == price)
            if price > est.price:
                basis = f"{est.basis}, raised to the cheapest holder's reservation"
    fee = rastro_fee(price)
    cap = rules.max_price_for(card.rarity)
    top = math.floor(value - params.min_buy_surplus - fee)  # the guardrail cap applies to the price, not the fee
    top = min(top, cap) if cap is not None else top
    blocked = None
    if cap is not None and round(price) > cap:
        blocked = f"max_price_{card.rarity} {cap} < team price {price:g}"
    elif value - params.min_buy_surplus < price + fee:
        blocked = f"worth {value:.0f}, teams ask ~{price:g} + fee {fee}: surplus below min_buy_surplus"
    note = f"holders paid {', '.join(f'{t} {p}' for t, p in sorted(paid.items()))}" if paid else ""
    if rivals:
        note += ("; " if note else "") + f"{', '.join(rivals)} chase {card.set_code} themselves"
    elif not holders and not mine:
        note += ("; " if note else "") + "holder unknown: a public bid"
    return Source("teams", "trade", price + fee, low, f"{basis} + fee {fee}", top, sellers, blocked, note)


def from_affinity_map(data: Mapping[str, Any], min_p: float = 0.5) -> tuple[dict[str, list[str]], dict[str, Any]]:
    """W4's `bazaar affinity --json` ({team: {p_top: {set: p}, expected: {set: multiplier}}}) as (chasers:
    set -> teams whose top set it is with probability ≥ `min_p`, expected: team -> set -> multiplier)."""
    chasers: dict[str, list[str]] = defaultdict(list)
    expected: dict[str, Any] = {}
    for team, row in dict(data).items():
        for set_code, p in dict(row.get("p_top") or {}).items():
            if float(p) >= min_p:
                chasers[str(set_code)].append(str(team))
        expected[str(team)] = multipliers_from({team: row.get("expected") or {}})[str(team)]
    return dict(chasers), expected


def chasers_from(data: Any) -> dict[str, list[str]]:
    """{set: [team, ...]}, checked."""
    return {str(k): [str(t) for t in list(v)] for k, v in dict(data).items()}


def multipliers_from(data: Any) -> dict[str, dict[str, float]]:
    """{team: {set: multiplier}}, checked."""
    return {str(t): {str(k): float(v) for k, v in dict(row).items()} for t, row in dict(data).items()}


# ---------------------------------------------------------------- per card and per page


@dataclass(frozen=True)
class CardEconomics:
    ref: str
    set_code: str
    rarity: str
    book: float
    affinity: float
    value: float  # book × affinity: one copy, without the page
    bonus_share: float  # its share (by book, among the page's missing cards) of the page bonus, weighted
    minted: int  # a lower bound: the catalog, raised by the highest serial seen
    print_run: int
    holders: tuple[str, ...]
    chasers: tuple[str, ...]
    sources: tuple[Source, ...]

    @property
    def value_with_bonus(self) -> float:
        return self.value + self.bonus_share

    @property
    def best(self) -> Source | None:
        """The cheapest source today's guardrails let fill."""
        ok = [s for s in self.sources if s.blocked is None]
        return min(ok, key=lambda s: s.price) if ok else None

    @property
    def cheapest(self) -> Source | None:
        """The cheapest source at all (it may need a guardrail change)."""
        return min(self.sources, key=lambda s: s.price) if self.sources else None

    def pick(self, min_surplus: float, scoring: Iterable[str] = ()) -> Source | None:
        """The source to buy from: the cheapest one that scores, else the cheapest fillable. A team buy
        scores its surplus at our private values (when its price leaves `min_surplus` of the card's own
        value, no page bonus); a dealer deal scores only while it is one of our best three for that level
        (RULES.md): `scoring` names the dealers where a deal still does."""
        dealers = set(scoring)
        ok = [s for s in self.sources if s.blocked is None]
        scores = [
            s
            for s in ok
            if (s.channel == "trade" and self.value - s.price >= min_surplus)
            or (s.channel == "ladder" and s.source in dealers)
        ]
        return min(scores, key=lambda s: s.price) if scores else self.best

    @property
    def surplus(self) -> float | None:
        return None if self.best is None else self.value_with_bonus - self.best.price

    @property
    def surplus_per_primas(self) -> float | None:
        best = self.best
        return None if best is None or best.price <= 0 else (self.value_with_bonus - best.price) / best.price


def card_economics(
    m: Market,
    card: Card,
    table: Mapping[tuple[str, str], DealerFills],
    asks: Sequence[Ask],
    params: StrategyParams,
    rules: Guardrails,
    expected: Multipliers | None = None,
    tape: TeamTape | None = None,
    what_if: DealerCaps | None = None,
) -> CardEconomics:
    aff = m.affinity.get(card.set_code, 1.0)
    share = strategy.bonus_shares(m, card.set_code).get(card.ref, 0.0) * params.page_bonus_weight
    value = card.book * aff
    sources: list[Source] = dealer_sources(m, card, value + share, table, params, rules, what_if)
    team = team_source(m, card, value + share, asks, params, rules, expected, tape)
    if team is not None:
        sources.append(team)
    return CardEconomics(
        card.ref,
        card.set_code,
        card.rarity,
        card.book,
        aff,
        round(value, 2),
        round(share, 2),
        card.minted,
        card.print_run,
        m.holders.get(card.ref, ()),
        m.chasers.get(card.set_code, ()),
        tuple(sorted(sources, key=lambda s: s.price)),
    )


@dataclass(frozen=True)
class PageEconomics:
    set_code: str
    affinity: float
    have: int
    of: int
    bonus: float  # the whole page bonus, in primas of private value
    missing: tuple[CardEconomics, ...]
    verdict: Verdict
    why: str

    @property
    def cost(self) -> float | None:
        """Every missing card at its cheapest fillable source; None when one has none."""
        prices = [c.best.price for c in self.missing if c.best is not None]
        return sum(prices) if len(prices) == len(self.missing) else None

    @property
    def cost_if_unblocked(self) -> float | None:
        prices = [c.cheapest.price for c in self.missing if c.cheapest is not None]
        return sum(prices) if len(prices) == len(self.missing) else None

    @property
    def value(self) -> float:
        """What the missing cards plus the bonus add to our private value."""
        return sum(c.value for c in self.missing) + self.bonus

    @property
    def surplus(self) -> float | None:
        cost = self.cost if self.cost is not None else self.cost_if_unblocked
        return None if cost is None else self.value - cost

    @property
    def bonus_per_primas(self) -> float | None:
        cost = self.cost if self.cost is not None else self.cost_if_unblocked
        return None if not cost else self.bonus / cost


def page_verdict(missing: Sequence[CardEconomics], bonus: float) -> tuple[Verdict, str]:
    if not missing:
        return "complete", "page complete: the bonus is ours; selling a page card gives it up"
    unsourced = [c.ref for c in missing if not c.sources]
    if unsourced:
        return "skip", f"no source today for {', '.join(unsourced)} (none minted beyond the holders' own)"
    blocked = [c for c in missing if c.best is None]
    total = sum(c.value for c in missing) + bonus
    cost = sum(s.price for c in missing if (s := c.best or c.cheapest) is not None)
    if total - cost <= 0:
        return "skip", f"costs {cost:.0f} for {total:.0f} of value: no surplus"
    if blocked:
        why = "; ".join(f"{c.ref}: {c.sources[0].blocked}" for c in blocked)
        return "blocked", f"worth {total:.0f} for {cost:.0f}, but {why}"
    return "finish", f"worth {total:.0f} (bonus {bonus:.0f}) for ~{cost:.0f}"


def page_economics(
    me: dict[str, Any],
    catalog: dict[str, Any],
    events: Sequence[intel.Event],
    dealers: Iterable[dict[str, Any]],
    params: StrategyParams,
    rules: Guardrails,
    chasers: Mapping[str, Sequence[str]] | None = None,
    expected: Multipliers | None = None,
    what_if: DealerCaps | None = None,
) -> list[PageEconomics]:
    """Every released page, best first: what is missing, from whom, at what price, and whether to finish it.
    `chasers` (set -> teams) replaces the feed's top-set guess and `expected` (team -> set -> multiplier)
    prices each holder, both e.g. from W4's affinity map."""
    m = market_for(me, catalog, events, dealers, chasers)
    return pages_of(m, events, params, rules, expected, what_if)


def market_for(
    me: dict[str, Any],
    catalog: dict[str, Any],
    events: Sequence[intel.Event],
    dealers: Iterable[dict[str, Any]],
    chasers: Mapping[str, Sequence[str]] | None = None,
) -> Market:
    """strategy's market, minted counts raised to what the feed has shown, chasers replaced when given."""
    m = refresh_minted(strategy.build_market(me, catalog, events, dealers), events, me.get("assets") or [])
    return m if chasers is None else replace(m, chasers={k: tuple(v) for k, v in chasers.items()})


def pages_of(
    m: Market,
    events: Sequence[intel.Event],
    params: StrategyParams,
    rules: Guardrails,
    expected: Multipliers | None = None,
    what_if: DealerCaps | None = None,
) -> list[PageEconomics]:
    table = dealer_fill_table(events)
    asks = open_asks(events, m.tick)
    tape = TeamTape.of(m)
    pages = []
    for set_code in m.released:
        page = strategy.page_cards(m, set_code)
        missing = [
            card_economics(m, c, table, asks, params, rules, expected, tape, what_if)
            for c in page
            if m.held.get(c.ref, 0) == 0
        ]
        bonus = strategy.page_bonus_of(m, set_code) * params.page_bonus_weight
        verdict, why = page_verdict(missing, bonus)
        pages.append(
            PageEconomics(
                set_code,
                m.affinity.get(set_code, 1.0),
                len(page) - len(missing),
                len(page),
                round(bonus, 2),
                tuple(sorted(missing, key=lambda c: -c.value_with_bonus)),
                verdict,
                why,
            )
        )
    order = {"finish": 0, "blocked": 1, "skip": 2, "complete": 3}
    return sorted(pages, key=lambda p: (order[p.verdict], -(p.surplus or 0.0)))


# ---------------------------------------------------------------- cash: grants, the venue, the ladder, pages


@dataclass(frozen=True)
class Grant:
    hour: float
    cash: int
    note: str


def grants_from(schedule: dict[str, Any], after_hours: float | None = None) -> list[Grant]:
    """Every organiser `grant_all` with cash still to come, from `GET /api/schedule` (not assumed)."""
    body = schedule.get("body", schedule)
    now = float(body.get("now_hours") or 0) if after_hours is None else after_hours
    out = []
    for item in body.get("upcoming") or []:
        cash = int((item.get("params") or {}).get("cash") or 0)
        if item.get("action") == "grant_all" and cash and float(item.get("at_hours") or 0) >= now:
            out.append(Grant(float(item["at_hours"]), cash, str(item.get("note") or "")))
    return sorted(out, key=lambda g: g.hour)


@dataclass(frozen=True)
class LadderSlot:
    """One planned dealer deal from W3's `ladder_plan.json` schedule: a price class, and the card when the
    plan names one (`--refs`)."""

    hour: int
    dealer: str
    price_class: str  # "card:common", "card:uncommon", "pack:sobre_barrio"
    expected: float  # the price the backtest expects
    reserve: int  # the plan's max: what the budget sets aside
    ref: str | None = None


def ladder_slots_from(plan: dict[str, Any]) -> list[LadderSlot]:
    """W3's scheduled dealer deals (`schedule`), one slot per conversation, in schedule order."""
    out = []
    for row in plan.get("schedule") or []:
        if not row.get("plan"):
            continue
        out.append(
            LadderSlot(
                int(row.get("game_hour") or 0),
                str(row.get("dealer")),
                str(row.get("price_class")),
                float((row.get("expected") or {}).get("price") or row["plan"]["max"]),
                int(row["plan"]["max"]),
                str(row["ref"]) if row.get("ref") else None,
            )
        )
    return out


@dataclass(frozen=True)
class Step:
    hour: int
    kind: Literal["grant", "venue", "ladder", "trade", "buy", "sell", "held"]
    item: str
    source: str
    amount: float  # cash out (positive) or in (negative)
    max_price: int | None
    cash_after: float
    note: str
    gain: float | None = None  # a team buy: private value gained (what the trade component scores)


@dataclass(frozen=True)
class Scenario:
    name: str
    venue_hour: int | None
    floor: int
    steps: tuple[Step, ...]
    start_cash: float = 0.0

    @property
    def bought(self) -> tuple[str, ...]:
        return tuple(s.item for s in self.steps if s.kind == "buy")

    @property
    def held(self) -> tuple[str, ...]:
        return tuple(s.item for s in self.steps if s.kind == "held")

    @property
    def venue_opened(self) -> int | None:
        return next((s.hour for s in self.steps if s.kind == "venue"), None)

    @property
    def ladder_deals(self) -> int:
        return sum(1 for s in self.steps if s.kind == "ladder" or (s.kind == "buy" and s.source != "teams"))

    @property
    def ladder_held(self) -> int:
        """Planned ladder deals that did not fit (the floor or the hour cap); duplicates are counted apart."""
        return sum(1 for s in self.steps if s.kind == "held" and s.item.startswith("ladder")) - self.ladder_duplicates

    @property
    def ladder_duplicates(self) -> int:
        """Planned ladder deals for a card the trade plan already buys from a team: never run."""
        return sum(1 for s in self.steps if s.kind == "held" and s.note.startswith("duplicate:"))

    @property
    def trade_surplus(self) -> float:
        """Private value gained on the planned team purchases (the page bonus on the card that completes it)."""
        return sum(s.gain for s in self.steps if s.kind in ("buy", "trade") and s.gain is not None)

    @property
    def end_cash(self) -> float:
        return self.steps[-1].cash_after if self.steps else self.start_cash


@dataclass(frozen=True)
class Want:
    """A planned buy: the card, the source we would buy it from, and what it scores."""

    card: CardEconomics
    source: Source
    page: str
    completes: bool = False  # the last missing card of a page worth finishing
    page_bonus: float = 0.0
    finishing: bool = False  # one leg of a page worth finishing: bought only when the rest of it fits too
    slot_only: bool = False  # a dealer buy outside a page we finish: only inside a planned ladder deal

    @property
    def price_class(self) -> str:
        return f"card:{self.card.rarity}"

    @property
    def trade_surplus(self) -> float | None:
        """A team buy scores value − price at our private values (book × affinity: one more copy of a card
        we lack); the card that completes the page also carries the whole page bonus, the others none
        (UNVERIFIED: how the server credits the bonus). A dealer buy: None, it scores as a ladder share."""
        if self.source.channel != "trade":
            return None
        return self.card.value + (self.page_bonus if self.completes else 0.0) - self.source.price


def buy_list(
    pages: Sequence[PageEconomics],
    min_surplus: float,
    skip: Iterable[str] = (),
    scoring: Mapping[str, int] | None = None,
) -> list[Want]:
    """The order to buy in, each card from its `pick`. Pages worth finishing first (best surplus first); in
    each page the dealer legs before the team legs, so the card that completes the page is bought from a
    team, where the page bonus can count as trade surplus. Then single cards worth more than they cost.
    `skip`: cards another plan already buys (W4's trade plan), never bought twice. `scoring`: dealer -> how
    many more deals still count among our best three (each pick of that dealer uses one)."""
    slots = dict(scoring or {})
    wants: list[Want] = []

    def pick(c: CardEconomics) -> Source | None:
        one = c.pick(min_surplus, [d for d, n in slots.items() if n > 0])
        if one is not None and one.channel == "ladder" and slots.get(one.source, 0) > 0:
            slots[one.source] -= 1
        return one

    seen: set[str] = set(skip)
    for p in pages:
        if p.verdict != "finish":
            continue
        legs = [(c, s) for c in p.missing if c.ref not in seen and (s := pick(c)) is not None]
        legs.sort(key=lambda cs: (cs[1].channel == "trade", -cs[0].value))
        for i, (c, src) in enumerate(legs):  # the trade plan's legs go first (its offers open at the start)
            wants.append(Want(c, src, p.set_code, completes=i == len(legs) - 1, page_bonus=p.bonus, finishing=True))
            seen.add(c.ref)
    singles = []
    for p in pages:
        for c in p.missing:
            if c.ref in seen:
                continue
            before = dict(slots)
            one = pick(c)
            if one is None or c.value - one.price < min_surplus:
                slots.update(before)  # not bought: its scoring deal stays free
                continue
            # a dealer deal beyond our best three scores nothing: such a card rides a planned ladder deal or waits
            singles.append((c, one, one.channel == "ladder" and before.get(one.source, 0) == slots.get(one.source, 0)))
    for c, one, slot_only in sorted(singles, key=lambda x: -(x[0].value - x[1].price) / max(x[1].price, 1.0)):
        wants.append(Want(c, one, c.set_code, slot_only=slot_only))
    return wants


@dataclass(frozen=True)
class PlannedTrade:
    """A team trade another plan already makes (W4's `trade-plan.json`): its cash is committed while it is open."""

    counterparty: str
    refs_in: tuple[str, ...]  # the cards we receive
    cash_out: int
    cash_in: int
    expected: float  # the plan's expected surplus for us
    note: str


def trades_from(plan: dict[str, Any]) -> list[PlannedTrade]:
    """W4's trade plan (`listings` and `threads`): what each trade brings in and the cash it promises."""
    out = []
    for item in (plan.get("listings") or []) + (plan.get("threads") or []):
        give, want = item.get("give") or {}, item.get("want") or {}
        out.append(
            PlannedTrade(
                str(item.get("counterparty") or item.get("to") or "anyone"),
                tuple(str(r) for r in want.get("cards") or []),
                int(give.get("cash") or 0),
                int(want.get("cash") or 0),
                float(item.get("expected") or 0),
                str(item.get("reason") or item.get("kind") or ""),
            )
        )
    return out


def _buy_step(hour: int, w: Want, price: float, cash: float, how: str) -> Step:
    gain = w.trade_surplus
    scores = f"trade surplus {gain:.1f} " if gain is not None else "ladder share "
    last = "; completes the page" if w.completes else ""
    note = f"{w.page} page, {scores}({how}; {w.source.basis}){last}"
    return Step(hour, "buy", w.card.ref, w.source.source, price, w.source.max_price, cash, note, gain)


def _blocker(cash: float, price: float, floor: int, spent: float, rules: Guardrails, reserve: float = 0.0) -> str:
    """Why a buy does not fit this hour ('' when it does): the floor, or the hour's spend cap."""
    cap = rules.max_spend_per_game_hour
    if price > cap:
        return f"price {price:.0f} > max_spend_per_game_hour {cap}"
    if cash - price - reserve < floor:
        rest = f" − the page's other team legs {reserve:.0f}" if reserve else ""
        return f"cash {cash:.0f} − {price:.0f}{rest} < floor {floor}"
    if spent + price > cap:
        return f"this hour's spend {spent:.0f} + {price:.0f} > max_spend_per_game_hour {cap}"
    return ""


def cash_plan(
    name: str,
    cash: float,
    start_hour: int,
    grants: Sequence[Grant],
    wants: Sequence[Want],
    rules: Guardrails,
    *,
    venue_hour: int | None = None,
    ladder: Sequence[LadderSlot] = (),
    sells: Sequence[tuple[int, str, float]] = (),
    trades: Sequence[PlannedTrade] = (),
    floor: int | None = None,
    venue_floor_rule: bool = True,
) -> Scenario:
    """Walk the game hours from `start_hour`: grants and planned sells in, the venue (bond + fee) out, W4's
    trades (tried every hour until they fit), W3's ladder slots (a slot that names a card buys it from its
    dealer; one that only names a price class buys a wanted page card of that class), then the other wanted
    cards, while the hour's spend cap (`max_spend_per_game_hour`) and the floor allow. `floor` is a what-if;
    by default GUARDRAILS.md's `cash_floor`. Prices are the expected fills; whatever does not fit is `held`,
    with the reason. Ladder slots before `start_hour` are history (the feed has them). `venue_floor_rule`:
    PR #71's check (unmerged), the bond and fee may not take cash below the floor; off, only the cash."""
    base = rules.cash_floor if floor is None else floor
    start_cash, planned = cash, venue_hour
    taken = {ref for t in trades for ref in t.refs_in}
    held_why: dict[str, str] = {}
    steps: list[Step] = []
    pending = list(wants)
    waiting = list(trades)
    opened = False
    for hour in range(start_hour, GAME_ENDS):
        # until the venue opens, its bond and fee are kept on top of the floor (PR #71 refuses the opening
        # when they would take cash below the floor)
        reserve_venue = venue_floor_rule and venue_hour is not None and not opened
        floor = base + (VENUE_BOND + VENUE_FEE if reserve_venue else 0)
        for g in grants:
            if hour <= g.hour < hour + 1 or (hour == start_hour and g.hour < start_hour):
                cash += g.cash
                steps.append(Step(hour, "grant", f"+{g.cash}", "organisers", -g.cash, None, cash, g.note))
        for h, ref, price in sells:
            if h == hour:
                cash += price
                steps.append(Step(hour, "sell", ref, "teams", -price, None, cash, "expected fill of a planned sell"))
        if venue_hour == hour:
            # PR #71's guardrail: the bond and fee may not take cash below the floor either
            if cash - (VENUE_BOND + VENUE_FEE) >= (base if venue_floor_rule else 0):
                cash -= VENUE_BOND + VENUE_FEE
                opened, floor = True, base
                note = f"bond {VENUE_BOND} (refundable after closing and a cooldown) + fee {VENUE_FEE}"
                steps.append(Step(hour, "venue", "venue", "organisers", VENUE_BOND + VENUE_FEE, None, cash, note))
            else:
                need = VENUE_BOND + VENUE_FEE + (base if venue_floor_rule else 0)
                rule = f" + floor {base}, #71's rule" if venue_floor_rule else ""
                note = f"cash {cash:.0f} < {need} (bond + fee {VENUE_BOND + VENUE_FEE}{rule}): refused"
                venue_hour, floor = None, base  # not retried: the plan says when; Marius decides again
                steps.append(Step(hour, "held", "venue", "organisers", 0, None, cash, note))
        spent = 0.0
        for t in sorted(waiting, key=lambda t: t.cash_out > t.cash_in):  # cash in first, else the plan's order
            if _blocker(cash, t.cash_out, floor, spent, rules):
                continue
            waiting.remove(t)
            cash += t.cash_in - t.cash_out
            spent += t.cash_out
            item = "+".join(t.refs_in) or "sell"
            note = f"W4 plan: {t.note}" + (f"; +{t.cash_in} P in" if t.cash_in else "")
            steps.append(
                Step(hour, "trade", item, t.counterparty, t.cash_out - t.cash_in, None, cash, note, t.expected)
            )
        for slot in (s for s in ladder if s.hour == hour):
            if slot.ref and slot.ref in taken:
                note = f"duplicate: the trade plan already buys {slot.ref} from a team"
                steps.append(Step(hour, "held", f"ladder {slot.ref}", slot.dealer, 0, slot.reserve, cash, note))
                continue
            if slot.ref:  # the plan names the card: it is bought from the slot's dealer, whatever our list picked
                match = next((w for w in pending if w.card.ref == slot.ref), None)
                price = slot.expected
            else:
                match = next(
                    (w for w in pending if w.source.source == slot.dealer and w.price_class == slot.price_class), None
                )
                price = match.source.price if match else slot.expected
            why = _blocker(cash, price, floor, spent, rules)
            if why:
                item = f"ladder {slot.ref or slot.price_class}"
                steps.append(Step(hour, "held", item, slot.dealer, 0, slot.reserve, cash, why))
                continue
            cash -= price
            spent += price
            if match is not None:
                pending.remove(match)
            if match is not None and match.source.source == slot.dealer:
                steps.append(_buy_step(hour, match, price, cash, f"ladder slot {slot.price_class}"))
                continue
            what = slot.ref or slot.price_class
            if match is not None:  # the list picked another source for it: the slot's dealer sells it instead
                note = f"W3 slot buys {what} from {slot.dealer} (the buy list picked {match.source.source})"
            else:
                kind = "not in our buy list" if slot.ref else "no page card fits it"
                note = f"W3 slot ({kind}): expected {slot.expected:g}, max {slot.reserve}"
            steps.append(Step(hour, "ladder", what, slot.dealer, price, slot.reserve, cash, note))
        still: list[Want] = []
        done: list[Want] = []
        for w in pending:
            if w.slot_only:
                still.append(w)
                continue
            price = w.source.price
            if w.completes:  # the card that completes the page comes last, or it completes nothing
                before = [
                    x.card.ref for x in pending if x is not w and x not in done and x.finishing and x.page == w.page
                ]
                if before:
                    still.append(w)
                    held_why[w.card.ref] = f"waits for the page's other legs: {', '.join(before)}"
                    continue
            # a team leg of a page we finish waits until the page's other team legs fit as well: a lone rare
            # bought without the card that completes the page earns its book value, not the bonus
            reserve = 0.0
            if w.finishing and w.source.channel == "trade":
                reserve = sum(
                    x.source.price
                    for x in pending
                    if x is not w and x not in done and x.finishing and x.page == w.page and x.source.channel == "trade"
                )
            why = _blocker(cash, price, floor, spent, rules, reserve)
            if why:
                still.append(w)
                held_why[w.card.ref] = why
                continue
            cash -= price
            spent += price
            done.append(w)
            steps.append(_buy_step(hour, w, price, cash, "own conversation" if w.source.channel == "ladder" else "bid"))
        pending = still
    for t in waiting:
        why = _blocker(cash, t.cash_out, base, 0.0, rules) or "never fitted an hour"
        item = f"trade {'+'.join(t.refs_in) or t.counterparty}"
        steps.append(Step(GAME_ENDS, "held", item, t.counterparty, 0, None, cash, why))
    for w in pending:
        reason = "no planned ladder deal of its class left" if w.slot_only else held_why.get(w.card.ref, "")
        steps.append(Step(GAME_ENDS, "held", w.card.ref, w.source.source, 0, w.source.max_price, cash, reason))
    return Scenario(name, planned, base, tuple(steps), start_cash)


# ---------------------------------------------------------------- the whole plan


@dataclass(frozen=True)
class PagePlan:
    tick: int | None
    cash: int
    pages: tuple[PageEconomics, ...]
    wants: tuple[Want, ...]
    grants: tuple[Grant, ...]
    scenarios: tuple[Scenario, ...]
    notes: tuple[str, ...]


def planned_sells(
    me: dict[str, Any], m: Market, params: StrategyParams, rules: Guardrails, hour: int
) -> list[tuple[int, str, float]]:
    """strategy.sell_moves as cash in (ask − the Rastro fee), for the scenario that counts them."""
    moves = strategy.sell_moves(m, me.get("assets") or [], params, rules)
    return [(hour, mv.ref, mv.price - rastro_fee(mv.price)) for mv in strategy.rank(moves, m, params)]


def unopened_packs(me: dict[str, Any]) -> list[str]:
    return [str(a.get("ref")) for a in me.get("assets") or [] if a.get("kind") == "pack"]


def scoring_dealers(
    events: Iterable[intel.Event],
    us: str,
    dealers: Iterable[dict[str, Any]],
    ladder: Sequence[LadderSlot] = (),
    *,
    start_hour: int = 0,
    taken: Iterable[str] = (),
    since_tick: int | None = 0,
) -> dict[str, int]:
    """dealer -> how many more deals still count among our best three: three minus our deals this round
    (ticks ≥ `since_tick`; None: none yet, the round has not opened in the feed) and the ladder plan's still
    to come (from `start_hour`; a slot naming a card the trade plan buys never runs). Earlier rounds' deals
    do not close a slot: if the ladder restarts each round they are gone, and if it does not, a better deal
    replaces them (W5: unverified which), so a round's first three good deals always score."""
    skip = set(taken)
    ours: Counter[str] = Counter()
    for p in intel.tape(events):
        if since_tick is not None and p.tick >= since_tick and p.persona and us in (p.buyer, p.seller):
            ours[p.persona] += 1
    for slot in ladder:
        if slot.hour >= start_hour and not (slot.ref and slot.ref in skip):
            ours[slot.dealer] += 1
    return {str(d.get("id")): 3 - ours[str(d.get("id"))] for d in dealers if ours[str(d.get("id"))] < 3}


def day_of(hour: float) -> str:
    """The game day (`day.opened` payload's `day`) a game hour falls in."""
    return "fri" if hour < SATURDAY_OPENS else "sat" if hour < SUNDAY_OPENS else "sun"


def round_start_tick(events: Iterable[intel.Event], hour: float) -> int | None:
    """The tick the feed shows the current day opening at; None when that day has not opened in the feed yet
    (planning Saturday from Friday's feed)."""
    day = day_of(hour)
    ticks = [
        int(e.get("tick") or 0)
        for e in events
        if e.get("type") == "day.opened" and (e.get("payload") or {}).get("day") == day
    ]
    return max(ticks) if ticks else None


def best_three(slots: Sequence[LadderSlot], keep: int = 3) -> list[LadderSlot]:
    """The first `keep` planned deals per dealer: only a level's best three deals score (RULES.md), so the
    rest of a ladder plan buys cards, not ladder points."""
    count: dict[str, int] = defaultdict(int)
    out = []
    for slot in slots:
        if count[slot.dealer] < keep:
            out.append(slot)
            count[slot.dealer] += 1
    return out


def build_plan(
    me: dict[str, Any],
    catalog: dict[str, Any],
    events: Sequence[intel.Event],
    dealers: Iterable[dict[str, Any]],
    schedule: dict[str, Any],
    params: StrategyParams,
    rules: Guardrails,
    *,
    now_hours: float | None = None,
    ladder: Sequence[LadderSlot] = (),
    trades: Sequence[PlannedTrade] = (),
    venue_later: int = 9,
    what_if_floor: int | None = None,
    chasers: Mapping[str, Sequence[str]] | None = None,
    expected: Multipliers | None = None,
    venue_floor_rule: bool = True,
    what_if_caps: DealerCaps | None = None,
) -> PagePlan:
    """Pages, the buy order and the cash plan: the venue at the open, later, on Sunday or never (with W3's
    ladder slots and W4's trades as given), the no-venue plan with our planned sells, a consolidated plan
    (W3's best three only, then W4's trades, then pages), and the venue plans at a what-if cash floor."""
    dealers = list(dealers)
    m = market_for(me, catalog, events, dealers, chasers)
    pages = pages_of(m, events, params, rules, expected, what_if_caps)
    grants = grants_from(schedule, now_hours)
    body = schedule.get("body", schedule)
    hour_now = now_hours if now_hours is not None else float(body.get("now_hours") or 0)
    start = max(SATURDAY_OPENS, math.floor(hour_now))
    taken = {ref for t in trades for ref in t.refs_in}
    us = str(me.get("id") or "")
    since = round_start_tick(events, hour_now)
    scoring = scoring_dealers(events, us, dealers, ladder, start_hour=start, taken=taken, since_tick=since)
    wants = buy_list(pages, params.min_buy_surplus, skip=taken, scoring=scoring)
    cash = int(me.get("cash") or 0)
    sells = planned_sells(me, m, params, rules, start)

    def run(name: str, venue: int | None, **kw: Any) -> Scenario:
        kw = {"ladder": ladder, "trades": trades, "venue_floor_rule": venue_floor_rule, **kw}
        return cash_plan(name, cash, start, grants, wants, rules, venue_hour=venue, **kw)

    venues = [(f"venue at open (h{start})", start)]
    if start < venue_later < GAME_ENDS:  # a later hour that is still to come
        venues.append((f"venue at h{venue_later}", venue_later))
    if venue_later < SUNDAY_OPENS and start < SUNDAY_OPENS:
        venues.append((f"venue Sunday (h{SUNDAY_OPENS})", SUNDAY_OPENS))
    scenarios = [run(n, v) for n, v in venues] + [run("no venue", None)]
    if venue_floor_rule:  # the same opening without #71's floor check: the floor then blocks buys instead
        scenarios.append(run(f"venue at open (h{start}) · without #71's floor rule", start, venue_floor_rule=False))
    if sells:
        scenarios.append(run(f"venue at open (h{start}) + planned sells", start, sells=sells))
        scenarios.append(run("no venue + planned sells", None, sells=sells))
    if ladder:
        top3 = best_three([slot for slot in ladder if not (slot.ref and slot.ref in taken)])
        scenarios.append(run("no venue · ladder best three, then trades and pages", None, ladder=top3))
        if what_if_floor is not None:
            name = f"venue at open (h{start}) · what-if cash_floor {what_if_floor} · best three, trades, pages"
            scenarios.append(run(name, start, ladder=top3, floor=what_if_floor))
    if what_if_floor is not None:
        scenarios += [run(f"{n} · what-if cash_floor {what_if_floor}", v, floor=what_if_floor) for n, v in venues[:2]]
    notes = []
    if what_if_caps:
        caps = ", ".join(f"{d}:{r}={p}" for (d, r), p in what_if_caps.items())
        notes.append(f"what-if dealer caps {caps}: not in GUARDRAILS.md, so a buy at these prices is still refused")
    packs = unopened_packs(me)
    if packs:
        notes.append(f"open first ({', '.join(packs)}): free, and a pull changes what is missing")
    if taken:
        notes.append(f"bought by the trade plan, not again here: {', '.join(sorted(taken))}")
    if not grants:
        notes.append("no organiser grant left in the schedule")
    return PagePlan(me.get("tick"), cash, tuple(pages), tuple(wants), tuple(grants), tuple(scenarios), tuple(notes))


def plan_dict(plan: PagePlan) -> dict[str, Any]:
    def card(c: CardEconomics) -> dict[str, Any]:
        return {
            **asdict(c),
            "value_with_bonus": round(c.value_with_bonus, 2),
            "best": asdict(c.best) if c.best else None,
            "surplus": None if c.surplus is None else round(c.surplus, 2),
            "surplus_per_primas": None if c.surplus_per_primas is None else round(c.surplus_per_primas, 3),
        }

    return {
        "tick": plan.tick,
        "cash": plan.cash,
        "grants": [asdict(g) for g in plan.grants],
        "pages": [
            {
                "set": p.set_code,
                "affinity": p.affinity,
                "have": p.have,
                "of": p.of,
                "bonus": p.bonus,
                "cost": p.cost,
                "cost_if_unblocked": p.cost_if_unblocked,
                "value": round(p.value, 2),
                "surplus": None if p.surplus is None else round(p.surplus, 2),
                "verdict": p.verdict,
                "why": p.why,
                "missing": [card(c) for c in p.missing],
            }
            for p in plan.pages
        ],
        "buy_order": [
            {
                "ref": w.card.ref,
                "page": w.page,
                "source": w.source.source,
                "channel": w.source.channel,
                "price": w.source.price,
                "max_price": w.source.max_price,
                "sellers": list(w.source.sellers),
                "completes": w.completes,
                "trade_surplus": None if w.trade_surplus is None else round(w.trade_surplus, 1),
            }
            for w in plan.wants
        ],
        "scenarios": [
            {
                "name": s.name,
                "venue_hour": s.venue_hour,
                "venue_opened": s.venue_opened,
                "floor": s.floor,
                "bought": list(s.bought),
                "held": list(s.held),
                "ladder_deals": s.ladder_deals,
                "ladder_held": s.ladder_held,
                "ladder_duplicates": s.ladder_duplicates,
                "trade_surplus": round(s.trade_surplus, 1),
                "end_cash": round(s.end_cash, 1),
                "steps": [asdict(x) for x in s.steps],
            }
            for s in plan.scenarios
        ],
        "notes": list(plan.notes),
    }
