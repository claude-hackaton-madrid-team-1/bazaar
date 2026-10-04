"""Promote an observed public ask without promising inventory or placing an offer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from bazaar_agent.agents.market import BoardOffer, parse_offer
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.agents.seller import OfferError, sell_listing
from bazaar_agent.agents.team_desk import DeskView
from bazaar_agent.guardrails import Action, Guardrails, LedgerStore, check, kill_switch
from bazaar_agent.intel import TEAM_ID, listed_makers
from bazaar_agent.ledger_pg import trade_lock
from bazaar_agent.team_matrix import TeamMatrix
from bazaar_agent.team_matrix_store import MAX_AGE_TICKS


def public_ask(raw: Any, venue: str, tick: int) -> BoardOffer | None:
    """Exact one-card public quote; no private/thread, expired or malformed offers."""
    if not isinstance(raw, dict) or raw.get("to") is not None or raw.get("thread") is not None:
        return None
    if raw.get("status") != "open" or raw.get("venue", venue) != venue:
        return None
    expiry = raw.get("expires_tick")
    if type(expiry) is not int or expiry <= tick or type(raw.get("id")) is not int:
        return None
    give, want = raw.get("give"), raw.get("want")
    if not isinstance(give, dict) or not isinstance(want, dict) or type(want.get("cash")) is not int:
        return None
    assets = give.get("assets")
    if (
        not isinstance(assets, list)
        or len(assets) != 1
        or not isinstance(assets[0], dict)
        or type(assets[0].get("id")) is not int
    ):
        return None
    try:
        offer = parse_offer(raw, venue)
    except (ValueError, TypeError, KeyError, OverflowError):
        return None
    if offer is None or offer.side != "ask" or not 0 < offer.price <= 10_000_000 or offer.asset_id is None:
        return None
    return offer


class SalesPromotion:
    def __init__(
        self, team: Any, public: Any, rules: Guardrails, ledger: LedgerStore, rec: Recorder, live: bool
    ) -> None:
        self.team, self.public, self.rules, self.ledger, self.rec, self.live = team, public, rules, ledger, rec, live
        self.sent_words: Callable[[int, str, str, int, int, str, dict[str, Any]], None] | None = None

    def on_tick(self, v: DeskView, matrix: TeamMatrix | None) -> bool:
        if (
            not self.live
            or not self.rules.team_threads_enabled
            or matrix is None
            or matrix.us != v.us
            or not 0 <= v.tick - matrix.tick <= MAX_AGE_TICKS
            or not v.window_open()
            or kill_switch(self.rules)
        ):
            return False
        if not any(p.id == "rastro" and p.status == "open" and p.owner != v.us for p in v.venues):
            return False
        needs = {
            (c.team, c.card)
            for c in matrix.cells
            if c.missing_for_page
            and TEAM_ID.fullmatch(c.team)
            and c.team != v.us
            and not self.rules.never_trades_with(c.team)
        }
        if not needs:
            return False
        world = getattr(self.ledger, "world", "unknown")
        tick_claim = f"operator_say:promotion:{world}"
        with trade_lock(self.ledger):
            if self.ledger.count_in_tick(tick_claim, v.tick) or not v.window_open():
                return False
            # At most one own-market read; the public feed may have evicted an older live ask.
            ours = next((p for p in v.venues if p.owner == v.us and p.status == "open"), None)
            candidates: list[BoardOffer] = []
            if ours is not None:
                candidates = [
                    o
                    for raw in self.public.board(ours.id).get("offers", [])
                    if (o := public_ask(raw, ours.id, v.tick)) is not None and o.maker != v.us
                ]
            preferred = set(v.params.preferred_sell_venue_owners.split(","))
            partners = {p.id for p in v.venues if p.status == "open" and p.owner in preferred and p.owner != v.us}
            fallback = [
                o
                for raw in v.offers
                if raw.get("maker") == v.us
                and raw.get("venue") in partners
                and (o := public_ask(raw, str(raw["venue"]), v.tick)) is not None
            ]
            # The selected fallback gets one fresh public-book read before any message.
            raw_threads = self.team.my_threads("open")
            threads = [
                t for t in raw_threads.get("threads", []) if isinstance(t, dict) and t.get("status", "open") == "open"
            ]
            dealers = sum(t.get("kind") in ("persona", "dealer") for t in threads)
            reserve = max(0, self.rules.team_threads_dealer_reserve - dealers)
            if len(threads) + reserve >= v.max_threads or len(threads) - dealers >= self.rules.team_threads_max_open:
                return False
            busy = {str(t.get(key)) for t in threads for key in ("team", "with")}
            owners = {p.id: p.owner for p in v.venues}
            makers = listed_makers(v.events)
            for offer in sorted(candidates, key=lambda o: (o.ref, o.price, o.id)) + sorted(
                fallback, key=lambda o: (o.ref, o.price, o.id)
            ):
                for target, ref in sorted(needs):
                    if (
                        ref != offer.ref
                        or target in busy
                        or target in (makers.get(offer.id, offer.maker), owners.get(offer.venue))
                    ):
                        continue
                    marker = f"operator_say:promotion:{world}:{offer.id}:{target}"
                    opening = f"operator_say:sales_open:{world}:{v.us}:{target}"
                    if self.ledger.count_since(marker, v.t_hours - 1):
                        continue
                    if self.ledger.count_since(opening, -1) > self.ledger.count_since(opening + ":resolved", -1):
                        continue
                    if offer in fallback:
                        try:
                            listing = sell_listing(v.me, str(offer.asset_id), offer.price, offer.venue, None)
                        except OfferError:
                            continue
                        ctx = v.ctx(None)
                        if ctx.sellable is not None:
                            # Evaluate this existing ask, not a second copy of its promise.
                            # All other commitments remain subtracted; nothing is released.
                            ctx = replace(ctx, sellable={**ctx.sellable, offer.ref: ctx.sellable.get(offer.ref, 0) + 1})
                        if not check(listing.action(), ctx, self.rules).allowed:
                            continue
                        current = [
                            public_ask(raw, offer.venue, v.tick)
                            for raw in self.public.board(offer.venue).get("offers", [])
                        ]
                        if not any(
                            o is not None
                            and (o.id, o.ref, o.price, o.asset_id) == (offer.id, offer.ref, offer.price, offer.asset_id)
                            for o in current
                        ):
                            return False
                    return self._send(v, offer, target, marker, opening, tick_claim)
        return False

    def _send(self, v: DeskView, offer: BoardOffer, target: str, marker: str, opening: str, tick_claim: str) -> bool:
        ctx = v.ctx(None)
        if not check(Action("team_open", counterparty=target), ctx, self.rules).allowed or not v.window_open():
            return False
        inputs = {
            "counterparty": target,
            "ref": offer.ref,
            "side": "promotion",
            "price": offer.price,
            "venue": offer.venue,
            "offer_id": offer.id,
        }
        did = self.rec.decide(
            v.tick,
            "sales_promotion_open",
            "open a public-offer introduction",
            inputs=inputs,
            reason="scanner need matches an observed public ask; no financial commitment",
            guardrail="allowed",
            chosen=True,
            status="approved",
            move={"kind": "sales_promotion_open"},
        )
        self.ledger.record(tick_claim, v.tick, v.t_hours)
        self.ledger.record(marker, v.tick, v.t_hours)
        self.ledger.record(opening, v.tick, v.t_hours)
        if kill_switch(self.rules) or not v.window_open():
            self.ledger.record(opening + ":resolved", v.tick, v.t_hours)
            self.rec.decisions.settle(did, "expired")
            return False
        body = self.rec.send(
            did,
            v.tick,
            "open_thread",
            {"team": target, "venue": "rastro"},
            lambda: self.team.open_thread(target, topic={"trade": "cards"}, venue="rastro"),
        )
        if body is None or type(body.get("id")) is not int:
            if (
                body is None
                and not self.rec.maybe_landed
                and 400 <= self.rec.last_status < 500
                and self.rec.last_status != 408
            ):
                self.ledger.record(opening + ":resolved", v.tick, v.t_hours)
            return True
        self.ledger.record(opening + ":resolved", v.tick, v.t_hours)
        tid = body["id"]
        text = (
            f"He visto la oferta pública #{offer.id}: {offer.ref} por {offer.price} P en {offer.venue}. "
            "Puede interesarte para tu colección. Revisa su vigencia y tus valores antes de aceptarla; "
            "este mensaje no reserva cartas ni acepta ningún trato."
        )
        action = Action("team_say", counterparty=target)
        if (
            self.ledger.count_in_tick(f"operator_say:{tid}", v.tick)
            or not check(action, ctx, self.rules).allowed
            or not v.window_open()
        ):
            return True
        did = self.rec.decide(
            v.tick,
            "sales_promotion",
            text,
            inputs={**inputs, "thread": tid},
            reason="text-only public offer introduction",
            guardrail="allowed",
            chosen=True,
            status="approved",
            thread_id=tid,
            move={"kind": "sales_promotion"},
        )
        self.ledger.record(f"operator_say:{tid}", v.tick, v.t_hours)
        if kill_switch(self.rules) or not v.window_open():
            self.rec.decisions.settle(did, "expired")
            return True
        body = self.rec.send(did, v.tick, "say", {"thread_id": tid}, lambda: self.team.say(tid, text))
        if body is not None and type(body.get("message")) is int and self.sent_words is not None:
            try:
                self.sent_words(tid, target, v.us, v.tick, body["message"], text, {})
            except Exception as error:
                self.rec.log(f"sales: promotion text not buffered ({type(error).__name__})")
        return True
