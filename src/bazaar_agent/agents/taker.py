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
accept a dealer's ask early, and neither ever goes above a limit. Every accept and bid passes
`guardrails.check()` with the live context. Dry run (the default) sends nothing and logs WOULD-moves.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from bazaar_agent.agents.dealer import Move, Negotiation, WordsFn, apply_advice, bid_words, template_words
from bazaar_agent.agents.dealer_plan import DealerPlan, plan_dealer_buy
from bazaar_agent.agents.desk import (
    Conversation,
    DeskMove,
    Opening,
    meet_the_ask,
    openings,
    plan_conversation,
    topic_for,
)
from bazaar_agent.agents.market import BoardOffer, OpenOffer, Venue, board_offers, our_open_offers, tradable_venues
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
from bazaar_agent.agents.seller import offers_in, open_commitments
from bazaar_agent.agents.words import WordsRequest
from bazaar_agent.decisions import DecisionLog, Status
from bazaar_agent.evals.dealers import price_class
from bazaar_agent.guardrails import Action, Context, Guardrails, LedgerStore, check
from bazaar_agent.learn.blockers import Blocks
from bazaar_agent.learn.live import LiveLearner
from bazaar_agent.learn.outcomes import OutcomeLearner
from bazaar_agent.learn.recall import Lessons
from bazaar_agent.ledger_pg import LedgerUnavailable
from bazaar_agent.pack_gate import PackJudge, gate_packs
from bazaar_agent.sdk import BazaarError
from bazaar_agent.strategy import (
    Market,
    PackSlots,
    Playbook,
    StrategyParams,
    build_market,
    build_playbook,
    buy_case,
)
from bazaar_agent.strategy import Move as StrategyMove
from bazaar_agent.strategy import guarded as guarded_playbook
from bazaar_agent.ticks import Clock, action_budget_s


@dataclass(frozen=True)
class TakerConfig:
    max_dealer_threads: int = 3  # dealer conversations at once (one per dealer; the team cap is 6 threads)
    jev_min_budget_s: float = 4.0  # ask Jev only with this much of the tick left (a call takes ~0.3 s, max 3 s)
    max_jev_calls_per_tick: int = 3
    duel_grace_s: float = 2.0  # duels own the first seconds of a tick (capped at 15 % of the tick)


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
    offers: list[dict[str, Any]]
    mine: list[OpenOffer]
    started: float  # monotonic time the tick's work began (the clock was read just before)
    jev_calls: int = 0
    accepted: list[AcceptProposal] = field(default_factory=list)
    blocks: Blocks = field(default_factory=Blocks)  # learned dealer blockers in force for us (N12)
    plans: dict[tuple[str, str], DealerPlan] = field(default_factory=dict)  # (dealer, item) -> its plan (N14a)


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
        learner: LiveLearner | None = None,
        outcome_learner: OutcomeLearner | None = None,
        lessons: Lessons | None = None,
    ) -> None:
        self.team, self.public, self.rules, self.params = team, public, rules, params
        self.ledger, self.feed, self.live, self.log = ledger, feed, live, log
        self.jev, self.pack_judge, self.words_fn, self.now = jev, pack_judge, words_fn, now
        self.config = config or TakerConfig()
        self.sleep = sleep
        self.learner = learner  # the live-feed reader: blockers recalled before a dealer thread opens
        self.outcome_learner = outcome_learner  # lessons from settled outcomes, on its own worker (N3)
        self.lessons = lessons  # the hybrid recall for the words context (Jev gets them through its JevFn)
        self._learned_skips: dict[tuple[str, str], str] = {}  # (dealer, class) -> the reason last recorded
        self.rec = Recorder("taker", decisions, live, log, hub)
        self.hub = hub  # agents.status.StatusHub: the read-only HTTP/WS view, when served
        self.convs: dict[str, Conversation] = {}  # dealer id -> the conversation we own
        self._skips: dict[str, str] = {}  # dealer -> the blocker last recorded as a `dealer_skip` (once each)
        self._dry_accepts: dict[int, int] = {}

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
        except Exception:
            self._after_sends()
            raise
        self._after_sends()

    def _after_sends(self) -> None:
        """After every send of the tick (an error included, never Ctrl-C): the learner's writes and the feed
        archive. No database write ever runs before a send."""
        if self.learner is not None:
            self.learner.flush()
        self.feed.archive_pending()

    def _tick(self, snap: Snapshot, threads: list[dict[str, Any]], window: TickWindow) -> None:
        clock = snap.clock
        if self.hub is not None:
            self.hub.tick(clock.tick, clock.t_hours, snap.us)
        offers = offers_in(snap.offers)
        mine, _ = our_open_offers(snap.offers, snap.us)
        run = _TickRun(snap, window, self.params(clock.tick), offers, mine, window.deadline - action_budget_s(clock))
        if self.learner is not None:
            run.blocks = self.learner.blocks(snap.events, snap.us, clock)
        market = build_market(snap.me, snap.catalog, snap.events, snap.dealers)
        book = build_playbook(snap.me, snap.catalog, snap.events, snap.dealers, run.params, self.rules)
        self._open(run, book, threads)
        desk = self._desk_moves(run)
        proposals = [desk_proposal(dm) for dm, _ in desk if dm.move.kind == "accept"]
        proposals += [board_proposal(c) for c in self._board(run, market)]
        self._accept(run, proposals)
        self._converse(run, desk)
        if self.hub is not None:
            self.hub.view(threads=[conversation_view(c) for c in self.convs.values()])
        if self.outcome_learner is not None:  # after the tick's sends; never waits for the pass
            self.outcome_learner.maybe_run(clock.tick, snap.us)
        self.log(
            f"tick {clock.tick} taker: {len(proposals)} accept candidate(s), {len(run.accepted)} taken, "
            f"{len(self.convs)} dealer thread(s), {window.left():.1f} s left · {'LIVE' if self.live else 'dry run'}"
        )

    def _ctx(self, run: _TickRun, *, skip_thread: int | None = None, skip_offer: int | None = None) -> Context:
        """Live guardrail context; our open offers count, except the thread or bid this move replaces."""
        kept = [
            o
            for o in run.offers
            if (skip_thread is None or o.get("thread") != skip_thread)
            and (skip_offer is None or o.get("id") != skip_offer)
        ]
        return guard_context(run.snap, self.ledger, self.rules, open_commitments(kept, run.snap.us))

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
        own_bids = {o.ref: o for o in run.mine if o.side == "bid"}
        return ask_candidates(market, offers, venues, run.params, {o.id for o in run.mine}, own_bids)

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
        moves = self._unblocked(run, moves, busy)
        moves = self._evolved(run, moves, busy)
        for op in openings(moves, busy, {c.item for c in self.convs.values()}, room):
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

    def _evolved(self, run: _TickRun, moves: list[StrategyMove], busy: set[str]) -> list[StrategyMove]:
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
        kept: list[StrategyMove] = []
        skipped: dict[tuple[str, str], tuple[StrategyMove, str]] = {}
        for mv in moves:
            cls = price_class(mv.ref)
            if mv.ladder is None or cls is None:
                kept.append(mv)
                continue
            key = (mv.source, cls)
            plan = plan_dealer_buy(mv, policies.get(key), curves.get(key), self.rules, run.params.min_buy_surplus)
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
        dp = run.plans.get((op.dealer, op.item))
        if dp is not None and dp.final_max is not None:
            op = replace(op, plan=replace(op.plan, final_max=dp.final_max))
        verdict = check(Action("buy", op.item, op.rarity, op.plan.start), ctx, self.rules)
        plan = f"{op.plan.start}→{op.plan.max_price} step {op.plan.step}"
        final = f", final ≤ {op.plan.final_max}" if op.plan.final_max is not None else ""
        # Private keys (not on the public /state allow-list): which learning changed the plan, and what was recalled.
        notes = dp.changed_by if dp is not None else []
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
            "recalled": self._recalled(run, op),
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
            self.convs[op.dealer] = Conversation(
                op.dealer,
                op.item,
                op.rarity,
                op.value,
                op.reason,
                Negotiation(op.plan),
                int(body["id"]),
                tick,
                notes=tuple(notes),
            )

    def _recalled(self, run: _TickRun, op: Opening) -> list[str]:
        """The top lessons about this dealer and item, recalled once per opened thread (quoted data for the log;
        they never set a price). None without the recall or with less than `jev_min_budget_s` of the tick left."""
        if self.lessons is None or run.window.left() < self.config.jev_min_budget_s:
            return []
        situation = f"open a thread with {op.dealer} to buy {op.item} ({op.rarity})"
        found = self.lessons(situation, subjects=(op.dealer,), tick=run.snap.clock.tick)
        return [str(x.get("quoted_lesson")) for x in found if isinstance(x, dict)]

    def _desk_moves(self, run: _TickRun) -> list[tuple[DeskMove, dict[str, Any]]]:
        out = []
        for dealer, conv in list(self.convs.items()):
            thread = self.team.thread(conv.thread_id)
            conv.ticks += 1
            dm = plan_conversation(conv, thread, self.rules.dealer_max_ticks_per_thread)
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
        if self.learner is not None:
            self.learner.thread_closed(thread, run.snap.us, run.snap.clock)
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

    def _desk_send(self, run: _TickRun, dm: DeskMove, thread: dict[str, Any]) -> None:
        conv, tick, move = dm.conv, run.snap.clock.tick, dm.move
        verdict_text = "allowed"
        if move.kind == "bid":
            at_final = dm.final and move.price is not None and move.price == dm.ask  # meeting her final (N14a)
            verdict = check(
                Action("bid", conv.item, conv.rarity, move.price, final=at_final),
                self._ctx(run, skip_thread=conv.thread_id),
                self.rules,
            )
            verdict_text = str(verdict)
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
            "final_max": conv.neg.plan.final_max,
            "changed_by": list(conv.notes),
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
        if status != "approved" or not self.live:
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
            WordsRequest(conv.dealer, price, len(conv.neg.bids), conv.item, lessons=self._lessons_for(run, conv)),
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

    def _lessons_for(self, run: _TickRun, conv: Conversation) -> tuple[str, ...]:
        """The top lessons about this dealer and item for the words (cached for a few ticks; none when short)."""
        if self.lessons is None or self.words_fn is template_words or run.window.left() < self.config.jev_min_budget_s:
            return ()  # the templates never read lessons: no recall for them
        situation = f"bid to {conv.dealer} for {conv.item} ({conv.rarity})"
        found = self.lessons(situation, subjects=(conv.dealer,), tick=run.snap.clock.tick)
        return tuple(str(x["quoted_lesson"]) for x in found)

    # ------------------------------------------------------------ accepts (shared quota)

    def _accept(self, run: _TickRun, proposals: list[AcceptProposal]) -> None:
        clock = run.snap.clock
        limit = accept_limit(clock, self.rules)
        used = self.ledger.accepts_in_tick(clock.tick) if self.live else self._dry_accepts.get(clock.tick, 0)
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
        final = p.final and p.desk is not None  # a dealer's final: its cap is `final_cap_for` (N14a)
        verdict = check(Action("accept_buy", p.ref, p.rarity, p.price, final=final), ctx, self.rules)
        if not verdict.allowed:
            self._skip(run, p, str(verdict), "rejected")
            return False
        jev = self._ask_jev(run, offer_state(p, run.snap, ctx, self.rules, limit)) if p.source == "board" else None
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
        if not self.live:
            return True
        if (
            self.rec.send(did, clock.tick, "accept", {"offer": p.offer_id}, lambda: self.team.accept(p.offer_id))
            is None
        ):
            return True  # the reserved slot stays spent: an accept that may have landed is never retried
        if p.desk is not None:
            p.desk.conv.accepted_tick, p.desk.conv.accepted_price = clock.tick, p.price
        else:
            self.ledger.record("spend", clock.tick, clock.t_hours, p.price, p.ref)
            if p.candidate is not None and p.candidate.replaces_bid is not None:
                self._withdraw(run, p.candidate.replaces_bid)
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
        did = self.rec.decide(
            clock.tick,
            "cancel_bid",
            f"cancel our bid {bid.id} for {bid.ref}: bought it cheaper",
            inputs={"offer_id": bid.id, "ref": bid.ref, "price": bid.price},
            reason="replaced",
            guardrail="allowed",
            chosen=True,
            status="approved",
        )
        if self.rec.send(did, clock.tick, "cancel", {"offer": bid.id}, lambda: self.team.cancel(bid.id)) is not None:
            self.ledger.record("spend", clock.tick, clock.t_hours, -bid.price, bid.ref)
