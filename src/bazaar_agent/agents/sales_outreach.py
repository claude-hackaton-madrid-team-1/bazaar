"""One proactive guarded cash-sale conversation; public demand is a hint, never a valuation.

No scans or model decisions: existing feed/matrix selects a buyer, fresh shared guards
price the exact copy. Unknown publications and openings are never retried blindly.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from bazaar_agent import rivals
from bazaar_agent.agents import publication
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.agents.seller import (
    MAX_PRICE,
    Listing,
    committed_context,
    offers_in,
    open_commitments,
    sell_listing,
    trade_book,
)
from bazaar_agent.agents.team_desk import DeskView, venue_invite
from bazaar_agent.agents.words import WordsFn, WordsRequest
from bazaar_agent.guardrails import Guardrails, LedgerStore, check, context_from, kill_switch
from bazaar_agent.intel import TEAM_ID, book_values
from bazaar_agent.ledger_pg import trade_lock
from bazaar_agent.team_matrix import TeamMatrix
from bazaar_agent.team_matrix_store import MAX_AGE_TICKS


def sale_lead(v: DeskView, rules: Guardrails, matrix: TeamMatrix | None) -> Listing | None:
    """Highest proposed surplus among unpromised, guard-eligible copies with a buyer hint."""
    if not any(p.id == "rastro" and p.status == "open" and p.owner != v.us for p in v.venues):
        return None
    needs: dict[tuple[str, str], int] = {}
    if matrix is not None and matrix.us == v.us and 0 <= v.tick - matrix.tick <= MAX_AGE_TICKS:
        needs.update(((c.team, c.card), 0) for c in matrix.cells if c.missing_for_page)
    try:
        for bid in rivals.listings(v.events):
            if bid.side == "bid" and bid.to in (None, v.us) and bid.expires_tick is not None and bid.open_at(v.tick):
                needs[bid.maker, bid.ref] = max(needs.get((bid.maker, bid.ref), 0), bid.price)
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        pass  # unreadable public hints cannot authorize a write
    busy = {str(t.get("with")) for t in v.threads} | {str(t.get("team")) for t in v.threads}
    busy |= {
        str(o.get("to"))
        for o in v.offers
        if o.get("thread") is not None and o.get("status") in ("open", "queued", "accepted")
    }
    committed = open_commitments(v.offers, v.us)
    ctx = v.ctx(None)
    leads: list[Listing] = []
    for a in v.me.get("assets") or []:
        value = a.get("your_value")
        if (
            a.get("kind") != "card"
            or type(a.get("id")) is not int
            or a["id"] in committed.listed
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value < 0
        ):
            continue
        for (team, ref), bid_price in needs.items():
            if (
                ref != a.get("ref")
                or team == v.us
                or not TEAM_ID.fullmatch(team)
                or team in busy
                or rules.never_trades_with(team)
            ):
                continue
            price = max(
                1,
                bid_price,
                math.ceil(value + v.params.sell_min_surplus),
                math.ceil(value * rules.sell_min_value_ratio),
            )
            if price > MAX_PRICE:
                continue
            listing = sell_listing(v.me, str(a["id"]), price, "rastro", team)
            if check(listing.action(), ctx, rules).allowed:
                leads.append(listing)
    return max(leads, key=lambda x: (x.price - (x.your_value or 0), x.to or "", x.asset_id or 0), default=None)


class SalesOutreach:
    def __init__(
        self,
        team: Any,
        rules: Guardrails,
        ledger: LedgerStore,
        rec: Recorder,
        log: Callable[[str], None],
        live: bool,
        words: WordsFn | None = None,
    ) -> None:
        self.team, self.rules, self.ledger, self.rec = team, rules, ledger, rec
        self.log, self.live, self.words = log, live, words
        self.sent_words: Callable[[int, str, str, int, int, str, dict[str, Any]], None] | None = None

    def on_tick(self, v: DeskView, matrix: TeamMatrix | None) -> None:
        if not self.live or not self.rules.team_threads_enabled or not v.window_open():
            return
        if sale_lead(v, self.rules, matrix) is None:
            return  # no extra reads when there is no uncommitted sale candidate
        with trade_lock(self.ledger):
            if not v.window_open() or kill_switch(self.rules):
                return
            me = self.team.me()
            if me.get("id") != v.us:
                return
            offers = publication.with_pending(
                self.ledger, me, offers_in(self.team.my_offers()), v.us, v.tick, v.t_hours
            )
            payload = self.team.my_threads()
            threads = [t for t in payload.get("threads", []) if t.get("status", "open") == "open"]
            dealers = sum(t.get("kind") == "dealer" for t in threads)
            team_count = len(threads) - dealers
            reserve = max(0, self.rules.team_threads_dealer_reserve - dealers)
            if (
                len(threads) + reserve >= v.max_threads
                or team_count >= self.rules.team_threads_max_open
                or len(offers) >= v.max_open_offers
                or self.ledger.count_in_tick("listing", v.tick) >= v.listing_cap
            ):
                return
            old = v.ctx(None)
            base = context_from(me, v.tick, v.t_hours, self.ledger, self.rules, old.values)
            trades = trade_book(offers, v.us, old.trades.settled, book_values(v.catalog)) if old.trades else None
            ctx = committed_context(replace(base, trades=trades), open_commitments(offers, v.us))
            fresh = replace(v, me=me, offers=offers, threads=threads, ctx=lambda _: ctx)
            listing = sale_lead(fresh, self.rules, matrix)
            if listing is None:
                return
            claim = f"operator_say:sales_open:{getattr(self.ledger, 'world', 'unknown')}:{v.us}:{listing.to}"
            # Durable unresolved opening claim: a lost response must be reconciled, not retried.
            if self.ledger.count_since(claim, -1) > self.ledger.count_since(claim + ":resolved", -1):
                return
            verdict = check(listing.action(), ctx, self.rules)
            if not verdict.allowed or not v.window_open() or kill_switch(self.rules):
                return
            terms = {"give": listing.give, "want": listing.want}
            inputs = {
                "ref": listing.ref,
                "side": "sell",
                "price": listing.price,
                "venue": "rastro",
                "counterparty": listing.to,
                "terms": terms,
            }
            did = self.rec.decide(
                v.tick,
                "team_open",
                "open a guarded sale conversation",
                inputs=inputs,
                reason="public demand hint; exact cash terms checked against our fresh inventory",
                guardrail=str(verdict),
                chosen=True,
                status="approved",
                move={"kind": "team_open"},
            )
            self.ledger.record(claim, v.tick, v.t_hours)
            if not v.window_open() or kill_switch(self.rules):
                self.ledger.record(claim + ":resolved", v.tick, v.t_hours)
                self.rec.decisions.settle(did, "expired")
                return
            body = self.rec.send(
                did,
                v.tick,
                "open_thread",
                {"team": listing.to, "venue": "rastro"},
                lambda: self.team.open_thread(listing.to, topic={"trade": "cards"}, venue="rastro"),
            )
            if body is None or type(body.get("id")) is not int:
                if (
                    body is None
                    and not self.rec.maybe_landed
                    and 400 <= self.rec.last_status < 500
                    and self.rec.last_status != 408
                ):
                    self.ledger.record(claim + ":resolved", v.tick, v.t_hours)
                return
            self.ledger.record(claim + ":resolved", v.tick, v.t_hours)
            self._offer(fresh, listing, body["id"], inputs)

    def _offer(self, v: DeskView, listing: Listing, tid: int, inputs: dict[str, Any]) -> None:
        if not v.window_open() or self.ledger.count_in_tick(f"operator_say:{tid}", v.tick):
            return
        text = "Hola: te propongo esta venta de un cromo. El precio y la copia exactos van en la oferta adjunta."
        if self.words is not None:
            phrase = self.words(
                WordsRequest(f"team:{listing.to}", listing.price, item=listing.ref, tick=v.tick, budget_s=v.budget_s())
            )
            if isinstance(phrase, str) and 0 < len(phrase.strip()) <= 1000:
                text = phrase.strip()
        own = next(
            (
                p
                for p in v.venues
                if p.owner == v.us
                and p.status == "open"
                and p.fee_bps == 0
                and p.fee_per_card == 0
                and p.pending_fee in (None, (0, 0))
            ),
            None,
        )
        if own is not None:
            text += " " + venue_invite(own.id)
        verdict = check(listing.action(), v.ctx(None), self.rules)
        if not verdict.allowed or not v.window_open() or kill_switch(self.rules):
            return
        did = self.rec.decide(
            v.tick,
            "team_cash_offer",
            listing.describe(),
            inputs={**inputs, "thread": tid},
            reason="proactive sale with exact guarded terms",
            guardrail=str(verdict),
            chosen=True,
            status="approved",
            thread_id=tid,
            move={"kind": "team_cash_offer"},
        )
        token = publication.reserve(
            self.ledger, v.tick, v.t_hours, v.us, listing.give, listing.want, tid, to=listing.to
        )
        self.ledger.record(f"operator_say:{tid}", v.tick, v.t_hours)
        self.ledger.record("listing", v.tick, v.t_hours, listing.price, listing.ref)
        if not v.window_open() or kill_switch(self.rules):
            publication.release(self.ledger, token, v.tick, v.t_hours)
            self.rec.decisions.settle(did, "expired")
            return
        terms = {"give": listing.give, "want": listing.want}
        body = self.rec.send(
            did, v.tick, "say", {"thread_id": tid, "cash_offer": terms}, lambda: self.team.say(tid, text, offer=terms)
        )
        if body is not None and type(body.get("message")) is int and self.sent_words is not None:
            try:
                self.sent_words(tid, listing.to or "", v.us, v.tick, body["message"], text, terms)
            except Exception as error:
                self.log(f"sales: sent words not buffered ({type(error).__name__})")
        if body is not None and type(body.get("offer")) is int:
            publication.confirm(self.ledger, token, body["offer"], v.tick, v.t_hours)
        elif (
            body is None
            and not self.rec.maybe_landed
            and 400 <= self.rec.last_status < 500
            and self.rec.last_status != 408
        ):
            publication.release(self.ledger, token, v.tick, v.t_hours)
