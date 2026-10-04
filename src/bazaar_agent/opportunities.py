"""The opportunity scanner: every standing offer on the boards, scored by what accepting it gains us.

Read-only. For each plain offer another team made (one card for cash, or cash for one card):
  - an ASK (we would buy): a page card we miss, worth `buy_case` to us (book × our affinity + its share of the
    page bonus), against its price plus the fee the accepting side pays. Cards we already hold are W8's
    (`arb.dup_candidates`) and the guardrails refuse them anyway (`block_buying_held_cards`);
  - a BID (we would sell): a card we hold, against what selling our least valuable copy costs us
    (`your_value` plus the page bonus it carries), the fee coming out of the price.
Each opportunity also carries the maker's side of the deal at its expected value from the rival affinity
map (`theirs`): a `snipe` is an ask below the maker's own value, an `overbid` a bid above the tape. Every one
is checked through `guardrails.check()` as the taker would send it (the accept quota, the cash floor, the
spend cap, the counterparty share), and one that would use a copy or a card the dry-run trade plan
(`trade_desk`) already counts on says so. `score_offer` is the one function W8's `arb scan` imports.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from bazaar_agent.affinity import AffinityMap
from bazaar_agent.agents.market import BoardOffer, Venue
from bazaar_agent.guardrails import Action, Context, Guardrails, check
from bazaar_agent.rivals import Listed, market_price, own_value, tape_reference
from bazaar_agent.strategy import Market, StrategyParams, bonus_at_stake, buy_case, page_cards
from bazaar_agent.trade_desk import Trade, holdings, items_used, team_copies

Kind = Literal["buy", "sell"]


@dataclass(frozen=True)
class Opportunity:
    offer_id: int
    venue: str
    kind: Kind  # buy: we accept their ask; sell: we accept their bid
    ref: str
    rarity: str
    price: int  # the offer's price
    fee: int  # ours: the accepting side pays it
    maker: str  # the team id (resolved from the feed), else the board pseudonym
    ours: float  # what accepting gains us at our values
    theirs: float | None  # the maker's expected gain at its value (None: unknown team)
    tag: str  # "snipe", "overbid" or ""
    verdict: str  # guardrails.check, as the taker would send the accept
    plan: str  # "" or the planned trade it would take a copy or a card from
    expires_tick: int | None
    reason: str

    @property
    def allowed(self) -> bool:
        return self.verdict == "allowed"


def missing_on_page(m: Market, set_code: str) -> int:
    """Page cards of `set_code` we hold no copy of."""
    return sum(1 for c in page_cards(m, set_code) if m.held.get(c.ref, 0) == 0)


def score_offer(
    o: BoardOffer,
    m: Market,
    me: dict[str, Any],
    params: StrategyParams,
    rules: Guardrails,
    amap: AffinityMap,
    venue: Venue | None,
    ctx: Context,
    tape: tuple[dict[str, float], dict[str, float]] = ({}, {}),
    copies: int = 0,
    asset_id: int | None = None,
    unavailable: frozenset[int] = frozenset(),
    page_horizon: int | None = None,
    keep_sets: frozenset[str] = frozenset(),
) -> Opportunity | None:
    """One standing offer scored for us, or None when it is not ours to take: an ask for a card we hold, a
    card off our pages or of a set not released (what the taker never buys), a bid for a card we do not
    hold (or hold no FREE copy of), an unknown card or venue. `copies`: how many of the card its maker is known
    to hold. `asset_id`: the copy we would hand over into a bid (default: the one we lose least by).
    `unavailable`: our copies already promised (in our open offers, sold and not settled yet); a sell is priced
    from the free copies only, so the last free copy carries the page bonus even when its twin is in an ask.
    `page_horizon` (card hunt): a sale from a page that misses more than this many cards puts no page bonus at
    stake (the page cannot complete before the freeze): it is priced at the copy's `your_value` alone, unless its
    set is in `keep_sets` (a page we are still completing)."""
    card = m.cards.get(o.ref)
    if card is None or venue is None:  # an unknown venue has an unknown fee: not priced blind
        return None
    fee = venue.fee(o.price)
    market = market_price(o.ref, card.rarity, *tape)
    their_value = None
    if o.maker in amap.teams:  # an ask's maker parts with its last copy; a bid's maker gets one more
        held = max(copies, 1) if o.side == "ask" else copies + 1
        their_value = own_value(amap, o.maker, o.ref, card.book, held, m.marginals)
    if o.side == "ask":
        if m.held.get(o.ref, 0) > 0 or not card.page or card.set_code not in m.released:
            return None
        worth = buy_case(m, card, params).value
        ours = worth - o.price - fee
        theirs = None if their_value is None else o.price - their_value
        tag = "snipe" if their_value is not None and o.price < their_value else ""
        action = Action("accept_buy", o.ref, card.rarity, o.price + fee, counterparty=o.maker, volume=o.price)
        reason = f"worth {worth:.1f} to us; ask {o.price} + fee {fee}"
    else:
        assets = me.get("assets") or []
        free = [
            a
            for a in assets
            if a.get("ref") == o.ref and isinstance(a.get("your_value"), int | float) and a.get("id") not in unavailable
        ]
        mine = [a for a in free if a.get("id") == asset_id] if asset_id is not None else free
        if not mine:
            return None
        value = min(float(a["your_value"]) for a in mine)
        as_held = m if m.held.get(o.ref, 0) == len(free) else replace(m, held={**m.held, o.ref: len(free)})
        stake = bonus_at_stake(as_held, card, params)
        if (
            page_horizon is not None
            and card.set_code not in keep_sets
            and missing_on_page(as_held, card.set_code) > page_horizon
        ):
            stake = 0.0
        loss = value + stake
        ours = o.price - fee - loss
        theirs = None if their_value is None else their_value - o.price
        tag = "overbid" if market is not None and o.price > market else ""
        # The sell floor sees what we net: the accepting side pays the fee out of the bid.
        action = Action(
            "accept_sell", o.ref, card.rarity, o.price - fee, your_value=value, counterparty=o.maker, volume=o.price
        )
        reason = f"selling loses us {loss:.1f}; bid {o.price} - fee {fee}"
    if market is not None:
        reason += f"; tape {market:g}"
    if their_value is not None:
        reason += f"; {o.maker} values it ~{their_value:.1f}"
    verdict = check(action, replace(ctx, ranking=True), rules)  # ranking: the accept's own check reads the value
    return Opportunity(
        o.id,
        o.venue,
        "buy" if o.side == "ask" else "sell",
        o.ref,
        card.rarity,
        o.price,
        fee,
        o.maker,
        round(ours, 2),
        None if theirs is None else round(theirs, 2),
        tag,
        str(verdict),
        "",
        o.expires_tick,
        reason,
    )


def offers_from(rows: Iterable[Listed], us: str) -> list[BoardOffer]:
    """Feed-rebuilt offers (`rivals.board_at`) as board rows, makers already named; never ours, never
    addressed to another team."""
    return [
        BoardOffer(r.id, r.venue, r.maker, r.side, r.ref, r.price, r.asset_id, None, r.expires_tick, r.tick)
        for r in rows
        if r.maker != us and r.to in (None, us)
    ]


def against_plan(op: Opportunity, plan: Sequence[Trade], me: dict[str, Any]) -> str:
    """The planned trade this accept would compete with: a sell of a copy the plan gives away, a buy of a
    card the plan already buys. "" when none."""
    for t in plan:
        used = items_used(t)
        if op.kind == "buy" and f"want:{op.ref}" in used:
            return f"{t.kind} {'/'.join(t.refs)} with {t.counterparty}"
        if op.kind == "sell":
            ids = {a.get("id") for a in me.get("assets") or [] if a.get("ref") == op.ref}
            if any(f"asset:{i}" in used for i in ids):
                return f"{t.kind} {'/'.join(t.refs)} with {t.counterparty}"
    return ""


def scan(
    offers: Iterable[BoardOffer],
    m: Market,
    me: dict[str, Any],
    params: StrategyParams,
    rules: Guardrails,
    amap: AffinityMap,
    venues: dict[str, Venue],
    ctx: Context,
    events: Sequence[dict[str, Any]] = (),
    catalog: dict[str, Any] | None = None,
    plan: Sequence[Trade] = (),
    min_surplus: float = 0.0,
    unavailable: frozenset[int] = frozenset(),
) -> list[Opportunity]:
    """Every offer worth more than `min_surplus` to us, best first (allowed ones before refused ones).
    `unavailable`: our copies already in our open offers (see `score_offer`)."""
    rarity_of = {ref: c.rarity for ref, c in m.cards.items()}
    tape = tape_reference(events, rarity_of)
    held = team_copies(holdings(events), m.us)
    out = []
    for o in offers:
        if o.maker == m.us:
            continue
        copies = held.get(o.maker, Counter())[o.ref]
        op = score_offer(o, m, me, params, rules, amap, venues.get(o.venue), ctx, tape, copies, None, unavailable)
        if op is None or op.ours <= min_surplus:
            continue
        out.append(replace(op, plan=against_plan(op, plan, me)))
    return sorted(out, key=lambda op: (not op.allowed, -op.ours, op.offer_id))


# ---------------------------------------------------------------- the replay: what Friday's boards offered us


@dataclass(frozen=True)
class Replay:
    """Every offer the boards showed that day, scored once with our album as it is now (an approximation:
    our values moved little during the day)."""

    offers: int  # plain offers by other teams
    worth_it: tuple[tuple[Listed, Opportunity], ...]  # with surplus for us and allowed by the guardrails
    taken_by_others: int
    left_open: int  # expired or cancelled untaken, or still open: what a scanner would have caught
    latency: tuple[int, ...]  # ticks from listing to another team taking one of them
    takers: dict[str, int]

    @property
    def surplus(self) -> float:
        return round(sum(op.ours for _, op in self.worth_it), 1)

    @property
    def left_surplus(self) -> float:
        return round(sum(op.ours for r, op in self.worth_it if not (r.outcome == "filled" and r.exact)), 1)


def replay(
    rows: Sequence[Listed],
    m: Market,
    me: dict[str, Any],
    params: StrategyParams,
    rules: Guardrails,
    amap: AffinityMap,
    venues: dict[str, Venue],
    ctx: Context,
    events: Sequence[dict[str, Any]],
    min_surplus: float = 0.0,
) -> Replay:
    rarity_of = {ref: c.rarity for ref, c in m.cards.items()}
    tape = tape_reference(events, rarity_of)
    held = team_copies(holdings(events), m.us)
    worth: list[tuple[Listed, Opportunity]] = []
    offers = 0
    by_id = {r.id: r for r in rows}
    for o in offers_from(rows, m.us):
        offers += 1
        copies = held.get(o.maker, Counter())[o.ref]
        op = score_offer(o, m, me, params, rules, amap, venues.get(o.venue), ctx, tape, copies)
        if op is not None and op.ours > min_surplus and op.allowed:
            worth.append((by_id[o.id], op))
    taken = [(r, op) for r, op in worth if r.outcome == "filled" and r.exact]  # a card-only match: unknown
    takers: dict[str, int] = {}
    for r, _ in taken:
        takers[str(r.taker)] = takers.get(str(r.taker), 0) + 1
    return Replay(
        offers,
        tuple(worth),
        len(taken),
        len(worth) - len(taken),
        tuple(int(r.end_tick or r.tick) - r.tick for r, _ in taken),
        dict(sorted(takers.items(), key=lambda kv: -kv[1])),
    )
