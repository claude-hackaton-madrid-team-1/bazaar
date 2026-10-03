"""The maker's dealer sell desk: sell spare copies TO dealers, one thread at a time, one move per maker tick.

Built on `agents/dealer_sell.py` (#179): its `decide_sell` decides every move (asks step down, never below our
floor, never a deal at her opening bid, her final taken only above both), `latest_dealer_bid` / `see_bids` read
only structured offers that want exactly our copy and give only cash. `negotiate_sell` (the `dealer sell` CLI)
blocks for a whole thread; an agent tick cannot, so `SellTalk.step()` plays ONE tick of the same policy: open,
read, decide, one send. `SellDesk` runs inside the maker behind `dealer_sell_enabled` (GUARDRAILS.md, false):

  - which dealers buy what, and what they paid, come from data (`dealer_sell_data`: `traders` and
    `dealer_curves` on the shared Postgres; `/api/dealers` and the feed window without it), never constants;
  - a thread opens only with a dealer we have unlocked and have NO open thread with (`/api/me/threads`: the
    taker's buy threads share the one open conversation per dealer);
  - candidates are spare copies (`strategy._spare`), never a protected card, never a copy an open offer of ours
    lists (locked) or the maker wants to list, never the last free copy of a page card;
  - every accept passes the offer inspector (`accept_gate.dealer_gate`), `guardrails.check` and the shared
    ledger's accept slot; every send is a decision row plus an execution row; a deal is a `dealer_sell` row;
  - a dealer may take our ask with words only ("Trato hecho"): the thread's `deal` status says so, and so does
    our copy leaving `/me` while the thread is ours (a settlement carries no thread id).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bazaar_agent.agents.dealer import Move, settled_price, with_name
from bazaar_agent.agents.dealer_sell import (
    SELL_WORDS,
    AskPlan,
    SellNegotiation,
    ask_schedule,
    decide_sell,
    latest_dealer_bid,
    only_copy,
    see_bids,
    sell_topic,
)
from bazaar_agent.agents.dealer_sell_data import REFRESH_TICKS, Fill, SellMarket, market_from_feed
from bazaar_agent.agents.strategy_gate import DEALER_SELL, StrategyGate
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.news import EVENTS_FILE, MarketEvent, active_signals, load_market_events
from bazaar_agent.persona_model import Persona, parse_personas, sell_weight

UNKNOWN_FILL_START = 2.0  # no bid seen from this dealer for this rarity: open at this × the floor
WALK_WORDS = "Muchas gracias por su tiempo, {n}. Otro día seguro que nos entendemos."


def plan_for(fill: Fill | None, floor: int, rules: Guardrails) -> AskPlan:
    """Open above the highest bid this dealer gave any team for this rarity (`dealer_sell_open_above_top` ×
    it), but at most `dealer_sell_open_max_over_median` × its median fill (one outlier deal must not open us
    far above what it ever pays and burn ticks stepping down), never below our floor; then reach its median
    fill in about `dealer_sell_rounds` steps. Nothing seen: open at twice the floor."""
    rounds = rules.dealer_sell_rounds
    if fill is None:
        start = max(floor + 2, math.ceil(UNKNOWN_FILL_START * floor))
        return AskPlan(start, max(1, (start - floor) // rounds), floor)
    above_top = math.ceil(fill.top * rules.dealer_sell_open_above_top - 1e-9)
    cap = math.ceil(fill.typical * rules.dealer_sell_open_max_over_median - 1e-9)
    start = max(floor, min(above_top, cap))
    target = max(floor, fill.typical)
    return AskPlan(start, max(1, round((start - target) / rounds)), floor)


def sell_floor(value: float, your_value: float, min_surplus: float, rules: Guardrails) -> int:
    """The lowest price we ever sell this copy at: what we lose (your_value + the page bonus at stake) +
    `min_surplus` (the desk passes `dealer_sell_min_surplus`), and never below `your_value ×
    sell_min_value_ratio`. With `min_surplus` ≥ 0 it is never below what we lose."""
    return max(
        1,
        math.ceil(value + min_surplus - 1e-9),
        math.ceil(your_value * rules.sell_min_value_ratio - 1e-9),
    )


def words(step: int, price: int, name: str) -> str:
    """#179's kind templates, addressed by the dealer's own name (`traders.name`)."""
    return with_name(SELL_WORDS[step % len(SELL_WORDS)], price, name or "")


@dataclass(frozen=True)
class Candidate:
    asset_id: int
    ref: str
    rarity: str
    value: float  # what we lose by selling this copy: your_value + the page bonus at stake
    your_value: float
    floor: int
    dealer: str
    expected: float  # the dealer's typical fill for this rarity
    name: str = ""
    fill: Fill | None = None

    @property
    def gain(self) -> float:
        return round(self.expected - self.value, 1)


def candidates(
    me: dict[str, Any],
    catalog: dict[str, Any],
    market: SellMarket,
    params: Any,
    rules: Guardrails,
    *,
    busy: Iterable[str] = (),
    locked: Iterable[int] = (),
    events: Sequence[Any] = (),
    personas: Mapping[str, Persona] | None = None,
    fever: Mapping[str, Mapping[str, float]] | None = None,
) -> list[Candidate]:
    """Spare copies an unlocked dealer we have no open thread with buys, whose floor that dealer has been seen
    to bid for the rarity. Best first: its typical fill minus what we lose.

    `personas` (the persona model, `/api/dealers`): a dealer whose published menu does not buy the copy's rarity
    in its set is dropped (never a common to Pilar), and the rank uses its typical fill × `sell_weight` (a
    favourite set, an official `fever`: dealer -> set -> pct over book). Ranking only: floors, expected fills
    and every ask stay as they are. Without personas: today's candidates and order."""
    from bazaar_agent.strategy import _spare, bonus_at_stake, build_market

    m = build_market(me, catalog, events, [])
    busy_ids, locked_ids = set(busy), set(locked)
    cards = [a for a in me.get("assets") or [] if a.get("kind") == "card" and isinstance(a.get("id"), int)]
    free: dict[str, int] = {}
    for a in cards:
        if a["id"] not in locked_ids:
            free[str(a.get("ref"))] = free.get(str(a.get("ref")), 0) + 1
    out: list[Candidate] = []
    weights: dict[tuple[int, str], float] = {}
    seen: set[tuple[str, str]] = set()
    for a in sorted(cards, key=lambda a: float(a.get("your_value") or 0)):
        ref, value = str(a.get("ref")), a.get("your_value")
        card = m.cards.get(ref)
        if card is None or not isinstance(value, int | float) or isinstance(value, bool) or a["id"] in locked_ids:
            continue
        if card.set_code not in m.released or rules.protects(ref, card.rarity, m.held.get(ref, 0)):
            continue
        if not _spare(m, card) or only_copy(ref, card.rarity, free.get(ref, 0)):
            continue
        ours = float(value) + bonus_at_stake(m, card, params)
        floor = sell_floor(ours, float(value), rules.dealer_sell_min_surplus, rules)
        for t in market.buyers(me, card.rarity, card.set_code, m.released):
            fill = market.fills.get((t.id, card.rarity))
            if fill is None or t.id in busy_ids or (ref, t.id) in seen or floor > fill.top:
                continue  # never seen bid that high: a thread would only walk
            persona = personas.get(t.id) if personas else None
            if persona is not None:
                weight = sell_weight(persona, card.rarity, card.set_code, (fever or {}).get(t.id, {}))
                if weight <= 0:
                    continue  # its menu does not buy this rarity in this set
                weights[(int(a["id"]), t.id)] = weight
            seen.add((ref, t.id))
            cand = Candidate(int(a["id"]), ref, card.rarity, round(ours, 1), float(value), floor, t.id, fill.expected)
            out.append(Candidate(**{**cand.__dict__, "name": t.greeting, "fill": fill}))
    if not personas:
        return sorted(out, key=lambda c: (-c.gain, c.asset_id))
    return sorted(out, key=lambda c: (-weighted_gain(c, weights.get((c.asset_id, c.dealer), 1.0)), c.asset_id))


def weighted_gain(c: Candidate, weight: float) -> float:
    """The rank of a candidate under the persona model: the dealer's typical fill × its weight, minus our loss."""
    return round(c.expected * weight - c.value, 1)


def fever_by_dealer(events: Iterable[MarketEvent], t_hours: float) -> dict[str, dict[str, float]]:
    """dealer -> set -> pct over book, from moves already filtered by `news.active_signals` (official, in force at
    `t_hours`). A move with no persona or a fall in price (pct <= 0) never raises a rank; the strongest wins."""
    out: dict[str, dict[str, float]] = {}
    for ev in events:
        if not ev.persona or ev.pct <= 0:
            continue
        if ev.start_hours is not None and t_hours < ev.start_hours:
            continue
        sets = out.setdefault(ev.persona, {})
        sets[ev.set_code] = max(ev.pct, sets.get(ev.set_code, 0.0))
    return out


def read_fever(rules: Guardrails, path: Path | None, t_hours: float) -> dict[str, dict[str, float]] | None:
    """The fevers in force from the news sentinel's `market_events.json` (None: no path known). Nothing while
    `news_signals_enabled` is off, and never a rumour (`news.active_signals`)."""
    if path is None:
        return None
    return fever_by_dealer(active_signals(rules, load_market_events(path), t_hours), t_hours)


# ---------------------------------------------------------------- one thread, one tick at a time


@dataclass
class SellHooks:
    """What a sell thread needs from its caller. `guard(kind, price)`: a refusal or None (may raise
    `dealer.Hold`); `reserve(price, clock)`: the team's accept slot (True, False taken, None unreadable);
    `inspect(thread, offer_id, price)`: the accept gate (a refusal or None); `on_deal(price, clock)` books it."""

    rec: Any  # agents.runtime.Recorder: decision + execution rows
    kill_switch: Callable[[], Sequence[str]]
    guard: Callable[[str, int], str | None]
    reserve: Callable[[int, Any], bool | None]
    inspect: Callable[[dict[str, Any], int, int], str | None]
    on_deal: Callable[[int, Any], None]
    log: Callable[[str], None]


ENDED = ("deal", "walked", "closed", "refused", "timeout", "accepted_pending", "expired")


@dataclass
class SellTalk:
    """One sell thread with one dealer for one copy. `status`: new → open (→ accepted) → deal | walked | ..."""

    client: Any
    cand: Candidate
    plan: AskPlan
    hooks: SellHooks
    max_ticks: int = 14
    final_share: float = 0.0  # `dealer_sell_final_min_first_ask_share`: a final must reach this × our first ask
    tid: int | None = None
    status: str = "new"
    ticks: int = 0
    accepted_ticks: int = 0
    price: int | None = None
    neg: SellNegotiation = field(init=False)

    def __post_init__(self) -> None:
        self.neg = SellNegotiation(self.plan)

    @property
    def done(self) -> bool:
        return self.status in ENDED

    def _decide(self, clock: Any, move: str, line: str, reason: str, **extra: Any) -> int:
        c = self.cand
        inputs = {"dealer": c.dealer, "asset_id": c.asset_id, "ref": c.ref, "rarity": c.rarity, "floor": c.floor}
        inputs |= {"start": self.plan.start, "asks": list(self.neg.asks)}
        return int(
            self.hooks.rec.decide(
                clock.tick,
                "dealer_sell",
                line,
                inputs=inputs,
                reason=reason,
                guardrail="allowed",
                chosen=True,
                status="approved",
                thread_id=self.tid,
                move={move: extra},
            )
        )

    def step(self, clock: Any, me: Mapping[str, Any] | None = None) -> str:
        """Play one tick. `me` (this tick's /api/me): our copy gone from it while the thread is ours means the
        dealer took our ask (a settlement carries no thread id). A game refusal is logged, never raised."""
        from bazaar_agent.agents.dealer import Hold
        from bazaar_agent.sdk import BazaarError

        if self.done:
            return self.status
        if stops := self.hooks.kill_switch():
            self.hooks.log(f"tick {clock.tick} dealer_sell: kill switch on: holding ({'; '.join(stops)})")
            return self.status
        try:
            if self.tid is None:
                self._open(clock)
                return self.status
            thread = self.client.thread(self.tid)
            if self._ended(thread, clock) or self._sold_by_me(me, clock):
                return self.status
            self.ticks += 1
            if self.status == "accepted":
                self.accepted_ticks += 1
                if self.accepted_ticks > 2:
                    self.status = "accepted_pending"
                    self.hooks.log(f"thread {self.tid}: accepted, not settled after 2 ticks: check it by hand")
                return self.status
            self._move(thread, clock)
        except Hold as e:
            self.hooks.log(f"tick {clock.tick} dealer_sell: HOLD: {e}")
        except BazaarError as e:
            self.hooks.log(f"tick {clock.tick} dealer_sell: refused {e.code} ({str(e.message)[:80]})")
        return self.status

    def _open(self, clock: Any) -> None:
        c = self.cand
        denied = self.hooks.guard("dealer_sell", self.plan.start)
        if denied:
            self.status = "refused"
            self.hooks.log(f"tick {clock.tick} dealer_sell: guardrails refuse to open: {denied}")
            return
        topic = sell_topic(c.asset_id)
        line = f"open sell thread with {c.dealer}: {c.ref} #{c.asset_id}, asks {ask_schedule(self.plan)}"
        why = f"spare {c.rarity}; {c.dealer} typically pays {c.expected:g}, we lose {c.value:g}"
        did = self._decide(clock, "open_thread", line, why, dealer=c.dealer, topic=topic)
        request = {"with": c.dealer, "topic": topic}
        body = self.hooks.rec.send(
            did, clock.tick, "open_thread", request, lambda: self.client.open_thread(c.dealer, topic=topic)
        )
        if body is None or not isinstance(body.get("id"), int):
            self.status = "refused"
            return
        self.tid, self.status = int(body["id"]), "open"

    def _deal(self, price: int | None, clock: Any, how: str) -> None:
        self.status = "deal"
        self.price = price or self.price or (self.neg.asks[-1] if self.neg.asks else None)
        if self.price is not None:
            self.hooks.on_deal(int(self.price), clock)
        self.hooks.log(f"tick {clock.tick} dealer_sell: thread {self.tid} deal at {self.price} P ({how})")

    def _ended(self, thread: Mapping[str, Any], clock: Any) -> bool:
        status = str(thread.get("status") or "open")
        if status == "open":
            return False
        if status == "deal":
            self._deal(settled_price(dict(thread)), clock, "the thread says deal")
        else:
            self.status = status
            self.hooks.log(f"tick {clock.tick} dealer_sell: thread {self.tid} {status}")
        return True

    def _sold_by_me(self, me: Mapping[str, Any] | None, clock: Any) -> bool:
        """Our copy left /me while our thread is open (or our accept is in): she took our ask in words only."""
        if me is None or (not self.neg.asks and self.status != "accepted"):
            return False
        ids = {a.get("id") for a in me.get("assets") or [] if isinstance(a, Mapping)}
        if self.cand.asset_id in ids:
            return False
        self._deal(None, clock, "our copy left /me")
        return True

    def _move(self, thread: dict[str, Any], clock: Any) -> None:
        c, tid = self.cand, self.tid
        bid, offer_id, final = latest_dealer_bid(thread, c.dealer, c.asset_id)
        see_bids(self.neg, thread, c.dealer, c.asset_id)
        final_min = math.ceil(self.final_share * self.neg.asks[0] - 1e-9) if self.neg.asks else 0
        move = decide_sell(self.neg, bid, offer_id, final, final_min)
        if self.ticks > self.max_ticks and move.kind != "accept":
            move = Move("walk", reason=f"no deal after {self.max_ticks} ticks")
        verb = "ask" if move.kind == "bid" else move.kind
        self.hooks.log(
            f"tick {clock.tick} dealer_sell: {c.dealer} bids {bid}{' FINAL' if final else ''} → {verb} "
            f"{move.price or ''} ({move.reason})"
        )
        if move.kind == "accept" and move.price is not None and move.offer_id is not None:
            nxt = self._accept(thread, clock, move.price, move.offer_id, move.reason)
            if nxt is None:
                return
            move = nxt
        if move.kind == "bid" and move.price is not None:
            denied = self.hooks.guard("dealer_sell", move.price)
            if not denied:
                self._ask(clock, move.price, move.reason)
                return
            move = Move("walk", reason=f"guardrail: {denied}")
        if move.kind == "walk":
            assert tid is not None
            self._walk(clock, move.reason)

    def _accept(self, thread: dict[str, Any], clock: Any, price: int, offer_id: int, why: str) -> Move | None:
        """Inspector, guardrails, the team's accept slot, then the accept. Returns a walk when a guardrail
        denies, else None (sent, or nothing this tick: the inspector refused, or the slot is taken/unreadable)."""
        c = self.cand
        refused = self.hooks.inspect(thread, offer_id, price)
        if refused:
            self.hooks.log(f"tick {clock.tick} dealer_sell: INSPECTOR refused offer {offer_id}: {refused}")
            return None
        denied = self.hooks.guard("accept_sell", price)
        if denied:
            return Move("walk", reason=f"guardrail: {denied}")
        slot = self.hooks.reserve(price, clock)
        if not slot:
            self.hooks.log(f"tick {clock.tick} dealer_sell: accept slot {'unreadable' if slot is None else 'taken'}")
            return None
        did = self._decide(clock, "accept", f"accept {c.dealer}'s {price} for {c.ref}", why, offer=offer_id)
        body = self.hooks.rec.send(did, clock.tick, "accept", {"offer": offer_id}, lambda: self.client.accept(offer_id))
        if body is not None:
            self.status, self.price = "accepted", price
        return None

    def _ask(self, clock: Any, price: int, why: str) -> None:
        c, tid = self.cand, self.tid
        text = words(len(self.neg.asks), price, c.name)
        did = self._decide(clock, "say", f"ask {c.dealer} {price} for {c.ref}", why, price=price)
        request = {"thread": tid, "price": price}
        if self.hooks.rec.send(did, clock.tick, "say", request, lambda: self.client.say(tid, text, price=price)):
            self.neg.asks.append(price)

    def _walk(self, clock: Any, why: str) -> None:
        c, tid = self.cand, self.tid
        name = c.name or ""
        did = self._decide(clock, "close_thread", f"walk from {c.dealer} on {c.ref}", why)
        # A kind last word first (dealers remember kindness), then the close: the walk itself.
        bye = with_name(WALK_WORDS, 0, name)
        self.hooks.rec.send(did, clock.tick, "say", {"thread": tid}, lambda: self.client.say(tid, bye))
        answer = self.hooks.rec.send(
            did, clock.tick, "close_thread", {"thread": tid}, lambda: self.client.close_thread(tid)
        )
        if answer is None:
            return
        if answer.get("status") == "deal":  # a deal landed before our close: book it
            self._ended(self.client.thread(tid), clock)
        else:
            self.status = "walked"


# ---------------------------------------------------------------- the maker's desk


class SellDesk:
    """Inside the maker, behind `dealer_sell_enabled`: at most one sell thread at a time, one step per tick,
    at most `dealer_sell_max_per_game_hour` openings per game hour and each dealer's own
    `deals_per_team_per_hour` (our sells, this process). After each thread the dealer stays free for
    `dealer_sell_dealer_gap_ticks`, and a card that walked with a dealer waits `dealer_sell_retry_game_hours`.
    The dealers and their sell curves come from `load` (Postgres), read again every `REFRESH_TICKS`; when it
    answers None, from `/api/dealers` and the feed window. Dry run: one WOULD-open decision per copy and
    dealer, nothing sent."""

    def __init__(
        self,
        team: Any,
        rules: Guardrails,
        rec: Any,
        live: bool,
        log: Callable[[str], None],
        hooks: Callable[[Candidate], SellHooks],
        load: Callable[[Any], SellMarket | None] | None = None,
        gate: StrategyGate | None = None,
    ) -> None:
        self.team, self.rules, self.rec, self.live, self.log = team, rules, rec, live, log
        self.hooks, self.load = hooks, load
        self.gate = gate  # Jev `dealer_sell_duplicates_worth_it` (SG1): None = no Jev, no new sell thread
        self.talk: SellTalk | None = None
        self.opened_at: list[tuple[float, str]] = []  # (game hour, dealer) of our openings
        self.market: SellMarket | None = None
        self._market_tick: int | None = None
        # The news sentinel writes `<data_dir>/agents/market_events.json`, the decisions log's own directory.
        events_dir = getattr(getattr(rec, "decisions", None), "dir", None)
        self.events_path: Path | None = Path(events_dir) / EVENTS_FILE if events_dir is not None else None
        self._said: set[tuple[int, str]] = set()
        self.ended_at: dict[str, int] = {}  # dealer → the tick our last sell thread with it ended
        self.walked: dict[tuple[str, str], tuple[float, int]] = {}  # (dealer, card) → (game hour, floor): no deal

    def _ended(self, talk: SellTalk, clock: Any) -> None:
        """Book a finished thread: the dealer stays free for `dealer_sell_dealer_gap_ticks` (the taker may buy
        from it: one open conversation per dealer per team), and a no-deal blocks that card (any copy of it)
        with that dealer for `dealer_sell_retry_game_hours` (its next thread would walk the same way)."""
        c = talk.cand
        self.ended_at[c.dealer] = int(clock.tick)
        if talk.status != "deal":
            self.walked[(c.dealer, c.ref)] = (float(clock.t_hours), c.floor)

    def _retry_ok(self, c: Candidate, clock: Any) -> bool:
        """A card that walked with this dealer comes back after `dealer_sell_retry_game_hours`, or sooner if
        our floor for it dropped below the one that walked."""
        seen = self.walked.get((c.dealer, c.ref))
        if seen is None:
            return True
        when, floor = seen
        return clock.t_hours - when >= self.rules.dealer_sell_retry_game_hours or c.floor < floor

    def gate_on(self, snap: Any) -> bool:
        """A new sell thread only on Jev's decided yes (SG1), asked again every `strategy_jev_refresh_ticks`;
        a thread already open plays on whatever the gate says."""
        if self.gate is None:
            return False
        return self.gate.allows(DEALER_SELL, int(snap.clock.tick), lambda: self.gate_state(snap))

    def gate_state(self, snap: Any) -> dict[str, Any]:
        """What Jev reads: our spare copies (a page keeps one) with `your_value`, cash, and the last no-deals."""
        me = snap.me or {}
        copies: dict[str, list[float]] = {}
        for a in me.get("assets") or []:
            if isinstance(a, Mapping) and a.get("kind") == "card" and a.get("ref"):
                copies.setdefault(str(a["ref"]), []).append(float(a.get("your_value") or 0))
        spare = [{"card": c, "copies": len(v), "your_value": min(v)} for c, v in sorted(copies.items()) if len(v) > 1]
        walked = [{"dealer": d, "card": c, "game_hour": round(h, 2)} for (d, c), (h, _) in self.walked.items()]
        return {
            "cash": me.get("cash"),
            "cash_floor": self.rules.cash_floor,
            "duplicates": spare,
            "rules": {
                "min_surplus": self.rules.dealer_sell_min_surplus,
                "final_min_first_ask_share": self.rules.dealer_sell_final_min_first_ask_share,
                "retry_game_hours": self.rules.dealer_sell_retry_game_hours,
                "dealer_gap_ticks": self.rules.dealer_sell_dealer_gap_ticks,
                "taker_window_ticks": self.rules.dealer_sell_taker_window_ticks,
            },
            "history": {"no_deals_this_process": walked, "sells_opened_last_hour": len(self.opened_at)},
        }

    def taker_wants(self, tick: int) -> set[str]:
        """Dealers the taker wanted in the last `dealer_sell_taker_window_ticks` (#200: its buys come first)."""
        window = self.rules.dealer_sell_taker_window_ticks
        log = getattr(self.rec, "decisions", None)
        if window <= 0 or log is None or not hasattr(log, "wanted_dealers"):
            return set()
        return set(log.wanted_dealers("taker", tick - window))

    def market_for(self, snap: Any) -> SellMarket:
        tick = snap.clock.tick
        if self.market is None or self._market_tick is None or tick - self._market_tick >= REFRESH_TICKS:
            loaded = self.load(snap) if self.load is not None else None
            self.market = loaded or market_from_feed(snap.dealers, snap.events, snap.me)
            self._market_tick = tick
        return self.market

    def persona_inputs(self, snap: Any) -> tuple[dict[str, Persona] | None, dict[str, dict[str, float]] | None]:
        """The personas from the snapshot's `/api/dealers` (no new request) and the official fevers in force,
        behind `persona_model_enabled`; (None, None) when it is off."""
        if not getattr(self.rules, "persona_model_enabled", True):
            return None, None
        personas = parse_personas(getattr(snap, "dealers", None) or [])
        return personas or None, read_fever(self.rules, self.events_path, snap.clock.t_hours)

    def on_tick(self, snap: Any, params: Any, locked: Iterable[int]) -> None:
        if not self.rules.dealer_sell_enabled:
            return
        clock = snap.clock
        if self.talk is not None:
            self.talk.step(clock, snap.me)
            if self.talk.done:
                c = self.talk.cand
                self.log(f"tick {clock.tick} dealer_sell: {c.ref} with {c.dealer}: {self.talk.status}")
                self._ended(self.talk, clock)
                self.talk = None
            return
        self.opened_at = [(h, d) for h, d in self.opened_at if h > clock.t_hours - 1.0]
        if len(self.opened_at) >= self.rules.dealer_sell_max_per_game_hour:
            return
        if not self.gate_on(snap):
            return
        market = self.market_for(snap)
        threads = [t for t in self.team.my_threads("open").get("threads") or [] if isinstance(t, dict)]
        busy = {str(t.get("with")) for t in threads}  # one open conversation per dealer, shared with the taker
        busy |= self.taker_wants(clock.tick)
        for t in market.traders:
            if t.deals_per_hour is not None and sum(d == t.id for _, d in self.opened_at) >= t.deals_per_hour:
                busy.add(t.id)
        gap = self.rules.dealer_sell_dealer_gap_ticks
        busy |= {d for d, tick in self.ended_at.items() if clock.tick - tick < gap}  # the dealer's turn for others
        try:  # a hostile persona never costs the sell desk its tick: today's ranking then
            personas, fever = self.persona_inputs(snap)
        except Exception:  # noqa: BLE001
            personas, fever = None, None
        found = candidates(
            snap.me,
            snap.catalog,
            market,
            params,
            self.rules,
            busy=busy,
            locked=locked,
            events=snap.events,
            personas=personas,
            fever=fever,
        )
        found = [c for c in found if self._retry_ok(c, clock)]
        if not found:
            return
        c = found[0]
        plan = plan_for(c.fill, c.floor, self.rules)
        if not self.live:
            if (c.asset_id, c.dealer) not in self._said:
                self._said.add((c.asset_id, c.dealer))
                self.rec.decide(
                    clock.tick,
                    "dealer_sell",
                    f"open sell thread with {c.dealer}: {c.ref} #{c.asset_id}, asks {ask_schedule(plan)}",
                    inputs={"dealer": c.dealer, "asset_id": c.asset_id, "ref": c.ref, "floor": c.floor},
                    reason=f"spare {c.rarity}; {c.dealer} typically pays {c.expected:g}, we lose {c.value:g} "
                    f"({market.source})",
                    guardrail="allowed",
                    chosen=True,
                    status="approved",
                    move={"open_thread": {"dealer": c.dealer, "topic": sell_topic(c.asset_id)}},
                )
            return
        self.talk = SellTalk(
            self.team,
            c,
            plan,
            self.hooks(c),
            self.rules.dealer_max_ticks_per_thread,
            self.rules.dealer_sell_final_min_first_ask_share,
        )
        self.opened_at.append((clock.t_hours, c.dealer))
        self.talk.step(clock, snap.me)
        if self.talk.done:
            self._ended(self.talk, clock)
            self.talk = None


def standard_hooks(
    cand: Candidate,
    *,
    rules: Guardrails,
    rec: Any,
    ledger: Any,
    context: Callable[[], Any],
    catalog: dict[str, Any],
    log: Callable[[str], None],
) -> SellHooks:
    """`guardrails.check` on a fresh context (the kill switch first), the shared ledger's accept slot, the
    offer inspector's accept gate (S1), and a `dealer_sell` row for each deal. A shared ledger that cannot
    answer holds the tick (`dealer.Hold`): no write without it."""
    from dataclasses import replace

    from bazaar_agent.agents.accept_gate import dealer_gate
    from bazaar_agent.agents.dealer import Hold
    from bazaar_agent.agents.inspector import CardIndex
    from bazaar_agent.guardrails import Action, action_kind, check, kill_switch
    from bazaar_agent.ledger_pg import LedgerUnavailable

    cards = CardIndex.from_catalog(catalog)
    topic = sell_topic(cand.asset_id)

    def guard(kind: str, price: int) -> str | None:
        try:  # the accept quota is no reason to walk: `reserve` claims it atomically on the tick of the accept
            ctx = replace(context(), accepts_this_tick=0)
        except LedgerUnavailable as e:
            raise Hold(f"{e}; no write without the shared ledger (fail closed)") from None
        action = Action(
            action_kind(kind), cand.ref, cand.rarity, price, your_value=cand.your_value, scope="dealer_sell"
        )
        verdict = check(action, ctx, rules)
        return None if verdict.allowed else "; ".join(verdict.violations)

    def reserve(price: int, clock: Any) -> bool | None:
        limit = min(rules.max_accepts_per_tick, clock.limits.accepts_per_team_per_tick)
        try:
            return bool(ledger.reserve_accept(clock.tick, clock.t_hours, price, cand.ref, limit))
        except LedgerUnavailable:
            return None

    def inspect(thread: dict[str, Any], offer_id: int, price: int) -> str | None:
        if not rules.inspect_accepts:
            return None  # the structure check (`sell_offer_problem`) already ran on every bid we read
        gate = dealer_gate(thread, cand.dealer, offer_id, price, topic, cards)
        return None if gate.allowed else f"{gate.verdict}: {gate.reason}"

    def on_deal(price: int, clock: Any) -> None:
        rec.decide(
            clock.tick,
            "dealer_sell",
            f"SOLD {cand.ref} #{cand.asset_id} to {cand.dealer} at {price} P (floor {cand.floor})",
            inputs={"dealer": cand.dealer, "asset_id": cand.asset_id, "ref": cand.ref, "floor": cand.floor},
            reason=f"deal at {price}; we lose {cand.value:g}",
            guardrail="allowed",
            chosen=False,
            status="done",
            move={"deal": {"price": price, "dealer": cand.dealer, "asset_id": cand.asset_id}},
        )

    return SellHooks(rec, lambda: kill_switch(rules), guard, reserve, inspect, on_deal, log)
