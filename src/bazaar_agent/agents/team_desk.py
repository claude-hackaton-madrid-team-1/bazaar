"""The taker's team desk (N17): swap threads with other teams' agents, inside the taker's tick.

No new service, no new stream, no wall clock: the taker calls it every tick, after its own reads.
1. `proposals()`: every standing offer a team made us in an open team thread is read from its structure
   (`swaps.read_offer`, never the words). The planned swap at a fair price (`swaps.judge`) becomes an accept
   candidate that the taker ranks against its board asks: one accept per tick for the whole team, duels first.
2. `converse()`: in each thread of ours not accepted this tick, the next proposal (our old offer cancelled
   first: one standing offer per thread), a walk after `team_thread_max_messages` proposals or
   `team_thread_idle_ticks` of silence. An inbound thread holds one of our six conversations: we answer it
   with a planned swap with that team, or close it after the same silence. Then at most one new thread, on
   the house venue (a rival's venue would score market-making for that rival) with a generic topic (a
   thread's topic is public), while `team_threads_max_open` and the dealer reserve allow.

The swaps come from the trade desk (`trade_desk.build_plan(...).threads`, #79): priced on the rival affinity
map, checked against GUARDRAILS and the per-team share of the plan. Every send passes `guardrails.check()`
as #79's `Swap` describes it (our copy as a sale at what we receive, the cash we add as a bid). Off unless
`team_threads_enabled` (read at start), and `BAZAAR_TEAM_THREADS=0` in the environment turns it off; off,
it withdraws our team-thread offers (refunding their spend). The cash we add is booked when an offer is posted.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

from bazaar_agent import affinity as af
from bazaar_agent import impact_board
from bazaar_agent.agents.market import Venue
from bazaar_agent.agents.runtime import JevAdvice, Recorder, no_jev
from bazaar_agent.agents.seller import Swap, open_commitments
from bazaar_agent.agents.words import WordsFn, WordsRequest
from bazaar_agent.decisions import Status
from bazaar_agent.guardrails import Action, Context, Guardrails, LedgerStore, Verdict, check, refund_row
from bazaar_agent.intel import TEAM_ID, set_of
from bazaar_agent.ledger_pg import LedgerUnavailable
from bazaar_agent.sdk import BazaarError
from bazaar_agent.strategy import Market, StrategyParams, build_market, page_bonus_of, page_cards
from bazaar_agent.swaps import (
    Ladder,
    SwapVerdict,
    TheirOffer,
    cash_at,
    is_the_planned_swap,
    judge,
    offer_terms,
    read_offer,
)
from bazaar_agent.trade_desk import PlanParams, Trade, build_plan, dealer_prices, wanted_cards

TEAM_THREADS_ENV = "BAZAAR_TEAM_THREADS"  # "0" turns the desk off at the next tick
HOUSE_VENUE = "rastro"
TOPIC = {"trade": "cards"}  # public with the thread: never the card we want
REST_TICKS = 20  # after a walk, the team is left alone this long (no reopening every few ticks)
DEAD = ("cancelled", "expired", "failed")  # an offer of ours in one of these will never settle: its spend comes back
CHECK_TICKS = 10  # how long an offer whose end we have not seen is re-read before its spend is simply kept
TEAM_SPEND = "team:"  # the item prefix of the cash we add to swaps: `team_swap_max_cash_per_hour` sums these rows
JEV_QUESTION = "team_swap_worth_it"  # questions/team_swaps.json
NO_JEV_BUDGET = "no tick budget for jev"  # the taker's answer when the tick has no room for a Jev call


def disabled(rules: Guardrails, env: Mapping[str, str] | None = None) -> str | None:
    """Why the desk sends nothing this tick; None when it may."""
    if not rules.team_threads_enabled:
        return "team_threads_enabled = false"
    if (os.environ if env is None else env).get(TEAM_THREADS_ENV, "").strip() == "0":
        return f"{TEAM_THREADS_ENV}=0"
    return None


def team_words(req: WordsRequest) -> str:
    """Template words for a swap proposal. They persuade; the structured offer is set by code."""
    if req.step == 0:
        return "Hola: tengo un cromo que te falta y tú uno que me falta a mí. ¿Lo cambiamos? La oferta va adjunta."
    return "Me acerco a ti: esta es una oferta justa para los dos. Si te encaja, acéptala."


def free_copies(
    me: dict[str, Any], offers: Sequence[dict[str, Any]], us: str, ref: str, thread: int | None = None
) -> list[int]:
    """Our copies of `ref` that no open offer of ours promises (the offer in `thread` itself excepted: that is
    the swap being priced), cheapest to us first."""
    others = [o for o in offers if thread is None or o.get("thread") != thread or o.get("status") == "accepted"]
    listed = open_commitments(others, us).listed  # this thread's OPEN offer is the one being replaced
    free = [
        a
        for a in me.get("assets") or []
        if a.get("ref") == ref and isinstance(a.get("id"), int) and a["id"] not in listed
    ]
    return [int(a["id"]) for a in sorted(free, key=lambda a: (float(a.get("your_value") or 0), int(a["id"])))]


def desk_copy(me: dict[str, Any], ref: str) -> int | None:
    """The one copy of `ref` the team desk may give, when we hold two or more: the cheapest to us (then the
    lowest id), from /me alone, so the maker (another process) knows it without a shared read and never lists
    it (`maker_may_list`). None: a single copy (never given) or none."""
    copies = [a for a in me.get("assets") or [] if a.get("ref") == ref and isinstance(a.get("id"), int)]
    if len(copies) < 2:
        return None
    return int(min(copies, key=lambda a: (float(a.get("your_value") or 0), int(a["id"])))["id"])


def maker_may_list(me: dict[str, Any], ref: str, asset_id: int | None, rules: Guardrails) -> bool:
    """While team threads are on, the maker leaves the desk its swap copy: with exactly two copies it lists
    neither (one listed would leave the desk a single free copy, which it never gives), with three or more it
    never lists the desk's copy (`desk_copy`). Off, or a single copy: the maker lists as before."""
    if disabled(rules) is not None:  # GUARDRAILS off, or BAZAAR_TEAM_THREADS=0 on this service too
        return True
    held = sum(1 for a in me.get("assets") or [] if a.get("ref") == ref)
    return held != 2 and (asset_id is None or asset_id != desk_copy(me, ref))


def spare_copy(
    me: dict[str, Any], offers: Sequence[dict[str, Any]], us: str, ref: str, thread: int | None = None
) -> int | None:
    """The copy we would give: only a DUPLICATE (two free copies at least), so a swap never takes the last copy
    a page of ours needs (N17 spec, criterion 1), never a copy already in one of our asks, and only the desk's
    own copy (`desk_copy`), which the maker never lists: the two never promise one asset in the same tick."""
    free = free_copies(me, offers, us, ref, thread)
    mine = desk_copy(me, ref)
    return mine if len(free) >= 2 and mine in free else None


def spare(me: dict[str, Any], offers: Sequence[dict[str, Any]], us: str, ref: str) -> bool:
    return spare_copy(me, offers, us, ref) is not None


def _ours_open(payload: dict[str, Any], us: str) -> dict[str, Any] | None:
    """Our open offer in a thread (after a restart, or a send whose answer was lost)."""
    mine = [o for o in payload.get("standing_offers") or [] if isinstance(o, dict) and o.get("maker") == us]
    return next((o for o in reversed(mine) if o.get("status") in (None, "open") and isinstance(o.get("id"), int)), None)


def offer_status(payload: dict[str, Any], oid: int, us: str | None = None) -> str | None:
    """The status of one offer of ours as a thread shows it (its standing offers, or the offer in a message)."""
    offers = [*(payload.get("standing_offers") or []), *(m.get("offer") for m in payload.get("messages") or [])]
    ours = [o for o in offers if isinstance(o, dict) and o.get("id") == oid and o.get("maker") in (None, us)]
    return next((str(o.get("status")) for o in ours), None)


def _ours_taken(o: Any, us: str) -> bool:
    """Our offer in the thread, accepted by them and settling at the next tick."""
    return isinstance(o, dict) and o.get("maker") == us and o.get("status") == "accepted"


@dataclass
class Talk:
    """One swap thread we run: the planned trade, how far we conceded, our standing offer."""

    thread_id: int
    team: str
    trade: Trade
    opened_tick: int
    step: int = 0  # proposals sent
    offer_id: int | None = None  # our standing offer in the thread
    sent_tick: int = -1
    heard_tick: int = -1  # the last tick they wrote
    accepted: bool = False  # a deal is pending (we took their offer, or they took ours): say nothing more
    cash: int = 0  # the cash leg of our last proposal (+ they add, - we add)


@dataclass(frozen=True)
class SwapAccept:
    """A team's offer we would take: the planned cards at a fair price."""

    thread_id: int
    offer: TheirOffer
    trade: Trade
    verdict: SwapVerdict
    fee: int  # we pay it: we are the accepting side
    pick: list[int] | None  # our copy, when the offer asks for any copy of a card


@dataclass(frozen=True)
class DeskView:
    """What the taker read this tick, and its live guardrail context (`ctx(thread)` leaves that thread's
    own offer out, as a replacement does)."""

    tick: int
    t_hours: float
    us: str
    me: dict[str, Any]
    catalog: dict[str, Any]
    events: Sequence[dict[str, Any]]
    venues: Sequence[Venue]
    threads: Sequence[dict[str, Any]]  # our open threads, dealers' included
    offers: Sequence[dict[str, Any]]  # our open offers
    params: StrategyParams
    max_threads: int
    in_use: int  # conversations open now, the dealer desk's openings of this tick included
    ctx: Callable[[int | None], Context]
    window_open: Callable[[], bool]
    listing_cap: int = 12  # /api/clock limits.offers_per_team_per_tick, shared with the maker (a thread offer counts)
    max_tick_seconds: float = 60.0  # /api/clock: dates a refund in the hour of its spend (`refund_row`)
    jev: Callable[[dict[str, Any]], JevAdvice] = no_jev  # `team_swap_worth_it`, inside the taker's tick budget
    scan: Sequence[dict[str, Any]] = ()  # the stored card scan: who holds the cards we miss (`bazaar supply scan`)


@dataclass
class _Plan:
    tick: int
    trades: tuple[Trade, ...]
    worth: dict[str, float] = field(default_factory=dict)  # card ref -> one more copy to us
    pages: dict[str, PageNeed] = field(default_factory=dict)  # set code -> how far our album page is
    books: dict[str, float] = field(default_factory=dict)  # card ref -> book value (catalog)


@dataclass(frozen=True)
class PageNeed:
    """One album page as the desk ranks it: the fewer cards missing (then the higher our affinity), the sooner
    its missing cards are asked for; the bonus is what completing it scores (`strategy.page_bonus_of`)."""

    set_code: str
    have: int
    of: int
    affinity: float
    bonus: float

    @property
    def missing(self) -> int:
        return self.of - self.have

    def rank(self) -> tuple[int, float]:
        return (self.missing, -self.affinity)


def closest_pages(pages: Mapping[str, PageNeed]) -> frozenset[str]:
    """The pages with the fewest cards missing: the plan picks swaps for their cards first (`build_plan(focus=)`),
    and for any other page only when none of theirs can be planned."""
    fewest = min((p.missing for p in pages.values()), default=None)
    return frozenset(code for code, p in pages.items() if p.missing == fewest)


def page_needs(m: Market) -> dict[str, PageNeed]:
    """Every released page with a card still missing, from the market built on /api/me."""
    out: dict[str, PageNeed] = {}
    for code in m.released:
        cards = page_cards(m, code)
        have = sum(1 for c in cards if m.held.get(c.ref, 0) > 0)
        if cards and have < len(cards):
            out[code] = PageNeed(code, have, len(cards), m.affinity.get(code, 1.0), round(page_bonus_of(m, code), 1))
    return out


class TeamDesk:
    def __init__(
        self,
        team: Any,
        rules: Guardrails,
        rec: Recorder,
        log: Callable[[str], None],
        live: bool,
        *,
        ladder: Ladder | None = None,
        words: WordsFn = team_words,
        env: Mapping[str, str] | None = None,
        plan_ttl_ticks: int = 5,
        ledger: LedgerStore | None = None,
    ) -> None:
        self.team, self.rules, self.rec, self.log, self.live = team, rules, rec, log, live
        self.ledger = ledger  # the shared ledger: spend we add, listings we post (the maker's budget)
        self.ladder, self.words, self.env, self.plan_ttl = ladder or Ladder(), words, env, plan_ttl_ticks
        self.talks: dict[int, Talk] = {}
        self.deals: Counter[str] = Counter()  # settled swaps per team (`judge(repeat=...)`)
        self.first_seen: dict[int, int] = {}  # inbound thread -> the tick we first saw it
        self._payloads: dict[int, dict[str, Any]] = {}
        self._closed: set[int] = set()  # threads we closed this tick: still in this tick's list, never adopted
        self._refused: set[int] = set()  # their offers we refused (logged once)
        self._tried: set[int] = set()  # threads whose read was tried this tick (refused ones included)
        self.rest_until: dict[str, int] = {}  # team -> the tick before which we open no new thread with it
        self.refunded: set[int] = set()  # our team-thread offers whose spend we gave back (by offer id)
        self.to_check: dict[int, tuple[int, dict[str, Any], int]] = {}  # offer id -> (thread, offer, since tick)
        self._synthetic = 0  # negative ids for the refund of a send the server refused
        self._plan: _Plan | None = None

    # ------------------------------------------------------------ reads

    def _team_threads(self, v: DeskView) -> list[dict[str, Any]]:
        return [
            t
            for t in v.threads
            if isinstance(t.get("id"), int)
            and (t.get("kind") == "team" or (t.get("kind") is None and TEAM_ID.match(str(t.get("with") or ""))))
            and t.get("status", "open") == "open"
        ]

    def _payload(self, t: dict[str, Any]) -> dict[str, Any]:
        """The thread with its messages and standing offers: the list entry when it has them, else one read."""
        if "standing_offers" in t and "messages" in t:
            return t
        try:
            body = self.team.thread(int(t["id"]))
        except BazaarError as e:
            self.log(f"team desk: thread {t['id']} read refused {e.code}; skipped this tick")
            return {}
        return body if isinstance(body, dict) else {}

    def _other(self, t: dict[str, Any], us: str) -> str:
        return str(t.get("with") if t.get("team") == us else t.get("team") or t.get("with") or "")

    def _heard(self, payload: dict[str, Any], us: str) -> int:
        ticks = [int(m.get("tick") or -1) for m in payload.get("messages") or [] if m.get("sender") not in (None, us)]
        return max(ticks, default=-1)

    # ------------------------------------------------------------ (1) what they offer us

    def proposals(self, v: DeskView) -> list[SwapAccept]:
        self._payloads, self._closed, self._tried = {}, set(), set()
        if disabled(self.rules, self.env):  # no accepts; still read our team threads (at most six, and none when
            for t in self._team_threads(v):  # /api/me/threads carries them), so a take is booked and its thread
                self._tried.add(int(t["id"]))
                if payload := self._payload(t):  # is never closed under a deal, with no memory after a restart
                    self._payloads[int(t["id"])] = payload
            return []
        venues = {x.id: x for x in v.venues}
        out: list[SwapAccept] = []
        for t in self._team_threads(v):
            self._tried.add(int(t["id"]))  # tried this tick, read or refused: never read twice in a tick
            payload = self._payload(t)
            if not payload:
                continue
            tid = int(t["id"])
            self._payloads[tid] = payload
            talk = self.talks.get(tid)
            if talk is None:
                self.first_seen.setdefault(tid, v.tick)
                if self._taken(v, tid):
                    continue  # a take of our offer is pending there (a restart forgot it): no other accept
            else:
                self._observe(v, talk, payload)
                if talk.accepted:
                    continue  # a deal is pending there: no other accept in that thread
            where = payload.get("venue") or t.get("venue")  # unknown venue: not answered (fail closed)
            venue = venues.get(str(where))
            if venue is None or where != HOUSE_VENUE:  # our own venue refuses us; a rival's earns its owner points
                continue
            for raw in payload.get("standing_offers") or []:
                offer = read_offer(raw, v.us)
                accept = None if offer is None else self._judge_offer(v, tid, talk, offer, venue)
                if accept is not None:
                    out.append(accept)
        return sorted(out, key=lambda a: -a.verdict.ours)[:1]  # one accept per tick for the whole team

    def _observe(self, v: DeskView, talk: Talk, payload: dict[str, Any]) -> None:
        """What changed in one of our threads: their last word, our standing offer, and whether they took it
        (from the thread, or from our offers: the server may drop an accepted offer from `standing_offers`)."""
        talk.heard_tick = max(talk.heard_tick, self._heard(payload, v.us))
        if talk.offer_id is None and (mine := _ours_open(payload, v.us)) is not None:
            talk.offer_id = int(mine["id"])  # a send whose answer was lost, or a thread adopted after a restart
            want, give = mine.get("want") or {}, mine.get("give") or {}
            talk.cash = int(want.get("cash") or 0) - int(give.get("cash") or 0)  # what stands, for its refund
            if isinstance(mine.get("created_tick"), int):
                talk.sent_tick = int(mine["created_tick"])
        taken = any(_ours_taken(o, v.us) for o in payload.get("standing_offers") or [])
        taken = taken or any(o.get("id") == talk.offer_id and o.get("status") == "accepted" for o in v.offers)
        if taken and not talk.accepted:
            talk.accepted, talk.sent_tick = True, max(talk.sent_tick, v.tick)

    def _spend(self, v: DeskView, cash: int, ref: str) -> None:
        """The cash we add to a swap is spend (`max_spend_per_game_hour`), booked when our offer is POSTED, as a
        bid is (`seller.post_swap`, the maker): it lives in the shared ledger, so no restart, settlement timing
        or desk turned off can lose it. A team-thread offer is therefore not counted again as thread cash."""
        if self.live and self.ledger is not None and cash > 0:
            self.ledger.record("spend", v.tick, v.t_hours, cash, f"{TEAM_SPEND}{ref}")

    def _refund(self, v: DeskView, offer: dict[str, Any]) -> None:
        """Give back the spend of an offer of ours that will never settle (we cancelled it, its thread closed, it
        expired), once per offer, dated in the hour it was spent (`guardrails.refund_row`)."""
        oid, cash = offer.get("id"), int((offer.get("give") or {}).get("cash") or 0)
        if not isinstance(oid, int) or oid in self.refunded or cash <= 0 or not self.live or self.ledger is None:
            return
        self.refunded.add(oid)
        ref = TEAM_SPEND + next(
            iter(str(t).split(":")[-1] for t in (offer.get("want") or {}).get("cards") or []), "swap"
        )
        created = offer.get("created_tick")
        self.ledger.record(
            *refund_row(cash, ref, created if isinstance(created, int) else None, v.tick, v.t_hours, v.max_tick_seconds)
        )

    def _ours_in(
        self, v: DeskView, tid: int, statuses: tuple[Any, ...] = (None, "open", "queued")
    ) -> list[dict[str, Any]]:
        """Our offers in one team thread, from our offers and the thread read this tick (deduplicated)."""
        payload = self._payloads.get(tid) or {}
        seen: dict[int, dict[str, Any]] = {}
        for o in [*(payload.get("standing_offers") or []), *v.offers]:
            mine = o.get("maker") == v.us and o.get("thread") == tid and isinstance(o.get("id"), int)
            if mine and o.get("status") in statuses:
                seen.setdefault(int(o["id"]), o)
        return list(seen.values())

    def _talk_offer(self, talk: Talk) -> dict[str, Any]:
        """Our last offer in a thread as the desk remembers it (for a refund when the server no longer shows it)."""
        give = {"cash": -talk.cash} if talk.cash < 0 else {}
        return {
            "id": talk.offer_id,
            "give": give,
            "want": {"cards": [talk.trade.refs[1]]},
            "created_tick": talk.sent_tick,
        }

    def thread_payload(self, tid: int) -> dict[str, Any] | None:
        """The team thread as `proposals` read it this tick (None: not read): the accept gate reads it again."""
        return self._payloads.get(tid)

    def clear_before_accept(self, v: DeskView, a: SwapAccept, decision_id: int) -> bool:
        """Before we take a team's offer in a thread, our own offer there goes: never two deals in one thread
        (both copies would leave). False when a cancel was refused: the taker then does not accept."""
        for o in self._ours_in(v, a.thread_id):
            oid = int(o["id"])
            if not self.live:
                continue
            body = self.rec.send(decision_id, v.tick, "cancel", {"offer": oid}, partial(self.team.cancel, oid))
            if body is None:
                self.log(f"tick {v.tick} team desk: cancel of our offer {oid} refused: their offer is not taken")
                return False
            self._after_cancel(v, a.thread_id, o, body)
        if (talk := self.talks.get(a.thread_id)) is not None:
            talk.offer_id = None
        return True

    def _judge_offer(
        self, v: DeskView, tid: int, talk: Talk | None, offer: TheirOffer, venue: Venue
    ) -> SwapAccept | None:
        trades = [talk.trade] if talk is not None else [t for t in self._trades(v) if t.counterparty == offer.team]
        for planned in trades:
            named = offer.give_assets[0] if len(offer.give_assets) == 1 and not offer.give_refs else None
            free = free_copies(v.me, v.offers, v.us, planned.refs[0], tid)
            copy = spare_copy(v.me, v.offers, v.us, planned.refs[0], tid) if named is None else named
            reserved = copy is not None and not maker_may_list(v.me, planned.refs[0], copy, self.rules)
            if len(free) < 2 or copy not in free or not reserved:
                continue  # only a free duplicate the maker never lists leaves: never the last copy or one in an ask
            trade = replace(planned, asset_id=copy)
            if not is_the_planned_swap(offer, trade):
                continue
            fee = venue.fee(
                offer.cash_in or offer.cash_out, len(offer.get_assets) + len(offer.give_assets or offer.give_refs)
            )
            verdict = judge(trade, offer.net_cash, fee, self.rules, repeat=self.deals[offer.team] > 0)
            if not verdict.ok:
                if offer.offer_id not in self._refused:  # once per offer: a standing offer is read every tick
                    self._refused.add(offer.offer_id)
                    self.log(
                        f"tick {v.tick} team desk: {offer.team}'s offer {offer.offer_id} refused: {verdict.reason}"
                    )
                return None
            pick = [trade.asset_id] if offer.give_refs and trade.asset_id is not None else None
            return SwapAccept(tid, offer, trade, verdict, fee, pick)
        return None

    def accepted(self, a: SwapAccept, tick: int) -> None:
        """The taker took their offer: the thread waits for its deal (the server settles it next tick), and no
        other proposal goes there."""
        talk = self.talks.get(a.thread_id) or Talk(a.thread_id, a.offer.team, a.trade, tick)
        talk.accepted, talk.sent_tick = True, tick  # the taker booked what we pay
        self.talks[a.thread_id] = talk

    # ------------------------------------------------------------ (2) what we say

    def converse(self, v: DeskView, taken: set[int]) -> None:
        self._check_refunds(v)  # also while the desk is off
        if (why := disabled(self.rules, self.env)) is not None:
            self._withdraw(v, why)
            return
        self._settle(v)
        for tid, talk in list(self.talks.items()):
            if tid in taken or tid not in self._payloads:
                continue
            self._next_move(v, talk)
        self._inbound(v)
        self._open(v)

    def _settle(self, v: DeskView) -> None:
        """Threads of ours that ended since the last tick (a deal, a walk, the message cap) leave the desk."""
        open_ids = {int(t["id"]) for t in self._team_threads(v)}
        for tid, talk in list(self.talks.items()):
            if tid in open_ids:
                continue
            payload = {} if talk.accepted else self._payload({"id": tid})
            status = "deal" if talk.accepted else str(payload.get("status") or "")
            if not status and v.tick - talk.sent_tick <= 2 * self.rules.team_thread_idle_ticks:
                continue  # its end is unknown (a refused read): keep it, read it again next tick
            del self.talks[tid]
            if status == "deal":  # their accept of our offer, or ours of theirs: the spend booked at the post stands
                self.deals[talk.team] += 1
                self._plan = None  # our album changed: the next plan is built on the new holdings
                for o in self._ours_in(v, tid):  # an offer of ours left open there would be a second deal
                    self._cancel_left(v, tid, talk.team, o)
            elif talk.offer_id is not None:  # closed (by them or the cap): its spend comes back only once our offer
                self._settled_or_dead(v, tid, self._talk_offer(talk), payload)  # reads dead: a closed thread
                # may still settle an offer accepted before the close
            self.log(f"tick {v.tick} team desk: thread {tid} with {talk.team} ended ({status or '?'})")

    def _our_threads(self, v: DeskView) -> set[int]:
        """Team threads holding an open offer of ours (from our offers: no memory needed), or run by the desk."""
        ours = {
            int(o["thread"])
            for o in v.offers
            if o.get("maker") == v.us
            and o.get("status") in (None, "open")
            and isinstance(o.get("thread"), int)
            and TEAM_ID.match(str(o.get("to") or ""))
        }
        return ours | {tid for tid, talk in self.talks.items() if not talk.accepted}

    def _taken(self, v: DeskView, tid: int) -> bool:
        """A team took our offer in this thread (it settles at the next tick)."""
        payload = self._payloads.get(tid) or {}
        mine = [*(payload.get("standing_offers") or []), *(o for o in v.offers if o.get("thread") == tid)]
        return any(_ours_taken(o, v.us) for o in mine)

    def _withdraw(self, v: DeskView, why: str) -> None:
        """The desk is off: every team thread holding an open offer of ours is closed (which cancels the offer),
        from what the server shows, so a desk turned off by a restart withdraws as well; a thread where a team
        just took our offer is left to settle."""
        ours = self._our_threads(v)
        for t in self._team_threads(v):
            tid = int(t["id"])
            off = f"the team desk is off ({why})"
            if tid in ours and not self._taken(v, tid) and self._close(v, tid, self._other(t, v.us), off):
                self.talks.pop(tid, None)

    def _next_move(self, v: DeskView, talk: Talk) -> None:
        if talk.accepted:  # a deal settles at the next tick; one that did not (all or nothing) frees the slot,
            if v.tick - talk.sent_tick > self.rules.team_thread_idle_ticks:  # whatever they write meanwhile
                self._walk(v, talk, "an accepted deal did not settle")
            return
        if talk.step >= self.rules.team_thread_max_messages:
            self._walk(v, talk, f"{talk.step} proposals sent (team_thread_max_messages)")
            return
        replied = talk.heard_tick > talk.sent_tick
        at_floor = talk.step >= self.ladder.steps
        if talk.step == 0 or (replied and not at_floor):
            self._propose(v, talk)
            return
        # At our last price their words buy no more time: they accept, or counter in a way we take, or we walk.
        since = talk.sent_tick if at_floor else max(talk.sent_tick, talk.heard_tick)
        if v.tick - since >= self.rules.team_thread_idle_ticks:
            self._walk(v, talk, f"no deal {self.rules.team_thread_idle_ticks} ticks after our last proposal")

    def _inbound(self, v: DeskView) -> None:
        """A team thread we do not run: one another team opened, or one of ours after a restart. On the house
        venue we answer it with a planned swap with that team (our standing offer and our proposals so far
        are picked up from the thread); otherwise it is closed `team_thread_idle_ticks` after we first saw it,
        whatever is written in it (another team cannot park on our conversation slots)."""
        for t in self._team_threads(v):
            tid = int(t["id"])
            if tid in self.talks or tid in self._closed or tid not in self._payloads:
                continue
            team, payload = self._other(t, v.us), self._payloads[tid]
            house = (payload.get("venue") or t.get("venue")) == HOUSE_VENUE
            talk = self._adopt(v, tid, team, payload) if house else None
            if talk is not None:
                self.talks[tid] = talk
                self._next_move(v, talk)
            elif self._taken(v, tid):
                continue  # a team took our offer there: it settles at the next tick, never closed under it
            elif v.tick - self.first_seen.get(tid, v.tick) >= self.rules.team_thread_idle_ticks:
                self._close(v, tid, team, "a team thread with no swap of ours planned with that team")

    def _adopt(self, v: DeskView, tid: int, team: str, payload: dict[str, Any]) -> Talk | None:
        busy = {k.trade.asset_id for k in self.talks.values()} | {k.trade.refs[1] for k in self.talks.values()}
        mine = _ours_open(payload, v.us)
        wants = [str(c) for c in ((mine or {}).get("want") or {}).get("cards") or []]
        plans = sorted(self._trades(v), key=lambda x: x.refs[1] not in wants)  # the pair our offer stands for first
        for planned in plans:
            if planned.counterparty != team or planned.refs[1] in busy:
                continue
            copy = spare_copy(v.me, v.offers, v.us, planned.refs[0], tid)
            if copy is None or copy in busy:
                continue
            ours = [m for m in payload.get("messages") or [] if m.get("sender") == v.us and m.get("offer")]
            same = planned.refs[1] in wants  # a different pair starts its own ladder at the anchor
            talk = Talk(tid, team, replace(planned, asset_id=copy), v.tick, step=len(ours) if same else 0)
            talk.offer_id = int(mine["id"]) if mine is not None else None
            if mine is not None:  # the cash leg of what stands: - we add, + they add (booked if they take it)
                talk.cash = int((mine.get("want") or {}).get("cash") or 0) - int(
                    (mine.get("give") or {}).get("cash") or 0
                )
            talk.sent_tick = max((int(m.get("tick") or -1) for m in ours), default=-1)
            talk.heard_tick = self._heard(payload, v.us)
            if self._taken(v, tid):  # taken before the restart: say nothing there (the take is booked from the offer)
                talk.accepted, talk.sent_tick = True, v.tick
            return talk
        return None

    def _open(self, v: DeskView) -> None:
        team_open = len(self._team_threads(v))
        room = min(
            self.rules.team_threads_max_open - team_open,
            v.max_threads - v.in_use - self.rules.team_threads_dealer_reserve,
        )
        if room <= 0:
            return
        busy_teams = {self._other(t, v.us) for t in self._team_threads(v)}
        busy_teams |= {team for team, until in self.rest_until.items() if v.tick < until}
        used = {k.trade.asset_id for k in self.talks.values()} | {k.trade.refs[1] for k in self.talks.values()}
        for planned in self._trades(v):
            if planned.counterparty in busy_teams or planned.refs[1] in used:
                continue
            copy = spare_copy(v.me, v.offers, v.us, planned.refs[0])  # a free duplicate, never one in an ask
            if copy is None or copy in used:
                continue
            trade = replace(planned, asset_id=copy)
            cash = cash_at(trade, 0, self.ladder)
            verdict = self._guard(v, trade, cash, None)
            if not verdict.allowed:
                self.log(f"tick {v.tick} team desk: not opening with {trade.counterparty}: {verdict}")
                continue
            ok, advice, why = self.jev_gate(v, trade, cash, 0, None, 0)
            if not ok:
                self._jev_refused(v, "team_open", trade, None, why, advice)
                self.rest_until[trade.counterparty] = v.tick + (REST_TICKS if advice and advice.decided else 3)
                return  # one Jev question per opening tick
            self._open_one(v, trade, advice)
            return  # one opening per tick

    def _open_one(self, v: DeskView, trade: Trade, advice: JevAdvice | None = None) -> None:
        what = f"open a swap thread with {trade.counterparty}: {trade.refs[0]} for {trade.refs[1]}"
        status: Status = "approved" if v.window_open() else "expired"
        did = self.rec.decide(
            v.tick,
            "team_open",
            what,
            inputs=self._inputs(trade, None),
            reason=trade.reason,
            guardrail="allowed",
            chosen=status == "approved",
            status=status,
            move={"kind": "team_open", "venue": HOUSE_VENUE},  # public: never the team (a private thread)
            jev=advice,
        )
        if status != "approved" or not self.live:
            return
        body = self.rec.send(
            did,
            v.tick,
            "open_thread",
            {"team": trade.counterparty, "topic": TOPIC, "venue": HOUSE_VENUE},  # "team", not the public "with"
            lambda: self.team.open_thread(trade.counterparty, topic=TOPIC, venue=HOUSE_VENUE),
        )
        if body is None or not isinstance(body.get("id"), int):
            return
        talk = Talk(int(body["id"]), trade.counterparty, trade, v.tick)
        self.talks[talk.thread_id] = talk
        self._payloads[talk.thread_id] = {}
        self._propose(v, talk, advice)  # the opening's own verdict: Jev is not asked twice for one proposal

    # ------------------------------------------------------------ sends

    def _guard(self, v: DeskView, trade: Trade, cash: int, thread: int | None, replacing: int = 0) -> Verdict:
        """The swap through the guardrails, as #79's `Swap` describes it, plus the fairness check."""
        swap = self._swap(v, trade, cash)
        if swap is None:
            return Verdict(False, ("our copy or the card's value is unknown",))
        ctx = v.ctx(thread)
        verdicts = [check(a, ctx, self.rules) for a in swap.actions()]
        problems = [p for x in verdicts for p in x.violations]
        halted = any(x.halted for x in verdicts)
        fair = judge(trade, cash, 0, self.rules, repeat=self.deals[trade.counterparty] > 0)
        if not fair.ok:
            problems.append(fair.reason)
        if (over := self._over_cash_cap(v, -cash, replacing)) is not None:
            problems.append(over)
        return Verdict(not problems, tuple(dict.fromkeys(problems)), halted)

    def _over_cash_cap(self, v: DeskView, add: int, replacing: int = 0) -> str | None:
        """`team_swap_max_cash_per_hour`: the cash we add to swaps in the last game hour (the shared ledger's
        `team:` spend rows, refunds netted, never below 0) plus this one, less `replacing` (the cash of our
        standing offer this one cancels and replaces). None when it fits, or when we add no cash."""
        if add <= 0:
            return None
        booked = self.ledger.spent_since(v.t_hours - 1.0, TEAM_SPEND) if self.ledger is not None else 0
        spent = max(0, booked - max(0, replacing))
        cap = self.rules.team_swap_max_cash_per_hour
        if spent + add > cap:
            return f"swap cash {add} + {spent} this hour > team_swap_max_cash_per_hour {cap}"
        return None

    def guard_accept(self, v: DeskView, a: SwapAccept) -> Verdict:
        """Taking their offer: our copy leaves at what we receive, and the cash we pay (their ask plus the fee:
        we are the accepting side) is a bid for their card. The fairness verdict was taken in `proposals`."""
        if (why := disabled(self.rules, self.env)) is not None:
            return Verdict(False, (why,))
        swap = self._swap(v, a.trade, a.offer.cash_in - a.offer.cash_out - a.fee)
        if swap is None:
            return Verdict(False, ("our copy or the card's value is unknown",))
        verdicts = [check(x, v.ctx(a.thread_id), self.rules) for x in swap.actions()]
        over = self._over_cash_cap(v, a.offer.cash_out + a.fee - a.offer.cash_in)
        problems = tuple(dict.fromkeys([*(p for x in verdicts for p in x.violations), *([over] if over else [])]))
        return Verdict(not problems, problems, any(x.halted for x in verdicts))

    # ------------------------------------------------------------ the Jev gate

    def jev_gate(
        self, v: DeskView, trade: Trade, cash: int, fee: int, thread: int | None, step: int, kind: str = "propose"
    ) -> tuple[bool, JevAdvice | None, str]:
        """Jev `team_swap_worth_it` on a swap every rule already allows: only a decided yes at or above
        `team_swap_jev_min_confidence` sends. Undecided, no, a timeout (`jev_timeout_s` makes it undecided), no
        tick budget, or an error: nothing is sent (fail closed). Off (`team_swap_jev_gate = false`): allowed."""
        if not self.rules.team_swap_jev_gate:
            return True, None, "jev gate off"
        try:
            advice = v.jev(self.swap_state(v, trade, cash, fee, thread, step, kind))
        except Exception as e:  # noqa: BLE001 — a Jev failure refuses the swap, never the tick
            return False, None, f"jev failed ({type(e).__name__}): not sent"
        bar = self.rules.team_swap_jev_min_confidence
        if advice.verdict == "yes" and advice.value >= bar:
            return True, advice, f"jev yes ({advice.value:.2f})"
        why = advice.reason or ""
        return (
            False,
            advice,
            f"jev {advice.verdict} ({advice.value:.2f} < {bar:g} or not yes{', ' + why if why else ''})",
        )

    def swap_state(
        self, v: DeskView, trade: Trade, cash: int, fee: int, thread: int | None, step: int, kind: str = "propose"
    ) -> dict[str, Any]:
        """What Jev reads: both cards at official and private values, the cash leg, the fee we pay (an accept),
        both gains at our values and their share, the score guard's estimate of the copy we give, and the
        history with this team."""
        give_ref, get_ref = trade.refs[0], trade.refs[1]
        ctx = v.ctx(thread)
        mine: dict[str, Any] = next((a for a in v.me.get("assets") or [] if a.get("id") == trade.asset_id), {})
        held_give = int(ctx.held.get(give_ref, 0))

        def official(ref: str, held: int) -> float | None:
            return None if ctx.values is None else ctx.values.value(ref, v.tick, held)

        verdict = judge(trade, cash, fee, self.rules, repeat=self.deals[trade.counterparty] > 0)
        total = verdict.ours + verdict.theirs
        plan = self._plan
        need = None if plan is None else plan.pages.get(set_of(get_ref) or "")
        get_official = official(get_ref, int(ctx.held.get(get_ref, 0)))
        page = None
        if need is not None:  # the page the card we receive belongs to
            page = {
                "set": need.set_code,
                "have": need.have,
                "of": need.of,
                "missing_after": need.missing - 1,
                "completes_page": need.missing == 1,
                "affinity": need.affinity,
                **self._page_bonus(need, get_ref, get_official),
            }
        return {
            "swap": {
                "give": {
                    "card": give_ref,
                    "copies_held": held_give,
                    "official_value": official(give_ref, max(0, held_give - 1)),  # the copy that leaves
                    "private_value": mine.get("your_value"),
                },
                "get": {
                    "card": get_ref,
                    "copies_held": int(ctx.held.get(get_ref, 0)),
                    "official_value": get_official,
                    "private_value": None if plan is None else plan.worth.get(get_ref),
                    "page": page,
                },
                "cash": cash,
                "fee": fee,
                "our_gain": round(verdict.ours, 2),
                "their_gain": round(verdict.theirs, 2),
                "their_share": round(verdict.theirs / total, 3) if total > 0 else None,
                "kind": kind,  # propose: ours, they accept and pay the fee; accept: theirs, we pay it
                "score_impact": self._score_impact(v, trade, cash - fee),  # net of our fee, as in `guard_accept`
            },
            "history": {"settled_with_team": self.deals[trade.counterparty], "proposal_step": step},
            "cash_above_floor": ctx.cash - self.rules.cash_floor,
        }

    def _page_bonus(self, need: PageNeed, ref: str, official: float | None) -> dict[str, Any]:
        """The bonus completing the page scores. /api/me/value is the source of truth: for the page's last
        missing card it already includes the completion gain (SAL-09 read 177.1 = 70 × 1.3 + 86.1 at SAL 9/10),
        so the bonus is that value less book × affinity. Our model (25 % of the page's value) only when the
        card does not complete the page or the official value is unread."""
        book = None if self._plan is None else self._plan.books.get(ref)
        if need.missing == 1 and official is not None and book is not None:
            return {"page_bonus": round(max(0.0, official - book * need.affinity), 1), "page_bonus_source": "official"}
        return {"page_bonus": need.bonus, "page_bonus_source": "model"}

    def _score_impact(self, v: DeskView, trade: Trade, cash: int) -> dict[str, Any] | None:
        """The score guard's estimate (`impact_board.sell_state`) of our copy `trade.asset_id` leaving at what the
        guard sees it leave for: the sale `Swap.actions()` checks, at `cash` (net of any fee we pay). None when the
        swap cannot be priced or the estimate fails: a state builder never costs the swap its tick."""
        try:
            swap = self._swap(v, trade, cash)
            if swap is None:
                return None
            sale = next((a for a in swap.actions() if a.kind == "sell"), None)
            if sale is None or sale.price is None:
                return None
            return impact_board.sell_state(
                v.me,
                sale.item,
                sale.rarity,
                sale.price,
                trade.counterparty,
                self.rules,
                v.tick,
                asset=trade.asset_id,
                value=sale.your_value,
            )
        except Exception:  # noqa: BLE001 — the estimate only informs Jev; the guard still checks the send
            return None

    def _jev_refused(
        self, v: DeskView, kind: str, trade: Trade, thread: int | None, why: str, advice: JevAdvice | None
    ) -> None:
        self.rec.decide(
            v.tick,
            kind,
            f"swap with {trade.counterparty} not sent: {why}",
            inputs=self._inputs(trade, thread),
            reason=why,
            guardrail="allowed",
            chosen=False,
            status="rejected",
            thread_id=thread,
            move={"kind": kind},
            jev=advice,
        )

    def _swap(self, v: DeskView, trade: Trade, cash: int) -> Swap | None:
        if trade.asset_id is None:
            return None
        mine = next((a for a in v.me.get("assets") or [] if a.get("id") == trade.asset_id), None)
        plan = self._plan
        worth = plan.worth.get(trade.refs[1]) if plan is not None else None
        if mine is None or not isinstance(mine.get("your_value"), int | float) or worth is None:
            return None
        return Swap(
            trade.asset_id,
            trade.refs[0],
            mine.get("rarity"),
            float(mine["your_value"]),
            trade.refs[1],
            trade.rarity or None,
            worth,
            HOUSE_VENUE,
            trade.counterparty,
            give_cash=max(0, -cash),
            want_cash=max(0, cash),
            notional=trade.volume,
        )

    def _propose(self, v: DeskView, talk: Talk, advice: JevAdvice | None = None) -> None:
        cash = cash_at(talk.trade, talk.step, self.ladder)
        standing = self._seen_open(v, talk)  # this proposal replaces it (netted only when SEEN open)
        verdict = self._guard(v, talk.trade, cash, talk.thread_id, max(0, -talk.cash) if standing else 0)
        if verdict.halted:
            self.log(f"tick {v.tick} team desk: kill switch on: holding thread {talk.thread_id} ({verdict})")
            return
        if not verdict.allowed:
            self._walk(v, talk, f"guardrail: {verdict}")
            return
        terms = offer_terms(talk.trade, cash)
        if self.ledger is not None and self.ledger.count_in_tick("listing", v.tick) >= v.listing_cap:
            self.log(
                f"tick {v.tick} team desk: this tick's {v.listing_cap} listings are used; thread {talk.thread_id} waits"
            )
            return
        if advice is None:
            ok, advice, why = self.jev_gate(v, talk.trade, cash, 0, talk.thread_id, talk.step)
            if not ok:
                self._jev_refused(v, "team_offer", talk.trade, talk.thread_id, why, advice)
                last = max(talk.opened_tick, talk.sent_tick)  # their messages never extend the hold
                if (
                    advice is not None
                    and advice.reason == NO_JEV_BUDGET
                    and v.tick - last < self.rules.team_thread_idle_ticks
                ):
                    return  # no Jev budget this tick (not a verdict): nothing is sent, the next tick asks again
                self._walk(v, talk, why)  # fail closed: no unjudged proposal, and no Jev question every tick
                return
        status: Status = "approved" if v.window_open() else "expired"
        what = f"swap proposal {talk.step + 1} to {talk.team} on thread {talk.thread_id}: {terms}"
        did = self.rec.decide(
            v.tick,
            "team_offer",
            f"{what} · guardrails {verdict}",
            inputs=self._inputs(talk.trade, talk.thread_id),
            reason=f"{talk.trade.reason}; step {talk.step} of the ladder",
            guardrail=str(verdict),
            chosen=status == "approved",
            status=status,
            thread_id=talk.thread_id,
            move={"kind": "team_offer"},  # public: never the cards or cash of a private thread
            jev=advice,
        )
        if status != "approved":
            return
        if self.live:
            if talk.offer_id is not None and self._still_open(v, talk):  # one standing offer per thread
                old = talk.offer_id
                body = self.rec.send(did, v.tick, "cancel", {"offer": old}, partial(self.team.cancel, old))
                if body is None:
                    self.log(f"tick {v.tick} team desk: cancel of offer {old} refused: no new offer this tick")
                    return  # never two standing offers in one thread; the next tick reads what stands
                self._after_cancel(v, talk.thread_id, self._talk_offer(talk), body)
                if body.get("status") not in DEAD:  # e.g. `settled`: it was taken, our copy may be gone
                    self.log(f"tick {v.tick} team desk: offer {old} reads {body.get('status')}: no new offer")
                    return  # the next tick reads the thread (a deal ends it)
            elif talk.offer_id is not None and self._gone(v, talk):  # expired or cancelled by the server
                self._refund(v, self._talk_offer(talk))
            talk.offer_id = None
            self._spend(v, -cash, talk.trade.refs[1])  # booked before the send: an outage never leaves it unbooked
            text = self.words(WordsRequest(f"team:{talk.team}", cash, talk.step, talk.trade.refs[1], tick=v.tick))
            body = self.rec.send(
                did,
                v.tick,
                "say",
                {"thread_id": talk.thread_id, "swap": terms},  # kept out of the public request fields
                lambda: self.team.say(talk.thread_id, text, offer=terms),
            )
            if body is None and not self.rec.maybe_landed:  # refused: nothing stands, the spend comes back, but
                # only on a definitive refusal (4xx): a 5xx may have landed, and then the spend stays booked
                if cash < 0 and 400 <= self.rec.last_status < 500:
                    refused = {"give": {"cash": -cash}, "want": {"cards": [talk.trade.refs[1]]}, "created_tick": v.tick}
                    self._synthetic -= 1  # a refused send has no offer id: a fresh synthetic one, refunded once
                    self._refund(v, {"id": self._synthetic, **refused})
                return
            offer_id = (body or {}).get("offer")  # lost on the way back: the next tick reads it from the thread
            talk.offer_id = offer_id if isinstance(offer_id, int) else None
        talk.step += 1
        talk.sent_tick, talk.cash = v.tick, cash  # before the ledger write: an outage there loses nothing
        if self.live and self.ledger is not None:
            # A thread offer is a new listing: the maker's offers_per_team_per_tick budget sees it.
            self.ledger.record("listing", v.tick, v.t_hours, 0, f"team:{talk.thread_id}")

    def _seen_open(self, v: DeskView, talk: Talk) -> bool:
        """Our last offer in the thread is SEEN open this tick and its spend not given back: only then does a
        replacement net its cash out of `team_swap_max_cash_per_hour` (unseen may mean settled)."""
        payload = self._payloads.get(talk.thread_id) or {}
        seen = [o for o in [*(payload.get("standing_offers") or []), *v.offers] if o.get("id") == talk.offer_id]
        open_ = any(o.get("status") in (None, "open", "queued") for o in seen)
        return talk.offer_id is not None and talk.offer_id not in self.refunded and open_

    def _still_open(self, v: DeskView, talk: Talk) -> bool:
        """Is our last offer in the thread still standing? An expired or cancelled one needs no cancel."""
        payload = self._payloads.get(talk.thread_id) or {}
        seen = [o for o in [*(payload.get("standing_offers") or []), *v.offers] if o.get("id") == talk.offer_id]
        return not seen or any(o.get("status") in (None, "open", "queued") for o in seen)

    def _cancel_left(self, v: DeskView, tid: int, team: str, offer: dict[str, Any]) -> None:
        """Cancel an offer of ours left open in a thread whose deal settled, and give its spend back."""
        oid = int(offer["id"])
        verdict = check(Action("cancel", str(oid)), v.ctx(tid), self.rules)
        if verdict.halted or not v.window_open():
            return  # the kill switch holds; the next tick tries again
        did = self.rec.decide(
            v.tick,
            "team_walk",
            f"cancel our offer {oid} left in thread {tid} with {team}: its deal settled",
            inputs={"thread": tid, "team": team},
            reason="one deal per thread",
            guardrail=str(verdict),
            chosen=True,
            status="approved",
            thread_id=tid,
            move={"kind": "cancel"},
        )
        if self.live and (body := self.rec.send(did, v.tick, "cancel", {"offer": oid}, partial(self.team.cancel, oid))):
            self._after_cancel(v, tid, offer, body)

    def _after_cancel(self, v: DeskView, tid: int, offer: dict[str, Any], body: dict[str, Any]) -> None:
        """A cancel answered: the spend comes back only if the answer says the offer is dead (a cancel of an
        offer that settled meanwhile answers `settled`); anything else is read again later."""
        if body.get("status") in DEAD:
            self._refund(v, offer)
        else:
            self._check_later(v, tid, offer)

    def _check_later(self, v: DeskView, tid: int, offer: dict[str, Any]) -> None:
        if isinstance(offer.get("id"), int) and offer["id"] not in self.refunded:
            self.to_check.setdefault(int(offer["id"]), (tid, offer, v.tick))

    def _settled_or_dead(self, v: DeskView, tid: int, offer: dict[str, Any], payload: dict[str, Any]) -> None:
        status = offer_status(payload, int(offer["id"]), v.us) if isinstance(offer.get("id"), int) else None
        if status in DEAD:
            self._refund(v, offer)
        elif status not in ("accepted", "settled"):
            self._check_later(v, tid, offer)

    def _check_refunds(self, v: DeskView) -> None:
        """Offers of ours whose end we have not seen yet (a closed thread, an unclear cancel): read their thread
        again, give the spend back once one reads dead, keep it if it settled or after `CHECK_TICKS`."""
        for oid, (tid, offer, since) in list(self.to_check.items()):
            if tid not in self._tried:  # one read per thread per tick, a refused one included (no retry);
                self._tried.add(tid)  # only a successful read is stored: the talk loop and _inbound trust it
                if fetched := self._payload({"id": tid}):
                    self._payloads[tid] = fetched
            payload = self._payloads.get(tid, {})
            status = offer_status(payload, oid, v.us)
            if status in DEAD:
                self._refund(v, offer)
            if status in (*DEAD, "accepted", "settled") or v.tick - since > CHECK_TICKS:
                del self.to_check[oid]

    def _gone(self, v: DeskView, talk: Talk) -> bool:
        """Our last offer reads expired or cancelled: it will never settle (accepted or unseen: not gone)."""
        payload = self._payloads.get(talk.thread_id) or {}
        seen = [o for o in [*(payload.get("standing_offers") or []), *v.offers] if o.get("id") == talk.offer_id]
        return bool(seen) and all(o.get("status") in ("expired", "cancelled") for o in seen)

    def _walk(self, v: DeskView, talk: Talk, why: str) -> None:
        if self._close(v, talk.thread_id, talk.team, why):
            self.talks.pop(talk.thread_id, None)
            self.rest_until[talk.team] = v.tick + REST_TICKS

    def _close(self, v: DeskView, tid: int, team: str, why: str) -> bool:
        verdict = check(Action("close_thread", str(tid)), v.ctx(tid), self.rules)
        if verdict.halted:
            self.log(f"tick {v.tick} team desk: kill switch on: holding thread {tid} ({verdict})")
            return False
        status: Status = "approved" if v.window_open() else "expired"
        did = self.rec.decide(
            v.tick,
            "team_walk",
            f"close thread {tid} with {team}: {why}",
            inputs={"thread": tid, "team": team},
            reason=why,
            guardrail=str(verdict),
            chosen=status == "approved",
            status=status,
            thread_id=tid,
            move={"kind": "walk"},
        )
        if status != "approved":
            return False
        if self.live:
            ours = self._ours_in(v, tid)  # a close cancels our open offers, never one already accepted: their
            if self.rec.send(did, v.tick, "close_thread", {"thread": tid}, lambda: self.team.close_thread(tid)):
                for o in ours:  # spend comes back once a read shows them dead
                    self._check_later(v, tid, o)
        self.first_seen.pop(tid, None)
        self._closed.add(tid)
        return True

    def _inputs(self, trade: Trade, thread: int | None) -> dict[str, Any]:
        """Public-safe: only the thread, venue and fee reach `/state` (the team, the cards and our values stay
        out: none of their keys is in the public allow-list)."""
        return {
            "thread": thread,
            "team": trade.counterparty,
            "give_card": trade.refs[0],
            "want_card": trade.refs[1],
        } | {
            "venue": HOUSE_VENUE,
            "fee": trade.fee,
        }

    # ------------------------------------------------------------ the plan

    def _trades(self, v: DeskView) -> tuple[Trade, ...]:
        """The trade desk's planned swaps, best first, recomputed every `plan_ttl_ticks` (the affinity map and
        the search take a fraction of a second on Friday's feed)."""
        if self._plan is not None and v.tick - self._plan.tick < self.plan_ttl:
            return self._plan.trades
        try:
            amap = af.affinity_map(
                v.events, af.catalog_sets(v.catalog), af.multipliers_from(v.me), v.catalog, exclude=[v.us]
            )
            rastro = next((x for x in v.venues if x.id == HOUSE_VENUE), None)
            # Swaps only, one thread at a time: the plan-wide share rule (25 % of a plan's volume per team) would
            # refuse any plan of fewer than four teams. Fairness here is per deal (`swaps.judge`) and, when it
            # is on, the cumulative `max_counterparty_share` in every guardrail check.
            pp = PlanParams(listings=0, threads=max(1, self.rules.team_threads_max_open * 2), max_share=1.0)
            spent = v.ctx(None).spent_last_hour
            m = build_market(v.me, v.catalog, v.events, [])
            pages = page_needs(m)
            args = (v.me, v.catalog, v.events, amap, v.params, self.rules, pp, rastro, v.offers, spent, v.scan)
            focus = closest_pages(pages)
            # The closest pages' swaps are planned on their own (build_plan's objective would trade them away
            # for bigger gains elsewhere), then the rest of the plan follows them, so a page whose holders will
            # not deal never stops every other swap.
            first = build_plan(*args, focus=focus).threads if focus else ()
            rest = build_plan(*args).threads
            planned = {(f.counterparty, f.refs) for f in first}
            threads = [*first, *(t for t in rest if (t.counterparty, t.refs) not in planned)]
            worth = {w.ref: w.worth for w in wanted_cards(m, v.params, self.rules, dealer_prices(v.events))}
        except (BazaarError, LedgerUnavailable):
            raise  # a refused read or a ledger outage is the taker's to report (it holds the tick)
        except Exception as e:  # noqa: BLE001 — a plan that cannot be built means no swaps, never a dead tick
            self.log(f"tick {v.tick} team desk: no plan this tick ({type(e).__name__}: {e})")
            self._plan = _Plan(v.tick, ())
            return ()
        trades = tuple(sorted(threads, key=lambda t: self._priority(t, pages)))
        self._plan = _Plan(v.tick, trades, worth, pages, {ref: c.book for ref, c in m.cards.items()})
        return trades

    @staticmethod
    def _priority(t: Trade, pages: Mapping[str, PageNeed]) -> tuple[Any, ...]:
        """Among the planned swaps (`build_plan` already kept only the closest pages' cards while it could), the
        page closest to complete first, then our affinity for it, then the expected gain: completing a page is
        what scores (Omar, Sat 3 Oct)."""
        page = pages.get(set_of(t.refs[1]) or "")
        return (*(page.rank() if page is not None else (99, 0.0)), -t.expected, t.counterparty, t.refs)
