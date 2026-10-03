"""The taker's counterfactual: replay a captured day tick by tick through the CURRENT taker, as if it had been live.

Nothing reaches the game. The taker runs with `live=True` against fake clients built from the feed, so every
gate a live accept meets is exercised (the guardrails with the shared ledger's hourly spend and accept slots, the
duel slot, the tick window), and every accept "settles" into a simulated wallet at the next tick:
  - the boards at tick t are rebuilt from `offer.listed` / cancels / fills / expiry (`arb_study.spans`), with
    their venues' fees at t; an ask we bought leaves the board;
  - the wallet starts as our album at the start of the day (`me`) and pays ask + fee for each card; a card's
    `your_value` is book × affinity × the copy marginal for the copies held;
  - the duels' accepts we really made take the team's accept slot at their tick (`duel_ticks`);
  - no dealer desk (a dealer's answers cannot be replayed) and no Jev (`undecided`: today's rules decide).
The result lists every buy (with its surplus at private values: the value of one more copy, the trade-score
proxy, and the strategy's value including the page-bonus share) and every refusal, by the guardrail that made it.
Optimistic in one way: an ask another team took in the same tick is counted as ours (`contested`).
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bazaar_agent.agents.market import Venue
from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.arb_study import Span, spans, venues_by_tick
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails, Ledger
from bazaar_agent.intel import Event
from bazaar_agent.strategy import StrategyParams
from bazaar_agent.ticks import Clock

MARGINALS = (1.0, 0.25, 0.1)


def _venue_row(v: Venue) -> dict[str, Any]:
    return {
        "venue": v.id,
        "owner": v.owner or ("world" if v.house else ""),
        "fee_bps": v.fee_bps,
        "fee_per_card": v.fee_per_card,
        "status": "open",
        "house": v.house,
        "trades": v.trades,
        "rules": {"mechanism": v.mechanism},
    }


@dataclass
class Bought:
    tick: int
    offer_id: int
    ref: str
    venue: str
    maker: str
    ask: int
    fee: int
    copy_value: float  # our value of the copy bought (book × affinity × marginal): the trade-score proxy
    contested: bool = False  # another team took this ask at the same settlement in reality
    strategy_value: float = 0.0  # the taker's own value: book × affinity + the page-bonus share it buys toward

    @property
    def total(self) -> int:
        return self.ask + self.fee

    @property
    def surplus(self) -> float:
        return round(self.copy_value - self.total, 2)

    @property
    def strategy_surplus(self) -> float:
        return round(self.strategy_value - self.total, 2)


@dataclass
class World:
    """The day as the fake clients serve it, and the wallet the taker's accepts settle into."""

    events: list[Event]
    me: dict[str, Any]
    catalog: dict[str, Any]
    tick: int = 0
    rows: list[Span] = field(default_factory=list)
    raw: dict[int, dict[str, Any]] = field(default_factory=dict)
    fees: list[dict[str, Venue]] = field(default_factory=list)
    taken: set[int] = field(default_factory=set)  # asks we bought: off the board from then on
    pending: list[tuple[int, int]] = field(default_factory=list)  # (offer id, accept tick), settled next tick
    bought: list[Bought] = field(default_factory=list)
    real_fills: dict[int, int] = field(default_factory=dict)  # ask id -> tick another team's fill settled
    cash_low: int = 10**9

    def __post_init__(self) -> None:
        last = max((int(e.get("tick") or 0) for e in self.events), default=0)
        self.rows = [s for s in spans(self.events, last) if s.offer.maker != self.us]
        self.fees = venues_by_tick(self.events, last)
        for e in self.events:
            if e.get("type") == "offer.listed":
                o = (e.get("payload") or {}).get("offer") or {}
                if isinstance(o.get("id"), int):
                    self.raw[int(o["id"])] = o
        self.real_fills = {s.offer.id: s.end for s in self.rows if s.how == "filled"}
        self.book = {
            str(c["id"]): float(c.get("book") or 0)
            for st in self.catalog.get("sets") or []
            for c in st.get("cards") or []
        }
        self.cash_low = int(self.me.get("cash") or 0)

    @property
    def us(self) -> str:
        return str(self.me.get("id") or "")

    @property
    def last_tick(self) -> int:
        return len(self.fees) - 1

    def value_of(self, ref: str, copies: int) -> float:
        """Our value of the n-th copy (n = `copies`) of a card: book × affinity × marginal[n − 1]."""
        aff = float((self.me.get("affinity") or {}).get(ref.split("-")[0], 1.0))
        marginal = MARGINALS[copies - 1] if 0 < copies <= len(MARGINALS) else 0.0
        return round(self.book.get(ref, 0.0) * aff * marginal, 2)

    def held(self, ref: str) -> int:
        return sum(1 for a in self.me.get("assets") or [] if a.get("kind") == "card" and a.get("ref") == ref)

    def board(self, venue: str) -> list[dict[str, Any]]:
        out = []
        for s in self.rows:
            o = s.offer
            if o.venue == venue and s.start <= self.tick < s.end and o.id not in self.taken and o.id in self.raw:
                out.append({**deepcopy(self.raw[o.id]), "status": "open"})
        return out

    def accept(self, offer_id: int) -> dict[str, Any]:
        self.pending.append((offer_id, self.tick))
        self.taken.add(offer_id)
        return {"ok": True, "settles_tick": self.tick + 1}

    def advance(self) -> None:
        """Settle last tick's accepts into the wallet, then move the clock on."""
        for offer_id, tick in self.pending:
            raw = self.raw[offer_id]
            assets = (raw.get("give") or {}).get("assets") or []
            if not assets:  # only plain asks are ever accepted by the taker
                continue
            asset, ask = assets[0], int((raw.get("want") or {}).get("cash") or 0)
            venue = self.fees[tick].get(str(raw.get("venue") or "rastro"))
            fee = venue.fee(ask) if venue is not None else 0
            ref = str(asset.get("ref"))
            n = self.held(ref) + 1
            value = self.value_of(ref, n)
            self.me["cash"] = int(self.me.get("cash") or 0) - ask - fee
            self.me.setdefault("assets", []).append(
                {**asset, "kind": "card", "ref": ref, "your_value": value, "id": int(asset.get("id") or -offer_id)}
            )
            fill = self.real_fills.get(offer_id)
            self.bought.append(
                Bought(
                    tick,
                    offer_id,
                    ref,
                    str(raw.get("venue")),
                    str(raw.get("maker")),
                    ask,
                    fee,
                    value,
                    fill is not None and fill <= tick + 1,
                )
            )
            self.cash_low = min(self.cash_low, int(self.me["cash"]))
        self.pending = []
        self.tick += 1


class FakeTeam:
    def __init__(self, world: World, tick_seconds: float) -> None:
        self.world, self.tick_seconds = world, tick_seconds

    def me(self) -> dict[str, Any]:
        return deepcopy(self.world.me)

    def my_offers(self) -> dict[str, Any]:
        return {"offers": []}

    def my_threads(self, status: str | None = None) -> dict[str, Any]:
        return {"threads": []}

    def clock(self) -> dict[str, Any]:
        return clock_at(self.world.tick, self.tick_seconds).model_dump()

    def accept(self, offer_id: int, assets: list[int] | None = None) -> dict[str, Any]:
        return self.world.accept(offer_id)


class FakePublic:
    def __init__(self, world: World) -> None:
        self.world = world

    def dealers(self) -> dict[str, Any]:
        return {"dealers": []}

    def catalog(self) -> dict[str, Any]:
        return deepcopy(self.world.catalog)

    def venues(self) -> dict[str, Any]:
        return {"venues": [_venue_row(v) for v in self.world.fees[self.world.tick].values()]}

    def board(self, venue: str = "rastro") -> dict[str, Any]:
        return {"offers": self.world.board(venue)}

    def feed_window(self, limit: int) -> list[Event]:
        t = self.world.tick
        return [
            e for e in self.world.events if int(e.get("tick") or 0) <= t and not str(e.get("type")).startswith("agent.")
        ]


def clock_at(tick: int, tick_seconds: float) -> Clock:
    return Clock(tick=tick, tick_seconds=tick_seconds, next_tick_in=tick_seconds, t_hours=tick * tick_seconds / 3600)


# ---------------------------------------------------------------- the run


@dataclass(frozen=True)
class Result:
    label: str
    bought: tuple[Bought, ...]
    cash_start: int
    cash_end: int
    cash_low: int
    blocked: dict[str, int]  # guardrail → distinct offers it refused (an offer counts once per guardrail)
    quota: int  # offers that lost the tick's accept slot to a better one or to a duel
    duel_slots: int  # ticks a duel held the team's accept and the taker wanted it
    spend_by_hour: dict[int, int]

    @property
    def surplus(self) -> float:
        return round(sum(b.surplus for b in self.bought), 2)

    @property
    def strategy_surplus(self) -> float:
        return round(sum(b.strategy_surplus for b in self.bought), 2)

    @property
    def spent(self) -> int:
        return sum(b.total for b in self.bought)


GUARD = re.compile(
    r"(cash_floor|max_spend_per_game_hour|max_price_\w+|block_buying_held_cards|max_counterparty_share|"
    r"no max_price for rarity|max_accepts_per_tick|accept\(s\) already)"
)


def classify(rows: Iterable[dict[str, Any]], duels: Iterable[int] = ()) -> tuple[dict[str, int], int, int]:
    """From the decision log: distinct offers each guardrail refused, offers that lost the accept slot, and
    ticks where a duel held the slot while the taker wanted it (`duels`: the ticks a duel took it)."""
    by_guard: dict[str, set[int]] = {}
    quota: set[int] = set()
    duel_ticks: set[int] = set()
    duel_set = set(duels)
    for r in rows:
        if r.get("kind") != "accept_ask" or r.get("status") != "rejected":
            continue
        oid = int((r.get("inputs") or {}).get("offer_id") or 0)
        tick = int(r.get("tick") or 0)
        if "duel holds" in json.dumps(r) or (tick in duel_set and not GUARD.findall(str(r.get("guardrail") or ""))):
            duel_ticks.add(tick)
            continue
        guards = set(GUARD.findall(str(r.get("guardrail") or "")))
        if guards:
            for g in guards:
                by_guard.setdefault("max_accepts_per_tick" if g.startswith("accept(s)") else g, set()).add(oid)
        else:
            quota.add(oid)
    return {g: len(ids) for g, ids in sorted(by_guard.items(), key=lambda kv: -len(kv[1]))}, len(quota), len(duel_ticks)


def run(
    events: Sequence[Event],
    me: dict[str, Any],
    catalog: dict[str, Any],
    rules: Guardrails,
    params: StrategyParams,
    *,
    duel_ticks: Iterable[int] = (),
    tick_seconds: float = 60.0,
    label: str = "",
) -> Result:
    world = World(list(events), deepcopy(me), catalog)
    folder = Path(tempfile.mkdtemp(prefix="taker-replay-"))
    ledger = Ledger(folder / "ledger.jsonl")
    duel_ticks = list(duel_ticks)
    for t in duel_ticks:  # the duel player went first in these ticks
        ledger.reserve_accept(t, t * tick_seconds / 3600, 0, f"duel:{t}", rules.max_accepts_per_tick)
    lines: list[str] = []
    team, public = FakeTeam(world, tick_seconds), FakePublic(world)
    taker = Taker(
        team,
        public,
        rules=rules,
        params=lambda tick: params,
        ledger=ledger,
        decisions=DecisionLog(folder),
        feed=MarketFeed(public.feed_window),
        live=True,
        log=lines.append,
        config=TakerConfig(max_dealer_threads=0, duel_grace_s=0.0),
        now=time.monotonic,
        sleep=lambda s: None,
    )
    cash_start = int(world.me.get("cash") or 0)
    while world.tick <= world.last_tick:
        taker.on_tick(clock_at(world.tick, tick_seconds))
        world.advance()
    rows = (
        [json.loads(x) for x in (folder / "agents" / "decisions.jsonl").read_text().splitlines()]
        if (folder / "agents" / "decisions.jsonl").is_file()
        else []
    )
    duel_ticks = list(duel_ticks)
    blocked, quota, duels = classify(rows, duel_ticks)
    values = {
        int((r.get("inputs") or {}).get("offer_id") or 0): float((r.get("inputs") or {}).get("value") or 0)
        for r in rows
        if r.get("kind") == "accept_ask" and r.get("status") == "approved"
    }
    for b in world.bought:
        b.strategy_value = values.get(b.offer_id, 0.0)
    hours: Counter[int] = Counter()
    for b in world.bought:
        hours[int(b.tick * tick_seconds // 3600)] += b.total
    return Result(
        label,
        tuple(world.bought),
        cash_start,
        int(world.me.get("cash") or 0),
        world.cash_low,
        blocked,
        quota,
        duels,
        dict(sorted(hours.items())),
    )


def start_of_day(me: dict[str, Any], events: Iterable[Event], cash: int) -> dict[str, Any]:
    """Our album at the start of the day: `me` (a later snapshot) without the cards we received during the day
    (settlements to us), with `cash`. Exact when we only bought (Friday: four dealer cards)."""
    us = str(me.get("id") or "")
    received = {
        int(i["id"])
        for e in events
        if e.get("type") == "settlement"
        for i in (e.get("payload") or {}).get("items") or []
        if i.get("to") == us and isinstance(i.get("id"), int)
    }
    out = deepcopy(me)
    out["assets"] = [a for a in me.get("assets") or [] if a.get("id") not in received]
    out["cash"] = cash
    return out


# ---------------------------------------------------------------- variants: what each cap and setting costs


def variants(rules: Guardrails, params: StrategyParams) -> list[tuple[str, Guardrails, StrategyParams]]:
    """Today's rules and settings, then one change at a time (never written anywhere: what-ifs only)."""
    big = 10**6
    return [
        ("current rules and settings", rules, params),
        ("no cash_floor", rules.model_copy(update={"cash_floor": 0}), params),
        ("no hourly spend cap", rules.model_copy(update={"max_spend_per_game_hour": big}), params),
        (
            "no price caps",
            rules.model_copy(update={"max_price_common": big, "max_price_uncommon": big, "max_price_rare": big}),
            params,
        ),
        ("min_buy_surplus 4", rules, params.model_copy(update={"min_buy_surplus": 4})),
        ("min_buy_surplus 6", rules, params.model_copy(update={"min_buy_surplus": 6})),
        ("page_bonus_weight 0", rules, params.model_copy(update={"page_bonus_weight": 0.0})),
    ]


def render(results: Sequence[Result], title: str) -> str:
    lines = [
        f"# {title}",
        "",
        "| run | buys | spent | surplus (one more copy) | surplus (taker's value) | cash start → end | blocked by "
        "| lost the slot |",
        "|---|---:|---:|---:|---:|---|---|---:|",
    ]
    for r in results:
        blocked = ", ".join(f"{g} {n}" for g, n in r.blocked.items()) or "-"
        lines.append(
            f"| {r.label} | {len(r.bought)} | {r.spent} | {r.surplus:+g} | {r.strategy_surplus:+g} "
            f"| {r.cash_start} → {r.cash_end} | {blocked} | {r.quota} |"
        )
    return "\n".join(lines) + "\n"
