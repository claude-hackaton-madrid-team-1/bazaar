"""MAKER: every tick, keep our standing offers on the boards in line with the strategy.

Album first (`/api/me`), then:
  - ASKS for the strategy's sell candidates (`sell_to_need`: duplicates and low-affinity cards, priced
    at the buyer's need), never below `your_value × sell_min_value_ratio` (plus the page bonus a sale
    gives up, which the strategy's ask already includes);
  - BIDS for missing page cards only teams hold, below their value to us, with the cash every open
    offer already promises counted, so open bids can never take cash below `cash_floor`;
  - each on the venue with the best expected fill (`market.best_venue`: El Rastro or a busier, cheaper
    team venue), never our own venue (`self_venue`);
  - stale offers repriced (the target moved by `reprice_min_change`: tape and supply move it) or
    cancelled (no longer a target: we hold the card, the copy is needed, the sell surplus is gone);
  - with Jev (`maker_jev.MakerJev`), each price picked among three legal candidates by
    `list_price_choice` and each reprice weighed by `reprice_or_hold`; `undecided` keeps today's move.
Caps: `offers_per_team_per_tick` new listings per tick for the whole team (counted in the shared
ledger), `max_open_offers_per_team` open offers. The maker owns our BOARD offers: one we listed by hand
that is not a strategy target is cancelled, so stop the maker before trading by hand.
Our own venue rides on the same tick (`agents/venue_keeper.py`, before the offers above): opened once
after `venue_open_after_game_hours`, then its broker matches the book every tick.
Dry run (the default) sends nothing and logs WOULD-moves.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from bazaar_agent.agents.maker_jev import (
    PRICE_QUESTION,
    REPRICE_QUESTION,
    MakerJev,
    ask_floor,
    listing_state,
    price_candidates,
    reprice_state,
)
from bazaar_agent.agents.market import OpenOffer, Side, best_venue, our_open_offers
from bazaar_agent.agents.runtime import (
    JevAdvice,
    MarketFeed,
    Recorder,
    Snapshot,
    TickWindow,
    read_snapshot,
    window_for,
)
from bazaar_agent.agents.seller import (
    Listing,
    OfferError,
    bid_listing,
    offers_in,
    open_commitments,
    post,
    sell_listing,
)
from bazaar_agent.decisions import DecisionLog, Status
from bazaar_agent.guardrails import Context, Guardrails, LedgerStore, context_from, effective_cash_floor
from bazaar_agent.holdings import Holdings
from bazaar_agent.ledger_pg import LedgerUnavailable
from bazaar_agent.sdk import BazaarError
from bazaar_agent.strategy import Playbook, StrategyParams, build_playbook
from bazaar_agent.ticks import Clock


@dataclass(frozen=True)
class MakerConfig:
    offer_ttl_ticks: int = 40  # expires_in_ticks of a new offer (the SDK default)
    reprice_min_change: float = 0.05  # reprice when the target moved by at least 5 % (and 1 P)


@dataclass(frozen=True)
class Target:
    """Where one of our offers should stand now, from the strategy."""

    side: Side
    ref: str
    rarity: str
    price: int
    asset_id: int | None  # asks: the exact copy we list
    value: float  # asks: what we lose by selling; bids: worth to us
    score: float
    reason: str


def targets_from(book: Playbook) -> list[Target]:
    asks = [
        Target("ask", mv.ref, mv.rarity, int(mv.limit), mv.asset_id, mv.value, mv.score, mv.reason)
        for mv in book.sells
        if mv.asset_id is not None and mv.limit > 0
    ]
    bids = [
        Target("bid", mv.ref, mv.rarity, int(mv.limit), None, mv.value, mv.score, mv.reason)
        for mv in book.buys
        if mv.action == "bid" and mv.limit > 0
    ]
    return sorted(asks + bids, key=lambda t: -t.score)


def sell_floor(your_value: float, rules: Guardrails) -> int:
    """The lowest ask GUARDRAILS.md allows for a copy: `your_value × sell_min_value_ratio`, rounded up."""
    return math.ceil(your_value * rules.sell_min_value_ratio - 1e-9)


@dataclass(frozen=True)
class MakerAction:
    kind: Literal["post", "cancel", "reprice"]
    why: str
    target: Target | None = None
    offer: OpenOffer | None = None


def moved(current: int, target: int, min_change: float) -> bool:
    return abs(current - target) >= max(1, round(target * min_change))


def plan_offers(targets: Iterable[Target], mine: Iterable[OpenOffer], tick: int, cfg: MakerConfig) -> list[MakerAction]:
    """Cancels first (they free open-offer slots), then reprices, then new posts by score."""
    targets = list(targets)
    asks = {t.asset_id: t for t in targets if t.side == "ask"}
    bids = {t.ref: t for t in targets if t.side == "bid"}
    covered: set[tuple[str, object]] = set()
    cancels, reprices = [], []
    for o in mine:
        key: tuple[str, object] = ("ask", o.asset_id) if o.side == "ask" else ("bid", o.ref)
        t = asks.get(o.asset_id) if o.side == "ask" else bids.get(o.ref)
        if t is None or key in covered:
            side = "sell" if o.side == "ask" else "buy"
            why = "duplicate offer" if key in covered else f"{o.ref} is no longer a {side} target"
            cancels.append(MakerAction("cancel", why, offer=o))
            continue
        covered.add(key)
        lapsing = o.expires_tick is not None and o.expires_tick <= tick
        if not lapsing and moved(o.price, t.price, cfg.reprice_min_change):
            reprices.append(MakerAction("reprice", f"target moved {o.price} → {t.price}", t, o))
    posts = [
        MakerAction("post", "new target", t)
        for t in targets
        if ("ask", t.asset_id) not in covered and ("bid", t.ref) not in covered
    ]
    return cancels + reprices + posts


@dataclass
class _MakerRun:
    snap: Snapshot
    window: TickWindow
    base: Context  # /me + ledger, before any open offer is counted
    offers: list[dict[str, Any]]  # our open offers as the server knows them, updated as we act
    open_total: int
    listings_left: int
    spent: int = 0  # bid cash committed this tick (posted or would-be), counted against the spend cap
    posted: list[str] = field(default_factory=list)
    params: StrategyParams | None = None  # this tick's strategy parameters (Jev's price candidates)


class Maker:
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
        config: MakerConfig | None = None,
        now: Callable[[], float] = time.monotonic,
        hub: Any = None,
        jev: MakerJev | None = None,
        holdings: Holdings | None = None,
        market: Any = None,
    ) -> None:
        self.team, self.public, self.rules, self.params = team, public, rules, params
        self.ledger, self.feed, self.live, self.log, self.now = ledger, feed, live, log, now
        self.config = config or MakerConfig()
        self.jev = jev  # Jev picks prices and reprice-or-hold among legal candidates; None = today's prices
        self.holdings = holdings  # /me from the shared Postgres snapshot while provably current, else live
        self.rec = Recorder("maker", decisions, live, log, hub)
        self.hub = hub  # agents.status.StatusHub: the read-only HTTP/WS view, when served
        self.market = market  # agents.venue_keeper.VenueKeeper: our venue and its broker; None = no venue

    def on_tick(self, clock: Clock) -> None:
        window = window_for(clock, self.now(), self.now)
        self.rec.decisions.begin_tick(clock.tick)
        snap: Snapshot | None = None
        try:
            snap = read_snapshot(self.team, self.public, self.feed, clock, self.holdings)
        except BazaarError as e:
            self.log(f"tick {clock.tick} maker: read refused {e.code} ({e.message[:80]}); nothing sent")
        if self.market is not None:  # the bench first: a broker without our reads still matches the bench
            self.market.on_tick(clock, snap, window)
        if snap is None:
            return
        try:
            self._tick(snap, window)
        except BazaarError as e:
            self.log(f"tick {clock.tick} maker: read refused {e.code} ({e.message[:80]}); nothing sent")
        except LedgerUnavailable as e:
            self.log(f"tick {clock.tick} maker: {e}; no write this tick (fail closed)")

    def _tick(self, snap: Snapshot, window: TickWindow) -> None:
        clock = snap.clock
        if self.hub is not None:
            self.hub.tick(clock.tick, clock.t_hours, snap.us)
        params = self.params(clock.tick)
        book = build_playbook(snap.me, snap.catalog, snap.events, snap.dealers, params, self.rules)
        mine, total = our_open_offers(snap.offers, snap.us)
        listed = self.ledger.count_in_tick("listing", clock.tick)
        run = _MakerRun(
            snap,
            window,
            context_from(snap.me, clock.tick, clock.t_hours, self.ledger, self.rules),
            offers_in(snap.offers),
            total,
            max(0, clock.limits.offers_per_team_per_tick - listed),
            params=params,
        )
        targets = targets_from(book)
        if self.jev is not None:
            for line in self.jev.watch.observe(mine, clock.tick):
                self.log(f"tick {clock.tick} maker: {line}")
            self.jev.begin_tick(mine)
            targets = [self.jev.remembered(t, params, self.rules) for t in targets]
        actions = plan_offers(targets, mine, clock.tick, self.config)
        for action in actions:
            self._do(run, action)
        if self.hub is not None:
            self.hub.view(open_offers=[asdict(o) for o in mine], posted_this_tick=list(run.posted))
        verb = "posted" if self.live else "would post"
        self.log(
            f"tick {clock.tick} maker: {len(actions)} action(s), {len(run.posted)} {verb}, {run.open_total} open "
            f"offer(s), {run.listings_left} listing(s) left, {window.left():.1f} s left · "
            f"{'LIVE' if self.live else 'dry run'}"
            + (f" · {snap.holdings.line()}" if snap.holdings is not None else "")
        )

    def _ctx(self, run: _MakerRun) -> Context:
        """/me + the shared ledger + the bid cash this tick already committed (posted or would-be)."""
        return replace(run.base, spent_last_hour=run.base.spent_last_hour + run.spent)

    def _do(self, run: _MakerRun, action: MakerAction) -> None:
        if action.kind == "cancel" and action.offer is not None:
            self._cancel(run, action.offer, action.why)
        elif action.kind == "reprice" and action.target is not None and action.offer is not None:
            if run.listings_left <= 0:
                offer = action.offer
                self.log(f"tick {run.snap.clock.tick} maker: keep {offer.ref} at {offer.price}: no listing left")
                return
            hold, advice = self._jev_hold(run, action.offer, action.target)
            if hold:
                return
            if self._cancel(run, action.offer, action.why):
                offer_id = self._post(run, action.target, f"reprice: {action.why}")
                if self.jev is not None and offer_id is not None:
                    self.jev.watch.watch(offer_id, advice, REPRICE_QUESTION, self._expires(run))
        elif action.target is not None:
            self._post(run, action.target, action.why)

    def _cancel(self, run: _MakerRun, offer: OpenOffer, why: str) -> bool:
        tick = run.snap.clock.tick
        status: Status = "approved" if run.window.open() else "expired"
        inputs = {
            "offer_id": offer.id,
            "side": offer.side,
            "ref": offer.ref,
            "price": offer.price,
            "venue": offer.venue,
        }
        did = self.rec.decide(
            tick,
            f"cancel_{offer.side}",
            f"cancel {offer.side} {offer.id} {offer.ref} at {offer.price} on {offer.venue}: {why}",
            inputs=inputs,
            reason=why,
            guardrail="allowed",
            chosen=status == "approved",
            status=status,
            move={"cancel": offer.id},
        )
        if status != "approved":
            return False
        if self.live:
            if self.rec.send(did, tick, "cancel", {"offer": offer.id}, lambda: self.team.cancel(offer.id)) is None:
                return False
            if self.jev is not None:
                self.jev.watch.cancelled(offer.id)
            if offer.side == "bid":  # a bid's cash was counted as spend when posted: give it back
                self.ledger.record("spend", tick, run.snap.clock.t_hours, -offer.price, offer.ref)
        run.offers = [o for o in run.offers if o.get("id") != offer.id]
        run.open_total -= 1
        return True

    def _listing(self, run: _MakerRun, t: Target, venue: str) -> Listing:
        if t.side == "ask" and t.asset_id is not None:
            listing = sell_listing(run.snap.me, str(t.asset_id), t.price, venue)
            if listing.your_value is not None and t.price < sell_floor(listing.your_value, self.rules):
                raise OfferError(f"ask {t.price} is below the sell floor {sell_floor(listing.your_value, self.rules)}")
            return listing
        return bid_listing(t.ref, t.rarity, t.price, venue)

    def _post(self, run: _MakerRun, t: Target, why: str) -> int | None:
        """Post one offer; the new offer's id when it went out live, else None."""
        tick = run.snap.clock.tick
        venue = best_venue(run.snap.venues, run.snap.us, t.price)
        blocked = self._blocked(run)
        t, advice, candidates = self._jev_price(run, t, venue.id) if venue and not blocked else (t, None, None)
        inputs = {
            "side": t.side,
            "ref": t.ref,
            "rarity": t.rarity,
            "price": t.price,
            "asset_id": t.asset_id,
            "value": t.value,
            "score": t.score,
            "venue": venue.id if venue else None,
            **({"price_candidates": candidates} if candidates else {}),
        }
        what = f"post {t.side} {t.ref}{f' #{t.asset_id}' if t.asset_id else ''} at {t.price}"
        if venue is None or blocked:
            why_not = blocked or "no venue we may trade on"
            self._reject(run, t, what, inputs, why_not, "rejected")
            return None
        try:
            listing = self._listing(run, t, venue.id)
        except OfferError as e:
            self._reject(run, t, what, inputs, f"denied: {e}", "rejected", advice)
            return None
        commitments = open_commitments(run.offers, run.snap.us)  # cash, cards and assets our offers promise
        verdict = post(self.team, listing, self._ctx(run), self.rules, live=False, commitments=commitments).verdict
        if not verdict.allowed:
            self._reject(run, t, f"{what} on {venue.id}", inputs, str(verdict), "rejected", advice)
            return None
        status: Status = "approved" if run.window.open() else "expired"
        line = f"{what} on {venue.id} (fee {venue.fee(t.price)} paid by the taker; {why}) · guardrails {verdict}"
        did = self.rec.decide(
            tick,
            f"post_{t.side}",
            line,
            inputs=inputs,
            reason=t.reason,
            guardrail=str(verdict),
            chosen=status == "approved",
            status=status,
            jev=advice,
            move={"give": listing.give, "want": listing.want, "venue": venue.id},
        )
        if status != "approved":
            return None
        offer_id = None
        if self.live:
            request = {"give": listing.give, "want": listing.want, "venue": venue.id}
            body = self.rec.send(
                did,
                tick,
                "list_offer",
                request,
                lambda: self.team.list_offer(
                    listing.give, listing.want, venue=venue.id, expires_in_ticks=self.config.offer_ttl_ticks
                ),
            )
            if body is None:
                return None
            self.ledger.record("listing", tick, run.snap.clock.t_hours, t.price, t.ref)
            if t.side == "bid":
                self.ledger.record("spend", tick, run.snap.clock.t_hours, t.price, t.ref)
            offer_id = body.get("id") if isinstance(body.get("id"), int) else None
            applied = advice is not None and (candidates or {}).get(advice.verdict) == t.price
            if self.jev is not None and offer_id is not None and applied:  # judged only on the price it set
                self.jev.watch.watch(offer_id, advice, PRICE_QUESTION, self._expires(run))
        if t.side == "bid":
            run.spent += t.price
        run.offers.append(
            {"id": -1, "status": "open", "maker": run.snap.us, "give": listing.give, "want": listing.want}
        )
        run.open_total += 1
        run.listings_left -= 1
        run.posted.append(t.ref)
        return offer_id

    def _blocked(self, run: _MakerRun) -> str | None:
        limits = run.snap.clock.limits
        if run.listings_left <= 0:
            return f"no new listing left this tick (offers_per_team_per_tick {limits.offers_per_team_per_tick})"
        if run.open_total >= limits.max_open_offers_per_team:
            return f"{run.open_total} open offers (max_open_offers_per_team {limits.max_open_offers_per_team})"
        return None

    def _reject(
        self,
        run: _MakerRun,
        t: Target,
        what: str,
        inputs: dict[str, Any],
        why: str,
        status: Status,
        jev: JevAdvice | None = None,
    ) -> None:
        self.rec.decide(
            run.snap.clock.tick,
            f"post_{t.side}",
            f"skip {what}: {why}",
            inputs=inputs,
            reason=t.reason,
            guardrail=why if why.startswith("denied") else "-",
            chosen=False,
            status=status,
            jev=jev,
        )

    # ------------------------------------------------------------ Jev: the price and reprice-or-hold

    def _expires(self, run: _MakerRun) -> int:
        return run.snap.clock.tick + self.config.offer_ttl_ticks

    def _allowed(self, run: _MakerRun, t: Target, venue: str) -> bool:
        """The checks `_post` runs, without sending: the sell floor, then every guardrail with our open offers.
        An ask also stays at or above what selling costs us, page bonus included (`ask_floor` of its value)."""
        if t.side == "ask" and t.price < ask_floor(t.value, self.rules):
            return False
        try:
            listing = self._listing(run, t, venue)
        except OfferError:
            return False
        commitments = open_commitments(run.offers, run.snap.us)
        return post(self.team, listing, self._ctx(run), self.rules, live=False, commitments=commitments).verdict.allowed

    def _jev_context(self, run: _MakerRun) -> dict[str, Any]:
        cash = int(run.snap.me.get("cash") or 0)
        return {
            "cash": cash,
            "cash_floor": effective_cash_floor(self.rules, run.base),
            "cash_above_floor": max(0, cash - effective_cash_floor(self.rules, run.base)),
            "open_offers": run.open_total,
            "max_open_offers": run.snap.clock.limits.max_open_offers_per_team,
            "listings_left_this_tick": run.listings_left,
            "offer_ttl_ticks": self.config.offer_ttl_ticks,
            "tick": run.snap.clock.tick,
        }

    def _jev_price(
        self, run: _MakerRun, t: Target, venue: str
    ) -> tuple[Target, JevAdvice | None, dict[str, int] | None]:
        """The target at the price Jev picked among the legal candidates (today's price when undecided)."""
        if self.jev is None or run.params is None:
            return t, None, None
        try:
            candidates = price_candidates(t, run.params, self.rules)
            legal = {label: p for label, p in candidates.items() if self._allowed(run, replace(t, price=p), venue)}
            state = listing_state(t, candidates, legal, {**self._jev_context(run), "venue": venue})
            label, advice, why = self.jev.choose_price(t, candidates, legal, state, run.window.left)
        except Exception as e:  # a bug in the Jev layer must never cost the tick: today's price
            self.log(f"tick {run.snap.clock.tick} maker: jev price failed ({type(e).__name__}); today's price")
            return t, None, None
        return replace(t, price=candidates.get(label, t.price), reason=f"{t.reason}; {why}"), advice, candidates

    def _jev_hold(self, run: _MakerRun, offer: OpenOffer, t: Target) -> tuple[bool, JevAdvice | None]:
        """Ask `reprice_or_hold` about a stale offer; a decided `no` holds it (one decision row, nothing sent)."""
        if self.jev is None:
            return False, None
        tick = run.snap.clock.tick
        state = reprice_state(offer, t, tick, self._jev_context(run))
        try:
            hold, advice = self.jev.should_hold(offer, t, self.rules, state, run.window.left)
        except Exception as e:  # a bug in the Jev layer must never cost the tick: reprice as today
            self.log(f"tick {tick} maker: jev reprice failed ({type(e).__name__}); repricing")
            return False, None
        if advice is None:
            return False, None
        verb = "hold" if hold else "reprice"
        self.rec.decide(
            tick,
            f"{verb}_{offer.side}",
            f"{verb} {offer.side} {offer.id} {offer.ref} at {offer.price} (target {t.price}): jev {advice.verdict}",
            inputs=state,
            reason=t.reason,
            guardrail="allowed",
            chosen=hold,
            status="approved",
            jev=advice,
            move={"hold": offer.id} if hold else {"reprice": offer.id, "price": t.price},
        )
        if hold:
            self.jev.watch.watch(offer.id, advice, REPRICE_QUESTION, offer.expires_tick)
        return hold, advice
