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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

from bazaar_agent.affinity import AffinityMap
from bazaar_agent.agents.accept_gate import Gate, GateKind, bid_gate, board_gate, dealer_gate, swap_gate
from bazaar_agent.agents.bluff import Choice, Counterparty, TacticBook, message_id
from bazaar_agent.agents.dealer import (
    MAX_WAITS,
    BidPlan,
    Move,
    Negotiation,
    WordsFn,
    apply_advice,
    bid_words,
    reopen_start,
    requested_item,
    settled_price,
    template_words,
)
from bazaar_agent.agents.dealer_plan import LIFTED_FINAL_MIN_BIDS, DealerPlan, plan_dealer_buy
from bazaar_agent.agents.desk import (
    Conversation,
    DeskMove,
    Opening,
    deal_price,
    meet_the_ask,
    openings,
    plan_conversation,
    topic_for,
)
from bazaar_agent.agents.injection_tags import INJECTIONS_FILE, InjectionTags, latest_message
from bazaar_agent.agents.inspector import CardIndex, FlagBook, Inspection, flag_step
from bazaar_agent.agents.jev_cache import CACHED_REASONS, VerdictCache, state_key
from bazaar_agent.agents.market import (
    BoardOffer,
    OpenOffer,
    Venue,
    board_offers,
    our_open_offers,
    parse_offer,
    tradable_venues,
)
from bazaar_agent.agents.persona_book import PersonaBook
from bazaar_agent.agents.persona_desk import shape as persona_shape
from bazaar_agent.agents.runtime import (
    JevAdvice,
    JevFn,
    MarketFeed,
    PageWatch,
    Recorder,
    Snapshot,
    TickWindow,
    accept_limit,
    cost_nothing,
    guard_context,
    new_page_line,
    no_jev,
    read_snapshot,
    read_together,
    window_for,
)
from bazaar_agent.agents.seller import (
    Commitments,
    committed_context,
    offers_in,
    open_commitments,
    trade_book,
    unsettled_accepts,
)
from bazaar_agent.agents.tactics import private_numbers
from bazaar_agent.agents.team_desk import NO_JEV_BUDGET, TEAM_SPEND, DeskView, SwapAccept, TeamDesk
from bazaar_agent.agents.words import WordsRequest
from bazaar_agent.cards_heartbeat import CardsHeartbeat
from bazaar_agent.decisions import PROCESS_STARTED, THREAD_CLOSED, DecisionLog, Status, ThreadTrail
from bazaar_agent.evals.dealers import price_class
from bazaar_agent.guardrails import (
    Action,
    Context,
    Guardrails,
    LedgerStore,
    check,
    effective_cash_floor,
    kill_switch,
    refund_row,
)
from bazaar_agent.holdings import Holdings
from bazaar_agent.intel import book_values, dealer_threads, listed_makers, settled_volume
from bazaar_agent.learn.blockers import Blocks
from bazaar_agent.learn.curves import curve_stats
from bazaar_agent.learn.live import LiveLearner
from bazaar_agent.learn.outcomes import OutcomeLearner
from bazaar_agent.learn.recall import Lessons
from bazaar_agent.learn.threads import ThreadStore
from bazaar_agent.ledger_pg import LedgerUnavailable, ensure_writable
from bazaar_agent.news import NewsSentinel
from bazaar_agent.official_values import OfficialValues, unread_only
from bazaar_agent.opportunities import Opportunity, score_offer
from bazaar_agent.pack_gate import PackJudge, gate_packs
from bazaar_agent.pack_open import choose, sealed_packs
from bazaar_agent.schedule_watch import crossing, ladder_ticks
from bazaar_agent.sdk import BazaarError
from bazaar_agent.strategy import (
    Market,
    PackSlots,
    Playbook,
    StrategyParams,
    boosted_score,
    build_market,
    build_playbook,
    buy_case,
)
from bazaar_agent.strategy import Move as StrategyMove
from bazaar_agent.strategy import guarded as guarded_playbook
from bazaar_agent.ticks import Clock, action_budget_s
from bazaar_agent.watchdog import Watchdog

OFFER_QUESTION = "offer_is_worth_accepting"  # questions/negotiation.json: the taker's advisory accept check
THREAD_GONE_STATUS = 404  # a dealer thread read refused with this may retire the thread (see `_thread_of`)


@dataclass(frozen=True)
class TakerConfig:
    max_dealer_threads: int = 3  # dealer conversations at once (one per dealer; the team cap is 6 threads)
    jev_min_budget_s: float = 4.0  # ask Jev only with this much of the tick left (a call takes ~0.3 s, max 3 s)
    max_jev_calls_per_tick: int = 3
    duel_grace_s: float = 2.0  # duels own the first seconds of a tick (capped at 15 % of the tick)
    # An open dealer thread of ours without a bid of ours newer than this many ticks has no driver (the
    # process that opened it restarted): it is adopted or closed. A live `bazaar dealer buy` bids every tick.
    orphan_after_ticks: int = 3
    restart_lookback_ticks: int = 40  # on start: our threads with a move this recent are checked for a deal
    # Also accept standing BIDS for cards we hold when the bid, less the fee, beats what selling our least
    # valuable copy costs us by `sell_min_surplus` (`opportunities.score_offer`). Off: today's taker.
    accept_bids: bool = False
    # No new dealer ladder whose bids would still run when a Market Test or a duel session starts (the official
    # schedule, read by the news sentinel): the duels take the team's accept slot and the bench wants the request
    # budget. A thread already open goes on. No price changes.
    schedule_guard: bool = True


FLAGS_FILE = "flags.jsonl"  # flags sent (or that may have landed), one per message, across restarts


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
    sell: Opportunity | None = None  # a standing bid we would sell into (`accept_bids`)
    asset_id: int | None = None  # sells: the copy we hand over
    swap: SwapAccept | None = None  # a team's offer in a swap thread (N17, `team_desk`)
    thread: dict[str, Any] | None = field(default=None, compare=False)  # the dealer thread read this tick
    bid: BoardOffer | None = field(default=None, compare=False)  # sells: the board bid `sell` was priced on

    @property
    def surplus(self) -> float:
        if self.swap is not None:
            return self.swap.verdict.ours
        return self.sell.ours if self.sell is not None else self.value - self.price

    @property
    def scarce(self) -> bool:
        return self.candidate is not None and self.candidate.scarce

    @property
    def score(self) -> float:
        return self.candidate.score if self.candidate is not None else self.surplus


def bid_proposal(op: Opportunity, asset_id: int, bid: BoardOffer | None = None) -> AcceptProposal:
    inputs = {
        "offer_id": op.offer_id,
        "venue": op.venue,
        "maker": op.maker,
        "ref": op.ref,
        "rarity": op.rarity,
        "bid": op.price,
        "fee": op.fee,
        "surplus": op.ours,
        "asset_id": asset_id,
        "tag": op.tag,
    }
    return AcceptProposal(
        "board",
        op.ref,
        op.rarity,
        op.offer_id,
        op.price,
        op.price - op.ours,
        False,
        op.reason,
        inputs,
        sell=op,
        asset_id=asset_id,
        bid=bid,
    )


def rank_accepts(proposals: Iterable[AcceptProposal]) -> list[AcceptProposal]:
    """A dealer's final offer first (it walks otherwise), then scarce cards, then the best score
    (surplus raised by urgency, as the strategy ranks)."""
    return sorted(proposals, key=lambda p: (not p.final, not p.scarce, -p.score, p.offer_id))


def accept_kind(p: AcceptProposal) -> str:
    """The decision kind of an accept: a team swap, a sell into a bid, a board ask or a dealer's offer."""
    if p.swap is not None:
        return "team_accept"
    if p.sell is not None:
        return "accept_bid"
    return "accept_ask" if p.source == "board" else "dealer_accept"


def swap_proposal(a: SwapAccept) -> AcceptProposal:
    """A team's swap offer as an accept candidate, ranked by our gain against the board's asks."""
    # Public view: only the thread and the fee (the cards and their offer stay private: a team thread is private)
    inputs = {"thread": a.thread_id, "their_offer": a.offer.offer_id, "give_card": a.trade.refs[0]}
    inputs["want_card"] = a.trade.refs[1]
    return AcceptProposal(
        "team",
        a.trade.refs[1],
        a.trade.rarity,
        a.offer.offer_id,
        a.offer.cash_out + a.fee,
        a.verdict.ours + a.offer.cash_out + a.fee,
        False,
        a.verdict.reason,
        inputs | {"fee": a.fee},
        swap=a,
    )


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
    return AcceptProposal("board", o.ref, c.rarity, o.id, c.total, c.value, False, c.reason, inputs, candidate=c)


def desk_proposal(dm: DeskMove, thread: dict[str, Any] | None = None) -> AcceptProposal:
    conv, price = dm.conv, int(dm.move.price or 0)
    inputs = {
        "dealer": conv.dealer,
        "thread": conv.thread_id,
        "item": conv.item,
        "ask": price,
        "final": dm.final,
        "our_bids": list(conv.neg.bids),
        "max": conv.neg.plan.max_price,
        "final_max": conv.neg.plan.final_max,
        "value": conv.value,
        "changed_by": list(conv.notes),
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
        thread=thread,
    )


def offer_state(
    p: AcceptProposal,
    snap: Snapshot,
    ctx: Context,
    rules: Guardrails,
    slots_left: int,
    rival_moves: Sequence[str] = (),
) -> dict[str, Any]:
    """What Jev reads for `offer_is_worth_accepting`: the offer, the album around the card, cash, the tick, and
    the latest rivals' climbs (`rank_watch`, public data, quoted)."""
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
        "cash_floor": effective_cash_floor(rules, ctx),
        "cash_above_floor": max(0, ctx.cash - effective_cash_floor(rules, ctx)),
        "accept_slots_left_this_tick": slots_left,
        "tick": snap.clock.tick,
        "rival_moves": list(rival_moves),
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


def _conversation(conv: Conversation) -> str:
    """The bluff book's key for a dealer thread."""
    return f"thread:{conv.thread_id}"


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
    cards: CardIndex | None = None  # the inspector's catalog index, built on first use this tick
    blocks: Blocks = field(default_factory=Blocks)  # learned dealer blockers in force for us (N12)
    boost: dict[str, float] = field(default_factory=dict)  # card ref -> rank multiplier (cards heartbeat)
    team_view: DeskView | None = None  # what the team desk saw this tick (N17)
    plans: dict[tuple[str, str], DealerPlan] = field(default_factory=dict)  # (dealer, item) -> its plan (N14a)
    unread: set[str] = field(default_factory=set)  # cards of dealer threads we could not read this tick
    listed: frozenset[int] = frozenset()  # our open threads as /api/me/threads listed them this tick


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
        holdings: Holdings | None = None,
        learner: LiveLearner | None = None,
        outcome_learner: OutcomeLearner | None = None,
        lessons: Lessons | None = None,
        thread_store: ThreadStore | None = None,
        bluff: TacticBook | None = None,
        swap_jev: JevFn = no_jev,
        cards: CardsHeartbeat | None = None,
        news: NewsSentinel | None = None,
        personas: PersonaBook | None = None,
    ) -> None:
        self.team, self.public, self.rules, self.params = team, public, rules, params
        self.swap_jev = swap_jev  # Jev `team_swap_worth_it`: the team desk sends a swap only on its decided yes
        self.ledger, self.feed, self.live, self.log = ledger, feed, live, log
        self.jev, self.pack_judge, self.words_fn, self.now = jev, pack_judge, words_fn, now
        self.config = config or TakerConfig()
        self.sleep = sleep
        self.holdings = holdings  # /me from the shared Postgres snapshot while provably current, else live
        self.learner = learner  # the live-feed reader: blockers recalled before a dealer thread opens
        self.outcome_learner = outcome_learner  # lessons from settled outcomes, on its own worker (N3)
        self.lessons = lessons  # the hybrid recall for the words context (Jev gets them through its JevFn)
        self._learned_skips: dict[tuple[str, str], str] = {}  # (dealer, class) -> the reason last recorded
        self.thread_store = thread_store  # our dealer threads as read each tick, written after the sends
        self.bluff = bluff  # the words' tactics, learned per dealer (N16); None: today's words only
        self.cards = cards  # the catalog diffed each tick: new releases rank up (no request; logged and stored after)
        self.news = news  # Radio Rastro + the schedule: logged and stored after the sends; no behaviour change
        self._event_skips: set[str] = set()  # scheduled events a dealer skip was recorded for (once each)
        self._news_view: tuple[int, list[Any], dict[str, Any], Clock, str] | None = None  # this tick's view
        # The dealers' published traits and menus (the /api/dealers read of every tick), stored when they change.
        self.personas = personas or PersonaBook(None, log)
        self._tones: dict[str, str] = {}  # dealer -> the words' tone its traits ask for (persona model)
        self.values = OfficialValues.of(team)  # GET /api/me/value: every card buy capped at it (Day-2 hint 1)
        self.rec = Recorder("taker", decisions, live, log, hub)
        self.hub = hub  # agents.status.StatusHub: the read-only HTTP/WS view, when served
        self.convs: dict[str, Conversation] = {}  # dealer id -> the conversation we own
        self._skips: dict[str, str] = {}  # dealer -> the blocker last recorded as a `dealer_skip` (once each)
        # She held her opening ask and we walked: (dealer, item) -> the lower first bid of the next thread
        # (once); after the lower one held too, (dealer, item) -> the game hour until which we leave it.
        self.reopen_at: dict[tuple[str, str], int] = {}
        self.cooling: dict[tuple[str, str], float] = {}
        self.pages = PageWatch()  # album pages seen: a new page is logged once (it is ranked at once anyway)
        self._pack_notes: set[tuple[int, str]] = set()  # (asset, verdict) already recorded and not sent
        self._pack_refused: set[int] = set()  # sealed packs the server refused to open: never sent again
        self._dry_accepts: dict[int, int] = {}
        self.flags = FlagBook.from_rules(rules, decisions.dir / FLAGS_FILE)  # S1: bad-faith flags, once each
        self._flag_rows: dict[int, tuple[int, bool]] = {}  # message id -> (its flag row, approved): a 429 reuses it
        if self.flags.skipped:
            log(f"taker: {self.flags.skipped} unreadable line(s) in {FLAGS_FILE}, counted as sent flags")
        self.injections = InjectionTags(decisions.dir / INJECTIONS_FILE)  # S1: tagged, never obeyed
        self._restart_checked = False  # the threads of the process before this one were wrapped up
        self._restart_tries: dict[int, int] = {}  # thread -> wrap-up reads that did not wrap it up
        self._first_start: int | None = None  # the earliest PROCESS_STARTED tick: threads since are booked
        self._trails: dict[int, ThreadTrail] = {}  # threads the taker drove, from the decisions log
        self._owner = decisions.writer()  # this service or checkout: only its own threads are touched
        self._restart_ticks = 0  # ticks the restart wrap-up ran (bounded by `restart_lookback_ticks`)
        self._accepts_stop: str | None = None  # why no more accepts are tried this tick (rate limit, lost race)
        self._accepts_refused = 0  # refused accepts this tick (each one cost a clock read and a POST)
        self._unsettled = Commitments()  # this tick: recent accepts /api/me does not show yet (bite X18)
        self._quiet: dict[int, int] = {}  # open dealer thread of ours with no bid standing -> first tick seen so
        # Swap threads with other teams (N17), off by default; it books spend and listings in the shared ledger.
        self.team_desk = TeamDesk(team, rules, self.rec, log, live, ledger=ledger)
        # Jev's answer per unchanged offer state (GUARDRAILS.md `jev_cache_ticks`, 0 = ask every time)
        self.jev_cache: VerdictCache[JevAdvice] = VerdictCache(rules.jev_cache_ticks)
        # The live watchdog (GUARDRAILS.md "Live guard"): reads the decisions' Postgres after the sends, trips breakers.
        self.watchdog: Any = Watchdog(getattr(decisions, "_connect", None), log)

    # ------------------------------------------------------------ entry point (run_per_tick calls it)

    def on_tick(self, clock: Clock) -> None:
        window = window_for(clock, self.now(), self.now)
        self.rec.decisions.begin_tick(clock.tick)
        try:
            snap = read_snapshot(
                self.team,
                self.public,
                self.feed,
                clock,
                self.holdings,
                parallel=self.rules.parallel_reads,
                extra={"threads": lambda: self.team.my_threads("open")},
            )
            threads = [t for t in snap.extra["threads"].get("threads") or [] if isinstance(t, dict)]
            ensure_writable(self.ledger)  # no game write at all while the shared ledger is down
            self._tick(snap, threads, window)
        except BazaarError as e:
            self.log(f"tick {clock.tick} taker: read refused {e.code} ({e.message[:80]}); nothing sent")
        except LedgerUnavailable as e:
            self.log(f"tick {clock.tick} taker: {e}; no further write this tick (fail closed)")
        except Exception:
            self._after_sends(clock.tick)
            raise
        self._after_sends(clock.tick)

    def _after_sends(self, tick: int) -> None:
        """After every send of the tick (an error included, never Ctrl-C): the learner's writes, our dealer
        threads and the feed archive. No database write ever runs before a send."""
        if self.learner is not None:
            self.learner.flush()
        if self.thread_store is not None:
            self.thread_store.flush(tick)
        if self.bluff is not None:
            self.bluff.flush()
        if self.news is not None and self._news_view is not None and self._news_view[0] == tick:
            self.news.on_tick(*self._news_view)  # never raises; at most 4 keyless GETs every 10 ticks
        if self.cards is not None:
            self.cards.flush(tick)
        self.feed.archive_pending()
        if self.rules.live_watchdog_enabled and self.live:
            try:
                self.watchdog.tick(tick, self.rules)  # Postgres only, bounded; it never raises by design
            except Exception as e:  # noqa: BLE001 — a watchdog bug must never cost the tick
                self.log(f"tick {tick} taker: watchdog failed ({type(e).__name__}); the tick goes on")

    def _card_boost(self, tick: int) -> dict[str, float]:
        """The cards heartbeat's rank multipliers; any failure is "no boost" (today's order), never a failed tick."""
        if self.cards is None:
            return {}
        try:
            return self.cards.boost(tick)
        except Exception as e:  # noqa: BLE001 — a hint only
            self.log(f"tick {tick} taker: card boost skipped ({type(e).__name__})")
            return {}

    def _keep(self, thread: dict[str, Any], snap: Snapshot, conv: Conversation | None = None) -> None:
        """Buffer a thread answer we already read (no request, no I/O): `threads` + `messages` after the sends."""
        if self.thread_store is not None:
            tactics = getattr(conv, "tactics", None)  # N16: message id -> tactic, when the desk records one
            self.thread_store.saw(thread, snap.us, snap.clock.tick, tactics if isinstance(tactics, dict) else None)

    def _tick(self, snap: Snapshot, threads: list[dict[str, Any]], window: TickWindow) -> None:
        clock = snap.clock
        self._news_view = (clock.tick, snap.events, snap.catalog, clock, snap.us)
        if self.hub is not None:
            self.hub.tick(clock.tick, clock.t_hours, snap.us)
        for listed in threads:  # GET /api/me/threads, already read: our open dealer threads
            self._keep(listed, snap)
        offers = offers_in(snap.offers)
        mine, _ = our_open_offers(snap.offers, snap.us)
        run = _TickRun(snap, window, self.params(clock.tick), offers, mine, window.deadline - action_budget_s(clock))
        self._unsettled = unsettled_accepts(snap.me, self.ledger, clock.tick)  # read once per tick
        run.listed = frozenset(int(t["id"]) for t in threads if isinstance(t.get("id"), int))
        try:  # no request: the snapshot's /api/dealers. A hostile persona never costs the tick: last tick's stay
            self.personas.observe(snap.dealers, clock.tick)
        except Exception as e:  # noqa: BLE001
            self.log(f"tick {clock.tick} taker: personas not read ({type(e).__name__}); last tick's kept")
        self._restart_wrapup(run, threads)
        self._adopt_orphans(run, threads)
        if self.rules.max_counterparty_share < 1:
            run.settled = settled_volume(snap.events, snap.us, book_values(snap.catalog))
        if self.cards is not None:  # memory only: the catalog and menus this tick already read
            self.cards.observe(clock.tick, snap.catalog, snap.dealers)
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
        if fresh := self.pages.new(snap.me):  # after the hold: a page seen while holding is said when we act
            self.log(new_page_line(clock.tick, "taker", fresh, snap.me))
        if self.learner is not None:
            known: dict[str, Any] = {str(d.get("id")): "dealer" for d in snap.dealers if d.get("id")}
            known.update({v.id: "venue" for v in snap.venues})
            run.blocks = self.learner.blocks(snap.events, snap.us, clock, known)
        if self.bluff is not None:  # memory only before the sends: a cooloff, strike or flag after a tactic
            self.bluff.begin_tick(clock.tick, clock.round, snap.us)
            self.bluff.events(snap.events, snap.us, clock.tick)
        market = build_market(snap.me, snap.catalog, snap.events, snap.dealers, snap.scan)
        run.boost = self._card_boost(clock.tick)
        book = build_playbook(
            snap.me, snap.catalog, snap.events, snap.dealers, run.params, self.rules, snap.scan, boost=run.boost
        )
        self._open(run, book, threads)
        desk = self._desk_moves(run)
        proposals = [desk_proposal(dm, thread) for dm, thread in desk if dm.move.kind == "accept"]
        board, board_venues = self._board_offers(run)
        proposals += [board_proposal(c) for c in self._board(run, market, board, board_venues)]
        if self.config.accept_bids:
            proposals += self._bids(run, market, board, board_venues)
        view = run.team_view = self._team_view(run, threads)
        proposals += [swap_proposal(a) for a in self._team_desk("proposals", lambda: self.team_desk.proposals(view))]
        self._accept(run, proposals)
        self._converse(run, desk)
        taken = {p.swap.thread_id for p in run.accepted if p.swap is not None}

        def converse() -> list[SwapAccept]:
            self.team_desk.converse(view, taken)
            return []

        self._team_desk("converse", converse)
        self._open_pack(run, market)
        if self.hub is not None:
            self.hub.view(threads=[conversation_view(c) for c in self.convs.values()])
        if self.outcome_learner is not None:  # after the tick's sends; never waits for the pass
            self.outcome_learner.maybe_run(clock.tick, snap.us)
        self.log(
            f"tick {clock.tick} taker: {len(proposals)} accept candidate(s), {len(run.accepted)} taken, "
            f"{len(self.convs)} dealer thread(s), {window.left():.1f} s left · {'LIVE' if self.live else 'dry run'}"
            + (f" · {snap.holdings.line()}" if snap.holdings is not None else "")  # what the tick decided from
        )

    def _ctx(
        self,
        run: _TickRun,
        *,
        skip_thread: int | None = None,
        skip_offer: int | None = None,
        unsettled: bool = True,
    ) -> Context:
        """Live guardrail context; our open offers count (this tick's accepts and bids too, `_commit`),
        except the thread or bid this move replaces; and, unless `unsettled` is False, recent accepts
        `/api/me` does not show yet."""
        kept = [
            o
            for o in run.offers
            if (skip_thread is None or o.get("thread") != skip_thread)
            and (skip_offer is None or o.get("id") != skip_offer)
        ]
        ctx = guard_context(run.snap, self.ledger, self.rules, open_commitments(kept, run.snap.us), self.values)
        if unsettled:  # an accept of the last ticks /api/me does not show yet
            ctx = committed_context(ctx, self._unsettled)
        book = book_values(run.snap.catalog)
        trades = None if run.settled is None else trade_book(kept, run.snap.us, run.settled, book)
        return replace(ctx, spent_last_hour=ctx.spent_last_hour + run.spent, trades=trades)

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

    def _team_desk(self, what: str, call: Callable[[], list[SwapAccept] | None]) -> list[SwapAccept]:
        """The team desk never costs the taker its tick: an error there is reported and the desk skips."""
        try:
            return call() or []
        except (BazaarError, LedgerUnavailable):
            raise  # a refused read or a ledger outage stops the taker's writes this tick (on_tick reports it)
        except Exception as e:  # noqa: BLE001 — fail closed for the desk, never for the board or the dealers
            self.log(f"team desk: {what} failed ({type(e).__name__}: {e}); no team-thread move this tick")
            return []

    def _team_view(self, run: _TickRun, threads: list[dict[str, Any]]) -> DeskView:
        snap, listed = run.snap, {t.get("id") for t in threads}
        opened_now = sum(1 for c in self.convs.values() if c.thread_id not in listed)  # this tick's dealer opens
        return DeskView(
            tick=snap.clock.tick,
            t_hours=snap.clock.t_hours,
            us=snap.us,
            me=snap.me,
            catalog=snap.catalog,
            events=snap.events,
            venues=snap.venues,
            threads=threads,
            offers=run.offers,
            params=run.params,
            max_threads=snap.clock.limits.max_open_threads_per_team,
            in_use=len(threads) + opened_now,
            ctx=lambda thread: self._ctx(run, skip_thread=thread),
            window_open=run.window.open,
            listing_cap=snap.clock.limits.offers_per_team_per_tick,
            max_tick_seconds=snap.clock.max_tick_seconds,
            jev=lambda state: self._ask_swap_jev(run, state),
            scan=snap.scan,
        )

    def _ask_jev(self, run: _TickRun, state: dict[str, Any]) -> JevAdvice:
        """`offer_is_worth_accepting` for this state. The same state (tick aside) asked within `jev_cache_ticks`
        gets the same answer without a call or a slot; a reused answer carries no digest (one call, one outcome)."""
        tick, key = run.snap.clock.tick, state_key(OFFER_QUESTION, state)
        cached = self.jev_cache.get(key, tick)
        if cached is not None:  # marked, so a reused answer never reads as a fresh call in the decision log
            return replace(cached, digest=None, reason=f"cached ({cached.reason})" if cached.reason else "cached")
        if run.jev_calls >= self.config.max_jev_calls_per_tick or run.window.left() < self.config.jev_min_budget_s:
            return JevAdvice("undecided", 0.0, reason="no tick budget for jev")
        run.jev_calls += 1
        advice = self.jev(state)
        if advice.reason in CACHED_REASONS:
            self.jev_cache.put(key, tick, advice)
        return advice

    def _ask_swap_jev(self, run: _TickRun, state: dict[str, Any]) -> JevAdvice:
        """`team_swap_worth_it` for the team desk, inside the same per-tick Jev budget; never cached (each swap
        is judged on its own state)."""
        if run.jev_calls >= self.config.max_jev_calls_per_tick or run.window.left() < self.config.jev_min_budget_s:
            return JevAdvice("undecided", 0.0, reason=NO_JEV_BUDGET)
        run.jev_calls += 1
        return self.swap_jev(state)

    # ------------------------------------------------------------ (a) boards

    def _board_offers(self, run: _TickRun) -> tuple[list[BoardOffer], dict[str, Venue]]:
        """Every plain standing offer on the venues we may trade on, makers named when the cap needs them."""
        venues = {v.id: v for v in tradable_venues(run.snap.venues, run.snap.us)}
        boards = read_together(
            {venue_id: partial(self._board_of, run, venue_id) for venue_id in venues}, self.rules.parallel_reads
        )
        offers: list[BoardOffer] = [o for venue_id in venues for o in boards[venue_id]]
        if run.settled is not None:  # the board shows pseudonyms; the feed's `offer.listed` names the team
            makers = listed_makers(run.snap.events)
            offers = [replace(o, maker=makers.get(o.id, o.maker)) for o in offers]
        return offers, venues

    def _board(
        self, run: _TickRun, market: Market, offers: list[BoardOffer], venues: dict[str, Venue]
    ) -> list[AskCandidate]:
        own_bids = {o.ref: o for o in run.mine if o.side == "bid"}
        # a card whose dealer thread we could not read this tick is not bought here: our bid there may still
        # stand (and she may take it), and that thread cannot be walked until we read it again
        offers = [o for o in offers if o.ref not in run.unread]
        return ask_candidates(market, offers, venues, run.params, {o.id for o in run.mine}, own_bids)

    def _bids(
        self, run: _TickRun, market: Market, offers: list[BoardOffer], venues: dict[str, Venue]
    ) -> list[AcceptProposal]:
        """`accept_bids`: standing bids for cards we hold that pay at least `sell_min_surplus` over what
        selling our least valuable copy costs us (fee and page bonus included). A copy already in one of
        our open offers is never sold twice."""
        clock = run.snap.clock
        listed = open_commitments(run.offers, run.snap.us).listed
        # An accept settles at the next tick: a copy sold last tick may still be in /me. Never sell it again.
        sold = {
            int(item[5:])
            for t in (clock.tick - 1, clock.tick)
            for item in self.ledger.accept_items(t)
            if item.startswith("sell:") and item[5:].isdigit()
        }
        ours = {m.id for m in run.mine}
        ctx, out = self._ctx(run), []
        for o in offers:
            if o.side != "bid" or o.id in ours:
                continue
            copies = sorted(
                (
                    a
                    for a in run.snap.me.get("assets") or []
                    if a.get("kind") == "card"
                    and a.get("ref") == o.ref
                    and isinstance(a.get("id"), int)
                    and isinstance(a.get("your_value"), int | float)
                    and int(a["id"]) not in listed | sold
                ),
                key=lambda a: (float(a["your_value"]), -int(a["id"])),
            )
            if not copies:
                continue
            copy_id = int(copies[0]["id"])  # the free copy we lose least by
            op = score_offer(
                o,
                market,
                run.snap.me,
                run.params,
                self.rules,
                AffinityMap(),
                venues.get(o.venue),
                ctx,
                asset_id=copy_id,
                unavailable=frozenset(listed | sold),
            )
            if op is not None and op.ours >= run.params.sell_min_surplus:
                out.append(bid_proposal(op, copy_id, o))
        return out

    def _board_of(self, run: _TickRun, venue_id: str) -> list[BoardOffer]:
        """One venue's board; a refusal skips that venue only."""
        try:
            return board_offers(self.public.board(venue_id), venue_id, run.snap.us)
        except BazaarError as e:
            self.log(f"tick {run.snap.clock.tick} taker: board {venue_id} refused {e.code}; skipped")
            return []

    # ------------------------------------------------------------ (c) sealed packs we hold

    def _open_pack(self, run: _TickRun, market: Market) -> None:
        """Open at most one sealed pack a tick when its cards are worth more to us than any sealed price
        (`pack_open.choose`), behind `open_sealed_packs`. The next tick re-reads /me (album first)."""
        packs = [p for p in sealed_packs(run.snap.me) if p.asset_id not in self._pack_refused]
        if not packs:
            return
        tick = run.snap.clock.tick
        choices = [choose(market, p, run.params) for p in packs]
        choice = next((c for c in choices if c.verdict == "open"), choices[0])
        verdict = check(Action("open_pack", choice.pack.pack, "pack"), self._ctx(run), self.rules)
        status: Status = "approved" if verdict.allowed and choice.verdict == "open" else "rejected"
        if status == "approved" and not run.window.open():
            status = "expired"
        note = (choice.pack.asset_id, f"{choice.verdict} {verdict}")
        if status != "approved" and note in self._pack_notes:
            return  # a pack kept sealed (or the switch off) is said once, not every tick
        self._pack_notes.add(note)
        did = self.rec.decide(
            tick,
            "pack_open",
            f"{choice.verdict} sealed {choice.pack.pack} #{choice.pack.asset_id} · guardrails {verdict}",
            inputs={"pack": choice.pack.pack, "asset_id": choice.pack.asset_id, "ev": round(choice.ev, 1)},
            reason=choice.reason,
            guardrail=str(verdict),
            chosen=status == "approved",
            status=status,
            move={"open_pack": choice.pack.asset_id},
        )
        if status != "approved" or not self.live:
            return
        body = self.rec.send(
            did,
            tick,
            "open_pack",
            {"asset": choice.pack.asset_id},
            lambda: self.team.open_pack(choice.pack.asset_id),
        )
        if body is None:  # refused (asset_locked, not_owner, a network blip, ...): never re-sent by this process
            self._pack_refused.add(choice.pack.asset_id)
            self.log(f"tick {tick} taker: {choice.pack.pack} #{choice.pack.asset_id} stays sealed until a restart")
        pulled = [str(c.get("ref")) for c in (body or {}).get("cards") or [] if isinstance(c, dict)]
        if pulled:
            self.log(f"tick {tick} taker: opened {choice.pack.pack} #{choice.pack.asset_id}: {', '.join(pulled)}")

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
        moves = sorted(
            [
                mv
                for mv in (*book.buys, *book.packs)
                if mv.source in dealer_ids and self.cooling.get((mv.source, mv.ref), -1.0) <= clock.t_hours
            ],
            key=lambda mv: -boosted_score(mv, run.boost),  # a fresh release opens first (order only, #185)
        )
        busy = {str(t.get("with")) for t in threads} | set(self.convs)
        moves = self._unblocked(run, moves, busy)
        moves = self._persona_shaped(run, moves, busy)
        floor = effective_cash_floor(self.rules, ctx)  # the floor check() applies, bond reserve included (#71)
        cash_room = min(ctx.cash - floor, self.rules.max_spend_per_game_hour - ctx.spent_last_hour)
        moves = self._evolved(run, moves, busy, max(0, cash_room))  # primas, never thread slots (`room` above)
        moves = self._before_events(run, moves, busy)
        # The card of every DEALER thread of ours is busy, this process's or another's (security #158 r2 P3-B):
        # with the lift on, two dealers may sell one card. Only threads with a dealer: we wrote their topic; a
        # thread another team opened with us carries a topic that team chose (review #158 r3 P2).
        busy_items = {c.item for c in self.convs.values()} | {
            item
            for t in threads
            if str(t.get("with")) in dealer_ids and (item := requested_item(t.get("topic") or {})) is not None
        }
        for op in openings(moves, busy, busy_items, room):
            self._open_one(run, op, ctx)

    def _unblocked(self, run: _TickRun, moves: list[StrategyMove], busy: set[str]) -> list[StrategyMove]:
        """Drop the dealer buys a learned blocker stops (cooloff, quota, sold out, locked), so the thread goes
        to the next dealer instead of a refusal. One `dealer_skip` row per dealer and blocker (not per tick);
        its inputs use keys the public status view does not list, so `/state` shows only that a skip happened."""
        if not run.blocks:
            return moves
        kept: list[StrategyMove] = []
        skipped: dict[str, tuple[StrategyMove, Any]] = {}
        for mv in moves:
            stop = run.blocks.stops(mv.source, mv.ref)
            if stop is None:
                kept.append(mv)
            elif mv.source not in busy and mv.source not in skipped:
                skipped[mv.source] = (mv, stop)
        for dealer, (mv, stop) in skipped.items():
            if self._skips.get(dealer) == stop.key():
                continue
            self._skips[dealer] = stop.key()
            self.rec.decide(
                run.snap.clock.tick,
                "dealer_skip",
                f"skip {dealer} for {mv.ref}: {stop.text}",
                inputs={"blocked_dealer": dealer, "wanted": mv.ref, "until_tick": stop.until_tick, "why": stop.text},
                reason=stop.text,
                guardrail="-",
                chosen=False,
                status="rejected",
            )
        return kept

    def _before_events(self, run: _TickRun, moves: list[StrategyMove], busy: set[str]) -> list[StrategyMove]:
        """Drop the dealer ladders a Market Test or a duel session would start in the middle of (`schedule_guard`).
        One `dealer_skip` row per scheduled event, with keys the public status view does not list."""
        if self.news is None or not self.config.schedule_guard or not self.news.upcoming:
            return moves
        clock, kept = run.snap.clock, list[StrategyMove]()
        skipped: dict[str, tuple[StrategyMove, dict[str, Any]]] = {}
        for mv in moves:
            ticks = ladder_ticks(mv.ladder, self.rules.dealer_max_ticks_per_thread)
            event = crossing(self.news.upcoming, clock.t_hours, clock.tick_seconds, ticks)
            if event is None:
                kept.append(mv)
            elif mv.source not in busy:
                skipped.setdefault(str(event.get("event_id")), (mv, event))
        for event_id, (mv, event) in skipped.items():
            if event_id in self._event_skips:
                continue
            self._event_skips.add(event_id)
            why = f"{event.get('note') or event.get('action')} starts in {event['lead_ticks']} ticks"
            self.rec.decide(
                clock.tick,
                "dealer_skip",
                f"skip {mv.source} for {mv.ref}: {why} (schedule_guard)",
                inputs={"blocked_dealer": mv.source, "wanted": mv.ref, "why": why, "event": event_id},
                reason=why,
                guardrail="-",
                chosen=False,
                status="rejected",
            )
        return kept

    def _rival_moves(self) -> list[str]:
        """The newest `rival_move` lines (public facts about rivals' climbs), for Jev's state."""
        return [lr.text for lr in self.news.ranks.latest] if self.news is not None else []

    def _persona_shaped(self, run: _TickRun, moves: list[StrategyMove], busy: set[str]) -> list[StrategyMove]:
        """The persona model (`agents/persona_desk.py`, GUARDRAILS `persona_model_enabled`): drop a dealer whose
        hourly deal budget we used, give a dealer with no price history its trait prior (only ever lowering the
        ladder), and put the dealers whose deals unlock the next one early first. One `dealer_skip` row per
        dealer and reason, with keys the public status view does not list."""
        if not self.rules.persona_model_enabled or not self.personas.personas:
            return moves
        learner = self.outcome_learner
        last = getattr(learner, "last", None)
        curves = last.curves if last is not None else curve_stats(dealer_threads(run.snap.events, run.snap.us or None))
        learned = learner.policies.keys() if learner is not None else ()
        clock = run.snap.clock
        unlocked = [str(d) for d in run.snap.me.get("unlocked") or [] if isinstance(d, str)]
        shaped = persona_shape(
            moves, self.personas.personas, curves, learned, run.snap.events, run.snap.us, unlocked, clock.tick,
            clock.tick_seconds,
        )  # fmt: skip
        for (dealer, _), params in shaped.params.items():
            self._tones[dealer] = params.tone
        for mv, why in shaped.skipped:
            if mv.source in busy or self._learned_skips.get((mv.source, "persona")) == why:
                continue
            self._learned_skips[(mv.source, "persona")] = why
            self.rec.decide(
                clock.tick,
                "dealer_skip",
                f"skip {mv.source} for {mv.ref}: {why}",
                inputs={"blocked_dealer": mv.source, "wanted": mv.ref, "why": why},
                reason=why,
                guardrail="-",
                chosen=False,
                status="rejected",
            )
        return shaped.moves

    def _evolved(
        self, run: _TickRun, moves: list[StrategyMove], busy: set[str], room: int | None = None
    ) -> list[StrategyMove]:
        """The per-dealer plan (N14a, `dealer_plan.py`): the learned ladder per (dealer, price class) replaces the
        strategy's, never above its start nor its top, and a class priced above what we may pay is skipped (N3);
        with `dealer_final_lift` on, a final above the cap may close it (the patience play). Each plan is kept in
        `run.plans` for the open's `changed_by`. One `dealer_skip` row per dealer, class and reason (not per
        tick), with keys the public status view does not list. No policy and the lift off: unchanged."""
        learner = self.outcome_learner
        policies = learner.policies if learner is not None else {}
        if not policies and self.rules.dealer_final_lift <= 0:
            return moves
        last = getattr(learner, "last", None)  # the last pass's curves (none before the first pass)
        curves = last.curves if last is not None else {}
        if not curves and self.rules.dealer_final_lift > 0:  # no pass yet: this tick's feed window is the history
            curves = curve_stats(dealer_threads(run.snap.events, run.snap.us or None))
        kept: list[StrategyMove] = []
        skipped: dict[tuple[str, str], tuple[StrategyMove, str]] = {}
        for mv in moves:
            cls = price_class(mv.ref)
            if mv.ladder is None or cls is None:
                kept.append(mv)
                continue
            key = (mv.source, cls)
            min_surplus = run.params.min_buy_surplus
            plan = plan_dealer_buy(mv, policies.get(key), curves.get(key), self.rules, min_surplus, room)
            if plan.move is None:
                if mv.source not in busy:
                    skipped.setdefault(key, (mv, plan.skip or "skip"))
                continue
            run.plans[(mv.source, mv.ref)] = plan
            kept.append(plan.move)
        for (dealer, cls), (mv, why) in skipped.items():
            if self._learned_skips.get((dealer, cls)) == why:
                continue
            self._learned_skips[(dealer, cls)] = why
            self.rec.decide(
                run.snap.clock.tick,
                "dealer_skip",
                f"skip {dealer} for {mv.ref}: {why}",
                inputs={"blocked_dealer": dealer, "wanted": mv.ref, "why": why},
                reason=why,
                guardrail="-",
                chosen=False,
                status="rejected",
            )
        return kept

    def _open_one(self, run: _TickRun, op: Opening, ctx: Context) -> None:
        tick = run.snap.clock.tick
        if stops := kill_switch(self.rules):  # the pack gate's Jev calls may take seconds: read it again
            self.log(f"tick {tick} taker: kill switch on: no thread opened with {op.dealer} ({'; '.join(stops)})")
            return
        lower = self.reopen_at.get((op.dealer, op.item))
        if lower is not None and lower < op.plan.start:  # she held her opening ask last time: start lower
            op = replace(op, plan=replace(op.plan, start=lower), reason=f"{op.reason}; reopened lower")
        dp = run.plans.get((op.dealer, op.item))
        if dp is not None and dp.final_max is not None:
            op = replace(op, plan=replace(op.plan, final_max=dp.final_max, lift_after=LIFTED_FINAL_MIN_BIDS))
        verdict = check(Action("buy", op.item, op.rarity, op.plan.start), ctx, self.rules)
        if not verdict.allowed and not verdict.halted and op.item in run.boost and self.cards is not None:
            self.cards.unboost(op.item)  # a refused release never holds this dealer's slot again
        plan = f"{op.plan.start}→{op.plan.max_price} step {op.plan.step}"
        final = f", final ≤ {op.plan.final_max}" if op.plan.final_max is not None else ""
        # Private keys (not on the public /state allow-list): which learning changed the plan, and what was recalled.
        notes = dp.changed_by if dp is not None else []
        recalled = self._recalled(run, op) if verdict.allowed else []
        inputs = {
            "dealer": op.dealer,
            "item": op.item,
            "rarity": op.rarity,
            "value": op.value,
            "plan": plan,
            "score": op.move.score,
            "surplus": op.move.surplus,
            "final_max": op.plan.final_max,
            "changed_by": notes,
            "learned": dp.lessons if dp is not None else [],
            "recalled": recalled,
        }
        what = f"open thread with {op.dealer} for {op.item} (ladder {plan}{final}, worth {op.value:g})"
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
        if body is None and self.learner is not None and self.rec.last_error is not None:
            self.learner.refused(op.dealer, self.rec.last_error, run.snap.us, run.snap.clock, op.item)
        if body is not None and isinstance(body.get("id"), int):
            reopened = self.reopen_at.pop((op.dealer, op.item), None) is not None
            self.convs[op.dealer] = Conversation(
                op.dealer,
                op.item,
                op.rarity,
                op.value,
                op.reason,
                Negotiation(op.plan),
                int(body["id"]),
                tick,
                reopened=reopened,
                notes=tuple(notes),
                recalled=tuple(recalled),
            )
            self._opened(run, op.dealer, op.item, int(body["id"]))

    def _recalled(self, run: _TickRun, op: Opening) -> list[str]:
        """The top lessons about this dealer and item, recalled once per opened thread (quoted data for the log;
        they never set a price). None without the recall or with less than `jev_min_budget_s` of the tick left."""
        if self.lessons is None or self.outcome_learner is None or run.window.left() < self.config.jev_min_budget_s:
            return []  # no learner (BAZAAR_LEARN=0): no recall either (review #158 r3 P3)
        situation = f"open a thread with {op.dealer} to buy {op.item} ({op.rarity})"
        found = self.lessons(situation, subjects=(op.dealer,), tick=run.snap.clock.tick)
        return [str(x.get("quoted_lesson")) for x in found if isinstance(x, dict)]

    def _desk_moves(self, run: _TickRun, *, held: bool = False) -> list[tuple[DeskMove, dict[str, Any]]]:
        """This tick's move per conversation. `held` (kill switch on): only threads that closed are wrapped
        up; an open one is left as it is, and the tick does not count toward its tick limit."""
        out = []
        for dealer, conv in list(self.convs.items()):
            thread = self._thread_of(run, conv)  # keyed: one at a time, like every keyed read
            if thread is None:  # refused: this conversation waits a tick, the others go on
                continue
            self._keep(thread, run.snap, conv)
            if held and str(thread.get("status") or "open") == "open":
                continue
            conv.ticks += 1
            self._inspect(run, conv, thread)
            self._tag(run, conv, thread)
            dm = plan_conversation(conv, thread, self.rules.dealer_max_ticks_per_thread, run.snap.clock.tick)
            if self.bluff is not None and dm.status == "open":  # her new offer scores our last tactic
                tick = run.snap.clock.tick
                self.bluff.observe(_conversation(conv), their_price=dm.ask, their_offer=dm.offer_id, tick=tick)
            if dm.status != "open":
                self._finished(run, conv, thread)
                del self.convs[dealer]
                continue
            if dm.ignored:
                self.log(f"tick {run.snap.clock.tick} taker: {dealer} offer ignored: {dm.ignored}")
            out.append((self._jev_early(run, dm), thread))
        return out

    def _card_index(self, run: _TickRun) -> CardIndex:
        """The catalog as the inspector reads it: built once per tick, from that tick's catalog read."""
        if run.cards is None:
            run.cards = CardIndex.from_catalog(run.snap.catalog)
        return run.cards

    def _inspect(self, run: _TickRun, conv: Conversation, thread: dict[str, Any]) -> None:
        """The offer inspector on the dealer's newest offer. A certain trickster becomes a `flag` decision
        row with its structural evidence (one row per message). It is SENT only by a live taker with GUARDRAILS.md
        allow_flags on (default off: the row says `would flag`), to an opted-in `flag_dealers` dealer only, at most
        `max_flags_sent` ever per data dir, never to a trusted dealer, never twice."""
        tick = run.snap.clock.tick

        def guard(i: Inspection) -> str | None:
            verdict = check(Action("flag", str(i.message_id)), self._ctx(run), self.rules)
            return None if verdict.allowed else str(verdict)

        def log(line: str) -> None:
            self.log(f"tick {tick} taker: {line}")

        def record(i: Inspection, why: str | None) -> None:
            """One row per message and decision: a denied row is never the one a later send is booked on."""
            mid = i.message_id
            if mid is None:
                return
            known = self._flag_rows.get(mid)
            if known is None or (why is None and not known[1]):
                self._flag_rows[mid] = (self._flag_row(run, conv, thread, i, why), why is None)

        def send(message_id: int, reason: str) -> Any:
            row = self._flag_rows.get(message_id)
            return self._send_flag(tick, row[0] if row and row[1] else None, message_id, reason)

        try:
            cards = self._card_index(run)
            flag_step(
                thread,
                conv.dealer,
                cards,
                self.flags,
                guard=guard,
                send=send if self.live else None,
                log=log,
                topic=conv.topic,
                record=record,
            )
        except Exception as e:  # inspection must never break the desk
            self.log(f"tick {tick} taker: offer inspection failed ({type(e).__name__}); desk continues")

    def _tag(self, run: _TickRun, conv: Conversation, thread: dict[str, Any]) -> None:
        """Tag the dealer's newest words for injection shapes (S1); a tagger bug never costs the desk its tick."""
        try:
            mid, text = latest_message(thread, conv.dealer)
            self.injections.tag(conv.dealer, mid, text, run.snap.clock.tick, self.log)
        except Exception as e:
            self.log(f"tick {run.snap.clock.tick} taker: injection tagging failed ({type(e).__name__}); desk continues")

    def _flag_row(
        self, run: _TickRun, conv: Conversation, thread: dict[str, Any], i: Inspection, why: str | None
    ) -> int:
        """The decision row that proves a flag: what the thread asked, what the structure binds, and how the
        words contradict it (catalog refs and names only, never the counterparty's raw text)."""
        standing = (o for o in thread.get("standing_offers") or [] if isinstance(o, dict) and o.get("id") == i.offer_id)
        said = (m.get("offer") for m in thread.get("messages") or [] if isinstance(m, dict))
        carried = (o for o in said if isinstance(o, dict) and o.get("id") == i.offer_id)
        offer = next(standing, None) or next(carried, None) or {}  # a withdrawn offer is still in its message
        inputs = {
            "dealer": conv.dealer,
            "thread": conv.thread_id,
            "message_id": i.message_id,
            "offer_id": i.offer_id,
            "asked": i.asked,
            "bound": list(i.bound),
            "verdict": i.verdict,
            "findings": list(i.findings),
            "structure": {"give": offer.get("give"), "want": offer.get("want")},
        }
        what = f"flag message {i.message_id} from {conv.dealer}" + (f" ({why})" if why else "")
        return self.rec.decide(
            run.snap.clock.tick,
            "flag",
            f"{what}: {i.reason}",
            inputs=inputs,
            reason=i.reason,
            guardrail=why or "allowed",  # every reason a flag was not sent, not only a guardrail denial
            chosen=why is None,
            status="approved" if why is None else "rejected",
            thread_id=conv.thread_id,
            move={"flag": i.message_id},
        )

    def _send_flag(self, tick: int, did: int | None, message_id: int, reason: str) -> Any:
        """POST /api/flags, booked on its decision row. A refusal is re-raised: flag_step decides whether the
        flag may be tried again (only a 429: not processed) or never (any other refusal, or no answer)."""
        request = {"message_id": message_id, "reason": reason}
        try:
            body = self.team.flag(message_id, reason)
        except BazaarError as e:
            if did is not None:
                self.rec.decisions.executed(did, tick, "flag", request, None, e.code)
                self.rec.decisions.settle(did, "failed")
            raise
        body = body if isinstance(body, dict) else {"result": body}
        if did is not None:
            self.rec.decisions.executed(did, tick, "flag", request, body, None)
            self.rec.decisions.settle(did, "done")
        return body

    def _gate(self, run: _TickRun, p: AcceptProposal) -> Gate | None:
        """The accept gate on the exact offer this accept binds (None: `inspect_accepts` is off). A payload the
        gate cannot read refuses the accept (fail closed) and never costs the desk its tick."""
        if not self.rules.inspect_accepts:
            return None
        try:
            return self._gate_unchecked(run, p)
        except Exception as e:  # a malformed counterparty payload: no accept, the desk goes on
            kind: GateKind = "dealer" if p.desk else "team" if p.swap else "board"
            return Gate(kind, p.offer_id, "block", (f"unreadable offer ({type(e).__name__})",))

    def _gate_unchecked(self, run: _TickRun, p: AcceptProposal) -> Gate:
        if p.swap is not None:
            a = p.swap
            payload = self.team_desk.thread_payload(a.thread_id)
            if payload is None:
                return Gate("team", p.offer_id, "block", (f"thread {a.thread_id} was not read this tick",))
            copy = next((x for x in run.snap.me.get("assets") or [] if x.get("id") == a.trade.asset_id), None)
            return swap_gate(payload, run.snap.us, a.offer, a.trade, copy)
        if p.sell is not None:
            if p.bid is None:
                return Gate("board", p.offer_id, "block", ("a sell with no bid to inspect",))
            copy = next((a for a in run.snap.me.get("assets") or [] if a.get("id") == p.asset_id), None)
            return bid_gate(p.bid, p.ref, p.sell.price, copy)
        if p.desk is not None:
            topic = p.desk.conv.topic
            return dealer_gate(p.thread or {}, p.source, p.offer_id, p.price, topic, self._card_index(run))
        if p.candidate is not None:
            c = p.candidate
            return board_gate(c.offer, p.ref, c.total, c.fee, p.rarity)
        return Gate("board", p.offer_id, "block", ("an accept with no offer to inspect",))

    def _thread_of(self, run: _TickRun, conv: Conversation) -> dict[str, Any] | None:
        """One dealer thread; a refusal skips that conversation for this tick only (no tick counted, no move) and
        keeps its card off the boards this tick (`run.unread`). A thread the server no longer knows is dropped."""
        try:
            thread: dict[str, Any] = self.team.thread(conv.thread_id)
            return thread
        except BazaarError as e:
            run.unread.add(conv.item)
            # gone: refused as unknown (404, whatever its code) AND missing from this tick's list of our open
            # threads; one read never retires a thread the server still lists
            if e.status == THREAD_GONE_STATUS and conv.thread_id not in run.listed:
                self.convs.pop(conv.dealer, None)
                gone = {"status": "closed", "closed_reason": "gone"}  # no deal can be booked on a thread it lost
                self._wrapped(run, conv.thread_id, conv.dealer, conv.item, gone, None)  # a restart never re-reads it
                self.log(
                    f"tick {run.snap.clock.tick} taker: thread {conv.thread_id} with {conv.dealer} is gone; dropped"
                )
                return None
            self.log(
                f"tick {run.snap.clock.tick} taker: thread {conv.thread_id} with {conv.dealer} refused {e.code}; "
                f"it waits a tick, and {conv.item} is not bought elsewhere this tick"
            )
            return None

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
        advice = self._ask_jev(
            run,
            offer_state(p, run.snap, self._ctx(run, skip_thread=conv.thread_id), self.rules, 1, self._rival_moves()),
        )
        if advice.verdict != "yes":
            return dm
        return replace(dm, move=apply_advice(dm.move, "accept", conv.neg, dm.ask, dm.offer_id))

    def _finished(self, run: _TickRun, conv: Conversation, thread: dict[str, Any]) -> None:
        status, tick = str(thread.get("status")), run.snap.clock.tick
        price = deal_price(conv, thread)
        if status == "deal" and price is not None and self.live:
            self.ledger.record("spend", tick, run.snap.clock.t_hours, int(price), conv.item)
        if status == "deal" and self.live:
            self._after_deal(run, f"deal in thread {conv.thread_id}")
        if self.learner is not None:
            self.learner.thread_closed(thread, run.snap.us, run.snap.clock)
        if self.bluff is not None:
            reason = thread.get("closed_reason")
            self.bluff.ended(
                _conversation(conv), status=status, closed_reason=reason if isinstance(reason, str) else None, tick=tick
            )
        self._wrapped(run, conv.thread_id, conv.dealer, conv.item, thread, price)

    def _converse(self, run: _TickRun, desk: list[tuple[DeskMove, dict[str, Any]]]) -> None:
        taken = {p.desk.conv.dealer for p in run.accepted if p.desk is not None}
        bought = {p.ref for p in run.accepted if p.desk is None and p.sell is None}  # a board buy: thread moot
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
            at_final = dm.final and move.price is not None and move.price == dm.ask  # meeting her final (N14a)
            action = Action("bid", conv.item, conv.rarity, move.price, final=at_final)
        else:  # a walk closes the thread: only the kill switch can refuse it
            action = Action("close_thread", str(conv.thread_id))
        verdict = check(action, self._ctx(run, skip_thread=conv.thread_id), self.rules)
        verdict_text = str(verdict)
        if verdict.halted:  # the kill switch went on this tick: hold, the thread stays open
            self.log(f"tick {tick} taker: kill switch on: holding {move.kind} on thread {conv.thread_id} ({verdict})")
            return
        if not verdict.allowed and move.kind == "bid" and self._unsettled != Commitments():
            # Denied only because of accepts `/api/me` may already show (a pack's cash counts for 2 ticks):
            # wait a tick instead of walking a thread a settled view would let us bid in.
            ctx = self._ctx(run, skip_thread=conv.thread_id, unsettled=False)
            if check(action, ctx, self.rules).allowed:
                self.log(f"tick {tick} taker: {conv.dealer} wait (guardrail with unsettled accepts: {verdict})")
                return
        if not verdict.allowed and move.kind == "bid" and unread_only(verdict.violations):
            # No official value this tick (a failed read): hold, the thread stays open (review #177 P1-2).
            self.log(f"tick {tick} taker: {conv.dealer} hold on thread {conv.thread_id} ({verdict})")
            return
        if not verdict.allowed:
            move = Move("walk", reason=f"guardrail: {verdict}")
        choice = self._tactic(conv, move, dm.ask)
        inputs = {
            "dealer": conv.dealer,
            "thread": conv.thread_id,
            "item": conv.item,
            "her_ask": dm.ask,
            "final": dm.final,
            "our_bids": list(conv.neg.bids),
            "max": conv.neg.plan.max_price,
            "final_max": conv.neg.plan.final_max,
            "changed_by": list(conv.notes),
            "recalled": list(conv.recalled),
            **(choice.inputs() if choice is not None else {}),  # private keys: never in the public view
        }
        what = f"{move.kind} {move.price or ''} to {conv.dealer} on thread {conv.thread_id} for {conv.item}"
        what += f" · tactic {choice.tactic}" if choice is not None and choice.tactic else ""
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
            closed = self.rec.send(
                did, tick, "close_thread", {"thread": conv.thread_id}, lambda: self.team.close_thread(conv.thread_id)
            )
            ended_as = closed.get("status") if isinstance(closed, dict) else None
            if closed is None or ended_as not in (None, "closed", "walked"):
                # Refused, or answered with an ended thread (our simulator says 200 {"status": "deal"}): her
                # "Deal!" may have landed first, and a deal is never dropped unbooked.
                self._after_refused_walk(run, conv, move)
                return
            # we never read this thread again: keep how it ended (no extra request)
            ended = {**thread, "status": ended_as or "walked", "closed_reason": thread.get("closed_reason") or "walked"}
            self._keep(ended, run.snap, conv)
            self.convs.pop(conv.dealer, None)
            self._wrapped(
                run, conv.thread_id, conv.dealer, conv.item, {"status": "closed", "closed_reason": "walk"}, None
            )
            if self.bluff is not None:
                self.bluff.dropped(_conversation(conv))
            if move.reopen:
                self._held_opening(run, conv)
            elif move.rest:  # she stopped answering: do not open, bid and walk on this item every few ticks
                self.cooling[(conv.dealer, conv.item)] = run.snap.clock.t_hours + 1.0
            return
        price = int(move.price or 0)
        text = bid_words(
            choice.words(self.words_fn) if choice is not None else self.words_fn,
            WordsRequest(
                conv.dealer,
                price,
                len(conv.neg.bids),
                conv.item,
                lessons=self._lessons_for(run, conv),
                tone=self._tones.get(conv.dealer, ""),
            ),
            thread,
            run.snap.clock,
            run.window.deadline,
        )
        if not run.window.open():
            self.rec.decisions.settle(did, "expired")
            self.log(f"tick {tick} taker: the words took the rest of the tick; {conv.dealer} bid next tick")
            return
        if stops := kill_switch(self.rules):  # it may have gone on while the words were written: hold
            self.rec.decisions.settle(did, "rejected")
            self.log(f"tick {tick} taker: kill switch on: holding bid on thread {conv.thread_id} ({'; '.join(stops)})")
            return
        body = self.rec.send(
            did,
            tick,
            "say",
            {"thread": conv.thread_id, "price": price},
            lambda: self.team.say(conv.thread_id, text, price=price),
        )
        if body is not None:
            conv.neg.bids.append(price)
            if self.bluff is not None and choice is not None:
                self.bluff.sent(
                    choice, their_price=dm.ask, their_offer=dm.offer_id, tick=tick, message=message_id(body)
                )
        elif not self.rec.maybe_landed:
            return
        self._commit(run, price, conv.item, conv.thread_id)

    def _after_refused_walk(self, run: _TickRun, conv: Conversation, move: Move) -> None:
        """Our close was refused: read the thread again. Ended (a deal that landed first): wrap it up, its
        spend booked; ended without a deal after she held her opening (our close landed, its answer was
        lost): reopen lower as after any held walk. Still open (or unreadable): keep the conversation; the
        next tick decides again. A rate limit is no reason to send one more request now: wait for the tick."""
        if self.rec.last_code in ("rate_limited", "wait_for_tick", "too_many_requests"):
            return
        try:
            after = self.team.thread(conv.thread_id)
            self._keep(after, run.snap, conv)  # the read we just made: how the thread really ended
        except BazaarError as e:
            self.log(
                f"tick {run.snap.clock.tick} taker: thread {conv.thread_id} unreadable after a refused walk ({e.code})"
            )
            return
        status = str(after.get("status") or "open")
        if status != "open":
            self._finished(run, conv, after)
            self.convs.pop(conv.dealer, None)
            if move.reopen and status != "deal":
                self._held_opening(run, conv)
            elif move.rest and status != "deal":  # she stopped answering: rest the item, as after a clean walk
                self.cooling[(conv.dealer, conv.item)] = run.snap.clock.t_hours + 1.0

    def _held_opening(self, run: _TickRun, conv: Conversation) -> None:
        """She held her opening ask and we walked (a deal there scores nothing): reopen once with a lower
        first bid; when the lower thread held too, leave that item with that dealer for a game hour."""
        key, clock = (conv.dealer, conv.item), run.snap.clock
        lower = None if conv.reopened else reopen_start(conv.neg)
        if lower is not None:
            self.reopen_at[key] = lower
            self.log(f"tick {clock.tick} taker: {conv.dealer} held her opening ask: reopen {conv.item} from {lower}")
        else:
            self.cooling[key] = clock.t_hours + 1.0
            self.log(
                f"tick {clock.tick} taker: {conv.dealer} held her opening ask again: {conv.item} rests 1 game hour"
            )

    def _lessons_for(self, run: _TickRun, conv: Conversation) -> tuple[str, ...]:
        """The top lessons about this dealer and item for the words (cached for a few ticks; none when short)."""
        if self.lessons is None or self.words_fn is template_words or run.window.left() < self.config.jev_min_budget_s:
            return ()  # the templates never read lessons: no recall for them
        situation = f"bid to {conv.dealer} for {conv.item} ({conv.rarity})"
        found = self.lessons(situation, subjects=(conv.dealer,), tick=run.snap.clock.tick)
        return tuple(str(x["quoted_lesson"]) for x in found)

    def _tactic(self, conv: Conversation, move: Move, her_ask: int | None) -> Choice | None:
        """The bluff tactic for a bid's words (N16): a dealer bid only, after the guardrails passed it. It never
        changes the price; it never sees our max or value except to keep an invented number off them."""
        if self.bluff is None or move.kind != "bid" or move.price is None:
            return None
        avoid = private_numbers(conv.neg.plan.max_price, conv.value)
        cp = Counterparty.dealer(conv.dealer)
        conversation, step = _conversation(conv), len(conv.neg.bids)
        return self.bluff.choose(cp, "buy", conversation, step, int(move.price), avoid=avoid, their_price=her_ask)

    # ------------------------------------------------------------ accepts (shared quota)

    def _accept(self, run: _TickRun, proposals: list[AcceptProposal]) -> None:
        clock = run.snap.clock
        limit = accept_limit(clock, self.rules)
        used = self.ledger.accepts_in_tick(clock.tick) if self.live else self._dry_accepts.get(clock.tick, 0)
        self._accepts_stop, self._accepts_refused = None, 0
        for p in rank_accepts(proposals):
            if used >= limit:
                self._skip(run, p, f"accept quota {limit}/tick used", "rejected")
                continue
            if self._accepts_stop is not None:
                self._skip(run, p, self._accepts_stop, "rejected")
                continue
            if p.ref in {a.ref for a in run.accepted}:
                self._skip(run, p, f"already buying {p.ref} this tick", "rejected")
                continue
            if self._accept_one(run, p, limit):
                used += 1
                run.accepted.append(p)
        if not self.live:
            self._dry_accepts = {clock.tick: used}

    def _skip(
        self,
        run: _TickRun,
        p: AcceptProposal,
        why: str,
        status: Status,
        jev: JevAdvice | None = None,
        gate: Gate | None = None,
    ) -> None:
        kind = accept_kind(p)
        verb = "" if status == "expired" else "skip "
        self.rec.decide(
            run.snap.clock.tick,
            kind,
            f"{verb}{p.ref} at {p.price} from {p.source}: {why}",
            inputs=_with_gate(p.inputs, gate),
            reason=p.reason,
            guardrail=why if why.startswith("denied") else "-",
            chosen=False,
            status=status,
            jev=jev,
        )

    def _accept_one(self, run: _TickRun, p: AcceptProposal, limit: int) -> bool:
        if p.sell is not None:
            return self._accept_bid(run, p, p.sell, limit)
        if p.swap is not None:
            return self._accept_swap(run, p, p.swap, limit)
        clock = run.snap.clock
        skip_thread = p.desk.conv.thread_id if p.desk else None
        skip_offer = p.candidate.replaces_bid.id if p.candidate and p.candidate.replaces_bid else None
        ctx = self._ctx(run, skip_thread=skip_thread, skip_offer=skip_offer)
        maker = p.candidate.offer.maker if p.candidate is not None else None  # a dealer is not a counterparty
        ask = p.candidate.offer.price if p.candidate is not None else None  # the maker's share: without the fee
        final = p.final and p.desk is not None  # a dealer's final: its cap is `final_cap_for` (N14a)
        action = Action("accept_buy", p.ref, p.rarity, p.price, counterparty=maker, volume=ask, final=final)
        verdict = check(action, ctx, self.rules)
        if not verdict.allowed:
            self._skip(run, p, str(verdict), "rejected")
            return False
        gate = self._gate(run, p)
        if gate is not None and not gate.allowed:
            self.log(f"tick {clock.tick} taker: inspector {gate.verdict} on offer {p.offer_id}: {gate.reason}")
            self._skip(run, p, f"inspector {gate.verdict}: {gate.reason}", "rejected", gate=gate)
            return False
        jev = (
            self._ask_jev(run, offer_state(p, run.snap, ctx, self.rules, limit, self._rival_moves()))
            if p.source == "board"
            else None
        )
        if jev is not None and jev.verdict == "no":
            self._skip(run, p, f"jev no ({jev.value:.2f}): kept the accept slot", "rejected", jev, gate)
            return False
        if self.live:
            self._duel_grace(run)
        if any(item.startswith("duel:") for item in self.ledger.accept_items(clock.tick)):
            self._skip(run, p, "a duel holds the team's accept this tick (duels first)", "rejected", jev, gate)
            return False
        if not run.window.open():
            self._skip(run, p, "tick budget spent, not sent late", "expired", jev, gate)
            return False
        if self.live and not self._fresh_tick(clock):
            run.window = TickWindow(clock.tick, 0.0, self.now)  # every later send this tick is dropped too
            self._accepts_stop = "the tick ended before the send"
            self._skip(run, p, "the tick ended before the send", "expired", jev, gate)
            return False
        if stops := kill_switch(self.rules):  # Jev and the duel grace took seconds: it may have gone on since
            self._skip(run, p, f"kill switch on: holding ({'; '.join(stops)})", "rejected", jev)
            return False
        if self.live and not self.ledger.reserve_accept(clock.tick, clock.t_hours, p.price, p.ref, limit):
            self._accepts_stop = "another process took the team's accept this tick"  # no clock read per proposal
            self._skip(run, p, self._accepts_stop, "rejected", jev, gate)
            return False
        kind = "accept_ask" if p.source == "board" else "dealer_accept"
        where = f"on {p.inputs.get('venue')}" if p.source == "board" else f"from {p.source}"
        did = self.rec.decide(
            clock.tick,
            kind,
            f"accept {p.ref} {where} for {p.price} (worth {p.value:g}, surplus {p.surplus:.1f}) · guardrails {verdict}",
            inputs=_with_gate(p.inputs, gate),
            reason=p.reason,
            guardrail=str(verdict),
            chosen=True,
            status="approved",
            jev=jev,
            thread_id=skip_thread,
            move={"accept": p.offer_id, "price": p.price},
        )
        if not self.live:
            run.spent += p.price if p.desk is None else 0  # a live board accept is booked in the ledger
            self._commit(run, p.price, p.ref, skip_thread, maker, ask)
            return True
        body = self.rec.send(did, clock.tick, "accept", {"offer": p.offer_id}, lambda: self.team.accept(p.offer_id))
        if body is None and cost_nothing(self.rec.last_code, self.rec.last_status):
            # Refused with a 4xx, so it cost nothing (RULES.md): the team's accept is free again, for the next
            # candidate or a duel. A rate limit, a refusal every candidate would meet (cash, cool-off, quota)
            # or MAX_REFUSED_ACCEPTS refusals end the tick's accepts: each try costs a clock read and a POST.
            code = self.rec.last_code
            self._accepts_refused += 1
            if code in RATE_LIMITED or code in TEAM_WIDE_REFUSALS or self._accepts_refused >= MAX_REFUSED_ACCEPTS:
                self._accepts_stop = f"accept refused {code}: no more accepts this tick"
            try:
                self.ledger.release_accept(clock.tick, p.ref)
            except LedgerUnavailable as e:  # the slot stays taken (fail closed); the tick goes on
                self._accepts_stop = f"accept refused {code}; its slot could not be given back ({e})"
            return False
        # A 5xx may come after the game applied the accept: booked as landed (fail safe for the caps). Only for
        # accepts: a maker post refused with a 5xx is not booked (nothing would ever refund it).
        landed = self.rec.maybe_landed or (self.rec.last_status or 0) >= 500
        if body is None and not landed:
            return True  # `wait_for_tick`: the team's accept of this tick is already used, the slot stays spent
        # Accepted, or lost on the way back (a network error): booked as bought (fail safe for the caps).
        if p.desk is not None:
            p.desk.conv.accepted_tick, p.desk.conv.accepted_price = clock.tick, p.price
        else:
            self.ledger.record("spend", clock.tick, clock.t_hours, p.price, p.ref)
            if body is not None and p.candidate is not None and p.candidate.replaces_bid is not None:
                self._withdraw(run, p.candidate.replaces_bid)
        self._commit(run, p.price, p.ref, skip_thread, maker, ask)
        # If /me already shows the accept paid, its cash counts twice for the rest of the tick: kept on purpose. It
        # only ever denies, and a same-tick cash drop from another process cannot be told apart from this deal.
        self._after_deal(run, f"accept of offer {p.offer_id}")  # after the books: a failed re-read loses nothing
        return True

    def _after_deal(self, run: _TickRun, what: str) -> None:
        """Album first after every deal: re-read /me (stored for every process) and decide on it from now on."""
        if self.holdings is None:
            return
        tick = run.snap.clock.tick
        try:
            run.snap = run.snap.with_me(self.holdings.after_deal(run.snap.clock, what))
        except Exception as e:  # any failure (a refusal, a dropped connection): the next tick reads /me again
            code = e.code if isinstance(e, BazaarError) else type(e).__name__
            self.log(f"tick {tick} taker: /me re-read after {what} failed ({code}); the next tick reads it")
            return
        self.log(f"tick {tick} taker: {what}: {run.snap.holdings.line() if run.snap.holdings else '/me re-read'}")

    def _accept_bid(self, run: _TickRun, p: AcceptProposal, op: Opportunity, limit: int) -> bool:
        """Sell our least valuable copy into a standing bid (`accept_bids`): the same gates as a buy (the
        guardrails with this tick's commitments, the duel grace, the shared accept quota), then
        `accept(offer, assets=[copy])`. Nothing is booked as spend: the bid's cash comes in."""
        clock = run.snap.clock
        your_value = next(
            (float(a["your_value"]) for a in run.snap.me.get("assets") or [] if a.get("id") == p.asset_id), None
        )
        # The sell floor sees what we net (the fee comes out of the bid); the maker's share counts the bid.
        action = Action(
            "accept_sell",
            p.ref,
            p.rarity,
            op.price - op.fee,
            your_value=your_value,
            counterparty=op.maker,
            volume=op.price,
        )
        verdict = check(action, self._ctx(run), self.rules)
        if not verdict.allowed:
            self._skip(run, p, str(verdict), "rejected")
            return False
        gate = self._gate(run, p)  # S1: the bid's structure is what we priced, and the copy is one of ours
        if gate is not None and not gate.allowed:
            self.log(f"tick {clock.tick} taker: inspector {gate.verdict} on bid {op.offer_id}: {gate.reason}")
            self._skip(run, p, f"inspector {gate.verdict}: {gate.reason}", "rejected", gate=gate)
            return False
        if not self._slot(run, p, 0, f"sell:{p.asset_id}", limit, gate):
            return False
        did = self.rec.decide(
            clock.tick,
            "accept_bid",
            f"sell {p.ref} #{p.asset_id} into {op.maker}'s bid {op.offer_id} on {op.venue} for {op.price} "
            f"(fee {op.fee}, surplus {op.ours:.1f}) · guardrails {verdict}",
            inputs=_with_gate(p.inputs, gate),
            reason=p.reason,
            guardrail=str(verdict),
            chosen=True,
            status="approved",
            move={"accept": op.offer_id, "assets": [p.asset_id]},
        )
        if self.live:
            body = self.rec.send(
                did,
                clock.tick,
                "accept",
                {"offer": op.offer_id, "assets": [p.asset_id]},
                lambda: self.team.accept(op.offer_id, assets=[p.asset_id]),
            )
            if body is None and not self.rec.maybe_landed:
                return True  # the reserved slot stays spent, as for a buy
        # This tick's later checks: the copy is promised and the maker's share counts the sale.
        run.offers.append(
            {
                "id": -1,
                "status": "open",
                "maker": run.snap.us,
                "give": {"assets": [{"id": p.asset_id, "ref": p.ref}]},
                "want": {"cash": op.price},
                "to": op.maker,
                "notional": op.price,
            }
        )
        if self.live:
            self._after_deal(run, f"sale into bid {op.offer_id}")  # album first (#105), as after any accept
        return True

    def _slot(
        self, run: _TickRun, p: AcceptProposal, price: int, item: str, limit: int, gate: Gate | None = None
    ) -> bool:
        """The gates every accept of a sell or a swap passes after its guardrails: the duel grace (duels
        first), the tick window, a fresh clock, the kill switch read again, then the team's accept slot."""
        clock = run.snap.clock
        if self.live:
            self._duel_grace(run)
        if any(i.startswith("duel:") for i in self.ledger.accept_items(clock.tick)):
            self._skip(run, p, "a duel holds the team's accept this tick (duels first)", "rejected", gate=gate)
            return False
        if not run.window.open():
            self._skip(run, p, "tick budget spent, not sent late", "expired", gate=gate)
            return False
        if self.live and not self._fresh_tick(clock):
            run.window = TickWindow(clock.tick, 0.0, self.now)
            self._skip(run, p, "the tick ended before the send", "expired", gate=gate)
            return False
        if stops := kill_switch(self.rules):  # the duel grace took seconds: it may have gone on since
            self._skip(run, p, f"kill switch on: holding ({'; '.join(stops)})", "rejected", gate=gate)
            return False
        if self.live and not self.ledger.reserve_accept(clock.tick, clock.t_hours, price, item, limit):
            self._skip(run, p, "another process took the team's accept this tick", "rejected", gate=gate)
            return False
        return True

    def _accept_swap(self, run: _TickRun, p: AcceptProposal, a: SwapAccept, limit: int) -> bool:
        """Take a team's offer in a swap thread (N17): our copy (and any cash we add, plus the fee: we are the
        accepting side) for their card, through the guardrails as #79's `Swap` describes it, then the same
        gates as any accept. The cash we pay is booked as spend; the thread settles at the next tick."""
        clock, view = run.snap.clock, run.team_view
        if view is None:
            self._skip(run, p, "denied: no team view this tick", "rejected")
            return False
        verdict = self.team_desk.guard_accept(view, a)
        if not verdict.allowed:
            self._skip(run, p, str(verdict), "rejected")
            return False
        gate = self._gate(run, p)  # S1: their standing offer, read again, is the swap we priced
        if gate is not None and not gate.allowed:
            self.log(f"tick {clock.tick} taker: inspector {gate.verdict} on thread {a.thread_id}: {gate.reason}")
            self._skip(run, p, f"inspector {gate.verdict}: {gate.reason}", "rejected", gate=gate)
            return False
        ok, advice, why = self.team_desk.jev_gate(view, a.trade, a.offer.net_cash, a.fee, a.thread_id, 0, "accept")
        if not ok:  # Jev `team_swap_worth_it`: only a decided yes takes a team's offer (fail closed)
            self._skip(run, p, why, "rejected", advice, gate)
            return False
        if not view.window_open():  # Jev may have taken seconds: never sent late
            self._skip(run, p, "the tick ended before the send", "expired", advice, gate)
            return False
        pay = a.offer.cash_out + a.fee
        if not self._slot(run, p, pay, f"team:{a.thread_id}", limit, gate):
            return False
        did = self.rec.decide(
            clock.tick,
            "team_accept",
            f"take {a.offer.team}'s swap offer {a.offer.offer_id} on thread {a.thread_id}: "
            f"{a.trade.refs[0]} for {a.trade.refs[1]}, cash {a.offer.net_cash:+d}, fee {a.fee} · guardrails {verdict}",
            inputs=_with_gate(p.inputs, gate),
            reason=f"{a.verdict.reason}; {a.trade.reason}",
            guardrail=str(verdict),
            chosen=True,
            status="approved",
            thread_id=a.thread_id,
            move={"kind": "team_accept"},  # public: never their offer id (a private thread)
            jev=advice,
        )
        if self.live:
            if not self.team_desk.clear_before_accept(view, a, did):  # our own offer there goes first
                return True  # a cancel was refused: their offer is not taken (the slot stays spent)
            pick = a.pick
            body = self.rec.send(
                did,
                clock.tick,
                "accept",
                {"their_offer": a.offer.offer_id},  # not the public "offer" key: a team thread is private
                lambda: self.team.accept(a.offer.offer_id, assets=pick),
            )
            if body is None and not self.rec.maybe_landed:
                return True  # the reserved slot stays spent, as for a buy
            if pay > 0:  # accepted, or maybe landed: booked (fail safe for the caps)
                self.ledger.record("spend", clock.tick, clock.t_hours, pay, f"{TEAM_SPEND}{a.trade.refs[1]}")
        self.team_desk.accepted(a, clock.tick)
        self._commit(run, pay, a.trade.refs[1], a.thread_id, a.offer.team, pay)
        run.offers.append(  # our copy is promised too: later checks this tick never offer it again
            {"id": -2, "status": "open", "maker": run.snap.us, "give": {"assets": [{"id": a.trade.asset_id}]}}
        )
        if self.live:
            self._after_deal(run, f"swap accept in thread {a.thread_id}")  # album first (#105), as after any accept
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
        """Re-read the clock right before an accept: a tick that rolled over drops it, and so does a refused
        read (no accept is sent on a clock we could not read)."""
        try:
            fresh = Clock.model_validate(self.team.clock())
        except BazaarError as e:
            self.log(f"tick {clock.tick} taker: clock read refused {e.code} before an accept: none sent this tick")
            return False
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
                *refund_row(bid.price, bid.ref, bid.created_tick, clock.tick, clock.t_hours, clock.max_tick_seconds)
            )
            run.offers = [o for o in run.offers if o.get("id") != bid.id]

    # ------------------------------------------------------------ threads from before a restart (bite X3)

    def _wrapped(
        self, run: _TickRun, thread_id: int, dealer: str, item: str, thread: dict[str, Any], price: int | None
    ) -> None:
        """A thread of ours is over: one THREAD_CLOSED decision, so a restarted process knows its deal (if
        any) is already booked (`DecisionLog.thread_trails`)."""
        status = str(thread.get("status"))
        paid = price if status == "deal" else None
        self._trails.pop(thread_id, None)  # a listing that still shows it open must not get it adopted again
        self.rec.decide(
            run.snap.clock.tick,
            THREAD_CLOSED,
            f"thread {thread_id} with {dealer} {status} ({thread.get('closed_reason') or '-'}) price {paid or '-'}",
            inputs={"dealer": dealer, "item": item, "status": status, "price": paid},
            reason=str(thread.get("closed_reason") or status),
            guardrail="-",
            chosen=False,
            status="done",
            thread_id=thread_id,
        )

    def _opened(self, run: _TickRun, dealer: str, item: str, thread_id: int) -> None:
        """The opened thread's id in the decisions log (the open decision is written before the id is known),
        so a restarted process knows the thread is the taker's even when no bid was sent in it."""
        self.rec.decide(
            run.snap.clock.tick,
            "dealer_opened",
            f"thread {thread_id} opened with {dealer} for {item}",
            inputs={"dealer": dealer, "item": item, "owner": self._owner},
            reason="opened",
            guardrail="-",
            chosen=False,
            status="done",
            thread_id=thread_id,
        )

    def _restart_wrapup(self, run: _TickRun, threads: list[dict[str, Any]]) -> None:
        """On start: the threads the taker before this process drove (the decisions log, `restart_lookback_ticks`
        back), never wrapped up and no longer open: read once each, newest first, at most `max_dealer_threads`
        reads per tick. A deal there (the dealer took our standing bid while no process watched) is booked as
        spend NOW: dated a little late, it over-counts for a moment, never under-counts. Only rows written since
        the first process that wraps its threads up started (`PROCESS_STARTED`) are booked: an older process
        booked its deals without saying so, and booking them again would block the hour's cap. Open ones are
        adopted (`_adopt_orphans`); this runs until none of them is left. A thread whose read does not wrap it
        up is tried again next tick, at most RESTART_TRIES times; a rate limit ends the tick's reads."""
        if self._restart_checked or not self.live:
            return
        clock, cfg = run.snap.clock, self.config
        decisions = self.rec.decisions
        if self._first_start is None:
            self.rec.decide(
                clock.tick,
                PROCESS_STARTED,
                "taker started",
                inputs={"owner": self._owner},
                reason="start",
                guardrail="-",
                chosen=False,
                status="done",
            )
            self._first_start = clock.tick
        known = decisions.first_tick("taker", PROCESS_STARTED, self._owner)  # read again while Postgres is away
        self._first_start = min(self._first_start, known if known is not None else clock.tick)
        open_ids = {tid for t in threads if isinstance(tid := t.get("id"), int)}
        trails = decisions.thread_trails("taker", clock.tick - cfg.restart_lookback_ticks, open_ids)
        self._trails = {i: tr for i, tr in trails.items() if tr.owner in (None, self._owner)}
        owned = {c.thread_id for c in self.convs.values()}
        first = self._first_start
        pending = [  # open ones wait for adoption; ended ones are read if a process that wraps up drove them
            tr
            for tr in self._trails.values()
            if not tr.closed
            and tr.thread_id not in owned
            and self._restart_tries.get(tr.thread_id, 0) < RESTART_TRIES
            and (tr.thread_id in open_ids or tr.last_tick >= first)
        ]
        todo = sorted((tr for tr in pending if tr.thread_id not in open_ids), key=lambda tr: -tr.last_tick)
        self._restart_ticks += 1
        # Done once nothing is left and every store answered (a Postgres blip at boot must not end it), or after
        # `restart_lookback_ticks` ticks (a thread that can never be adopted must not cost a read every tick).
        done = not pending and decisions.complete
        self._restart_checked = done or self._restart_ticks >= cfg.restart_lookback_ticks
        for trail in todo[: cfg.max_dealer_threads]:
            self._restart_tries[trail.thread_id] = self._restart_tries.get(trail.thread_id, 0) + 1
            try:
                thread = self.team.thread(trail.thread_id)
                self._wrap_up_trail(run, trail, thread)
            except BazaarError as e:
                self.log(f"tick {clock.tick} taker: thread {trail.thread_id} from before the restart: read {e.code}")
                if e.code in RATE_LIMITED or e.code == "network":  # the server never answered: not a try
                    self._restart_tries[trail.thread_id] -= 1
                if e.code in RATE_LIMITED:
                    return
            except (TypeError, ValueError, AttributeError, KeyError) as e:  # a thread body we cannot read
                self.log(
                    f"tick {clock.tick} taker: thread {trail.thread_id} from before the restart: {type(e).__name__}"
                )

    def _wrap_up_trail(self, run: _TickRun, trail: ThreadTrail, thread: dict[str, Any]) -> None:
        clock = run.snap.clock
        status = str(thread.get("status") or "open")
        if status == "open":  # the listing was older than this read: adopted next tick
            return
        price = (settled_price(thread) or trail.top_price) if status == "deal" else None
        if price is not None:
            self.ledger.record("spend", clock.tick, clock.t_hours, int(price), trail.item)
            self._after_deal(run, f"deal in thread {trail.thread_id} from before the restart")
        dealer = str(thread.get("with") or "-")
        line = f"thread {trail.thread_id} from before the restart: {status} price {price or '-'}"
        self.log(f"tick {clock.tick} taker: {line}")
        self._wrapped(run, trail.thread_id, dealer, trail.item, thread, price)

    def _adopt_orphans(self, run: _TickRun, threads: list[dict[str, Any]]) -> None:
        """Open dealer threads the taker before this process drove (its decisions log), that no `Conversation`
        owns: a thread only another driver knows (`bazaar dealer buy`, the desk) is never touched. One where
        we know our price (the bid standing there, else our last bid in the log) is adopted at once with that
        price as its whole plan (start = max): read every tick, a deal on it booked by `_finished`, and it walks
        once her answer is not coming (a fresh bid gets the usual `MAX_WAITS`). One with no price of ours is
        closed after `orphan_after_ticks` quiet ticks (nothing of ours is at stake in it). Adoption only reads,
        so it goes on under the kill switch (a deal that lands during a hold is booked); the close holds."""
        if not self.live:
            return
        clock, us, after = run.snap.clock, run.snap.us, self.config.orphan_after_ticks
        held = bool(kill_switch(self.rules))
        dealers = {str(d.get("id")) for d in run.snap.dealers}
        owned = {c.thread_id for c in self.convs.values()}
        bids: dict[int, OpenOffer] = {}
        for o in run.offers:
            tid = o.get("thread")
            if not isinstance(tid, int) or o.get("maker") != us or o.get("status") not in (None, "open"):
                continue
            p = parse_offer(o)
            if p is not None and p.side == "bid" and (tid not in bids or p.id > bids[tid].id):
                bids[tid] = OpenOffer(p.id, "bid", p.ref, p.price, "", None, p.expires_tick, p.created_tick)
        open_ids = {t.get("id") for t in threads}
        self._quiet = {tid: seen for tid, seen in self._quiet.items() if tid in open_ids and not held}
        for t in threads:
            tid, dealer = t.get("id"), str(t.get("with") or "")
            trail = self._trails.get(tid) if isinstance(tid, int) else None
            if trail is None or trail.closed or tid in owned or dealer not in dealers or dealer in self.convs:
                continue
            bid = bids.get(trail.thread_id)
            price, ref = (bid.price, bid.ref) if bid is not None else (trail.top_price, trail.item)
            if price is not None and ref:
                fresh = bid is not None and (bid.created_tick is None or bid.created_tick > clock.tick - after)
                opened = bid.created_tick if bid is not None and bid.created_tick is not None else trail.last_tick
                self._adopt(run, trail.thread_id, dealer, ref, price, opened, fresh)
            elif not held and clock.tick - self._quiet.setdefault(trail.thread_id, clock.tick) >= after:
                self._close_orphan(run, trail.thread_id, dealer, trail.item)

    def _adopt(
        self, run: _TickRun, thread_id: int, dealer: str, ref: str, price: int, opened: int, fresh: bool
    ) -> None:
        tick = run.snap.clock.tick
        rarity = _rarity(run.snap.catalog, ref) if "-" in ref else "pack"  # as `desk.topic_for`
        neg = Negotiation(BidPlan(price, 1, price), bids=[price])
        if not fresh:  # her answer to that bid had `orphan_after_ticks` ticks to come in already: no fresh wait
            neg.waits, neg.waited_after = MAX_WAITS, 1
        reason = f"adopted after a restart: our bid {price} is its whole plan"
        self.convs[dealer] = Conversation(dealer, ref, rarity, price, reason, neg, thread_id, opened)
        self.log(f"tick {tick} taker: thread {thread_id} with {dealer} for {ref}: {reason}")

    def _close_orphan(self, run: _TickRun, thread_id: int, dealer: str, item: str) -> None:
        tick = run.snap.clock.tick
        verdict = check(Action("close_thread", str(thread_id)), self._ctx(run), self.rules)
        if verdict.halted:  # the kill switch holds: the thread stays open
            return
        what = f"close thread {thread_id} with {dealer}: no bid of ours for {self.config.orphan_after_ticks} ticks"
        did = self.rec.decide(
            tick,
            "dealer_walk",
            f"{what} (a restart orphaned it) · guardrails {verdict}",
            inputs={"dealer": dealer, "thread": thread_id},
            reason="orphan thread",
            guardrail=str(verdict),
            chosen=run.window.open(),
            status="approved" if run.window.open() else "expired",
            thread_id=thread_id,
            move={"kind": "walk"},
        )
        if not run.window.open():
            return
        if self.rec.send(did, tick, "close_thread", {"thread": thread_id}, lambda: self.team.close_thread(thread_id)):
            self._quiet.pop(thread_id, None)
            self._wrapped(run, thread_id, dealer, item, {"status": "closed", "closed_reason": "orphan"}, None)


RATE_LIMITED = ("rate_limited", "too_many_requests", "too_many_failures")
# Refusals every other candidate of the tick would meet too: no more accepts are tried after one.
TEAM_WIDE_REFUSALS = ("insufficient_cash", "locked", "cooloff", "persona_quota")
MAX_REFUSED_ACCEPTS = 2  # refused accepts per tick before the taker stops trying (2 keyed calls each)
RESTART_TRIES = 5  # wrap-up reads of one thread from before a restart that did not wrap it up, then given up


def _rarity(catalog: dict[str, Any], ref: str) -> str:
    for s in catalog.get("sets") or []:
        for c in s.get("cards") or []:
            if str(c.get("id")) == ref:
                return str(c.get("rarity"))
    return "unknown"


def _with_gate(inputs: dict[str, Any], gate: Gate | None) -> dict[str, Any]:
    """The decision row's inputs with what the accept gate saw (a new dict)."""
    return inputs if gate is None else {**inputs, "inspector": gate.as_inputs()}
