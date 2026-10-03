"""The trade desk: a dry-run plan of team-to-team trades, priced on the rival affinity map.

Pure and offline: our `/api/me`, the catalog, the public feed and the affinity map in, a `TradePlan` out.
Nothing is sent; the plan is what the maker, the taker or a human would post at the opening tick.

  - listings (one tick's worth, `PlanParams.listings`): ASKS of our copies to the team that values them
    most, BIDS for missing page cards to the holder that values them least. Each is priced inside the
    expected pie (`split` of it for us at most), at the price with the best expected surplus (our surplus
    × P(the counterparty's value clears the price), from its posterior over the multipliers);
  - direct proposals (`threads`): card-for-card swaps in a thread with one team, no cash needed, with a
    cash leg only to split the expected pie;
  - the page buy list for one set (`page_list`): every missing page card, who holds it, what it is worth to
    us and to them, and the most a guardrail lets us pay.

Fair play: every planned trade has surplus for us at our private values (`min_buy_surplus`,
`sell_min_surplus`), the counterparty keeps an expected share of the pie, and no counterparty takes more
than `max_share` of the planned volume (cash plus the book of the cards that move, as
`intel.settled_volume` counts it).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from statistics import median
from typing import Any, Literal

from bazaar_agent import intel
from bazaar_agent.affinity import AffinityMap, TeamAffinity
from bazaar_agent.agents.market import Venue
from bazaar_agent.guardrails import ANY_TEAM, Action, Context, Guardrails, TradeBook, check, counterparty_refusal
from bazaar_agent.strategy import Market, StrategyParams, bonus_at_stake, build_market, buy_case

Event = dict[str, Any]
Kind = Literal["ask", "bid", "swap"]


@dataclass(frozen=True)
class PlanParams:
    listings: int = 12  # offers_per_team_per_tick: one tick's worth of new listings
    threads: int = 3  # direct proposals (swaps in a team thread)
    max_share: float = 0.25  # no counterparty above this share of the planned volume
    split: float = 0.5  # the most of the expected pie we ask for; the counterparty keeps the rest
    min_fill: float = 0.2  # skip a trade the counterparty values beyond its price with less than this probability
    page_set: str = "LAV"  # the set whose page buy list is drawn up
    min_swap_surplus: float = 2.0  # our least gain on a swap, after its cash leg (as `min_buy_surplus`)
    cap_base: int = 400  # `counterparty_cap_base` the posting is checked with (GUARDRAILS.md default: 200)
    swaps_per_team: int = 5  # swap candidates kept per team (the best by expected surplus)


# ---------------------------------------------------------------- who holds what


@dataclass(frozen=True)
class Copy:
    asset_id: int
    ref: str
    holder: str
    tick: int


def holdings(events: Iterable[Event]) -> dict[int, Copy]:
    """asset id -> its last known holder, from the public feed: a settlement moves it, a team listing it
    (on a board or to a dealer) still holds it. Copies never seen (packs, starting hands) are unknown."""
    owner: dict[int, Copy] = {}
    for e in events:
        kind, p, tick = e.get("type"), e.get("payload") or {}, int(e.get("tick") or 0)
        if kind == "settlement":
            for i in p.get("items") or []:
                if isinstance(i.get("id"), int) and intel.set_of(i.get("ref")):
                    owner[int(i["id"])] = Copy(int(i["id"]), str(i["ref"]), str(i.get("to")), tick)
        elif kind == "offer.listed" and e.get("actor"):
            for a in ((p.get("offer") or {}).get("give") or {}).get("assets") or []:
                if isinstance(a, dict) and isinstance(a.get("id"), int) and intel.set_of(a.get("ref")):
                    owner[int(a["id"])] = Copy(int(a["id"]), str(a["ref"]), str(e["actor"]), tick)
    return owner


def team_copies(owner: dict[int, Copy], us: str) -> dict[str, Counter[str]]:
    """team -> card ref -> copies we know it holds (never us: /api/me is the truth for our album)."""
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for c in owner.values():
        if intel.TEAM_ID.match(c.holder) and c.holder != us:
            out[c.holder][c.ref] += 1
    return out


# ---------------------------------------------------------------- one counterparty's value of a card


def _marginal(m: Market, n: int) -> float:
    return m.marginals[n] if n < len(m.marginals) else 0.0


def value_dist(t: TeamAffinity | None, set_code: str, book: float, marginal: float) -> list[tuple[float, float]]:
    """(value, probability) of one card to a team: book × multiplier × the copy marginal. Unknown team: the
    prior (every multiplier alike)."""
    if t is None or set_code not in t.distribution:
        return [(book * marginal, 1.0)]
    return [(book * a * marginal, p) for a, p in t.distribution[set_code].items()]


def p_at_least(dist: Sequence[tuple[float, float]], x: float) -> float:
    return sum(p for v, p in dist if v >= x - 1e-9)


def p_at_most(dist: Sequence[tuple[float, float]], x: float) -> float:
    return sum(p for v, p in dist if v <= x + 1e-9)


def mean(dist: Sequence[tuple[float, float]]) -> float:
    return sum(v * p for v, p in dist)


# ---------------------------------------------------------------- planned trades


@dataclass(frozen=True)
class Trade:
    kind: Kind
    counterparty: str
    give: dict[str, Any]  # the structured offer, from our side
    want: dict[str, Any]
    refs: tuple[str, ...]  # (card given,) for an ask, (card wanted,) for a bid, (given, wanted) for a swap
    asset_id: int | None  # our copy that leaves (asks, swaps)
    price: int  # cash: what we ask (ask), bid (bid), or the swap's cash leg (+ we receive, − we pay)
    fee: int  # paid by the accepting side
    ours: float  # what the trade gains us at our values when it fills
    theirs: float  # the counterparty's expected gain at the price
    p_fill: float  # P(the counterparty's value clears the price), from its posterior
    volume: int  # notional: the larger of the cash and the book of the cards that move (`intel.settled_volume`)
    reason: str
    rarity: str = ""  # of the card we buy (bids, swaps) or give (asks)
    to: str | None = None  # posted addressed to the counterparty; None: on the board for anyone (`post_as`)

    @property
    def expected(self) -> float:
        return round(self.ours * self.p_fill, 2)


def _venue_fee(venue: Venue | None, price: int, cards: int = 1) -> int:
    return venue.fee(price, cards) if venue is not None else 0


@dataclass(frozen=True)
class Ours:
    """One copy we could part with: what selling it costs us (`your_value` plus the page bonus it carries)."""

    asset_id: int
    ref: str
    loss: float
    floor: int  # the lowest ask: our loss plus `sell_min_surplus`, never below the guardrail's sell floor


@dataclass(frozen=True)
class Wanted:
    """One missing page card: what it is worth to us (with its page bonus share) and the most we may bid."""

    ref: str
    worth: float
    top: int  # min(worth − min_buy_surplus, the guardrail cap, the median dealer fill when a dealer sells it)


def dealer_prices(events: Iterable[Event]) -> dict[str, list[int]]:
    """card ref -> what dealers sold it for (one-card settlements from a dealer to a team), oldest first."""
    out: dict[str, list[int]] = defaultdict(list)
    for p in intel.tape(events):
        if p.persona and p.seller == p.persona and p.items == 1 and intel.set_of(p.ref):
            out[p.ref].append(p.price)
    return dict(out)


def our_copies(m: Market, me: dict[str, Any], params: StrategyParams, rules: Guardrails) -> list[Ours]:
    """One copy per card we hold, the one we lose least by selling; a copy without `your_value` never."""
    best: dict[str, dict[str, Any]] = {}
    for a in me.get("assets") or []:
        if a.get("kind") != "card" or not isinstance(a.get("id"), int):
            continue
        if not isinstance(a.get("your_value"), int | float) or str(a.get("ref")) not in m.cards:
            continue
        ref = str(a["ref"])
        if ref not in best or float(a["your_value"]) < float(best[ref]["your_value"]):
            best[ref] = a
    out = []
    for ref, a in best.items():
        value = float(a["your_value"])
        loss = value + bonus_at_stake(m, m.cards[ref], params)
        floor = max(math.ceil(loss + params.sell_min_surplus), math.ceil(value * rules.sell_min_value_ratio - 1e-9))
        out.append(Ours(int(a["id"]), ref, round(loss, 2), floor))
    return out


def wanted_cards(
    m: Market, params: StrategyParams, rules: Guardrails, dealers: dict[str, list[int]] | None = None
) -> list[Wanted]:
    """Missing page cards of released sets. A team bid never beats a dealer: when a dealer sells the card,
    the most we bid a team is its median fill."""
    out = []
    for card in m.cards.values():
        if card.set_code not in m.released or not card.page or m.held.get(card.ref, 0) > 0:
            continue
        worth = buy_case(m, card, params).value
        cap = rules.max_price_for(card.rarity)
        top = math.floor(worth - params.min_buy_surplus)
        top = min(top, cap) if cap is not None else top
        fills = (dealers or {}).get(card.ref)
        if fills:
            top = min(top, math.floor(median(fills)))
        out.append(Wanted(card.ref, round(worth, 2), top))
    return out


def ask_trades(
    m: Market,
    ours: Sequence[Ours],
    amap: AffinityMap,
    copies: dict[str, Counter[str]],
    pp: PlanParams,
    venue: Venue | None,
) -> list[Trade]:
    """Every (our copy, team) ask with surplus for both: priced between our floor and our `split` of the
    expected pie, at the best expected surplus (our surplus × P(its value clears price + fee))."""
    out: list[Trade] = []
    for o in ours:
        card = m.cards[o.ref]
        for team, t in amap.teams.items():
            if team == m.us:
                continue
            dist = value_dist(t, card.set_code, card.book, _marginal(m, copies.get(team, Counter())[o.ref]))
            pie = mean(dist) - _venue_fee(venue, o.floor) - o.loss
            ceiling = math.floor(o.loss + pp.split * pie)
            best: Trade | None = None
            for price in range(o.floor, ceiling + 1):
                fee = _venue_fee(venue, price)
                trade = Trade(
                    "ask",
                    team,
                    {"assets": [o.asset_id]},
                    {"cash": price},
                    (o.ref,),
                    o.asset_id,
                    price,
                    fee,
                    round(price - o.loss, 2),
                    round(mean(dist) - price - fee, 2),
                    round(p_at_least(dist, price + fee), 3),
                    max(price, round(card.book)),
                    f"{o.ref}: we lose {o.loss:.1f}; {team} values it {mean(dist):.1f} "
                    f"(P {card.set_code} is its top set {t.p_top.get(card.set_code, 0):.2f}); fee {fee} (theirs)",
                    card.rarity,
                )
                if (
                    trade.p_fill >= pp.min_fill
                    and trade.theirs > 0
                    and (best is None or trade.expected > best.expected)
                ):
                    best = trade
            if best is not None:
                out.append(best)
    return out


def bid_trades(
    m: Market,
    wanted: Sequence[Wanted],
    amap: AffinityMap,
    copies: dict[str, Counter[str]],
    pp: PlanParams,
    venue: Venue | None,
) -> list[Trade]:
    """Every (missing page card, known holder) bid with surplus for both: from our `split` of the expected
    pie up to our top (a guardrail cap below the fair price leaves us more than half: bid the cap)."""
    out: list[Trade] = []
    for w in wanted:
        card = m.cards[w.ref]
        for team, held in copies.items():
            n = held.get(w.ref, 0)
            if n == 0 or team == m.us:
                continue
            dist = value_dist(amap.teams.get(team), card.set_code, card.book, _marginal(m, n - 1))
            pie = w.worth - _venue_fee(venue, w.top) - mean(dist)
            if pie <= 0 or w.top < 1:
                continue
            floor = min(w.top, max(1, math.ceil(w.worth - pp.split * pie)))
            best: Trade | None = None
            for price in range(floor, w.top + 1):
                fee = _venue_fee(venue, price)  # the holder accepts our bid: it pays the fee
                trade = Trade(
                    "bid",
                    team,
                    {"cash": price},
                    {"cards": [w.ref]},
                    (w.ref,),
                    None,
                    price,
                    fee,
                    round(w.worth - price, 2),
                    round(price - fee - mean(dist), 2),
                    round(p_at_most(dist, price - fee), 3),
                    max(price, round(card.book)),
                    f"{w.ref}: worth {w.worth:.1f} to us; {team} holds {n} and loses {mean(dist):.1f}; "
                    f"fee {fee} (theirs); our top {w.top}",
                    card.rarity,
                )
                if (
                    trade.p_fill >= pp.min_fill
                    and trade.theirs > 0
                    and (best is None or trade.expected > best.expected)
                ):
                    best = trade
            if best is not None:
                out.append(best)
    return out


def swap_trades(
    m: Market,
    ours: Sequence[Ours],
    wanted: Sequence[Wanted],
    amap: AffinityMap,
    copies: dict[str, Counter[str]],
    pp: PlanParams,
    venue: Venue | None,
) -> list[Trade]:
    """Card for card with one team: our copy for a missing card it holds, with a cash leg that splits the
    expected pie (`want.cash` when they add, `give.cash` when we do), so a swap can also carry a card worth
    more to us than to them. The `swaps_per_team` best per team."""
    best: dict[str, list[Trade]] = defaultdict(list)
    fee = _venue_fee(venue, 0, 2)
    for team, held in copies.items():
        if team == m.us:
            continue
        t = amap.teams.get(team)
        for w in wanted:
            n = held.get(w.ref, 0)
            if n == 0:
                continue
            want_card = m.cards[w.ref]
            lose_dist = value_dist(t, want_card.set_code, want_card.book, _marginal(m, n - 1))
            for o in ours:
                give_card = m.cards[o.ref]
                gain_dist = value_dist(t, give_card.set_code, give_card.book, _marginal(m, held.get(o.ref, 0)))
                ours_raw, theirs_raw = w.worth - o.loss, mean(gain_dist) - mean(lose_dist) - fee
                if ours_raw + theirs_raw <= 0:  # no pie: no cash leg makes it good for both
                    continue
                cash = round((theirs_raw - ours_raw) / 2)  # > 0: they add cash; < 0: we do
                p_fill = sum(pg * pl for vg, pg in gain_dist for vl, pl in lose_dist if vg - vl - fee - cash >= 0)
                give: dict[str, Any] = {"assets": [o.asset_id]}
                want: dict[str, Any] = {"cards": [w.ref]}
                if cash < 0:
                    give["cash"] = -cash
                elif cash > 0:
                    want["cash"] = cash
                trade = Trade(
                    "swap",
                    team,
                    give,
                    want,
                    (o.ref, w.ref),
                    o.asset_id,
                    cash,
                    fee,
                    round(ours_raw + cash, 2),
                    round(theirs_raw - cash, 2),
                    round(p_fill, 3),
                    max(abs(cash), round(give_card.book + want_card.book)),
                    f"{o.ref} for {w.ref}: +{ours_raw:.1f} to us, +{theirs_raw:.1f} to {team} before the cash leg "
                    f"{cash:+d}; fee {fee} (theirs)",
                    want_card.rarity,
                )
                if trade.p_fill < pp.min_fill or trade.ours < pp.min_swap_surplus or trade.theirs <= 0:
                    continue
                best[team].append(trade)
    return [t for ts in best.values() for t in sorted(ts, key=lambda t: (-t.expected, t.refs))[: pp.swaps_per_team]]


# ---------------------------------------------------------------- choosing, under the share cap


def shares(trades: Iterable[Trade]) -> dict[str, float]:
    volume: Counter[str] = Counter()
    for t in trades:
        volume[t.counterparty] += t.volume
    total = sum(volume.values())
    return {team: round(v / total, 4) for team, v in volume.most_common()} if total else {}


def _items(t: Trade) -> set[str]:
    """What a trade uses up: our copy, the card we want."""
    used = {f"asset:{t.asset_id}"} if t.asset_id is not None else set()
    if t.kind == "bid":
        used.add(f"want:{t.refs[0]}")
    if t.kind == "swap":
        used.add(f"want:{t.refs[1]}")
    return used


def _cash_out(t: Trade) -> int:
    return t.price if t.kind == "bid" else max(0, -t.price) if t.kind == "swap" else 0


def _greedy(pool: Sequence[Trade], pp: PlanParams, cash_room: int, per_team: float) -> list[Trade]:
    chosen: list[Trade] = []
    used: set[str] = set()
    volume: Counter[str] = Counter()
    cash = n_list = n_swap = 0
    for t in pool:
        if _items(t) & used or volume[t.counterparty] + t.volume > per_team + 1e-9:
            continue
        if (n_swap >= pp.threads) if t.kind == "swap" else (n_list >= pp.listings):
            continue
        if cash + _cash_out(t) > cash_room:
            continue
        chosen.append(t)
        used |= _items(t)
        volume[t.counterparty] += t.volume
        cash += _cash_out(t)
        n_swap += t.kind == "swap"
        n_list += t.kind != "swap"
    return chosen


def _fair(trades: Sequence[Trade], max_share: float) -> bool:
    return all(v <= max_share + 1e-9 for v in shares(trades).values())


def _repair(trades: list[Trade], max_share: float) -> list[Trade]:
    """Drop the smallest expected trade of the team furthest over `max_share` until none is."""
    trades = list(trades)
    while trades and not _fair(trades, max_share):
        worst = max(shares(trades).items(), key=lambda kv: (kv[1], kv[0]))[0]
        trades.remove(min((t for t in trades if t.counterparty == worst), key=lambda t: (t.expected, t.refs)))
    return trades


def _search(
    pool: Sequence[Trade], pp: PlanParams, cash_room: int, seed: list[Trade], max_nodes: int
) -> tuple[list[Trade], bool]:
    """Branch and bound over `pool` (best expected first): each copy and wanted card once, the listing and
    thread counts, our cash out within `cash_room`, and at a leaf no team above `max_share` of the plan's
    volume. Bound: the best expected per item still free, top-N by the slots left. Stops after `max_nodes`
    nodes with the best plan found (at least `seed`)."""
    best, best_value = list(seed), sum(t.expected for t in seed)
    item_of = [sorted(_items(t)) for t in pool]
    nodes = 0

    def bound(i: int, used: set[str], slots: int, cash_left: int) -> tuple[float, int]:
        """(the most expected surplus, the most volume) the trades from `i` on can still add."""
        top: dict[str, float] = {}
        vol: dict[str, int] = {}
        for j in range(i, len(pool)):
            if pool[j].expected <= 0 or used.intersection(item_of[j]) or _cash_out(pool[j]) > cash_left:
                continue
            key = item_of[j][0] if item_of[j] else f"#{j}"
            top[key] = max(top.get(key, 0.0), pool[j].expected)
            vol[key] = max(vol.get(key, 0), pool[j].volume)
        return sum(sorted(top.values(), reverse=True)[:slots]), sum(sorted(vol.values(), reverse=True)[:slots])

    def dfs(i: int, chosen: list[Trade], used: set[str], cash: int, value: float, n_list: int, n_swap: int) -> None:
        nonlocal best, best_value, nodes
        nodes += 1
        if nodes > max_nodes:
            return
        if value > best_value + 1e-9 and _fair(chosen, pp.max_share):
            best, best_value = list(chosen), value
        slots = (pp.listings - n_list) + (pp.threads - n_swap)
        if i >= len(pool) or slots <= 0:
            return
        more_value, more_volume = bound(i, used, slots, cash_room - cash)
        if value + more_value <= best_value + 1e-9:
            return
        volume: Counter[str] = Counter()
        for t in chosen:
            volume[t.counterparty] += t.volume
        reach = sum(volume.values()) + more_volume  # no later trade lowers a team's volume
        if volume and max(volume.values()) > pp.max_share * reach + 1e-9:
            return
        t = pool[i]
        swap = t.kind == "swap"
        fits = (n_swap < pp.threads) if swap else (n_list < pp.listings)
        if fits and not used.intersection(item_of[i]) and cash + _cash_out(t) <= cash_room:
            chosen.append(t)
            dfs(
                i + 1,
                chosen,
                used | set(item_of[i]),
                cash + _cash_out(t),
                value + t.expected,
                n_list + (not swap),
                n_swap + swap,
            )
            chosen.pop()
        dfs(i + 1, chosen, used, cash, value, n_list, n_swap)

    dfs(0, [], set(), 0, 0.0, 0, 0)
    return best, nodes <= max_nodes


def choose(
    listings: Sequence[Trade], swaps: Sequence[Trade], pp: PlanParams, cash_room: int, max_nodes: int = 500_000
) -> tuple[list[Trade], list[Trade], list[Trade], bool]:
    """(listings, threads, the plan without the share rule, proven best): the plan with the best expected
    surplus in which no team passes `max_share` of the planned volume (`_search`, seeded by a greedy plan
    repaired to meet it), each copy and wanted card once, our cash out within `cash_room`."""
    pool = sorted([*listings, *swaps], key=lambda t: (-t.expected, t.counterparty, t.refs))
    free = _greedy(pool, pp, cash_room, math.inf)
    seed: list[Trade] = []
    for budget in range(0, sum(t.volume for t in pool) + 5, 5):  # a fair plan to start from
        plan = _repair(_greedy(pool, pp, cash_room, pp.max_share * budget), pp.max_share)
        if sum(t.expected for t in plan) > sum(t.expected for t in seed) + 1e-9:
            seed = plan
    best, proven = _search(pool, pp, cash_room, seed, max_nodes)
    return [t for t in best if t.kind != "swap"], [t for t in best if t.kind == "swap"], free, proven


# ---------------------------------------------------------------- posting, as the guardrails will see it


def _action(t: Trade, your_value: dict[int, float], to: str) -> Action | None:
    if t.kind == "ask":
        return Action("sell", t.refs[0], t.rarity, t.price, your_value.get(t.asset_id or -1), to)
    if t.kind == "bid":
        return Action("bid", t.refs[0], t.rarity, t.price, None, to)
    if t.price < 0:  # a swap where we add cash: the cash floor and the spend cap see it as a bid
        return Action("bid", t.refs[1], t.rarity, -t.price)
    return None  # a swap with no cash from us: only its counterparty's share applies


def post_as(
    trades: Sequence[Trade],
    me: dict[str, Any],
    settled: dict[str, int],
    rules: Guardrails,
    tick: int = 0,
    t_hours: float = 0.0,
) -> tuple[list[Trade], list[str]]:
    """Each trade as it would be posted, in order, through `guardrails.check()` with what the earlier ones
    promise (cash out, cards wanted, our team-to-team exposure). A swap always goes in a thread with its
    team. A listing goes for anyone when it can (on a board 34 % of Friday's asks filled, 7 % of the
    addressed ones), but an offer anyone may take counts against every team's share: so as many listings
    as possible, in order, go public while every trade still passes; the rest are addressed. (trades with
    `to` set, every refusal of the best such posting.)"""
    best: tuple[list[Trade], list[str]] | None = None
    for public_first in range(len(trades), -1, -1):
        posted, problems = _posting(trades, me, settled, rules, tick, t_hours, public_first)
        if not problems:
            return posted, problems
        if best is None or len(problems) < len(best[1]):
            best = (posted, problems)
    assert best is not None
    return best


def _posting(
    trades: Sequence[Trade],
    me: dict[str, Any],
    settled: dict[str, int],
    rules: Guardrails,
    tick: int,
    t_hours: float,
    public_first: int,
) -> tuple[list[Trade], list[str]]:
    """`post_as` with only the first `public_first` trades tried for anyone (a swap never is)."""
    your_value = {
        int(a["id"]): float(a["your_value"])
        for a in me.get("assets") or []
        if isinstance(a.get("id"), int) and isinstance(a.get("your_value"), int | float)
    }
    held = Counter(str(a.get("ref")) for a in me.get("assets") or [] if a.get("kind") == "card")
    cash, spent = int(me.get("cash") or 0), 0
    public, addressed = 0, Counter[str]()
    out, problems = [], []

    def ctx() -> Context:
        book = TradeBook(dict(settled), dict(addressed), public)
        return Context(cash, dict(held), tick, t_hours, spent_last_hour=spent, trades=book)

    for i, t in enumerate(trades):
        options = [t.counterparty] if t.kind == "swap" or i >= public_first else [ANY_TEAM, t.counterparty]
        chosen, why = None, ""
        for to in options:
            action = _action(t, your_value, to)
            reasons = [] if action is None else list(check(action, ctx(), rules).violations)
            if t.kind == "swap":  # cards for cards: its share counts the notional, not the cash leg
                book = TradeBook(dict(settled), dict(addressed), public)
                reasons += [r for r in [counterparty_refusal(book, to, t.volume, rules)] if r]
            why = "; ".join(reasons)
            if not reasons:
                chosen = to
                break
        if chosen is None:
            problems.append(f"{t.kind} {'/'.join(t.refs)} to {t.counterparty}: {why}")
            out.append(replace(t, to=t.counterparty))
            continue
        cash_out = _cash_out(t)
        cash -= cash_out
        spent += cash_out
        if t.kind in ("bid", "swap"):
            held[t.refs[-1]] += 1
        exposure = max(abs(t.price), 0) if t.kind != "swap" else t.volume
        if chosen == ANY_TEAM:
            public += exposure
        else:
            addressed[chosen] += exposure
        out.append(replace(t, to=None if chosen == ANY_TEAM else chosen))
    return out, problems


# ---------------------------------------------------------------- the page buy list


@dataclass(frozen=True)
class PageRow:
    ref: str
    rarity: str
    worth: float  # to us: book × affinity + its share of the page bonus
    cap: int | None  # GUARDRAILS.md max price for its rarity
    max_bid: int  # min(worth − min_buy_surplus, cap)
    tape: tuple[int, ...]  # team-to-team prices paid for it
    dealer: tuple[int, ...]  # what dealers sold it for
    holders: tuple[tuple[str, int, float, float], ...]  # (team, copies, P(set is its top), its loss on one copy)
    verdict: str


def page_list(
    m: Market,
    amap: AffinityMap,
    copies: dict[str, Counter[str]],
    events: Sequence[Event],
    params: StrategyParams,
    rules: Guardrails,
    set_code: str,
) -> list[PageRow]:
    prints = [p for p in intel.tape(events) if p.persona is None and p.items == 1]
    dealer = dealer_prices(events)
    rows = []
    for card in sorted((c for c in m.cards.values() if c.set_code == set_code and c.page), key=lambda c: c.ref):
        if m.held.get(card.ref, 0) > 0:
            continue
        worth = buy_case(m, card, params).value
        cap = rules.max_price_for(card.rarity)
        fills = tuple(dealer.get(card.ref, ()))
        max_bid = min(math.floor(worth - params.min_buy_surplus), cap if cap is not None else 10**9)
        if fills:  # a team bid never beats a dealer (`wanted_cards`)
            max_bid = min(max_bid, math.floor(median(fills)))
        tape = tuple(p.price for p in prints if p.ref == card.ref)
        holders = []
        for team, held in sorted(copies.items()):
            n = held.get(card.ref, 0)
            if n:
                t = amap.teams.get(team)
                loss = mean(value_dist(t, card.set_code, card.book, _marginal(m, n - 1)))
                holders.append((team, n, t.p_top.get(card.set_code, 0.0) if t else 0.0, round(loss, 1)))
        holders.sort(key=lambda h: (h[3], h[0]))
        if not holders:
            verdict = "no known holder: pull from packs or wait for a listing"
        elif all(h[3] >= max_bid for h in holders):
            verdict = f"every known holder loses >= {max_bid} (our max bid): a cash bid cannot fill; swap only"
        else:
            cheapest = holders[0]
            verdict = f"bid up to {max_bid} to {cheapest[0]} (loses ~{cheapest[3]})"
        if tape and cap is not None and min(tape) > cap:
            verdict += f"; the tape ({min(tape)}-{max(tape)}) is above max_price_{card.rarity} {cap}"
        if fills and cap is not None and min(fills) > cap:
            verdict += (
                f"; dealers sold it at {min(fills)}-{max(fills)} ({len(fills)} fills), above max_price_{card.rarity} "
                f"{cap}: a dealer buy at ~{min(fills)} would gain {worth - min(fills):.0f}"
            )
        elif fills:
            verdict += f"; dealers sell it at {min(fills)}-{max(fills)} ({len(fills)} fills): the cheaper route"
        rows.append(PageRow(card.ref, card.rarity, round(worth, 1), cap, max_bid, tape, fills, tuple(holders), verdict))
    return rows


# ---------------------------------------------------------------- the plan


@dataclass(frozen=True)
class TradePlan:
    tick: int | None
    cash: int
    cash_room: int  # cash above cash_floor: what open bids may promise
    listings: tuple[Trade, ...]
    threads: tuple[Trade, ...]
    page: tuple[PageRow, ...]
    shares: dict[str, float]
    held_back: tuple[Trade, ...]  # the plan without the share rule, less what this plan already has
    candidates: int
    checks: tuple[str, ...] = field(default_factory=tuple)
    unconstrained: float = 0.0  # expected surplus of the plan without the share rule
    proven: bool = False  # the search finished: no fair plan from these candidates beats this one

    @property
    def expected(self) -> float:
        return round(sum(t.expected for t in (*self.listings, *self.threads)), 2)

    @property
    def if_all_fill(self) -> float:
        return round(sum(t.ours for t in (*self.listings, *self.threads)), 2)

    @property
    def volume(self) -> int:
        return sum(t.volume for t in (*self.listings, *self.threads))


def verify(plan: TradePlan, pp: PlanParams, rules: Guardrails) -> tuple[str, ...]:
    """The fair-play and guardrail promises, checked on the plan itself (empty: every one holds)."""
    problems = []
    for t in (*plan.listings, *plan.threads):
        if t.ours <= 0:
            problems.append(f"{t.kind} {'/'.join(t.refs)} to {t.counterparty}: no surplus for us ({t.ours})")
        if t.theirs <= 0:
            problems.append(f"{t.kind} {'/'.join(t.refs)} to {t.counterparty}: nothing left for them ({t.theirs})")
        if t.kind == "bid":
            cap = rules.max_price_for(t.rarity)
            if cap is None or t.price > cap:
                problems.append(f"bid {t.refs[0]} at {t.price} above max_price_{t.rarity} {cap}")
    for team, s in plan.shares.items():
        if s > pp.max_share + 1e-9:
            problems.append(f"{team} takes {s:.0%} of planned volume (max {pp.max_share:.0%})")
    cash_out = sum(t.price for t in plan.listings if t.kind == "bid") + sum(max(0, -t.price) for t in plan.threads)
    if cash_out > plan.cash_room:
        problems.append(f"bids promise {cash_out} > {plan.cash_room} above cash_floor")
    if cash_out > rules.max_spend_per_game_hour:
        problems.append(f"bids promise {cash_out} > max_spend_per_game_hour {rules.max_spend_per_game_hour}")
    return tuple(problems)


def build_plan(
    me: dict[str, Any],
    catalog: dict[str, Any],
    events: Sequence[Event],
    amap: AffinityMap,
    params: StrategyParams,
    rules: Guardrails,
    pp: PlanParams | None = None,
    venue: Venue | None = None,
) -> TradePlan:
    pp = pp or PlanParams()
    m = build_market(me, catalog, events, [])
    copies = team_copies(holdings(events), m.us)
    cash_room = max(0, min(m.cash - rules.cash_floor, rules.max_spend_per_game_hour))
    ours, wanted = our_copies(m, me, params, rules), wanted_cards(m, params, rules, dealer_prices(events))
    asks = ask_trades(m, ours, amap, copies, pp, venue)
    bids = bid_trades(m, wanted, amap, copies, pp, venue)
    swaps = swap_trades(m, ours, wanted, amap, copies, pp, venue)
    listings, threads, free, proven = choose([*asks, *bids], swaps, pp, cash_room)
    # Posted as the guardrails would enforce the same share live (`max_counterparty_share` = max_share).
    live = rules.model_copy(update={"max_counterparty_share": pp.max_share, "counterparty_cap_base": pp.cap_base})
    # Threads first: they are addressed, and a public listing counts against every team's share.
    posted, refused = post_as([*threads, *listings], me, intel.settled_volume(events, m.us), live, m.tick or 0)
    page = page_list(m, amap, copies, events, params, rules, pp.page_set)
    plan = TradePlan(
        m.tick,
        m.cash,
        cash_room,
        tuple(t for t in posted if t.kind != "swap"),
        tuple(t for t in posted if t.kind == "swap"),
        tuple(page),
        shares(posted),
        tuple(t for t in free if t not in (*listings, *threads)),
        len(asks) + len(bids) + len(swaps),
        unconstrained=round(sum(t.expected for t in free), 2),
        proven=proven,
    )
    return replace(plan, checks=verify(plan, pp, rules) + tuple(refused))


# ---------------------------------------------------------------- what to send, and the page to read


def thread_proposal(t: Trade, venue: str = "rastro") -> dict[str, Any]:
    """The two calls a direct proposal makes: open a thread with the team on a venue, then one structured
    offer in it (words persuade, structure binds: the text only names the offer)."""
    gives = " + ".join([f"our {t.refs[0]}", *([f"{t.give['cash']} P"] if t.give.get("cash") else [])])
    wants = " + ".join([f"your {t.refs[1]}", *([f"{t.want['cash']} P"] if t.want.get("cash") else [])])
    return {
        "open_thread": {
            "with": t.counterparty,
            "venue": venue,
            "topic": {"swap": {"give": t.refs[0], "want": t.refs[1]}},
        },
        "message": {
            "text": f"Swap proposal: {gives} for {wants}. Accept the offer if it works for you.",
            "offer": {"give": t.give, "want": t.want},
        },
    }


def listing_request(t: Trade, venue: str = "rastro", expires_in_ticks: int = 40) -> dict[str, Any]:
    """`POST /api/offers` for one listing (with `to` when it is addressed)."""
    body: dict[str, Any] = {"venue": venue, "give": t.give, "want": t.want, "expires_in_ticks": expires_in_ticks}
    if t.to:
        body["to"] = t.to
    return body


def plan_dict(plan: TradePlan, venue: str = "rastro") -> dict[str, Any]:
    from dataclasses import asdict

    return {
        **{k: v for k, v in asdict(plan).items() if k not in ("listings", "threads", "held_back", "page")},
        "expected": plan.expected,
        "if_all_fill": plan.if_all_fill,
        "volume": plan.volume,
        "listings": [
            {**asdict(t), "expected": t.expected, "request": listing_request(t, venue)} for t in plan.listings
        ],
        "threads": [{**asdict(t), "expected": t.expected, "requests": thread_proposal(t, venue)} for t in plan.threads],
        "held_back": [{**asdict(t), "expected": t.expected} for t in plan.held_back],
        "page": [asdict(r) for r in plan.page],
    }


def _offer(side: dict[str, Any]) -> str:
    parts = [f"{side['cash']} P"] if side.get("cash") else []
    parts += [f"#{a}" for a in side.get("assets") or []]
    parts += [f"any {c}" for c in side.get("cards") or []]
    return " + ".join(parts) or "-"


def plan_markdown(plan: TradePlan, pp: PlanParams) -> str:
    """The plan as a page: every trade, the shares, the checks, the page buy list."""

    def row(t: Trade, posted: bool = True) -> str:
        to = (t.to or "anyone") if posted else "-"
        return (
            f"| {t.kind} | {'/'.join(t.refs)} | {t.counterparty} | {to} | {_offer(t.give)} | {_offer(t.want)} | "
            f"{t.fee} | {t.ours:+.1f} | {t.theirs:+.1f} | {t.p_fill:.2f} | {t.expected:+.1f} | {t.volume} |"
        )

    head = (
        "| kind | card(s) | counterparty | posted to | we give | we want | fee | ours | theirs | P(fill) | E[ours] "
        "| volume |\n|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|"
    )
    lines = [
        f"# Trade plan · tick {plan.tick} (dry run)",
        "",
        f"Cash {plan.cash}, {plan.cash_room} above `cash_floor` for bids. {plan.candidates} candidate trades; "
        f"{len(plan.listings)} listings + {len(plan.threads)} direct proposals chosen "
        f"({'proven best' if plan.proven else 'best found'} under the share rule).",
        f"Expected surplus for us **{plan.expected:+.1f} P** (all fill: {plan.if_all_fill:+.1f} P) on {plan.volume} P "
        f"of volume. Without the {pp.max_share:.0%} share rule: {plan.unconstrained:+.1f} P.",
        "",
        "## Listings and proposals",
        "",
        head,
        *(row(t) for t in (*plan.listings, *plan.threads)),
        "",
        "## Share of planned volume per counterparty",
        "",
        " · ".join(f"{team} {s:.0%}" for team, s in plan.shares.items()) or "-",
        "",
        "## Checks",
        "",
        *(f"- {c}" for c in plan.checks),
        *(
            [
                "- every trade has surplus for us and for its counterparty; no counterparty above the share; "
                "bids within the cash above the floor; every listing passes `guardrails.check()`"
            ]
            if not plan.checks
            else []
        ),
        "",
        "## Held back by the share rule",
        "",
        head,
        *(row(t, posted=False) for t in plan.held_back),
        "",
        f"## {pp.page_set} page buy list",
        "",
        "| card | rarity | worth to us | cap | max bid | team tape | dealer fills | known holders (team ×copies "
        "P(top) loss) | verdict |",
        "|---|---|---:|---:|---:|---|---|---|---|",
        *(
            f"| {r.ref} | {r.rarity} | {r.worth:.1f} | {r.cap} | {r.max_bid} | {' '.join(map(str, r.tape)) or '-'} | "
            f"{' '.join(map(str, r.dealer)) or '-'} | "
            f"{', '.join(f'{h[0]} ×{h[1]} {h[2]:.2f} {h[3]:.0f}' for h in r.holders) or '-'} | {r.verdict} |"
            for r in plan.page
        ),
    ]
    return "\n".join(lines) + "\n"
