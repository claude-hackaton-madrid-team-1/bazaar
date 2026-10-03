"""The strategy engine: every strategy in STRATEGY.md as a pure function, ranked into one playbook.

Inputs are plain API payloads (`/api/me`, `/api/catalog`, `/api/dealers` personas) and feed events;
there is no network here, so every number is testable offline. Strategy proposes, guardrails dispose:
each move carries the exact CLI command, and `guarded()` shows what GUARDRAILS.md would say first.

Supply is finite: a card exists only once a pack or a dealer mints it (print runs 300/90/30/9/3), so
a card with zero minted copies is never a buy, only a pull or a wait.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bazaar_agent import intel
from bazaar_agent.config import REPO_ROOT
from bazaar_agent.guardrails import (
    Action,
    Context,
    Guardrails,
    GuardrailsError,
    RuleLine,
    action_kind,
    check,
    parse_md_config,
)
from bazaar_agent.guardrails import validated as validated_model
from bazaar_agent.ladder import FloorRow, conversations, floor_table, main_rows, plan_for

STRATEGY_FILE = REPO_ROOT / "STRATEGY.md"
BASIC_PACK = "sobre_barrio"  # the pack `pack_price_estimate` prices (STRATEGY.md)
TEAM_ID = re.compile(r"^t\d+$")

Availability = Literal["dealer", "teams", "packs", "none"]


class StrategyParams(BaseModel):
    """Every `` - `param` = value — why `` line of STRATEGY.md. All required: the file is the source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_bonus_weight: float = Field(ge=0)
    scarcity_weight: float = Field(ge=0)
    scarce_minted_max: int = Field(ge=0)
    min_buy_surplus: float = Field(ge=0)
    sell_need_share: float = Field(gt=0, le=1)
    sell_min_surplus: float = Field(ge=0)
    rare_fallback_price: int = Field(ge=1)
    pack_price_estimate: int = Field(ge=1)
    max_moves: int = Field(ge=1)
    ladder_floor_quantile: float = Field(default=0.0, ge=0, le=1)
    ladder_level_deals: int = Field(default=0, ge=0, le=8)


@dataclass(frozen=True)
class LoadedStrategy:
    params: StrategyParams
    lines: tuple[RuleLine, ...]
    path: Path


def parse_strategy(text: str, path: Path = STRATEGY_FILE) -> LoadedStrategy:
    lines, _, values = parse_md_config(text, path)
    return LoadedStrategy(validated_model(StrategyParams, values, path), lines, path)


def load_strategy(path: Path = STRATEGY_FILE) -> LoadedStrategy:
    if not path.is_file():
        raise GuardrailsError(f"{path} is missing: the strategy engine has no parameters")
    return parse_strategy(path.read_text(encoding="utf-8"), path)


# ---------------------------------------------------------------- the market, read once


@dataclass(frozen=True)
class Card:
    ref: str
    set_code: str
    rarity: str
    book: float
    page: bool
    minted: int
    print_run: int


@dataclass(frozen=True)
class Quote:
    dealer: str
    item: str  # a rarity ("common") or a pack id ("sobre_barrio")
    list_price: int
    sets: tuple[str, ...] | None  # None: every released set
    per_team_per_hour: int | None = None  # the dealer's quota for this item, when it has one


@dataclass(frozen=True)
class Market:
    us: str
    cash: int
    affinity: dict[str, float]
    held: dict[str, int]
    cards: dict[str, Card]
    released: tuple[str, ...]
    marginals: tuple[float, ...]
    page_bonus: float
    quotes: tuple[Quote, ...]
    newest_dealer: str | None
    packs: dict[str, tuple[dict[str, float], ...]]
    expected_book: dict[str, float]
    rarity_order: tuple[str, ...]  # cheapest first
    prints: tuple[intel.Print, ...]  # one-item settlements where a team paid
    holders: dict[str, tuple[str, ...]]
    chasers: dict[str, tuple[str, ...]]
    tick: int | None
    floors: dict[tuple[str, str], FloorRow] = field(default_factory=dict)  # (dealer, price class) → limits seen


def is_team(party: str | None) -> bool:
    return bool(party and TEAM_ID.match(party))


def _set_scope(sets: Any) -> tuple[str, ...] | None:
    """None for "released" (or unset); otherwise the set codes a dealer line covers."""
    if isinstance(sets, list):
        return tuple(str(x) for x in sets)
    return None if sets in (None, "released") else (str(sets),)


def dealer_quotes(dealers: Iterable[dict[str, Any]], unlocked: Iterable[str]) -> tuple[tuple[Quote, ...], str | None]:
    """What the dealers we can trade with sell, and the newest of them (for level_unlock)."""
    ours = set(unlocked)
    quotes: list[Quote] = []
    levels: dict[str, int] = {}
    for d in dealers:
        did = str(d.get("id"))
        if d.get("status") != "active" or did not in ours:
            continue
        levels[did] = int(d.get("level") or 0)
        for s in (d.get("menu") or {}).get("sells") or []:
            item = s.get("pack") or s.get("rarity")
            if item and s.get("list_price") is not None:
                quota = int(s["per_team_per_hour"]) if s.get("per_team_per_hour") else None
                quotes.append(Quote(did, str(item), int(s["list_price"]), _set_scope(s.get("sets")), quota))
    newest = max(levels, key=lambda k: (levels[k], k)) if levels else None
    return tuple(quotes), newest


def likely_holders(events: Iterable[intel.Event], us: str) -> dict[str, tuple[str, ...]]:
    """card ref -> teams that last received, were gifted or listed a copy (from the public feed)."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for e in events:
        kind, p = e.get("type"), e.get("payload") or {}
        if kind == "settlement":
            for item in p.get("items") or []:
                ref = str(item.get("ref"))
                counts[ref][str(item.get("to"))] += 1
                counts[ref][str(item.get("frm"))] -= 1
        elif kind == "gift.given":
            for ref in p.get("cards") or []:
                counts[str(ref)][str(p.get("team"))] += 1
        elif kind == "offer.listed" and e.get("actor"):
            for a in ((p.get("offer") or {}).get("give") or {}).get("assets") or []:
                c = counts[str(a.get("ref"))]
                c[str(e["actor"])] = max(c[str(e["actor"])], 1)
    return {
        ref: tuple(sorted(t for t, n in c.items() if n > 0 and is_team(t) and t != us)) for ref, c in counts.items()
    }


def build_market(
    me: dict[str, Any],
    catalog: dict[str, Any],
    events: Sequence[intel.Event],
    dealers: Iterable[dict[str, Any]],
    *,
    floors: bool = False,
) -> Market:
    """Everything a strategy reads, from plain payloads. `floors` also rebuilds the dealer floor table
    from every thread in `events` (only `ladder_floor_quantile` > 0 reads it)."""
    us = str(me.get("id") or "")
    cards = {
        str(c["id"]): Card(
            str(c["id"]),
            str(s.get("id")),
            str(c.get("rarity")),
            float(c.get("book") or 0),
            bool(c.get("page")),
            int(c.get("minted") or 0),
            int(c.get("print_run") or 0),
        )
        for s in catalog.get("sets") or []
        for c in s.get("cards") or []
    }
    held = Counter(str(a.get("ref")) for a in me.get("assets") or [] if a.get("kind") == "card")
    values = catalog.get("values") or {}
    rarities = catalog.get("rarities") or {}
    quotes, newest = dealer_quotes(dealers, me.get("unlocked") or [])
    chasers: dict[str, list[str]] = defaultdict(list)
    for f in intel.team_flows(events):
        if f.top_set and is_team(f.team) and f.team != us:
            chasers[f.top_set].append(f.team)
    prints = tuple(p for p in intel.tape(events) if p.items == 1 and is_team(p.buyer))
    return Market(
        us=us,
        cash=int(me.get("cash") or 0),
        affinity={str(k): float(v) for k, v in (me.get("affinity") or {}).items()},
        held=dict(held),
        cards=cards,
        released=tuple(str(p.get("set")) for p in (me.get("album") or {}).get("pages") or []),
        marginals=tuple(float(m) for m in values.get("copy_marginals") or [1.0]),
        page_bonus=float(values.get("page_bonus") or 0),
        quotes=quotes,
        newest_dealer=newest,
        packs={str(p["id"]): tuple(p.get("slots") or []) for p in catalog.get("packs") or []},
        expected_book={str(p["id"]): float(p.get("expected_book") or 0) for p in catalog.get("packs") or []},
        rarity_order=tuple(sorted(rarities, key=lambda r: float(rarities[r].get("book") or 0))),
        prints=prints,
        holders=likely_holders(events, us),
        chasers={k: tuple(sorted(v)) for k, v in chasers.items()},
        tick=me.get("tick"),
        floors=main_rows(floor_table(conversations(events))) if floors else {},
    )


# ---------------------------------------------------------------- supply and prices


@dataclass(frozen=True)
class Supply:
    ref: str
    rarity: str
    minted: int
    print_run: int
    ours: int
    scarce: bool
    availability: Availability


def quote_for(m: Market, card: Card) -> Quote | None:
    """The cheapest dealer quote that sells this card's rarity for its set."""
    fits = [q for q in m.quotes if q.item == card.rarity and (q.sets is None or card.set_code in q.sets)]
    return min(fits, key=lambda q: q.list_price) if fits else None


def pack_rarities(m: Market) -> set[str]:
    sold = {q.item for q in m.quotes if q.item in m.packs}
    return {r for p in sold for slot in m.packs[p] for r, odds in slot.items() if odds > 0}


def supply_of(m: Market, card: Card, params: StrategyParams) -> Supply:
    ours = m.held.get(card.ref, 0)
    mintable = card.minted < card.print_run
    where: Availability
    if card.minted == 0:
        where = "packs" if mintable and card.rarity in pack_rarities(m) else "none"
    elif card.set_code in m.released and quote_for(m, card) is not None and mintable:
        where = "dealer"
    elif card.minted > ours:
        where = "teams"
    else:
        where = "none"
    return Supply(
        card.ref, card.rarity, card.minted, card.print_run, ours, card.minted <= params.scarce_minted_max, where
    )


def supply_view(m: Market, params: StrategyParams) -> list[Supply]:
    return [supply_of(m, c, params) for c in m.cards.values() if c.set_code in m.released]


@dataclass(frozen=True)
class Estimate:
    price: float
    basis: str


def estimate_price(
    ref: str,
    rarity: str,
    prints: Sequence[intel.Print],
    rarity_of: dict[str, str],
    list_price: int | None,
    fallback: float,
) -> Estimate:
    """Median tape price for the ref, else for its rarity, else the dealer list price, else the fallback."""
    own = [p.price for p in prints if p.ref == ref]
    if own:
        return Estimate(float(median(own)), f"tape {ref} ×{len(own)}")
    same = [p.price for p in prints if rarity_of.get(p.ref) == rarity]
    if same:
        return Estimate(float(median(same)), f"tape {rarity} ×{len(same)}")
    if list_price is not None:
        return Estimate(float(list_price), "dealer list")
    return Estimate(fallback, "fallback")


def _rarity_of(m: Market) -> dict[str, str]:
    return {ref: c.rarity for ref, c in m.cards.items()}


def dealer_fills(m: Market, dealer: str) -> list[intel.Print]:
    return [p for p in m.prints if p.persona == dealer and p.seller == dealer]


def peer_prints(m: Market) -> list[intel.Print]:
    return [p for p in m.prints if p.persona is None and is_team(p.seller)]


def card_estimate(m: Market, card: Card, params: StrategyParams) -> Estimate:
    """What a team pays for this card (dealer sales and team trades): the tape floor of a sell ask."""
    quote = quote_for(m, card)
    fallback = params.rare_fallback_price if card.rarity == "rare" else card.book
    return estimate_price(card.ref, card.rarity, m.prints, _rarity_of(m), quote.list_price if quote else None, fallback)


# ---------------------------------------------------------------- moves


@dataclass(frozen=True)
class Move:
    side: Literal["buy", "sell", "pack"]
    strategy: str
    ref: str
    rarity: str
    value: float  # buys: worth to us (+ page bonus share); sells: our your_value
    price: float  # buys: expected price; sells: our ask
    surplus: float
    urgency: float
    score: float
    source: str  # dealer id, "teams" or "rastro"
    counterparties: tuple[str, ...]
    action: str  # guardrails action kind: buy | bid | sell
    limit: int  # the price in the command: dealer max, bid or ask
    reason: str
    command: str
    guardrail: str = "-"
    asset_id: int | None = None  # sells: the exact copy listed
    jev: str = "-"  # packs: Jev's verdict and probability on spending a slot
    ladder: tuple[int, int, int] | None = None  # dealer buys and packs: (start, max, step) of the bid ladder


def urgency_of(card: Card, chasers: int, params: StrategyParams) -> float:
    """Mean of scarcity (1 at or below scarce_minted_max copies) and competitor demand."""
    scarcity = min(1.0, params.scarce_minted_max / card.minted) if card.minted else 1.0
    demand = chasers / (chasers + 1)
    return round((scarcity + demand) / 2, 3)


def score_of(surplus: float, urgency: float, params: StrategyParams) -> float:
    return round(surplus * (1 + params.scarcity_weight * urgency), 2)


def copy_value(m: Market, card: Card, copies_held: int) -> float:
    marginal = m.marginals[copies_held] if copies_held < len(m.marginals) else 0.0
    return card.book * m.affinity.get(card.set_code, 1.0) * marginal


def page_cards(m: Market, set_code: str) -> list[Card]:
    return [c for c in m.cards.values() if c.set_code == set_code and c.page]


def page_bonus_of(m: Market, set_code: str) -> float:
    """The page bonus: 25 % of the page's value to us (book × affinity of every page card)."""
    return m.page_bonus * sum(c.book for c in page_cards(m, set_code)) * m.affinity.get(set_code, 1.0)


def bonus_shares(m: Market, set_code: str) -> dict[str, float]:
    """Each missing page card's share (by book) of the page bonus."""
    missing = [c for c in page_cards(m, set_code) if m.held.get(c.ref, 0) == 0]
    missing_book = sum(c.book for c in missing)
    if not missing_book:
        return {}
    bonus = page_bonus_of(m, set_code)
    return {c.ref: bonus * c.book / missing_book for c in missing}


def bonus_at_stake(m: Market, card: Card, params: StrategyParams) -> float:
    """Page bonus we give up by selling our only copy of a page card: all of it when the page is
    complete, else the share (× page_bonus_weight) the card would carry once missing again."""
    if not card.page or m.held.get(card.ref, 0) != 1:
        return 0.0
    missing_book = sum(c.book for c in page_cards(m, card.set_code) if m.held.get(c.ref, 0) == 0)
    if not missing_book:
        return page_bonus_of(m, card.set_code)
    return params.page_bonus_weight * page_bonus_of(m, card.set_code) * card.book / (missing_book + card.book)


def bid_range(
    fills: Sequence[float],
    estimate: float,
    value: float,
    cap: int | None,
    min_surplus: float,
    opening_ratio: float | None = None,
) -> tuple[int, int] | None:
    """(start, max) for a dealer ladder: open at the lowest proven fill, never above what anyone paid,
    our value minus the minimum surplus, or the guardrail cap. None when nothing fits.
    With no fills yet (a new dealer), open at `opening_ratio` × the estimate: the deepest discount any
    dealer has given off its list price."""
    top = min(math.floor(value - min_surplus), math.ceil(max(fills)) if fills else math.ceil(estimate))
    if cap is not None:
        top = min(top, cap)
    if top < 1:
        return None
    start = math.floor(min(fills)) if fills else math.floor(estimate * (opening_ratio or 1.0))
    return max(1, min(start, top)), top


def floor_range(row: FloorRow, value: float, cap: int | None, min_surplus: float, q: float) -> tuple[int, int] | None:
    """(start, max) from the floor table (`bazaar ladder floors`): open 2 under the q-quantile of the
    limits every team's conversations closed at, stop 2 over it, never above the cap or our value minus
    the minimum surplus. None when that leaves no room (the caller keeps the lowest-fill ladder then)."""
    choice = plan_for(row, cap, q=q)
    if choice.plan is None:
        return None
    top = min(choice.plan.max_price, math.floor(value - min_surplus))
    return (choice.plan.start, top) if top >= choice.plan.start else None


def opening_ratio(m: Market) -> float | None:
    """The lowest fill / list price any dealer has accepted (e.g. Abuela: a 26 P pack at 17 → 0.65)."""
    rarity_of = _rarity_of(m)
    ratios = []
    for p in m.prints:
        item = p.ref if p.ref in m.packs else rarity_of.get(p.ref)
        quote = next((q for q in m.quotes if q.dealer == p.persona and q.item == item), None)
        if p.persona is not None and p.seller == p.persona and quote is not None and quote.list_price > 0:
            ratios.append(p.price / quote.list_price)
    return min(ratios) if ratios else None


def ladder_step(start: int, top: int, max_ticks: int) -> int:
    """The smallest raise that still reaches `top` before the thread times out (one bid per tick)."""
    return max(1, math.ceil((top - start) / max(1, max_ticks - 1)))


def dealer_command(item: str, dealer: str, start: int, top: int, step: int = 1) -> str:
    raise_by = f" --step {step}" if step > 1 else ""
    return f"uv run bazaar dealer buy {item} --start {start} --max {top}{raise_by} --dealer {dealer}"


def bid_command(ref: str, price: int) -> str:
    return f"uv run bazaar sell bid {ref} --price {price}"


def list_command(asset_id: int, price: int) -> str:
    return f"uv run bazaar sell list {asset_id} --price {price}"


def _strategies(*names: str | None) -> str:
    return "+".join(n for n in names if n)


@dataclass(frozen=True)
class BuyCase:
    """What every buy move shares: the card, its worth to us and why it is urgent."""

    card: Card
    supply: Supply
    value: float
    urgency: float
    worth: str  # how the value was computed
    supply_note: str
    chasers: tuple[str, ...]


def buy_case(m: Market, card: Card, params: StrategyParams) -> BuyCase:
    aff = m.affinity.get(card.set_code, 1.0)
    share = bonus_shares(m, card.set_code).get(card.ref, 0.0) * params.page_bonus_weight
    value = card.book * aff + share
    chasers = m.chasers.get(card.set_code, ())
    note = f"{card.minted}/{card.print_run} minted" + (", chased by " + ", ".join(chasers) if chasers else "")
    return BuyCase(
        card,
        supply_of(m, card, params),
        value,
        urgency_of(card, len(chasers), params),
        f"{card.book:g}×{aff:g} + bonus share {share:.1f} = {value:.1f}",
        note,
        chasers,
    )


def dealer_buy(m: Market, case: BuyCase, quote: Quote, params: StrategyParams, rules: Guardrails) -> Move | str:
    """dealer_floor: a plentiful card from a dealer, laddered from the lowest fill anyone got."""
    card, rarity_of = case.card, _rarity_of(m)
    fills = dealer_fills(m, quote.dealer)
    est = estimate_price(card.ref, card.rarity, fills, rarity_of, quote.list_price, card.book)
    same = [float(p.price) for p in fills if rarity_of.get(p.ref) == card.rarity]
    cap = rules.max_price_for(card.rarity, quote.dealer)
    plan = bid_range(same, est.price, case.value, cap, params.min_buy_surplus, opening_ratio(m))
    row = m.floors.get((quote.dealer, f"card:{card.rarity}")) if params.ladder_floor_quantile > 0 else None
    floored = floor_range(row, case.value, cap, params.min_buy_surplus, params.ladder_floor_quantile) if row else None
    if floored is not None and floored[1] >= est.price:  # never drop a buy today's ladder would make
        plan = floored
    if plan is None or case.value - est.price < params.min_buy_surplus:
        return f"{card.ref}: worth {case.value:.1f}, {quote.dealer} fills ~{est.price:g} — surplus too small"
    if plan[1] < est.price:
        return f"{card.ref}: {quote.dealer} fills ~{est.price:g}, our max is {plan[1]} — cap below market"
    level = "level_unlock" if quote.dealer == m.newest_dealer else None
    scarce = "scarcity_first" if case.supply.scarce else None
    return Move(
        "buy",
        _strategies("complete_pages", scarce, "dealer_floor", level),
        card.ref,
        card.rarity,
        round(case.value, 1),
        est.price,
        round(case.value - est.price, 1),
        case.urgency,
        score_of(case.value - est.price, case.urgency, params),
        quote.dealer,
        (quote.dealer,),
        "buy",
        plan[1],
        f"worth {case.worth}; {quote.dealer} fills {est.basis} → {est.price:g}; ladder {plan[0]}→{plan[1]}; "
        f"{case.supply_note}",
        dealer_command(card.ref, quote.dealer, *plan, ladder_step(*plan, rules.dealer_max_ticks_per_thread)),
        ladder=(*plan, ladder_step(*plan, rules.dealer_max_ticks_per_thread)),
    )


def team_buy(m: Market, case: BuyCase, params: StrategyParams, rules: Guardrails) -> Move | str:
    """A card only teams hold (rares): a public bid at the tape price, naming the likely holders."""
    card = case.card
    fallback = params.rare_fallback_price if card.rarity == "rare" else card.book
    est = estimate_price(card.ref, card.rarity, peer_prints(m), _rarity_of(m), None, fallback)
    cap = rules.max_price_for(card.rarity)
    if cap is None or round(est.price) > cap:
        return f"{card.ref}: teams pay ~{est.price:g}, max_price_{card.rarity} is {cap} — cap below market"
    bid = round(est.price)
    if case.value - bid < params.min_buy_surplus:
        return f"{card.ref}: worth {case.value:.1f}, teams pay ~{est.price:g} — surplus too small"
    holders = m.holders.get(card.ref, ())
    rivals = [h for h in holders if h in case.chasers]
    who = ", ".join(holders) if holders else "holder unknown (public bid)"
    who += f"; {', '.join(rivals)} chase {card.set_code} themselves" if rivals else ""
    return Move(
        "buy",
        _strategies("complete_pages", "scarcity_first" if case.supply.scarce else None),
        card.ref,
        card.rarity,
        round(case.value, 1),
        float(bid),
        round(case.value - bid, 1),
        case.urgency,
        score_of(case.value - bid, case.urgency, params),
        "teams",
        holders,
        "bid",
        bid,
        f"worth {case.worth}; teams pay {est.basis} → {est.price:g}; holders: {who}; {case.supply_note}",
        bid_command(card.ref, bid),
    )


def buy_move(m: Market, card: Card, params: StrategyParams, rules: Guardrails) -> Move | str:
    """complete_pages + scarcity_first for one missing page card. A str says why it is not a buy."""
    case = buy_case(m, card, params)
    if case.supply.availability in ("none", "packs"):
        return f"{card.ref}: {card.minted} minted, not buyable ({case.supply.availability}) — pull or wait"
    quote = quote_for(m, card) if case.supply.availability == "dealer" else None
    level = level_quote(m, card, params) if quote else None
    if level is not None and level != quote and isinstance(moved := dealer_buy(m, case, level, params, rules), Move):
        return moved  # the newest dealer's ladder still needs deals, and its plan fits our caps and value
    return dealer_buy(m, case, quote, params, rules) if quote else team_buy(m, case, params, rules)


def level_quote(m: Market, card: Card, params: StrategyParams) -> Quote | None:
    """level_unlock: the newest dealer's quote for this card while we have closed fewer than
    `ladder_level_deals` deals with it (the ladder counts each level's best three deals, and deals with
    the newest dealer unlock the next level early). None when it is off (0), done, or the dealer does
    not sell this card. Deals are counted over the whole feed we hold, not per day."""
    if params.ladder_level_deals <= 0 or m.newest_dealer is None:
        return None
    ours = sum(1 for p in m.prints if p.persona == m.newest_dealer and p.buyer == m.us)
    if ours >= params.ladder_level_deals:
        return None
    fits = [
        q
        for q in m.quotes
        if q.dealer == m.newest_dealer and q.item == card.rarity and (q.sets is None or card.set_code in q.sets)
    ]
    return min(fits, key=lambda q: q.list_price) if fits else None


def buy_moves(m: Market, params: StrategyParams, rules: Guardrails) -> tuple[list[Move], list[str]]:
    moves: list[Move] = []
    skipped: list[str] = []
    for card in m.cards.values():
        if card.set_code not in m.released or not card.page or m.held.get(card.ref, 0) > 0:
            continue
        result = buy_move(m, card, params, rules)
        if isinstance(result, Move):
            moves.append(result)
        else:
            skipped.append(result)
    return moves, skipped


def _priced(asset: dict[str, Any]) -> bool:
    """A card copy we can price: no `your_value` means no floor, so it is never offered (fail closed)."""
    value = asset.get("your_value")
    return asset.get("kind") == "card" and isinstance(asset.get("id"), int) and isinstance(value, int | float)


def sell_moves(m: Market, assets: Iterable[dict[str, Any]], params: StrategyParams, rules: Guardrails) -> list[Move]:
    """sell_to_need: one copy per card we hold, to the teams that chase its set, never below what we lose
    (our your_value plus any page bonus that selling our only copy gives up)."""
    copies: dict[str, dict[str, Any]] = {}
    for a in filter(_priced, assets):
        ref = str(a.get("ref"))
        if ref not in copies or float(a["your_value"]) <= float(copies[ref]["your_value"]):
            copies[ref] = a
    chaser_aff = max(m.affinity.values(), default=1.0)  # every team has exactly one top-affinity set
    moves = []
    for ref, asset in copies.items():
        card = m.cards.get(ref)
        buyers = m.chasers.get(card.set_code, ()) if card else ()
        if card is None or not buyers:
            continue
        stake = bonus_at_stake(m, card, params)
        ours = float(asset["your_value"]) + stake
        need = params.sell_need_share * card.book * chaser_aff
        tape = card_estimate(m, card, params)
        ask = math.ceil(max(ours * rules.sell_min_value_ratio, need, tape.price))
        if ask - ours < params.sell_min_surplus:
            continue
        urgency = urgency_of(card, len(buyers), params)
        dup = ", duplicate" if m.held.get(ref, 0) > 1 else ""
        bonus = f" + page bonus {stake:.1f}" if stake else ""
        moves.append(
            Move(
                "sell",
                "sell_to_need",
                ref,
                card.rarity,
                round(ours, 1),
                float(ask),
                round(ask - ours, 1),
                urgency,
                score_of(ask - ours, urgency, params),
                "rastro",
                buyers,
                "sell",
                ask,
                f"ours {float(asset['your_value']):g}{bonus} ({card.set_code} ×{m.affinity.get(card.set_code, 1.0):g}"
                f"{dup}); {', '.join(buyers)} chase {card.set_code}: "
                f"{params.sell_need_share:g}×{card.book:g}×{chaser_aff:g} = {need:.0f}; {tape.basis} {tape.price:g}",
                list_command(int(asset["id"]), ask),
                asset_id=int(asset["id"]),
            )
        )
    return moves


def rank(moves: Iterable[Move], m: Market, params: StrategyParams) -> list[Move]:
    """Highest score first; ties go to duplicates, then to low-affinity sets."""

    def key(mv: Move) -> tuple[float, int, float]:
        card = m.cards.get(mv.ref)
        aff = m.affinity.get(card.set_code, 1.0) if card else 1.0
        return (-mv.score, 0 if m.held.get(mv.ref, 0) > 1 else 1, aff)

    return sorted(moves, key=key)[: params.max_moves]


# ---------------------------------------------------------------- packs


def rarity_value(m: Market, rarity: str) -> float:
    """Mean value to us of one more copy of a released card of this rarity (copy marginals applied).
    When every such card is printed out, packs give the next rarity down."""
    order = list(m.rarity_order)
    while True:
        pool = [
            c for c in m.cards.values() if c.set_code in m.released and c.rarity == rarity and c.minted < c.print_run
        ]
        if pool:
            return sum(copy_value(m, c, m.held.get(c.ref, 0)) for c in pool) / len(pool)
        if rarity not in order or order.index(rarity) == 0:
            return 0.0
        rarity = order[order.index(rarity) - 1]


def pack_moves(m: Market, params: StrategyParams, rules: Guardrails) -> list[Move]:
    """pack_value: expected value to us of each pack vs its learned price; a command when a dealer sells it."""
    moves = []
    for pack, slots in m.packs.items():
        means = {r: rarity_value(m, r) for slot in slots for r in slot}
        ev = sum(odds * means[r] for slot in slots for r, odds in slot.items())
        quote = next((q for q in sorted(m.quotes, key=lambda q: q.list_price) if q.item == pack), None)
        fills = [float(p.price) for p in dealer_fills(m, quote.dealer) if p.ref == pack] if quote else []
        if pack == BASIC_PACK:
            est = Estimate(float(params.pack_price_estimate), "pack_price_estimate")
        elif quote is not None:
            est = estimate_price(pack, "pack", dealer_fills(m, quote.dealer), {}, quote.list_price, quote.list_price)
        else:
            est = Estimate(m.expected_book.get(pack, 0.0), "expected book (no seller)")
        cap = rules.max_price_for("pack", quote.dealer if quote else None)
        cap_rule = (
            "max_price_pack" if cap == rules.max_price_pack else f"dealer_price_caps {quote and quote.dealer}:pack"
        )
        plan = bid_range(fills, est.price, ev, cap, params.min_buy_surplus, opening_ratio(m))
        capped = plan is not None and plan[1] < est.price
        actionable = quote is not None and plan is not None and not capped and ev - est.price >= params.min_buy_surplus
        slot_text = " + ".join("/".join(f"{odds:g} {r} {means[r]:.1f}" for r, odds in slot.items()) for slot in slots)
        moves.append(
            Move(
                "pack",
                "pack_value",
                pack,
                "pack",
                round(ev, 1),
                est.price,
                round(ev - est.price, 1),
                0.0,
                score_of(ev - est.price, 0.0, params),
                quote.dealer if quote else "none",
                (quote.dealer,) if quote else (),
                "buy",
                plan[1] if plan else 0,
                f"EV {ev:.1f} = {slot_text}; price {est.basis} {est.price:g}"
                + ("" if quote else "; no dealer we can reach sells it")
                + (f"; {cap_rule} caps us at {plan[1]}, below the price" if quote and plan and capped else ""),
                (
                    dealer_command(pack, quote.dealer, *plan, ladder_step(*plan, rules.dealer_max_ticks_per_thread))
                    if actionable and quote and plan
                    else ""
                ),
                ladder=(*plan, ladder_step(*plan, rules.dealer_max_ticks_per_thread)) if actionable and plan else None,
            )
        )
    return sorted(moves, key=lambda mv: (not mv.command, -mv.surplus))[: params.max_moves]  # actionable first


# ---------------------------------------------------------------- the playbook


@dataclass(frozen=True)
class PackSlots:
    """Packs bought this game hour (every pack id, from the shared ledger) against max_packs_per_game_hour."""

    used: int
    limit: int

    @property
    def left(self) -> int:
        return max(0, self.limit - self.used)


@dataclass(frozen=True)
class Playbook:
    tick: int | None
    cash: int
    supply: tuple[Supply, ...]
    buys: tuple[Move, ...]
    sells: tuple[Move, ...]
    packs: tuple[Move, ...]
    skipped: tuple[str, ...]
    pack_quotas: dict[str, int]  # pack id -> the selling dealer's per_team_per_hour (from /api/dealers)
    pack_slots: PackSlots | None = None  # set by gate_packs


def build_playbook(
    me: dict[str, Any],
    catalog: dict[str, Any],
    events: Sequence[intel.Event],
    dealers: Iterable[dict[str, Any]],
    params: StrategyParams,
    rules: Guardrails,
) -> Playbook:
    m = build_market(me, catalog, events, dealers, floors=params.ladder_floor_quantile > 0)
    buys, skipped = buy_moves(m, params, rules)
    quotas: dict[str, int] = {}
    for q in m.quotes:
        if q.item in m.packs and q.per_team_per_hour is not None:
            quotas[q.item] = min(quotas.get(q.item, q.per_team_per_hour), q.per_team_per_hour)
    return Playbook(
        tick=m.tick,
        cash=m.cash,
        supply=tuple(supply_view(m, params)),
        buys=tuple(rank(buys, m, params)),
        sells=tuple(rank(sell_moves(m, me.get("assets") or [], params, rules), m, params)),
        packs=tuple(pack_moves(m, params, rules)),
        skipped=tuple(skipped),
        pack_quotas=quotas,
    )


def guarded(book: Playbook, ctx: Context, rules: Guardrails, listed: frozenset[int] = frozenset()) -> Playbook:
    """Every move with the verdict GUARDRAILS.md would give it now (strategy proposes, guardrails dispose).
    `listed` holds assets already in our open offers: listing one again is refused."""

    def verdict(mv: Move) -> Move:
        if not mv.command:
            return mv
        if mv.asset_id is not None and mv.asset_id in listed:
            return replace(mv, guardrail=f"denied: asset {mv.asset_id} is already in one of our open offers")
        your_value = mv.value if mv.side == "sell" else None
        dealer = None if is_team(mv.source) or mv.source in ("teams", "rastro") else mv.source  # dealer_price_caps
        action = Action(action_kind(mv.action), mv.ref, mv.rarity, mv.limit, your_value, dealer=dealer)
        return replace(mv, guardrail=str(check(action, ctx, rules)))

    return replace(
        book,
        buys=tuple(map(verdict, book.buys)),
        sells=tuple(map(verdict, book.sells)),
        packs=tuple(map(verdict, book.packs)),
    )


def playbook_dict(book: Playbook, loaded: LoadedStrategy) -> dict[str, Any]:
    slots = None if book.pack_slots is None else {**asdict(book.pack_slots), "left": book.pack_slots.left}
    return {**asdict(book), "pack_slots": slots, "params": loaded.params.model_dump(), "source": loaded.path.name}
