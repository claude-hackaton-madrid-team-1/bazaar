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
`team_threads_enabled`, and `BAZAAR_TEAM_THREADS=0` in the environment turns it off at the next tick.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bazaar_agent import affinity as af
from bazaar_agent.agents.market import Venue
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.agents.seller import Swap, open_commitments
from bazaar_agent.agents.words import WordsFn, WordsRequest
from bazaar_agent.decisions import Status
from bazaar_agent.guardrails import Action, Context, Guardrails, Verdict, check
from bazaar_agent.intel import TEAM_ID
from bazaar_agent.sdk import BazaarError
from bazaar_agent.strategy import StrategyParams, build_market
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
    accepted: bool = False  # we took their offer: the thread waits for its deal


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


@dataclass
class _Plan:
    tick: int
    trades: tuple[Trade, ...]
    worth: dict[str, float] = field(default_factory=dict)  # card ref -> one more copy to us


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
    ) -> None:
        self.team, self.rules, self.rec, self.log, self.live = team, rules, rec, log, live
        self.ladder, self.words, self.env, self.plan_ttl = ladder or Ladder(), words, env, plan_ttl_ticks
        self.talks: dict[int, Talk] = {}
        self.deals: Counter[str] = Counter()  # settled swaps per team (`judge(repeat=...)`)
        self.first_seen: dict[int, int] = {}  # inbound thread -> the tick we first saw it
        self._payloads: dict[int, dict[str, Any]] = {}
        self._closed: set[int] = set()  # threads we closed this tick: still in this tick's list, never adopted
        self._plan: _Plan | None = None

    # ------------------------------------------------------------ reads

    def _team_threads(self, v: DeskView) -> list[dict[str, Any]]:
        return [
            t
            for t in v.threads
            if isinstance(t.get("id"), int)
            and (t.get("kind") == "team" or TEAM_ID.match(str(t.get("with") or "")))
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
        self._payloads, self._closed = {}, set()
        if disabled(self.rules, self.env):
            return []
        venues = {x.id: x for x in v.venues}
        out: list[SwapAccept] = []
        for t in self._team_threads(v):
            payload = self._payload(t)
            if not payload:
                continue
            tid = int(t["id"])
            self._payloads[tid] = payload
            talk = self.talks.get(tid)
            if talk is not None:
                talk.heard_tick = max(talk.heard_tick, self._heard(payload, v.us))
            else:
                self.first_seen.setdefault(tid, v.tick)
            venue = venues.get(str(payload.get("venue") or t.get("venue") or HOUSE_VENUE))
            for raw in payload.get("standing_offers") or []:
                offer = read_offer(raw, v.us)
                accept = None if offer is None or venue is None else self._judge_offer(v, tid, talk, offer, venue)
                if accept is not None:
                    out.append(accept)
        return sorted(out, key=lambda a: -a.verdict.ours)[:1]  # one accept per tick for the whole team

    def _judge_offer(
        self, v: DeskView, tid: int, talk: Talk | None, offer: TheirOffer, venue: Venue
    ) -> SwapAccept | None:
        trades = [talk.trade] if talk is not None else [t for t in self._trades(v) if t.counterparty == offer.team]
        trade = next((t for t in trades if is_the_planned_swap(offer, t)), None)
        if trade is None:
            return None
        fee = venue.fee(
            offer.cash_in or offer.cash_out, len(offer.get_assets) + len(offer.give_assets or offer.give_refs)
        )
        verdict = judge(trade, offer.net_cash, fee, self.rules, repeat=self.deals[offer.team] > 0)
        if not verdict.ok:
            self.log(f"tick {v.tick} team desk: {offer.team}'s offer {offer.offer_id} refused: {verdict.reason}")
            return None
        return SwapAccept(
            tid,
            offer,
            trade,
            verdict,
            fee,
            [trade.asset_id] if offer.give_refs and trade.asset_id is not None else None,
        )

    def accepted(self, a: SwapAccept, tick: int) -> None:
        """The taker took their offer: the thread waits for its deal (the server settles it next tick), and no
        other proposal goes there."""
        talk = self.talks.get(a.thread_id) or Talk(a.thread_id, a.offer.team, a.trade, tick)
        talk.accepted, talk.sent_tick = True, tick
        self.talks[a.thread_id] = talk

    # ------------------------------------------------------------ (2) what we say

    def converse(self, v: DeskView, taken: set[int]) -> None:
        if (why := disabled(self.rules, self.env)) is not None:
            if self.talks:
                self.log(f"tick {v.tick} team desk: off ({why}): {len(self.talks)} thread(s) left as they are")
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
            del self.talks[tid]
            status = "deal" if talk.accepted else str(self._payload({"id": tid}).get("status") or "?")
            if status == "deal":  # their accept of our offer, or ours of theirs
                self.deals[talk.team] += 1
            self.log(f"tick {v.tick} team desk: thread {tid} with {talk.team} ended ({status})")

    def _next_move(self, v: DeskView, talk: Talk) -> None:
        if talk.accepted:
            return
        if talk.step >= self.rules.team_thread_max_messages:
            self._walk(v, talk, f"{talk.step} proposals sent (team_thread_max_messages)")
            return
        replied = talk.heard_tick > talk.sent_tick
        if talk.step == 0 or (replied and talk.step < self.ladder.steps):
            self._propose(v, talk)
        elif v.tick - max(talk.sent_tick, talk.heard_tick) >= self.rules.team_thread_idle_ticks:
            self._walk(v, talk, f"nothing new for {self.rules.team_thread_idle_ticks} ticks at our last price")

    def _inbound(self, v: DeskView) -> None:
        """A thread another team opened: answer it with a planned swap with that team, else close it once it
        has been silent `team_thread_idle_ticks` (another team cannot park on our conversation slots)."""
        for t in self._team_threads(v):
            tid = int(t["id"])
            if tid in self.talks or tid in self._closed or tid not in self._payloads:
                continue
            team = self._other(t, v.us)
            busy = {k.trade.asset_id for k in self.talks.values()} | {k.trade.refs[1] for k in self.talks.values()}
            trade = next(
                (x for x in self._trades(v) if x.counterparty == team and not {x.asset_id, x.refs[1]} & busy), None
            )
            if trade is not None:
                talk = Talk(tid, team, trade, v.tick)
                self.talks[tid] = talk
                self._propose(v, talk)
                continue
            since = max(self.first_seen.get(tid, v.tick), self._heard(self._payloads[tid], v.us))
            if v.tick - since >= self.rules.team_thread_idle_ticks:
                self._close(v, tid, team, "an inbound thread with no swap planned with that team, silent")

    def _open(self, v: DeskView) -> None:
        team_open = len(self._team_threads(v))
        room = min(
            self.rules.team_threads_max_open - team_open,
            v.max_threads - v.in_use - self.rules.team_threads_dealer_reserve,
        )
        if room <= 0:
            return
        busy_teams = {self._other(t, v.us) for t in self._team_threads(v)}
        used = {k.trade.asset_id for k in self.talks.values()} | {k.trade.refs[1] for k in self.talks.values()}
        listed = open_commitments(v.offers, v.us).listed
        for trade in self._trades(v):
            if trade.counterparty in busy_teams or {trade.asset_id, trade.refs[1]} & used:
                continue
            if trade.asset_id in listed:
                continue
            verdict = self._guard(v, trade, cash_at(trade, 0, self.ladder), None)
            if not verdict.allowed:
                self.log(f"tick {v.tick} team desk: not opening with {trade.counterparty}: {verdict}")
                continue
            self._open_one(v, trade)
            return  # one opening per tick

    def _open_one(self, v: DeskView, trade: Trade) -> None:
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
            move={"open_thread": trade.counterparty, "topic": TOPIC, "venue": HOUSE_VENUE},
        )
        if status != "approved" or not self.live:
            return
        body = self.rec.send(
            did,
            v.tick,
            "open_thread",
            {"with": trade.counterparty, "topic": TOPIC, "venue": HOUSE_VENUE},
            lambda: self.team.open_thread(trade.counterparty, topic=TOPIC, venue=HOUSE_VENUE),
        )
        if body is None or not isinstance(body.get("id"), int):
            return
        talk = Talk(int(body["id"]), trade.counterparty, trade, v.tick)
        self.talks[talk.thread_id] = talk
        self._payloads[talk.thread_id] = {}
        self._propose(v, talk)

    # ------------------------------------------------------------ sends

    def _guard(self, v: DeskView, trade: Trade, cash: int, thread: int | None) -> Verdict:
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
        return Verdict(not problems, tuple(dict.fromkeys(problems)), halted)

    def guard_accept(self, v: DeskView, a: SwapAccept) -> Verdict:
        """Taking their offer: our copy leaves at what we receive, and the cash we pay (their ask plus the fee:
        we are the accepting side) is a bid for their card. The fairness verdict was taken in `proposals`."""
        if (why := disabled(self.rules, self.env)) is not None:
            return Verdict(False, (why,))
        swap = self._swap(v, a.trade, a.offer.cash_in - a.offer.cash_out - a.fee)
        if swap is None:
            return Verdict(False, ("our copy or the card's value is unknown",))
        verdicts = [check(x, v.ctx(a.thread_id), self.rules) for x in swap.actions()]
        problems = tuple(dict.fromkeys(p for x in verdicts for p in x.violations))
        return Verdict(not problems, problems, any(x.halted for x in verdicts))

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

    def _propose(self, v: DeskView, talk: Talk) -> None:
        cash = cash_at(talk.trade, talk.step, self.ladder)
        verdict = self._guard(v, talk.trade, cash, talk.thread_id)
        if verdict.halted:
            self.log(f"tick {v.tick} team desk: kill switch on: holding thread {talk.thread_id} ({verdict})")
            return
        if not verdict.allowed:
            self._walk(v, talk, f"guardrail: {verdict}")
            return
        terms = offer_terms(talk.trade, cash)
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
            move={"give": terms["give"], "want": terms["want"]},
        )
        if status != "approved":
            return
        if self.live:
            if talk.offer_id is not None:  # one standing offer per thread: the old one goes first
                old = talk.offer_id
                self.rec.send(did, v.tick, "cancel", {"offer": old}, lambda: self.team.cancel(old))
                talk.offer_id = None
            text = self.words(WordsRequest(f"team:{talk.team}", cash, talk.step, talk.trade.refs[1], tick=v.tick))
            body = self.rec.send(
                did,
                v.tick,
                "say",
                {"thread": talk.thread_id, "give": terms["give"], "want": terms["want"]},
                lambda: self.team.say(talk.thread_id, text, offer=terms),
            )
            if body is None:
                return
            offer_id = body.get("offer")
            talk.offer_id = offer_id if isinstance(offer_id, int) else None
        talk.step += 1
        talk.sent_tick = v.tick

    def _walk(self, v: DeskView, talk: Talk, why: str) -> None:
        if self._close(v, talk.thread_id, talk.team, why):
            self.talks.pop(talk.thread_id, None)

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
            self.rec.send(did, v.tick, "close_thread", {"thread": tid}, lambda: self.team.close_thread(tid))
        self.first_seen.pop(tid, None)
        self._closed.add(tid)
        return True

    def _inputs(self, trade: Trade, thread: int | None) -> dict[str, Any]:
        """Public-safe: the cards and the venue (the counterparty and our values stay out of `/state`)."""
        return {"thread": thread, "team": trade.counterparty, "card": trade.refs[0], "ref": trade.refs[1]} | {
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
            pp = PlanParams(listings=0, threads=max(1, self.rules.team_threads_max_open * 2))
            spent = v.ctx(None).spent_last_hour
            plan = build_plan(v.me, v.catalog, v.events, amap, v.params, self.rules, pp, rastro, v.offers, spent)
            m = build_market(v.me, v.catalog, v.events, [])
            worth = {w.ref: w.worth for w in wanted_cards(m, v.params, self.rules, dealer_prices(v.events))}
        except Exception as e:  # noqa: BLE001 — a plan that cannot be built means no swaps, never a dead tick
            self.log(f"tick {v.tick} team desk: no plan this tick ({type(e).__name__}: {e})")
            self._plan = _Plan(v.tick, ())
            return ()
        trades = tuple(sorted(plan.threads, key=lambda t: (-t.expected, t.counterparty, t.refs)))
        self._plan = _Plan(v.tick, trades, worth)
        return trades
