"""TAKER: every tick, take what is cheaper than its worth to us — standing asks and dealer offers.

Per tick, album first (`/api/me`), then:
  (a) scan every venue board (El Rastro and the team venues we may trade on) for standing ASKS of
      missing page cards whose total cost (ask + the venue fee the accepting side pays) is below the
      card's value to us (strategy: book × affinity + page bonus share) by at least `min_buy_surplus`;
  (b) keep up to `max_dealer_threads` dealer conversations (one per dealer) for the strategy's top
      dealer buys, one move per tick each (`desk.py`), never blocking on one thread.
Accepts from (a) and (b) compete for the team's accept quota (`accepts_per_team_per_tick`, shared
across machines through the ledger): finals first, then the biggest surplus. Jev's
`offer_is_worth_accepting` is advisory: a decided `no` vetoes a board accept, a decided `yes` may
accept a dealer's ask early, and neither ever goes above a limit. Every accept, bid, walk and cancel
passes `guardrails.check()` with the live context, which also counts what this tick already committed
(an accept's cash, a new bid in place of its thread's old one). With `max_counterparty_share` on (#14) a
board accept is refused when its maker (the real team id from the feed's `offer.listed`; a pseudonym the
feed never named is refused) would pass its share of our team-to-team volume. While the kill switch is on the taker
HOLDS: it reads, sends nothing (no opens, accepts, bids, walks or cancels), and its dealer threads stay
open and resume when the switch goes off. Dry run (the default) sends nothing and logs WOULD-moves.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from bazaar_agent.agents.dealer import Move, Negotiation, WordsFn, apply_advice, bid_words, template_words
from bazaar_agent.agents.desk import (
    Conversation,
    DeskMove,
    Opening,
    meet_the_ask,
    openings,
    plan_conversation,
    topic_for,
)
from bazaar_agent.agents.market import (
    BoardOffer,
    OpenOffer,
    Venue,
    board_offers,
    our_open_offers,
    standing_at,
    tradable_venues,
)
from bazaar_agent.agents.runtime import (
    JevAdvice,
    JevFn,
    MarketFeed,
    Recorder,
    Snapshot,
    TickWindow,
    accept_limit,
    guard_context,
    no_jev,
    read_snapshot,
    window_for,
)
from bazaar_agent.agents.seller import offers_in, open_commitments, trade_book
from bazaar_agent.agents.words import WordsRequest
from bazaar_agent.arb import Crossing, best_per_ask, crossings, dup_buys, leg_proceeds, next_copy_values
from bazaar_agent.decisions import DecisionLog, Status
from bazaar_agent.guardrails import (
    ARB_TAG,
    Action,
    ArbRow,
    Context,
    Guardrails,
    LedgerStore,
    card_assets,
    check,
    dup_item,
    kill_switch,
    refund_row,
)
from bazaar_agent.intel import book_values, listed_makers, settled_volume
from bazaar_agent.ledger_pg import LedgerUnavailable
from bazaar_agent.pack_gate import PackJudge, gate_packs
from bazaar_agent.sdk import BazaarError
from bazaar_agent.strategy import Market, PackSlots, Playbook, StrategyParams, build_market, build_playbook, buy_case
from bazaar_agent.strategy import guarded as guarded_playbook
from bazaar_agent.ticks import Clock, action_budget_s


@dataclass(frozen=True)
class TakerConfig:
    max_dealer_threads: int = 3  # dealer conversations at once (one per dealer; the team cap is 6 threads)
    jev_min_budget_s: float = 4.0  # ask Jev only with this much of the tick left (a call takes ~0.3 s, max 3 s)
    max_jev_calls_per_tick: int = 3
    duel_grace_s: float = 2.0  # duels own the first seconds of a tick (capped at 15 % of the tick)
    arb_exit_ticks: int = 3  # an arbitrage exit not taken this many ticks after the buy is dropped (maker sells)


# ---------------------------------------------------------------- (a) standing asks on the boards


@dataclass(frozen=True)
class AskCandidate:
    offer: BoardOffer
    rarity: str
    fee: int
    total: int  # ask + fee: what the card costs us
    value: float  # worth to us (strategy buy_case)
    surplus: float
    scarce: bool
    score: float
    reason: str
    replaces_bid: OpenOffer | None = None  # our own open bid for the same card, withdrawn after the accept
    held_buy: Literal["arb", "dup"] | None = None  # an exception to block_buying_held_cards (guardrails)
    exit: Crossing | None = None  # arbitrage: the standing bid we sell into next tick


def ask_candidates(
    m: Market,
    offers: Iterable[BoardOffer],
    venues: dict[str, Venue],
    params: StrategyParams,
    ours: set[int],
    own_bids: dict[str, OpenOffer],
) -> list[AskCandidate]:
    """Standing asks for missing page cards whose total cost (fee included) is below their value to us
    by at least `min_buy_surplus`. One per card (the cheapest), scarce cards first, then by score."""
    best: dict[str, AskCandidate] = {}
    for o in offers:
        card, venue = m.cards.get(o.ref), venues.get(o.venue)
        if o.side != "ask" or o.id in ours or card is None or venue is None:
            continue
        if not card.page or card.set_code not in m.released or m.held.get(o.ref, 0) > 0:
            continue
        case = buy_case(m, card, params)
        fee = venue.fee(o.price)
        total = o.price + fee
        surplus = case.value - total
        bid = own_bids.get(o.ref)
        if surplus < params.min_buy_surplus or (bid is not None and total >= bid.price):
            continue
        score = round(surplus * (1 + params.scarcity_weight * case.urgency), 2)
        reason = f"worth {case.worth}; ask {o.price} + fee {fee} on {o.venue} = {total}; {case.supply_note}"
        cand = AskCandidate(
            o, card.rarity, fee, total, round(case.value, 1), round(surplus, 1), case.supply.scarce, score, reason, bid
        )
        if o.ref not in best or cand.total < best[o.ref].total:
            best[o.ref] = cand
    return sorted(best.values(), key=lambda c: (not c.scarce, -c.score, c.offer.id))


# ---------------------------------------------------------------- held cards: duplicates and arbitrage


def dup_candidates(
    m: Market, offers: Iterable[BoardOffer], venues: dict[str, Venue], ours: set[int], rules: Guardrails
) -> list[AskCandidate]:
    """Asks for cards we hold whose next copy is worth `dup_min_surplus` more than the ask and its fee."""
    if not rules.dup_buy_enabled:
        return []
    out = []
    for d in dup_buys(offers, venues, m.held, next_copy_values(m), min_surplus=rules.dup_min_surplus, exclude=ours):
        card = m.cards[d.ask.ref]
        reason = f"duplicate #{d.held + 1}: one more copy worth {d.value:g}; ask {d.ask.price} + fee on {d.ask.venue}"
        out.append(
            AskCandidate(
                d.ask,
                card.rarity,
                d.cost - d.ask.price,
                d.cost,
                d.value,
                d.surplus,
                False,
                d.surplus,
                reason,
                None,
                "dup",
            )
        )
    return out


def arb_candidates(
    m: Market,
    offers: Iterable[BoardOffer],
    venues: dict[str, Venue],
    ours: set[int],
    rules: Guardrails,
    busy_refs: set[str],
    recent_parties: set[str],
    tick: int,
) -> list[AskCandidate]:
    """Asks we can resell at once into a standing bid on any venue: net after both fees at least
    `arb_min_net_spread`, makers known and different, neither of them in an arbitrage of ours lately (ring
    guard), an exit bid that still stands next tick, no exit already pending for the card, and a resale that
    clears the sell floor (`sell_min_value_ratio` × our value of the copy, so a page card worth more to us is
    kept instead)."""
    if not rules.arb_enabled:
        return []
    value = next_copy_values(m)
    found = crossings(offers, venues, exclude=ours, min_net=rules.arb_min_net_spread, value=value, known_makers=True)

    def eligible(c: Crossing) -> bool:
        ring = c.ask.maker in recent_parties or c.bid.maker in recent_parties
        if c.ref not in m.cards or c.value is None or c.ask.asset_id is None or c.ref in busy_refs or ring:
            return False
        return standing_at(c.bid, tick + 1) and c.proceeds >= c.value * rules.sell_min_value_ratio

    out = []
    for c in best_per_ask(c for c in found if eligible(c)):  # filter first: an ineligible pair never takes a bid
        card = m.cards[c.ref]
        reason = (
            f"arbitrage: buy {c.ask.price} + fee on {c.ask.venue} ({c.ask.maker}) = {c.cost}, sell into "
            f"{c.bid.price} − fee on {c.bid.venue} ({c.bid.maker}) = {c.proceeds}: net {c.net:+d}"
        )
        out.append(
            AskCandidate(
                c.ask,
                card.rarity,
                c.cost - c.ask.price,
                c.cost,
                c.proceeds,
                c.net,
                False,
                c.net,
                reason,
                None,
                "arb",
                c,
            )
        )
    return out


@dataclass(frozen=True)
class PendingExit:
    """An arbitrage buy waiting for its exact copy (`asset`) to reach /me, then for its exit: the next tick,
    with priority, that copy handed to `bid`."""

    ref: str
    asset: int
    bid: BoardOffer
    cost: int
    tick: int

    @property
    def net(self) -> int:
        return self.bid.price - self.cost  # before the exit venue's fee

    @classmethod
    def from_row(cls, row: ArbRow, cost: int, tick: int) -> PendingExit:
        bid = BoardOffer(row.bid, row.venue, row.buyer, "bid", row.ref, row.bid_price, None, None, None, None)
        return cls(row.ref, row.asset, bid, cost, tick)


def arb_row(c: Crossing) -> ArbRow:
    return ArbRow(c.ref, int(c.ask.asset_id or 0), c.bid.id, c.bid.venue, c.bid.price, c.ask.maker, c.bid.maker)


# ---------------------------------------------------------------- the accept quota


@dataclass(frozen=True)
class AcceptProposal:
    source: str  # "board" or the dealer id
    ref: str
    rarity: str
    offer_id: int
    price: int  # what we pay in all (board: ask + fee)
    value: float
    final: bool
    reason: str
    inputs: dict[str, Any]
    candidate: AskCandidate | None = None
    desk: DeskMove | None = None

    @property
    def surplus(self) -> float:
        return self.value - self.price

    @property
    def scarce(self) -> bool:
        return self.candidate is not None and self.candidate.scarce

    @property
    def score(self) -> float:
        return self.candidate.score if self.candidate is not None else self.surplus


def rank_accepts(proposals: Iterable[AcceptProposal]) -> list[AcceptProposal]:
    """A dealer's final offer first (it walks otherwise), then scarce cards, then the best score
    (surplus raised by urgency, as the strategy ranks)."""
    return sorted(proposals, key=lambda p: (not p.final, not p.scarce, -p.score, p.offer_id))


def board_proposal(c: AskCandidate) -> AcceptProposal:
    o = c.offer
    inputs = {
        "offer_id": o.id,
        "venue": o.venue,
        "maker": o.maker,
        "ref": o.ref,
        "rarity": c.rarity,
        "ask": o.price,
        "fee": c.fee,
        "total": c.total,
        "value": c.value,
        "surplus": c.surplus,
        "scarce": c.scarce,
        "score": c.score,
        "replaces_bid": c.replaces_bid.id if c.replaces_bid else None,
    }
    if c.held_buy is not None:
        inputs["held_buy"] = c.held_buy
    if c.exit is not None:
        x = c.exit
        inputs["exit"] = {"offer_id": x.bid.id, "venue": x.bid.venue, "maker": x.bid.maker, "bid": x.bid.price}
        inputs |= {"proceeds": x.proceeds, "net": x.net, "legs": x.legs}
    return AcceptProposal("board", o.ref, c.rarity, o.id, c.total, c.value, False, c.reason, inputs, candidate=c)


def desk_proposal(dm: DeskMove) -> AcceptProposal:
    conv, price = dm.conv, int(dm.move.price or 0)
    inputs = {
        "dealer": conv.dealer,
        "thread": conv.thread_id,
        "item": conv.item,
        "ask": price,
        "final": dm.final,
        "our_bids": list(conv.neg.bids),
        "max": conv.neg.plan.max_price,
        "value": conv.value,
    }
    reason = f"{dm.move.reason}; {conv.reason}"
    return AcceptProposal(
        conv.dealer,
        conv.item,
        conv.rarity,
        int(dm.move.offer_id or 0),
        price,
        conv.value,
        dm.final,
        reason,
        inputs,
        desk=dm,
    )


def offer_state(p: AcceptProposal, snap: Snapshot, ctx: Context, rules: Guardrails, slots_left: int) -> dict[str, Any]:
    """What Jev reads for `offer_is_worth_accepting`: the offer, the album around the card, cash, the tick."""
    set_code = p.ref.split("-", 1)[0] if "-" in p.ref else None
    page: dict[str, Any] = next(
        (pg for pg in (snap.me.get("album") or {}).get("pages") or [] if pg.get("set") == set_code), {}
    )
    return {
        "offer": {
            "item": p.ref,
            "rarity": p.rarity,
            "source": p.source,
            "total_cost": p.price,
            "final": p.final,
            "fee": (p.candidate.fee if p.candidate else 0),
        },
        "album": {
            "set": set_code,
            "page_have": page.get("have"),
            "page_of": page.get("of"),
            "we_hold_this_card": ctx.held.get(p.ref, 0) > 0,
            "value_to_us": p.value,
            "how_the_value_was_computed": p.reason,
        },
        "cash": ctx.cash,
        "cash_floor": rules.cash_floor,
        "cash_above_floor": max(0, ctx.cash - rules.cash_floor),
        "accept_slots_left_this_tick": slots_left,
        "tick": snap.clock.tick,
    }


def conversation_view(c: Conversation) -> dict[str, Any]:
    """One dealer thread as the status server shows it."""
    return {
        "dealer": c.dealer,
        "thread": c.thread_id,
        "item": c.item,
        "our_bids": list(c.neg.bids),
        "max": c.neg.plan.max_price,
        "value": c.value,
        "ticks": c.ticks,
        "opened_tick": c.opened_tick,
        "accepted_price": c.accepted_price,
    }


# ---------------------------------------------------------------- the loop


@dataclass
class _TickRun:
    snap: Snapshot
    window: TickWindow
    params: StrategyParams
    offers: list[dict[str, Any]]  # our open offers as the server knows them, updated as we act this tick
    mine: list[OpenOffer]
    started: float  # monotonic time the tick's work began (the clock was read just before)
    spent: int = 0  # dry run: this tick's board accepts, which only a live accept books in the ledger
    jev_calls: int = 0
    settled: dict[str, int] | None = None  # primas settled with each team; None: max_counterparty_share is off
    accepted: list[AcceptProposal] = field(default_factory=list)
    venues: dict[str, Venue] = field(default_factory=dict)  # the boards read this tick: venues and offers
    board: list[BoardOffer] = field(default_factory=list)
    dup_spent: int = 0  # dry run: this tick's duplicate buys (a live one is a `dup:` row in the ledger)
    exits_used: int = 0  # accepts this tick's arbitrage exits took


class Taker:
    def __init__(
        self,
        team: Any,
        public: Any,
        *,
        rules: Guardrails,
        params: Callable[[int], StrategyParams],
        ledger: LedgerStore,
        decisions: DecisionLog,
        feed: MarketFeed,
        live: bool,
        log: Callable[[str], None],
        jev: JevFn = no_jev,
        pack_judge: PackJudge | None = None,
        words_fn: WordsFn = template_words,
        config: TakerConfig | None = None,
        now: Callable[[], float] = time.monotonic,
        hub: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.team, self.public, self.rules, self.params = team, public, rules, params
        self.ledger, self.feed, self.live, self.log = ledger, feed, live, log
        self.jev, self.pack_judge, self.words_fn, self.now = jev, pack_judge, words_fn, now
        self.config = config or TakerConfig()
        self.sleep = sleep
        self.rec = Recorder("taker", decisions, live, log, hub)
        self.hub = hub  # agents.status.StatusHub: the read-only HTTP/WS view, when served
        self.convs: dict[str, Conversation] = {}  # dealer id -> the conversation we own
        self._dry_accepts: dict[int, int] = {}
        self.exits: dict[str, PendingExit] = {}  # card ref -> the arbitrage exit waiting for it
        self._arb_pairs: dict[frozenset[str], int] = {}  # dry run: the two makers of an arbitrage -> its tick
        self._exits_rebuilt = False  # after a restart, pending exits come back from the ledger's arb: rows

    # ------------------------------------------------------------ entry point (run_per_tick calls it)

    def on_tick(self, clock: Clock) -> None:
        window = window_for(clock, self.now(), self.now)
        self.rec.decisions.begin_tick(clock.tick)
        try:
            snap = read_snapshot(self.team, self.public, self.feed, clock)
            threads = [t for t in self.team.my_threads("open").get("threads") or [] if isinstance(t, dict)]
            self._tick(snap, threads, window)
        except BazaarError as e:
            self.log(f"tick {clock.tick} taker: read refused {e.code} ({e.message[:80]}); nothing sent")
        except LedgerUnavailable as e:
            self.log(f"tick {clock.tick} taker: {e}; no write this tick (fail closed)")

    def _tick(self, snap: Snapshot, threads: list[dict[str, Any]], window: TickWindow) -> None:
        clock = snap.clock
        if self.hub is not None:
            self.hub.tick(clock.tick, clock.t_hours, snap.us)
        offers = offers_in(snap.offers)
        mine, _ = our_open_offers(snap.offers, snap.us)
        run = _TickRun(snap, window, self.params(clock.tick), offers, mine, window.deadline - action_budget_s(clock))
        if self.rules.max_counterparty_share < 1:
            run.settled = settled_volume(snap.events, snap.us, book_values(snap.catalog))
        stops = kill_switch(self.rules)
        if stops:
            self._desk_moves(run, held=True)  # reads go on: a deal that settles during the hold is still booked
            if self.hub is not None:
                self.hub.view(threads=[conversation_view(c) for c in self.convs.values()])
            self.log(
                f"tick {clock.tick} taker: kill switch on: holding (no opens, accepts, bids, walks or cancels; "
                f"{len(self.convs)} dealer thread(s) stay open): {'; '.join(stops)}"
            )
            return
        if self.exits or self.rules.arb_enabled:  # a pending exit goes first, before any other read or move
            run.exits_used = self._exits(run, accept_limit(clock, self.rules))
        market = build_market(snap.me, snap.catalog, snap.events, snap.dealers)
        book = build_playbook(snap.me, snap.catalog, snap.events, snap.dealers, run.params, self.rules)
        self._open(run, book, threads)
        desk = self._desk_moves(run)
        proposals = [desk_proposal(dm) for dm, _ in desk if dm.move.kind == "accept"]
        proposals += [board_proposal(c) for c in self._board(run, market)]
        proposals += [board_proposal(c) for c in self._held_buys(run, market)]
        self._accept(run, proposals)
        self._converse(run, desk)
        if self.hub is not None:
            self.hub.view(threads=[conversation_view(c) for c in self.convs.values()])
        self.log(
            f"tick {clock.tick} taker: {len(proposals)} accept candidate(s), {len(run.accepted)} taken, "
            f"{len(self.convs)} dealer thread(s), {window.left():.1f} s left · {'LIVE' if self.live else 'dry run'}"
        )

    def _ctx(self, run: _TickRun, *, skip_thread: int | None = None, skip_offer: int | None = None) -> Context:
        """Live guardrail context; our open offers count (this tick's accepts and bids too, `_commit`),
        except the thread or bid this move replaces."""
        kept = [
            o
            for o in run.offers
            if (skip_thread is None or o.get("thread") != skip_thread)
            and (skip_offer is None or o.get("id") != skip_offer)
        ]
        ctx = guard_context(run.snap, self.ledger, self.rules, open_commitments(kept, run.snap.us))
        book = book_values(run.snap.catalog)
        trades = None if run.settled is None else trade_book(kept, run.snap.us, run.settled, book)
        assets = card_assets(run.snap.me)
        unseen = sum(x.cost for x in self.exits.values() if x.asset not in assets)
        return replace(
            ctx,
            spent_last_hour=ctx.spent_last_hour + run.spent,
            trades=trades,
            arb_inventory=ctx.arb_inventory + unseen,  # bought, not yet in /me: still inventory
            dup_spent_last_hour=ctx.dup_spent_last_hour + run.dup_spent,
        )

    def _commit(
        self,
        run: _TickRun,
        cash: int,
        item: str,
        thread: int | None,
        to: str | None = None,
        notional: int | None = None,
    ) -> None:
        """An accept or bid this tick (sent, would-be, or maybe landed): every later check this tick sees its
        cash go out and the card as ours, as for an open offer (`/me` was read before it). In a thread it
        replaces our earlier bid there and counts as spend until the deal settles (`committed_context`).
        A board accept counts toward its maker's share (`to`) at its price without the fee (`notional`)."""
        if thread is not None:
            run.offers = [o for o in run.offers if o.get("thread") != thread]
        give, want = {"cash": cash}, {"types": [item]}
        run.offers.append(
            {
                "id": -1,
                "status": "open",
                "maker": run.snap.us,
                "thread": thread,
                "give": give,
                "want": want,
                "to": to,
                "notional": notional,
            }
        )

    def _ask_jev(self, run: _TickRun, state: dict[str, Any]) -> JevAdvice:
        if run.jev_calls >= self.config.max_jev_calls_per_tick or run.window.left() < self.config.jev_min_budget_s:
            return JevAdvice("undecided", 0.0, reason="no tick budget for jev")
        run.jev_calls += 1
        return self.jev(state)

    # ------------------------------------------------------------ (a) boards

    def _board(self, run: _TickRun, market: Market) -> list[AskCandidate]:
        venues = {v.id: v for v in tradable_venues(run.snap.venues, run.snap.us)}
        offers: list[BoardOffer] = []
        for venue in venues.values():
            try:
                offers += board_offers(self.public.board(venue.id), venue.id, run.snap.us)
            except BazaarError as e:
                self.log(f"tick {run.snap.clock.tick} taker: board {venue.id} refused {e.code}; skipped")
        if run.settled is not None or self.rules.arb_enabled:  # the board shows pseudonyms; the feed names teams
            makers = listed_makers(run.snap.events)
            offers = [replace(o, maker=makers.get(o.id, o.maker)) for o in offers]
        run.venues, run.board = venues, offers
        own_bids = {o.ref: o for o in run.mine if o.side == "bid"}
        return ask_candidates(market, offers, venues, run.params, {o.id for o in run.mine}, own_bids)

    def _held_buys(self, run: _TickRun, market: Market) -> list[AskCandidate]:
        """Duplicate buys and arbitrage buys (both off by default): `guardrails.check` has the last word."""
        ours = {o.id for o in run.mine}
        dups = dup_candidates(market, run.board, run.venues, ours, self.rules)
        recent = self._recent_parties(run.snap.clock) if self.rules.arb_enabled else set()
        tick = run.snap.clock.tick
        arbs = arb_candidates(market, run.board, run.venues, ours, self.rules, set(self.exits), recent, tick)
        return dups + arbs

    def _recent_parties(self, clock: Clock) -> set[str]:
        """The teams on either side of an arbitrage within `arb_party_cooldown_ticks` (ring guard): one round
        trip per team per cooldown, whoever the other side is. From the shared ledger, so a restart or a
        second machine remembers them, plus this process's dry runs."""
        ticks = self.rules.arb_party_cooldown_ticks
        rows = self.ledger.spend_rows(ARB_TAG, clock.t_hours - ticks * clock.tick_seconds / 3600)
        parties = {t for item, _, _ in rows if (r := ArbRow.parse(item)) is not None for t in (r.seller, r.buyer)}
        return parties | {t for pair, at in self._arb_pairs.items() if clock.tick - at < ticks for t in pair}

    # ------------------------------------------------------------ (b) the dealer desk

    def _open(self, run: _TickRun, book: Playbook, threads: list[dict[str, Any]]) -> None:
        clock = run.snap.clock
        room = min(
            self.config.max_dealer_threads - len(self.convs),
            clock.limits.max_open_threads_per_team - len(threads),
        )
        if room <= 0:
            return
        dealer_ids = {str(d.get("id")) for d in run.snap.dealers}
        ctx = self._ctx(run)
        if self.pack_judge is not None and run.window.left() >= self.config.jev_min_budget_s:
            used = self.ledger.packs_since(clock.t_hours - 1.0)
            slots = PackSlots(sum(used.values()), self.rules.max_packs_per_game_hour)
            book = gate_packs(book, self.pack_judge, slots, used, self.rules, clock.t_hours)
        else:  # no Jev (or no time to ask it): a pack slot is never spent without its yes
            book = replace(book, packs=())
        book = guarded_playbook(book, ctx, self.rules)
        moves = sorted([mv for mv in (*book.buys, *book.packs) if mv.source in dealer_ids], key=lambda mv: -mv.score)
        busy = {str(t.get("with")) for t in threads} | set(self.convs)
        for op in openings(moves, busy, {c.item for c in self.convs.values()}, room):
            self._open_one(run, op, ctx)

    def _open_one(self, run: _TickRun, op: Opening, ctx: Context) -> None:
        tick = run.snap.clock.tick
        verdict = check(Action("buy", op.item, op.rarity, op.plan.start), ctx, self.rules)
        plan = f"{op.plan.start}→{op.plan.max_price} step {op.plan.step}"
        inputs = {
            "dealer": op.dealer,
            "item": op.item,
            "rarity": op.rarity,
            "value": op.value,
            "plan": plan,
            "score": op.move.score,
            "surplus": op.move.surplus,
        }
        what = f"open thread with {op.dealer} for {op.item} (ladder {plan}, worth {op.value:g})"
        status: Status = "approved" if verdict.allowed else "rejected"
        if verdict.allowed and not run.window.open():
            status = "expired"
        did = self.rec.decide(
            tick,
            "dealer_open",
            f"{what} · guardrails {verdict}",
            inputs=inputs,
            reason=op.reason,
            guardrail=str(verdict),
            chosen=status == "approved",
            status=status,
            move={"open_thread": op.dealer, "topic": {"buy": op.item}},
        )
        if status != "approved" or not self.live:
            return
        topic = topic_for(op.item)
        body = self.rec.send(
            did,
            tick,
            "open_thread",
            {"with": op.dealer, "topic": topic},
            lambda: self.team.open_thread(op.dealer, topic=topic),
        )
        if body is not None and isinstance(body.get("id"), int):
            self.convs[op.dealer] = Conversation(
                op.dealer, op.item, op.rarity, op.value, op.reason, Negotiation(op.plan), int(body["id"]), tick
            )

    def _desk_moves(self, run: _TickRun, *, held: bool = False) -> list[tuple[DeskMove, dict[str, Any]]]:
        """This tick's move per conversation. `held` (kill switch on): only threads that closed are wrapped
        up; an open one is left as it is, and the tick does not count toward its tick limit."""
        out = []
        for dealer, conv in list(self.convs.items()):
            thread = self.team.thread(conv.thread_id)
            if held and str(thread.get("status") or "open") == "open":
                continue
            conv.ticks += 1
            dm = plan_conversation(conv, thread, self.rules.dealer_max_ticks_per_thread, run.snap.clock.tick)
            if dm.status != "open":
                self._finished(run, conv, thread)
                del self.convs[dealer]
                continue
            if dm.ignored:
                self.log(f"tick {run.snap.clock.tick} taker: {dealer} offer ignored: {dm.ignored}")
            out.append((self._jev_early(run, dm), thread))
        return out

    def _jev_early(self, run: _TickRun, dm: DeskMove) -> DeskMove:
        """Jev may accept a dealer's ask early (still inside our max); it never lifts the limit."""
        conv = dm.conv
        if dm.move.kind != "bid" or dm.ask is None or dm.offer_id is None or not self.rules.jev_can_accept_early:
            return dm
        if dm.ask > conv.neg.plan.max_price:
            return dm
        p = AcceptProposal(
            conv.dealer, conv.item, conv.rarity, dm.offer_id, dm.ask, conv.value, dm.final, conv.reason, {}
        )
        advice = self._ask_jev(run, offer_state(p, run.snap, self._ctx(run, skip_thread=conv.thread_id), self.rules, 1))
        if advice.verdict != "yes":
            return dm
        return replace(dm, move=apply_advice(dm.move, "accept", conv.neg, dm.ask, dm.offer_id))

    def _finished(self, run: _TickRun, conv: Conversation, thread: dict[str, Any]) -> None:
        status, tick = str(thread.get("status")), run.snap.clock.tick
        price = conv.accepted_price or (conv.neg.bids[-1] if conv.neg.bids else None)
        if status == "deal" and price is not None and self.live:
            self.ledger.record("spend", tick, run.snap.clock.t_hours, int(price), conv.item)
        self.log(
            f"tick {tick} taker: thread {conv.thread_id} with {conv.dealer} {status} "
            f"({thread.get('closed_reason') or '-'}) price {price if status == 'deal' else '-'}"
        )

    def _converse(self, run: _TickRun, desk: list[tuple[DeskMove, dict[str, Any]]]) -> None:
        taken = {p.desk.conv.dealer for p in run.accepted if p.desk is not None}
        bought = {p.ref for p in run.accepted if p.desk is None}  # from a board: the dealer thread is moot
        for dm, thread in desk:
            if dm.conv.item in bought:
                dm = replace(dm, move=Move("walk", reason=f"bought {dm.conv.item} on a board this tick"))
            elif dm.move.kind == "accept" and dm.conv.dealer not in taken:
                dm = meet_the_ask(dm)
            if dm.move.kind in ("bid", "walk"):
                self._desk_send(run, dm, thread)
            elif dm.move.kind == "wait":
                self.log(f"tick {run.snap.clock.tick} taker: {dm.conv.dealer} wait ({dm.move.reason})")

    def _desk_send(self, run: _TickRun, dm: DeskMove, thread: dict[str, Any]) -> None:
        conv, tick, move = dm.conv, run.snap.clock.tick, dm.move
        if move.kind == "bid":
            action = Action("bid", conv.item, conv.rarity, move.price)
        else:  # a walk closes the thread: only the kill switch can refuse it
            action = Action("close_thread", str(conv.thread_id))
        verdict = check(action, self._ctx(run, skip_thread=conv.thread_id), self.rules)
        verdict_text = str(verdict)
        if verdict.halted:  # the kill switch went on this tick: hold, the thread stays open
            self.log(f"tick {tick} taker: kill switch on: holding {move.kind} on thread {conv.thread_id} ({verdict})")
            return
        if not verdict.allowed:
            move = Move("walk", reason=f"guardrail: {verdict}")
        inputs = {
            "dealer": conv.dealer,
            "thread": conv.thread_id,
            "item": conv.item,
            "her_ask": dm.ask,
            "final": dm.final,
            "our_bids": list(conv.neg.bids),
            "max": conv.neg.plan.max_price,
        }
        what = f"{move.kind} {move.price or ''} to {conv.dealer} on thread {conv.thread_id} for {conv.item}"
        status: Status = "approved" if run.window.open() else "expired"
        kind = f"dealer_{move.kind}"
        did = self.rec.decide(
            tick,
            kind,
            f"{what} ({move.reason}) · guardrails {verdict_text}",
            inputs=inputs,
            reason=move.reason,
            guardrail=verdict_text,
            chosen=status == "approved",
            status=status,
            thread_id=conv.thread_id,
            move={"kind": move.kind, "price": move.price},
        )
        if status != "approved":
            return
        if not self.live:
            if move.kind == "bid":
                self._commit(run, int(move.price or 0), conv.item, conv.thread_id)
            return
        if move.kind == "walk":
            self.rec.send(
                did, tick, "close_thread", {"thread": conv.thread_id}, lambda: self.team.close_thread(conv.thread_id)
            )
            self.convs.pop(conv.dealer, None)
            return
        price = int(move.price or 0)
        text = bid_words(
            self.words_fn,
            WordsRequest(conv.dealer, price, len(conv.neg.bids), conv.item),
            thread,
            run.snap.clock,
            run.window.deadline,
        )
        if not run.window.open():
            self.rec.decisions.settle(did, "expired")
            self.log(f"tick {tick} taker: the words took the rest of the tick; {conv.dealer} bid next tick")
            return
        if (
            self.rec.send(
                did,
                tick,
                "say",
                {"thread": conv.thread_id, "price": price},
                lambda: self.team.say(conv.thread_id, text, price=price),
            )
            is not None
        ):
            conv.neg.bids.append(price)
        elif not self.rec.maybe_landed:
            return
        self._commit(run, price, conv.item, conv.thread_id)

    # ------------------------------------------------------------ accepts (shared quota)

    def _accept(self, run: _TickRun, proposals: list[AcceptProposal]) -> None:
        clock = run.snap.clock
        limit = accept_limit(clock, self.rules)
        used = self.ledger.accepts_in_tick(clock.tick) if self.live else self._dry_accepts.get(clock.tick, 0)
        used += 0 if self.live else run.exits_used  # a live exit's accept is in the ledger already
        for p in rank_accepts(proposals):
            if used >= limit:
                self._skip(run, p, f"accept quota {limit}/tick used", "rejected")
                continue
            if p.ref in {a.ref for a in run.accepted}:
                self._skip(run, p, f"already buying {p.ref} this tick", "rejected")
                continue
            if self._accept_one(run, p, limit):
                used += 1
                run.accepted.append(p)
        if not self.live:
            self._dry_accepts = {clock.tick: used}

    def _skip(self, run: _TickRun, p: AcceptProposal, why: str, status: Status, jev: JevAdvice | None = None) -> None:
        kind = "accept_ask" if p.source == "board" else "dealer_accept"
        verb = "" if status == "expired" else "skip "
        self.rec.decide(
            run.snap.clock.tick,
            kind,
            f"{verb}{p.ref} at {p.price} from {p.source}: {why}",
            inputs=p.inputs,
            reason=p.reason,
            guardrail=why if why.startswith("denied") else "-",
            chosen=False,
            status=status,
            jev=jev,
        )

    def _accept_one(self, run: _TickRun, p: AcceptProposal, limit: int) -> bool:
        clock = run.snap.clock
        skip_thread = p.desk.conv.thread_id if p.desk else None
        skip_offer = p.candidate.replaces_bid.id if p.candidate and p.candidate.replaces_bid else None
        ctx = self._ctx(run, skip_thread=skip_thread, skip_offer=skip_offer)
        maker = p.candidate.offer.maker if p.candidate is not None else None  # a dealer is not a counterparty
        ask = p.candidate.offer.price if p.candidate is not None else None  # the maker's share: without the fee
        c = p.candidate
        held_buy, exit_ = (c.held_buy, c.exit) if c is not None else (None, None)
        action = Action(
            "accept_buy",
            p.ref,
            p.rarity,
            p.price,
            counterparty=maker,
            volume=ask,
            held_buy=held_buy,
            exit_net=exit_.net if exit_ is not None else None,
            next_copy_value=p.value if held_buy == "dup" else None,
        )
        verdict = check(action, ctx, self.rules)
        if verdict.allowed and exit_ is not None and (refused := self._exit_refusal(exit_, ctx)):
            verdict = replace(verdict, allowed=False, violations=(f"the exit would be refused: {refused}",))
        if not verdict.allowed:
            self._skip(run, p, str(verdict), "rejected")
            return False
        # Jev weighs a card we keep at its value to us; a held-card buy is decided by its own deterministic guards
        # (a duplicate's marginal value, an exit already standing), which Jev's state does not carry
        ask_jev = p.source == "board" and held_buy is None
        jev = self._ask_jev(run, offer_state(p, run.snap, ctx, self.rules, limit)) if ask_jev else None
        if jev is not None and jev.verdict == "no":
            self._skip(run, p, f"jev no ({jev.value:.2f}): kept the accept slot", "rejected", jev)
            return False
        if self.live:
            self._duel_grace(run)
        if any(item.startswith("duel:") for item in self.ledger.accept_items(clock.tick)):
            self._skip(run, p, "a duel holds the team's accept this tick (duels first)", "rejected", jev)
            return False
        if not run.window.open():
            self._skip(run, p, "tick budget spent, not sent late", "expired", jev)
            return False
        if self.live and not self._fresh_tick(clock):
            run.window = TickWindow(clock.tick, 0.0, self.now)  # every later send this tick is dropped too
            self._skip(run, p, "the tick ended before the send", "expired", jev)
            return False
        if exit_ is not None and (gone := self._exit_gone(exit_.bid, run.snap.us, clock.tick + 1)):
            self._skip(run, p, f"arbitrage exit re-read: {gone}", "rejected", jev)
            return False
        if self.live and not self.ledger.reserve_accept(clock.tick, clock.t_hours, p.price, p.ref, limit):
            self._skip(run, p, "another process took the team's accept this tick", "rejected", jev)
            return False
        kind = "accept_ask" if p.source == "board" else "dealer_accept"
        where = f"on {p.inputs.get('venue')}" if p.source == "board" else f"from {p.source}"
        did = self.rec.decide(
            clock.tick,
            kind,
            f"accept {p.ref} {where} for {p.price} (worth {p.value:g}, surplus {p.surplus:.1f}) · guardrails {verdict}",
            inputs=p.inputs,
            reason=p.reason,
            guardrail=str(verdict),
            chosen=True,
            status="approved",
            jev=jev,
            thread_id=skip_thread,
            move={"accept": p.offer_id, "price": p.price},
        )
        if exit_ is not None:  # from now on the exit has priority, and the two makers rest (ring guard)
            self.exits[p.ref] = PendingExit.from_row(arb_row(exit_), p.price, clock.tick)
            self._arb_pairs[frozenset((exit_.ask.maker, exit_.bid.maker))] = clock.tick
        if not self.live:
            run.spent += p.price if p.desk is None else 0  # a live board accept is booked in the ledger
            run.dup_spent += p.price if held_buy == "dup" else 0
            self._commit(run, p.price, p.ref, skip_thread, maker, ask)
            return True
        body = self.rec.send(did, clock.tick, "accept", {"offer": p.offer_id}, lambda: self.team.accept(p.offer_id))
        if body is None and not self.rec.maybe_landed:
            if exit_ is not None:
                self.exits.pop(p.ref, None)  # refused: nothing to resell
            return True  # the reserved slot stays spent: an accept that may have landed is never retried
        # Accepted, or lost on the way back (a network error): booked as bought (fail safe for the caps).
        if p.desk is not None:
            p.desk.conv.accepted_tick, p.desk.conv.accepted_price = clock.tick, p.price
        else:
            item = arb_row(exit_).item() if exit_ is not None else dup_item(p.ref) if held_buy == "dup" else p.ref
            self.ledger.record("spend", clock.tick, clock.t_hours, p.price, item)
            if body is not None and p.candidate is not None and p.candidate.replaces_bid is not None:
                self._withdraw(run, p.candidate.replaces_bid)
        self._commit(run, p.price, p.ref, skip_thread, maker, ask)
        return True

    # ------------------------------------------------------------ arbitrage exits (priority, next tick)

    def _exit_gone(self, bid: BoardOffer, us: str, at_tick: int) -> str | None:
        """Re-read the exit's venue: why its bid cannot be hit at its price on `at_tick` (None: it can)."""
        try:
            fresh = board_offers(self.public.board(bid.venue), bid.venue, us)
        except BazaarError as e:
            return f"board {bid.venue} refused {e.code}"
        same = [o for o in fresh if o.id == bid.id and o.side == "bid" and o.ref == bid.ref and o.price == bid.price]
        if not same:
            return f"bid {bid.id} for {bid.ref} at {bid.price} on {bid.venue} is gone or moved"
        if not standing_at(same[0], at_tick):
            return f"bid {bid.id} expires at tick {same[0].expires_tick}, before tick {at_tick}"
        return None

    def _exit_refusal(self, c: Crossing, ctx: Context) -> str | None:
        """Would the exit pass the guardrails as they stand now (sell floor, counterparty share)? Checked
        before the buy, so a copy is never bought for an exit that would be refused. The accept slot is the
        next tick's, so this tick's accepts do not count."""
        sell = Action(
            "accept_sell", c.ref, None, c.proceeds, your_value=c.value, counterparty=c.bid.maker, volume=c.bid.price
        )
        verdict = check(sell, replace(ctx, accepts_this_tick=0), self.rules)
        return None if verdict.allowed else "; ".join(verdict.violations)

    def _exits(self, run: _TickRun, slots: int) -> int:
        """Pending arbitrage exits first, before any other accept: the card is in /me, the bid still stands.
        One not taken within `arb_exit_ticks` (or whose bid vanished) is dropped: the card stays ours and the
        maker's sell-to-need flow lists it, never below `sell_min_value_ratio`. Returns the accepts used."""
        clock, assets = run.snap.clock, card_assets(run.snap.me)
        if not self._exits_rebuilt and self.rules.arb_enabled:
            self._exits_rebuilt = True
            self._rebuild_exits(clock, assets)
        used = 0
        for ref, x in list(self.exits.items()):
            if clock.tick - x.tick > self.config.arb_exit_ticks:
                self.exits.pop(ref)
                self.log(f"tick {clock.tick} taker: arbitrage exit for {ref} expired: the maker's sell flow takes it")
            elif x.asset in assets and used < slots and self._exit_one(run, x):
                used += 1
        return used

    def _rebuild_exits(self, clock: Clock, assets: set[int]) -> None:
        """After a restart: the arbitrage buys of the last `arb_exit_ticks` whose copy we hold get their exit
        back (it re-reads its bid before it is sent, so one already taken is dropped)."""
        since = clock.t_hours - (self.config.arb_exit_ticks + 1) * clock.tick_seconds / 3600
        for item, cost, tick in self.ledger.spend_rows(ARB_TAG, since):
            row = ArbRow.parse(item)
            if row is not None and row.asset in assets and row.ref not in self.exits:
                self.exits[row.ref] = PendingExit.from_row(row, cost, tick)

    def _exit_one(self, run: _TickRun, x: PendingExit) -> bool:
        clock, bid = run.snap.clock, x.bid
        venue = next((v for v in tradable_venues(run.snap.venues, run.snap.us) if v.id == bid.venue), None)
        copy = next((a for a in run.snap.me.get("assets") or [] if a.get("id") == x.asset), None)  # the copy bought
        if venue is None or copy is None:
            return False
        # At the venue's fee in force now. Its cost is sunk: below it, but above the sell floor, selling still
        # beats keeping a copy worth `your_value` to us (the resale scores proceeds − your_value).
        proceeds = leg_proceeds(venue, bid.price)
        your_value = copy.get("your_value")
        action = Action(
            "accept_sell",
            x.ref,
            str(copy.get("rarity") or ""),
            proceeds,
            your_value=float(your_value) if isinstance(your_value, int | float) else None,
            counterparty=bid.maker,
            volume=bid.price,
        )
        verdict = check(action, self._ctx(run), self.rules)
        inputs = {"offer_id": bid.id, "venue": bid.venue, "maker": bid.maker, "bid": bid.price, "asset": copy["id"]}
        inputs |= {"proceeds": proceeds, "your_value": your_value, "bought_tick": x.tick, "cost": x.cost}
        what = f"arbitrage exit: sell {x.ref} (asset {copy['id']}) into bid {bid.id} on {bid.venue} for {proceeds}"
        if verdict.halted:
            self.log(f"tick {clock.tick} taker: kill switch on: holding the {what} ({verdict})")
            return False
        gone = None if not verdict.allowed else self._exit_gone(bid, run.snap.us, clock.tick)
        bid_gone = gone is not None
        if action.your_value is None:
            verdict = replace(verdict, allowed=False, violations=(*verdict.violations, "no your_value: no sell floor"))
        status: Status = "approved" if verdict.allowed and gone is None else "rejected"
        if status == "approved" and self.live:
            self._duel_grace(run)
            if any(item.startswith("duel:") for item in self.ledger.accept_items(clock.tick)):
                status, gone = "rejected", "a duel holds the team's accept this tick (duels first)"
            elif not run.window.open() or not self._fresh_tick(clock):
                status, gone = "expired", "the tick ended before the send"
            elif not self.ledger.reserve_accept(clock.tick, clock.t_hours, 0, x.ref, accept_limit(clock, self.rules)):
                status, gone = "rejected", "another process took the team's accept this tick"
        did = self.rec.decide(
            clock.tick,
            "arb_exit",
            f"{what} (net {proceeds - x.cost:+d}) · guardrails {verdict}{f' · {gone}' if gone else ''}",
            inputs=inputs,
            reason=f"bought at {x.cost} on tick {x.tick}",
            guardrail=str(verdict),
            chosen=status == "approved",
            status=status,
            move={"accept": bid.id, "assets": [copy["id"]], "price": proceeds},
        )
        if status != "approved":  # kept until `arb_exit_ticks`, unless its bid is gone: then the maker sells
            if bid_gone:
                self.exits.pop(x.ref, None)
                self.log(f"tick {clock.tick} taker: {what}: {gone}; the maker's sell flow takes the card")
            return False
        self.exits.pop(x.ref, None)
        if self.live:
            self.rec.send(did, clock.tick, "accept", {"offer": bid.id}, lambda: self.team.accept(bid.id, [copy["id"]]))
        return True

    def _duel_grace(self, run: _TickRun) -> None:
        """Duels own the first `duel_grace_s` of a tick: the duel player decides right after the tick lands
        (a duel's pie shrinks every round, and a timed-out scored duel is 0), so the taker claims the team's
        accept only after that, and only if no duel took it. Tick-relative (from `next_tick_in`)."""
        clock = run.snap.clock
        grace = min(self.config.duel_grace_s, clock.tick_seconds * 0.15)
        into_tick = clock.tick_seconds - clock.next_tick_in + (self.now() - run.started)
        wait = grace - into_tick
        if 0 < wait < run.window.left():
            self.sleep(wait)

    def _fresh_tick(self, clock: Clock) -> bool:
        """Re-read the clock right before an accept: a tick that rolled over drops it."""
        fresh = Clock.model_validate(self.team.clock())
        return fresh.tick == clock.tick and action_budget_s(fresh) > 0

    def _withdraw(self, run: _TickRun, bid: OpenOffer) -> None:
        """A cheaper ask filled the card our bid was waiting for: withdraw the bid, refund its spend."""
        clock = run.snap.clock
        verdict = check(Action("cancel", str(bid.id)), self._ctx(run), self.rules)
        did = self.rec.decide(
            clock.tick,
            "cancel_bid",
            f"cancel our bid {bid.id} for {bid.ref}: bought it cheaper · guardrails {verdict}",
            inputs={"offer_id": bid.id, "ref": bid.ref, "price": bid.price},
            reason="replaced",
            guardrail=str(verdict),
            chosen=verdict.allowed,
            status="approved" if verdict.allowed else "rejected",
        )
        if not verdict.allowed:  # the kill switch holds: the bid stays open and its spend stays counted
            return
        if self.rec.send(did, clock.tick, "cancel", {"offer": bid.id}, lambda: self.team.cancel(bid.id)) is not None:
            self.ledger.record(
                *refund_row(bid.price, bid.ref, bid.created_tick, clock.tick, clock.t_hours, clock.tick_seconds)
            )
            run.offers = [o for o in run.offers if o.get("id") != bid.id]
