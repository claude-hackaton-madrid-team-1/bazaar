"""Who to sell a card to: the other teams ranked by what a sale to each is worth, with the reasons.

Pure functions, no network. Per team and card:
  - willingness to pay: the median price it paid for cards of that set and rarity (every one-card settlement
    in the feed where it was the buyer, from a team or a dealer), else of that rarity, else the market's median
    for the rarity (team-to-team), else the card's book value;
  - interest: P(the set is its top set), from the rival affinity map (`affinity.py`);
  - need: whether we place a copy of the card with it (card scan + feed, `supply.py`): a team that already holds
    one values another copy at a quarter (the catalog's copy marginals), so it ranks low.
`expected` = willingness × need × (0.5 + 0.5 × interest). Rivals (RULES.md scoring is a ranking): a team in
the top 5, or up to 3 ranks above us, has its expected value cut by `rival_penalty`, so an equal-priced buyer
elsewhere always comes first; `pick` never addresses an ask to a rival at all. A sale that would complete a
top-5 team's page (it holds every other page card of the set, not this one) is blocked unless the price is at
least `complete_ratio` × our value. Every row says why.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from statistics import median
from typing import Any

from bazaar_agent import intel

Event = dict[str, Any]


@dataclass(frozen=True)
class BuyerConfig:
    top_n: int = 5  # the podium and its chasers: never fed below a fair price
    rival_window: int = 3  # a team at most this many ranks above us is a rival too
    rival_penalty: float = 0.15  # a rival's expected value is cut by this share
    complete_ratio: float = 1.5  # completing a top-n team's page needs price >= this × our value
    held_marginal: float = 0.25  # a second copy's worth to a team that holds one (copy_marginals)
    unknown_need: float = 0.6  # need when the card's holders are not known at all
    min_willing_ratio: float = 0.8  # address an ask only to a team seen paying at least this share of its price


@dataclass(frozen=True)
class CardInfo:
    ref: str
    set_code: str
    rarity: str
    book: float
    page: bool


@dataclass(frozen=True)
class Willingness:
    price: float
    basis: str


@dataclass(frozen=True)
class BuyerRow:
    card: str
    team: str
    rank: int | None
    willing: float  # what the team is seen to pay for such a card
    interest: float  # P(the card's set is its top set)
    missing: bool | None  # None: nobody's holdings of the card are known
    expected: float  # willing × need × interest weight, after the rival penalty
    rival: str | None  # why the team is a podium rival, else None
    blocked: bool  # the sale would complete a top-n team's page below `complete_ratio` × our value
    why: str


def card_info(catalog: Mapping[str, Any]) -> dict[str, CardInfo]:
    out = {}
    for s in catalog.get("sets") or []:
        for c in s.get("cards") or []:
            if isinstance(c, dict) and c.get("id"):
                ref = str(c["id"])
                set_code = str(s.get("id") or intel.set_of(ref) or "")
                book = float(c.get("book") or 0)
                out[ref] = CardInfo(ref, set_code, str(c.get("rarity") or ""), book, bool(c.get("page")))
    return out


def leaderboard_ranks(board: Mapping[str, Any] | None) -> dict[str, int]:
    """team -> rank from `GET /api/leaderboard`; rows without a team id or an integer rank are skipped."""
    rows = (board or {}).get("teams") or []
    return {
        str(r["team"]): int(r["rank"])
        for r in rows
        if isinstance(r, dict) and intel.TEAM_ID.match(str(r.get("team") or "")) and isinstance(r.get("rank"), int)
    }


def team_buys(events: Iterable[Event], info: Mapping[str, CardInfo]) -> dict[str, dict[tuple[str, str], list[int]]]:
    """team -> (set, rarity) -> prices it paid for one card (dealer sales included: they show what it pays)."""
    out: dict[str, dict[tuple[str, str], list[int]]] = defaultdict(lambda: defaultdict(list))
    for p in intel.tape(events):
        card = info.get(p.ref)
        if p.items == 1 and p.kind == "card" and card and p.price > 0 and intel.TEAM_ID.match(p.buyer):
            out[p.buyer][(card.set_code, card.rarity)].append(p.price)
    return out


def market_by_rarity(events: Iterable[Event], info: Mapping[str, CardInfo]) -> dict[str, float]:
    """rarity -> the median team-to-team price of one card."""
    prices: dict[str, list[int]] = defaultdict(list)
    for p in intel.tape(events):
        card = info.get(p.ref)
        if p.persona is None and p.items == 1 and card and intel.TEAM_ID.match(p.seller) and p.price > 0:
            prices[card.rarity].append(p.price)
    return {k: float(median(v)) for k, v in prices.items()}


def willingness(
    buys: Mapping[str, Mapping[tuple[str, str], list[int]]], team: str, card: CardInfo, market: Mapping[str, float]
) -> Willingness:
    mine = buys.get(team) or {}
    same = mine.get((card.set_code, card.rarity)) or []
    if same:
        return Willingness(
            float(median(same)), f"paid {median(same):g} for {card.set_code} {card.rarity} (n={len(same)})"
        )
    rarity = [p for (_, r), v in mine.items() if r == card.rarity for p in v]
    if rarity:
        return Willingness(float(median(rarity)), f"paid {median(rarity):g} for any {card.rarity} (n={len(rarity)})")
    if card.rarity in market:
        return Willingness(market[card.rarity], f"no buy seen; market {card.rarity} {market[card.rarity]:g}")
    return Willingness(card.book, f"no buy seen; book {card.book:g}")


def is_rival(rank: int | None, our_rank: int | None, cfg: BuyerConfig) -> str | None:
    if rank is None:
        return None
    if rank <= cfg.top_n:
        return f"top {cfg.top_n}"
    if our_rank is not None and rank < our_rank and our_rank - rank <= cfg.rival_window:
        return f"{cfg.rival_window} ranks above us"
    return None


def completes_page(
    card: CardInfo, team: str, info: Mapping[str, CardInfo], holders: Mapping[str, Mapping[str, int]]
) -> bool:
    """The team holds every other page card of the set (as far as we place copies) and not this one."""
    if not card.page or holders.get(card.ref, {}).get(team, 0) > 0:
        return False
    others = [c.ref for c in info.values() if c.set_code == card.set_code and c.page and c.ref != card.ref]
    return bool(others) and all(holders.get(ref, {}).get(team, 0) > 0 for ref in others)


def rank_buyers(
    card: str,
    *,
    catalog: Mapping[str, Any],
    events: Iterable[Event],
    holders: Mapping[str, Mapping[str, int]],
    interest: Mapping[str, Mapping[str, float]],
    ranks: Mapping[str, int],
    us: str,
    our_value: float,
    price: float | None = None,
    cfg: BuyerConfig | None = None,
) -> list[BuyerRow]:
    """Every other team as a buyer of `card`, best first (blocked rows last). `holders`: card -> team -> copies
    we place with it; `interest`: team -> set -> P(top set); `price`: the ask we would post (default: each
    team's willingness), checked against the page-completion rule."""
    cfg = cfg or BuyerConfig()
    events = list(events)
    info = card_info(catalog)
    target = info.get(card)
    if target is None:
        return []
    buys, market = team_buys(events, info), market_by_rarity(events, info)
    known = card in holders
    teams = (set(ranks) | set(buys) | set(interest) | {t for h in holders.values() for t in h}) - {us}
    rows = []
    for team in sorted(teams, key=lambda t: (len(t), t)):
        w = willingness(buys, team, target, market)
        held = holders.get(card, {}).get(team, 0)
        missing = held == 0 if known else None
        need = 1.0 if missing else cfg.held_marginal if missing is False else cfg.unknown_need
        likes = float((interest.get(team) or {}).get(target.set_code, 0.0))
        rank = ranks.get(team)
        rival = is_rival(rank, ranks.get(us), cfg)
        expected = w.price * need * (0.5 + 0.5 * likes) * (1 - cfg.rival_penalty if rival else 1.0)
        ask = w.price if price is None else price
        top = rank is not None and rank <= cfg.top_n
        blocked = top and completes_page(target, team, info, holders) and ask < cfg.complete_ratio * our_value
        parts = [w.basis, f"likes {target.set_code} {likes:.2f}"]
        parts.append(f"holds {held}" if missing is False else "misses it" if missing else "holdings unknown")
        parts.append(f"rank {rank}" if rank is not None else "rank unknown")
        if rival:
            parts.append(f"rival ({rival}): -{cfg.rival_penalty:.0%}")
        if blocked:
            parts.append(
                f"BLOCKED: completes its {target.set_code} page below {cfg.complete_ratio:g}× our value {our_value:g}"
            )
        rows.append(
            BuyerRow(
                card,
                team,
                rank,
                round(w.price, 1),
                round(likes, 2),
                missing,
                round(expected, 2),
                rival,
                blocked,
                "; ".join(parts),
            )
        )
    return sorted(rows, key=lambda r: (r.blocked, -r.expected, r.team))


def market_inputs(
    me: Mapping[str, Any], catalog: Mapping[str, Any], events: Iterable[Event], scan: Iterable[Mapping[str, Any]] = ()
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, float]]]:
    """(holders: card -> team -> copies, interest: team -> set -> P(top set)) from the supply map (card scan +
    feed) and the rival affinity map. An affinity map that cannot be drawn leaves interest empty."""
    from bazaar_agent import affinity as af
    from bazaar_agent.supply import supply_map

    events = list(events)
    us = str(me.get("id") or "")
    supply = supply_map(catalog, me, events, scan)
    holders = {ref: dict(c.holders) for ref, c in supply.cards.items() if c.unplaced == 0 or c.holders}
    try:
        amap = af.affinity_map(
            events, af.catalog_sets(dict(catalog)), af.multipliers_from(dict(me)), dict(catalog), exclude=[us]
        )
    except (ValueError, KeyError, ZeroDivisionError):
        return holders, {}
    return holders, {t: dict(a.p_top) for t, a in amap.teams.items() if a.signals > 0}


def pick(
    rows: Iterable[BuyerRow], tried: Iterable[str] = (), price: float | None = None, cfg: BuyerConfig | None = None
) -> str | None:
    """The team to address an ask to: the best row that is not blocked, not a rival, has a known rank (a team
    missing from the leaderboard could be anyone), is not known to hold the card, was not already tried at this
    price, and (with `price`) was seen paying at least `min_willing_ratio` of it. None: the ask stays public."""
    cfg = cfg or BuyerConfig()
    skip = set(tried)
    for r in rows:
        if r.blocked or r.rival is not None or r.rank is None or r.missing is False or r.team in skip:
            continue
        if price is not None and r.willing < cfg.min_willing_ratio * price:
            continue
        return r.team
    return None
