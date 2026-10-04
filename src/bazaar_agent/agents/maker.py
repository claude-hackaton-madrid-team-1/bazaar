"""MAKER: every tick, keep our standing offers on the boards in line with the strategy.

Album first (`/api/me`), then:
  - ASKS for the strategy's sell candidates (`sell_to_need`: duplicates and low-affinity cards, priced
    at the buyer's need), never below `your_value × sell_min_value_ratio` (plus the page bonus a sale
    gives up, which the strategy's ask already includes);
  - BIDS for missing page cards only teams hold, below their value to us, with the cash every open
    offer already promises counted, so open bids can never take cash below `cash_floor`;
  - with `buy_targets_enabled`, a BID for each buy target (`buy_targets.py`: an off-page card a human approved
    buying), at its ladder price, public, at that exact price (no Jev price);
  - each on the venue with the best expected fill (`market.best_venue`: El Rastro or a busier, cheaper
    team venue), never our own venue (`self_venue`);
  - with `max_counterparty_share` on (GUARDRAILS.md, #14), an offer anyone may take is posted only while no
    team could pass its share by taking it; otherwise it is addressed (`to`) to the strategy's counterparty
    (the set's chaser for an ask, a holder for a bid) with the most room, or not posted;
  - stale offers repriced (the target moved by `reprice_min_change`: tape and supply move it, or an ask
    fell below its floor) or cancelled (no longer a target: we hold the card, the copy is needed, the sell
    surplus is gone). A reprice cancels only once its replacement would pass the guardrails; an offer
    that can no longer stand (an ask below its floor, a bid above its new target) is cancelled even
    when it cannot be replaced (no listing left, the post denied), any other one is kept;
  - with `buyer_rank_enabled` (GUARDRAILS.md, off by default), an ask it already decided to post is addressed
    to the best non-rival buyer (`buyers.rank_buyers`), and posted for anyone at the same price once it stood
    `buyer_rank_fallback_ticks` unfilled; one copy never goes to one team twice at one price;
  - with Jev (`maker_jev.MakerJev`), each price picked among three legal candidates by
    `list_price_choice` and each reprice weighed by `reprice_or_hold`; `undecided` keeps today's move.
Caps: `offers_per_team_per_tick` new listings per tick for the whole team (counted in the shared
ledger), `max_open_offers_per_team` open offers. The maker owns our BOARD offers, except those a person
posted with `bazaar sell ... --live` (booked in the shared ledger as `guardrails.HANDS_OFF` listings): it
never cancels or reprices those, and posts nothing for the copy or card they already cover. Any other
offer of ours that is not a strategy target is cancelled.
While the kill switch is on (`guardrails.kill_switch`, read every tick) the maker HOLDS: it reads, but
posts nothing and cancels nothing (a reprice is a cancel plus a post), so our open offers stay open.
Our own venue rides on the same tick (`agents/venue_keeper.py`, before the offers above): opened once
after `venue_open_after_game_hours`, then its broker matches the book every tick.
Dry run (the default) sends nothing and logs WOULD-moves.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from bazaar_agent import buy_targets, rivals
from bazaar_agent import buyers as buyer_rank
from bazaar_agent.agents import counter_bids, outreach_bids, publication
from bazaar_agent.agents.dealer_sell_data import SellMarket
from bazaar_agent.agents.dealer_sell_desk import Candidate, SellDesk, SellHooks, standard_hooks
from bazaar_agent.agents.maker_jev import (
    PRICE_QUESTION,
    REPRICE_QUESTION,
    MakerJev,
    ask_floor,
    listing_state,
    price_candidates,
    reprice_state,
)
from bazaar_agent.agents.market import OpenOffer, Side, Venue, addressed_to_us, best_venue, our_open_offers, parse_offer
from bazaar_agent.agents.relist import MEMORY_TICKS, AskTrail, Relist, market_median, relist_price
from bazaar_agent.agents.runtime import (
    JevAdvice,
    MarketFeed,
    PageWatch,
    Recorder,
    Snapshot,
    TickWindow,
    new_page_line,
    read_snapshot,
    window_for,
)
from bazaar_agent.agents.seller import (
    Listing,
    OfferError,
    bid_listing,
    committed_context,
    offers_in,
    open_commitments,
    post,
    sell_listing,
    trade_book,
    unsettled_accepts,
)
from bazaar_agent.agents.strategy_gate import AskFn, StrategyGate
from bazaar_agent.decisions import RELIST_REST, DecisionLog, Status
from bazaar_agent.guardrails import (
    Action,
    Context,
    Guardrails,
    LedgerRow,
    LedgerStore,
    TradeBook,
    check,
    context_from,
    effective_cash_floor,
    kill_switch,
    refund_row,
    team_ids,
)
from bazaar_agent.holdings import Holdings
from bazaar_agent.intel import book_values, card_rarities, listed_makers, settled_volume, tape
from bazaar_agent.learn.venues import VenueNotices
from bazaar_agent.ledger_pg import LedgerUnavailable, ensure_writable, trade_lock
from bazaar_agent.move_impact import our_cards
from bazaar_agent.official_values import OfficialValues, over_cap
from bazaar_agent.rate_budget import maker_post_attempts
from bazaar_agent.sdk import BazaarError
from bazaar_agent.strategy import Playbook, StrategyParams, build_market, build_playbook, buy_case
from bazaar_agent.team_matrix_store import LatestMatrix
from bazaar_agent.ticks import Clock

CEILING_TICKS = 20  # an outreach card's official value is read again after this many ticks (it moves with our album)
LEADERBOARD_EVERY = 10  # ticks between leaderboard reads for the buyer rank (it refreshes every few minutes)


@dataclass(frozen=True)
class MakerConfig:
    # expires_in_ticks of a new offer. The live server answers 40 (the SDK default) with expires_tick = created + 20
    # (every one of our listings since Friday, and t02/t06/t13's): ask for 80 to keep an ask up ~40 ticks.
    offer_ttl_ticks: int = 80
    reprice_min_change: float = 0.05  # reprice when the target moved by at least 5 % (and 1 P)
    # A bid another team addresses to us below our floor gets an ask addressed back to that team, stepping from an
    # anchor down to our floor like a duel (`agents/counter_bids.py`). BAZAAR_COUNTER_BIDS=0 turns it off.
    counter_bids: bool = True
    # Bids for cards we want go addressed to the teams holding a spare, stepping up to our ceiling, holder after
    # holder (`agents/outreach_bids.py`). BAZAAR_OUTREACH_BIDS=0 turns it off.
    outreach_bids: bool = True
    # Card hunt (`card_hunt.py`, BAZAAR_CARD_HUNT): with `dealer_sell_enabled` on, a dealer sell thread opens only
    # where it fills an empty ladder slot, on the desk's deterministic plan instead of Jev's yes. Off here (code
    # default); the CLI turns it on unless BAZAAR_CARD_HUNT=0. `dealer_sell_enabled` stays the only switch.
    card_hunt: bool = False


HOLDERS_EVERY = 10  # ticks between two walks of the feed for a buy target's holders (a hint for the record)


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
    counterparties: tuple[str, ...] = ()  # asks: the teams that chase the set; bids: the likely holders
    to: str | None = None  # addressed to this team (`max_counterparty_share`); None: anyone on the venue
    final: bool = False  # a buyer-rank fallback: posted for anyone at this exact price (no addressee, no Jev price)
    min_price: int = 0  # asks: a relist's floor (`agents.relist`); no Jev pick may go under it
    counter: bool = False  # a counter (`counter_bids`) or an outreach bid (`outreach_bids`): own price path, no Jev


def targets_from(book: Playbook) -> list[Target]:
    asks = [
        Target("ask", mv.ref, mv.rarity, int(mv.limit), mv.asset_id, mv.value, mv.score, mv.reason, mv.counterparties)
        for mv in book.sells
        if mv.asset_id is not None and mv.limit > 0
    ]
    bids = [
        Target("bid", mv.ref, mv.rarity, int(mv.limit), None, mv.value, mv.score, mv.reason, mv.counterparties)
        for mv in book.buys
        if mv.action == "bid" and mv.limit > 0
    ]
    return sorted(asks + bids, key=lambda t: -t.score)


def _leave_desk_copy(targets: Iterable[Target], me: dict[str, Any], rules: Guardrails) -> list[Target]:
    """Keep the page copies protected by the current album policy.

    Actual standing and uncertain offers are checked again under the publication lock.
    """
    held = Counter(str(a.get("ref")) for a in me.get("assets") or [])
    complete = our_cards(me).complete if me.get("album") else None
    kept: list[Target] = []
    for target in targets:
        if target.side == "ask":
            if held[target.ref] <= 1 and rules.protects(target.ref, target.rarity, 1, complete):
                continue
            held[target.ref] -= 1
        kept.append(target)
    return kept


def _covered_by(t: Target, offers: Iterable[OpenOffer]) -> bool:
    """An offer a person posted by hand already stands for this target's copy (asks) or card (bids)."""
    return any(o.side == t.side and (o.asset_id == t.asset_id if t.side == "ask" else o.ref == t.ref) for o in offers)


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


def cannot_stand(o: OpenOffer, t: Target, rules: Guardrails) -> str | None:
    """Why an open offer may not stay at its price: an ask below the floor of what selling its copy costs us
    now (e.g. a page completed since it was posted), a bid above its new target. None: it may stand."""
    if o.side == "ask":
        floor = ask_floor(t.value, rules)
        return f"ask {o.price} is below its floor {floor}" if o.price < floor else None
    return f"bid {o.price} is above its target {t.price}" if o.price > t.price else None


def plan_offers(
    targets: Iterable[Target],
    mine: Iterable[OpenOffer],
    tick: int,
    cfg: MakerConfig,
    rules: Guardrails,
    above_value: Callable[[OpenOffer], str | None] = lambda o: None,
) -> list[MakerAction]:
    """Cancels first (they free open-offer slots), then reprices, then new posts by score. An ask below its
    floor is repriced however little its target moved; a bid above the official value (`above_value`) is
    cancelled, and its card is not bid again this tick."""
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
        if o.side == "bid" and (over := above_value(o)) is not None:
            cancels.append(MakerAction("cancel", over, offer=o))
            continue
        lapsing = o.expires_tick is not None and o.expires_tick <= tick
        below_floor = o.side == "ask" and cannot_stand(o, t, rules)
        if not lapsing and (moved(o.price, t.price, cfg.reprice_min_change) or below_floor):
            reprices.append(MakerAction("reprice", f"target moved {o.price} → {t.price}", t, o))
    posts = [
        MakerAction("post", "new target", t)
        for t in targets
        if ("ask", t.asset_id) not in covered and ("bid", t.ref) not in covered
    ]
    return cancels + reprices + posts


@dataclass(frozen=True)
class _Bid:
    """One of our board bids as last seen: what a lapse would refund, and how many copies of its card we held
    then (more copies later means it filled)."""

    offer: OpenOffer
    held: int
    seen_tick: int


@dataclass
class _MakerRun:
    snap: Snapshot
    window: TickWindow
    base: Context  # /me + ledger, before any open offer is counted
    offers: list[dict[str, Any]]  # our open offers as the server knows them, updated as we act
    open_total: int
    listings_left: int
    spent: int = 0  # bid cash committed this tick (posted or would-be), counted against the spend cap
    post_attempts: int = 0  # fresh read + guard attempts share the key's request budget, even when refused
    posted: list[str] = field(default_factory=list)
    params: StrategyParams | None = None  # this tick's strategy parameters (Jev's price candidates)
    settled: dict[str, int] | None = None  # primas settled with each team; None: max_counterparty_share is off
    buyer_inputs: tuple[dict[str, dict[str, int]], dict[str, dict[str, float]]] | None = None  # (holders, interest)
    buyer_failed: bool = False  # the buyer rank failed this tick: every ask stays public


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
        notices: VenueNotices | None = None,
        sell_market: Callable[[Any], SellMarket | None] | None = None,
        strategy_jev: AskFn | None = None,
        latest_matrix: LatestMatrix | None = None,
    ) -> None:
        self.team, self.public, self.rules, self.params = team, public, rules, params
        self.ledger, self.feed, self.live, self.log, self.now = ledger, feed, live, log, now
        self.config = config or MakerConfig()
        self._preferred_sell_owners: tuple[str, ...] = ()
        self.jev = jev  # Jev picks prices and reprice-or-hold among legal candidates; None = today's prices
        self.holdings = holdings  # /me from the shared Postgres snapshot while provably current, else live
        self.notices = notices  # announced venue fees and closings from the feed (N12); None = /api/venues only
        self.latest_matrix = latest_matrix  # the team matrix the taker stores (Postgres); None: no team context
        self.rec = Recorder("maker", decisions, live, log, hub)
        self.hub = hub  # agents.status.StatusHub: the read-only HTTP/WS view, when served
        self.market = market  # agents.venue_keeper.VenueKeeper: our venue and its broker; None = no venue
        self.pages = PageWatch()  # album pages seen: a new page is logged once (it is ranked at once anyway)
        # Lapsed bids (bite X15): a bid's cash is booked as spend when posted, so one that expires unfilled
        # must give it back, or every repost books it again. Live only; memory only (a restart forgets: no
        # refund, over-counts).
        self._bids: dict[int, _Bid] = {}  # our board bids seen open (or posted) last tick
        self._lapsing: dict[int, _Bid] = {}  # gone at or after expiry without the card: refunded next tick
        self._spent_at: dict[int, tuple[int, float]] = {}  # bid id -> (tick, t_hours) of the spend we booked
        self.values = OfficialValues.of(team)  # GET /api/me/value: every bid capped at it (Day-2 hint 1)
        self.buy_targets = buy_targets.TargetBook()  # off-page cards a human approved buying (`buy_targets.py`)
        self._target_notes: dict[str, str] = {}  # card -> why its target bid was last not set (logged once each)
        self._holder_notes: dict[str, tuple[int, str]] = {}  # card -> (tick, the teams the feed places it with)
        self._seen: dict[tuple[str, str], counter_bids.Seen] = {}  # (team, card) -> its bids to us (`counter_bids`)
        self._counter_said: dict[tuple[str, str], str] = {}  # (team, card) -> why it is not countered (said once)
        self._liquidity_tick: int | None = None
        # Feed hints reused across this tick's targets; never authorise accepts.
        self._liquidity: list[rivals.Listed] = []
        self._turns: dict[str, outreach_bids.Turn] = {}  # card -> the holder our addressed bid goes to, and since when
        self._ceilings: dict[str, tuple[int, float | None]] = {}  # card -> (tick read, official value) for outreach
        # Selling spares to dealers (`dealer_sell_enabled`, off by default): one sell thread at a time.
        self._run: _MakerRun | None = None
        self._dealer_promises: dict[int, str] = {}
        # Buyer rank (`buyer_rank_enabled`): the leaderboard's ranks (read every LEADERBOARD_EVERY ticks), the
        # (team, price) each copy was addressed to, and our open asks the rank addressed. Memory only.
        self._ranks: dict[str, int] = {}
        self._ranks_tick: int | None = None
        self._tried: dict[int, set[tuple[str, int]]] = {}
        self._ranked: dict[int, str] = {}
        # Jev gates new sell threads (SG1, `dealer_sell_duplicates_worth_it`); no Jev = no new sell thread.
        gate = (
            StrategyGate(strategy_jev, self.rec, rules.strategy_jev_refresh_ticks, rules.risk_posture)
            if strategy_jev
            else None
        )
        self.sell_desk = SellDesk(
            team, rules, self.rec, live, log, self._sell_hooks, sell_market, gate, hunt=self.config.card_hunt
        )

    def on_tick(self, clock: Clock) -> None:
        window = window_for(clock, self.now(), self.now)
        self.rec.decisions.begin_tick(clock.tick, clock.round)
        snap: Snapshot | None = None
        try:
            snap = read_snapshot(
                self.team, self.public, self.feed, clock, self.holdings, parallel=self.rules.parallel_reads
            )
        except BazaarError as e:
            self.log(f"tick {clock.tick} maker: read refused {e.code} ({e.message[:80]}); nothing sent")
        try:
            ensure_writable(self.ledger)  # no game write at all while the shared ledger is down, our venue's included
            if self.market is not None:  # the bench first: a broker without our reads still matches the bench
                self.market.on_tick(clock, snap, window)
            if snap is None:
                return
            self._tick(snap, window)
        except BazaarError as e:
            self.log(f"tick {clock.tick} maker: read refused {e.code} ({e.message[:80]}); nothing sent")
        except LedgerUnavailable as e:
            self.log(f"tick {clock.tick} maker: {e}; no further write this tick (fail closed)")
        finally:
            if self.latest_matrix is not None:  # after the sends: one read every 10 ticks, never raises
                self.latest_matrix.refresh(clock.tick)

    def _tick(self, snap: Snapshot, window: TickWindow) -> None:
        clock = snap.clock
        if self.live:
            # Observe acknowledged offers before any cancel/reprice removes them.
            with trade_lock(self.ledger):
                publication.with_pending(
                    self.ledger, snap.me, offers_in(snap.offers), snap.us, clock.tick, clock.t_hours
                )
        if self.hub is not None:
            self.hub.tick(clock.tick, clock.t_hours, snap.us)
        mine, total = our_open_offers(snap.offers, snap.us)
        self._lapsed_bids(snap, mine)
        stops = kill_switch(self.rules)
        if stops:
            if self.hub is not None:
                self.hub.view(open_offers=[asdict(o) for o in mine], posted_this_tick=[])
            self.log(
                f"tick {clock.tick} maker: kill switch on: holding (no posts, no cancels; {total} open offer(s) "
                f"stay open): {'; '.join(stops)}"
            )
            return
        if fresh := self.pages.new(snap.me):  # after the hold: a page seen while holding is said when we act
            self.log(new_page_line(clock.tick, "maker", fresh, snap.me))
        hands_off = self.ledger.hands_off_ids()
        by_hand = [o for o in mine if o.id in hands_off]
        mine = [o for o in mine if o.id not in hands_off]
        params = self.params(clock.tick)
        # the ranks, before any venue is picked: who is addressed (buyer rank) and whose venue we avoid
        if self.rules.buyer_rank_enabled or self.rules.venue_avoid_rivals:
            self._refresh_ranks(clock.tick)
        self._preferred_sell_owners = team_ids(params.preferred_sell_venue_owners)
        if self.notices is not None:
            self.notices.update(snap.events, snap.us)
        book = build_playbook(snap.me, snap.catalog, snap.events, snap.dealers, params, self.rules, snap.scan)
        listed = self.ledger.count_in_tick("listing", clock.tick)
        run = _MakerRun(
            snap,
            window,
            committed_context(  # an accept of the last ticks /api/me does not show yet counts (bite X18)
                context_from(snap.me, clock.tick, clock.t_hours, self.ledger, self.rules, self.values),
                unsettled_accepts(snap.me, self.ledger, clock.tick),
            ),
            offers_in(snap.offers),
            total,
            max(0, clock.limits.offers_per_team_per_tick - listed),
            params=params,
            settled=(
                settled_volume(snap.events, snap.us, book_values(snap.catalog))
                if self.rules.max_counterparty_share < 1
                else None
            ),
        )
        targets = [t for t in targets_from(book) if not _covered_by(t, by_hand)]
        targets = _leave_desk_copy(targets, snap.me, self.rules)
        if self.jev is not None:
            for line in self.jev.watch.observe(mine, clock.tick):
                self.log(f"tick {clock.tick} maker: {line}")
            self.jev.begin_tick(mine)
            targets = [self.jev.remembered(t, params, self.rules) for t in targets]
        targets = self._relisted(snap, targets, mine)
        held = Counter(str(a.get("ref")) for a in snap.me.get("assets") or [] if a.get("kind") == "card")
        targets = self._with_buy_targets(snap, targets, held)
        if self.config.counter_bids:
            targets = self._with_counters(snap, targets, params)
        if self.config.outreach_bids:
            targets = self._with_outreach(snap, targets, params)
        rarities = card_rarities(snap.catalog)

        def above_value(o: OpenOffer) -> str | None:  # our bids, re-capped every tick (review #177 P2)
            margin = self.rules.value_margin_for(rarities.get(o.ref))  # an epic: strictly below our value
            return over_cap(o.price, o.ref, self.values, clock.tick, held.get(o.ref, 0), margin)

        actions = plan_offers(targets, mine, clock.tick, self.config, self.rules, above_value)
        if self.rules.buyer_rank_enabled:
            actions = self._with_fallbacks(actions, targets, mine, clock.tick)
        actions = self._with_relocations(run, actions, targets, mine)
        for action in actions:
            self._do(run, action)
        # Dealer sales last: never a copy an open offer of ours lists (it is locked) or one the maker wants listed.
        locked = {o.asset_id for o in [*mine, *by_hand] if o.asset_id is not None}
        locked |= {t.asset_id for t in targets if t.side == "ask" and t.asset_id is not None}
        self._run = run
        previous_talk = self.sell_desk.talk
        previous_promises = set(self._dealer_promises)
        self.sell_desk.on_tick(snap, params, locked, window.left)
        # The only first step is opening a thread (no price). A failed opening
        # cannot sell a card; later promises need a confirmed terminal thread.
        ended_assets = set()
        if previous_talk is not None and previous_talk.status in {"walked", "closed", "expired", "refused", "timeout"}:
            ended_assets.add(previous_talk.cand.asset_id)
        if previous_talk is None and self.sell_desk.talk is None:
            ended_assets |= set(self._dealer_promises) - previous_promises
        if self.live and ended_assets:
            with trade_lock(self.ledger):
                for asset in ended_assets:
                    token = self._dealer_promises.pop(asset, None)
                    if token is not None:
                        publication.release(self.ledger, token, clock.tick, clock.t_hours)
        if self.hub is not None:
            self.hub.view(open_offers=[asdict(o) for o in mine], posted_this_tick=list(run.posted))
        verb = "posted" if self.live else "would post"
        self.log(
            f"tick {clock.tick} maker: {len(actions)} action(s), {len(run.posted)} {verb}, {run.open_total} open "
            f"offer(s), {run.listings_left} listing(s) left, {window.left():.1f} s left · "
            f"{'LIVE' if self.live else 'dry run'}"
            + (f" · {snap.holdings.line()}" if snap.holdings is not None else "")
        )

    # ------------------------------------------------------------ counters to bids addressed to us (`counter_bids`)

    def _with_counters(self, snap: Snapshot, targets: list[Target], params: StrategyParams) -> list[Target]:
        """The counter asks first (one per card, addressed to the team that bid to us), then every other target. A
        copy one of our asks already lists (a counter, or a public ask the maker posted) stays free for its counter,
        which takes over that copy's ask at no more than its public price; a copy listed by hand stays locked. No
        request: `/api/me/offers` and the feed are the snapshot's."""
        tick, us = snap.clock.tick, snap.us
        makers = listed_makers(snap.events)
        bids = []
        for raw in addressed_to_us(snap.offers, us):
            o = parse_offer(raw)
            if o is not None and o.side == "bid":
                bids.append(replace(o, maker=makers.get(o.id, o.maker)))
        self._seen = counter_bids.observe(self._seen, bids, tick)
        if not self._seen:
            return targets
        ours = [
            o
            for o in offers_in(snap.offers)
            if o.get("maker") == us and o.get("status") in (None, "open", "queued") and o.get("thread") is None
        ]

        def assets(o: dict[str, Any]) -> set[int]:
            give = o.get("give") or {}
            return {a["id"] if isinstance(a, dict) else a for a in give.get("assets") or [] if a is not None}

        hands_off = self.ledger.hands_off_ids()
        movable = {
            a for o in ours if o.get("id") not in hands_off and (o.get("want") or {}).get("cash") for a in assets(o)
        }
        listed = open_commitments(offers_in(snap.offers), us).listed - movable
        public = {
            a: int((o.get("want") or {}).get("cash") or 0)
            for o in ours
            if not o.get("to") and o.get("id") not in hands_off
            for a in assets(o)
        }
        public.update({t.asset_id: t.price for t in targets if t.side == "ask" and t.asset_id is not None and not t.to})
        market = build_market(snap.me, snap.catalog, snap.events, snap.dealers, snap.scan)
        venue = best_venue(snap.venues, us, max((s.best_bid for s in self._seen.values()), default=1))
        counters, skipped = counter_bids.plan_counters(
            self._seen, market, snap.me, params, self.rules, venue, listed, public, tick
        )
        self._counter_said = {k: v for k, v in self._counter_said.items() if k in self._seen}
        for (team, ref), why in sorted(skipped.items()):
            if self._counter_said.get((team, ref)) == why:
                continue
            self._counter_said[(team, ref)] = why
            self.rec.decide(
                tick,
                "counter_bid",
                f"no counter to {team}'s bid for {ref}: {why}",
                inputs={"team": team, "ref": ref, "their_bid": self._seen[(team, ref)].best_bid, "why": why},
                reason=why,
                guardrail="-",
                chosen=False,
                status="rejected",
            )
        made = [
            Target("ask", c.ref, c.rarity, c.price, c.asset_id, c.loss, 1000.0, c.reason, (c.team,), to=c.team,
                   counter=True)
            for c in counters
        ]  # fmt: skip
        countered = {t.asset_id for t in made}
        return made + [t for t in targets if not (t.side == "ask" and t.asset_id in countered)]

    def _with_outreach(self, snap: Snapshot, targets: list[Target], params: StrategyParams) -> list[Target]:
        """Up to `outreach_bids.MAX_CARDS` cards we want get a bid addressed to a team the team matrix places a spare
        copy with, from START_SHARE of the ceiling up to it, holder after holder. The cards: our public bids, then
        every missing page card of a released set that some team holds spare (the strategy may route those to a
        dealer and bid nothing: Sunday 10:25, the maker had no bid at all). No matrix (or a stale one): nothing
        changes. Requests: none for the matrix; the official value of at most MAX_CARDS cards, cached CEILING_TICKS."""
        tick = snap.clock.tick
        m = self.latest_matrix.current(tick) if self.latest_matrix is not None else None
        if m is None:
            self._turns = {}
            return targets
        public = {t.ref: t for t in targets if t.side == "bid" and t.to is None and not t.counter}
        held = Counter(str(a.get("ref")) for a in snap.me.get("assets") or [] if a.get("kind") == "card")

        def holders(ref: str) -> list[str]:
            return [str(row.get("team")) for row in m.card(ref).get("spare") or [] if row.get("team")]

        market = build_market(snap.me, snap.catalog, snap.events, snap.dealers, snap.scan)
        wanted: dict[str, tuple[str, float]] = {ref: (t.rarity, t.score) for ref, t in public.items()}
        for card in market.cards.values():
            missing = card.page and card.set_code in market.released and market.held.get(card.ref, 0) == 0
            if missing and card.ref not in wanted:
                wanted[card.ref] = (card.rarity, buy_case(market, card, params).value)
        ranked = sorted((r for r in wanted if holders(r)), key=lambda r: (r not in public, -wanted[r][1], r))
        wants = []
        for ref in ranked[: outreach_bids.MAX_CARDS]:
            rarity, score = wanted[ref]
            top = self._outreach_ceiling(ref, rarity, public[ref].price if ref in public else None, held, tick, params)
            if top is not None and top > 0:
                wants.append(outreach_bids.Want(ref, rarity, top, score))
        made, self._turns = outreach_bids.plan_outreach(wants, holders, self._turns, self.rules, snap.us, tick)
        addressed = []
        for o in made:
            base = public.get(o.ref)
            if base is not None:
                addressed.append(
                    replace(base, price=o.price, reason=f"{o.reason}; {base.reason}", to=o.team, counter=True)
                )
            else:
                score = wanted[o.ref][1]
                addressed.append(Target("bid", o.ref, o.rarity, o.price, None, score, score, o.reason, (o.team,),
                                        to=o.team, counter=True))  # fmt: skip
        worked = {o.ref for o in made}
        return addressed + [t for t in targets if not (t.side == "bid" and t.to is None and t.ref in worked)]

    def _outreach_ceiling(
        self, ref: str, rarity: str, bid: int | None, held: Counter[str], tick: int, params: StrategyParams
    ) -> int | None:
        """The most an outreach bid offers: under the official value of one more copy by at least `min_buy_surplus`
        and its margin (a fill always gains us neg_points), the rarity cap, just under `human_approval_above` (no bid
        a human must approve first), and our public bid when there is one. The official value is read at most once
        per card every CEILING_TICKS (it moves with our album, not by the tick). Unread: our public bid, else None."""
        cached = self._ceilings.get(ref)
        if cached is None or tick - cached[0] >= CEILING_TICKS:
            cached = (tick, self.values.value(ref, tick, held.get(ref, 0)))
            self._ceilings[ref] = cached
        official, cap = cached[1], self.rules.max_price_for(rarity)
        if official is None or cap is None:  # a public bid keeps its own price (the post's cap refuses it unread)
            return bid
        top = min(cap, math.floor(official - max(self.rules.value_margin_for(rarity), params.min_buy_surplus) + 1e-9))
        if self.rules.human_approval_above > 0:
            top = min(top, self.rules.human_approval_above - 1)
        return top if bid is None else min(top, bid)

    # ------------------------------------------------------------ buy targets (`buy_targets.py`)

    def _with_buy_targets(self, snap: Snapshot, targets: list[Target], held: Counter[str]) -> list[Target]:
        """A bid for each buy target, first (a human ordered it), at its ladder price and for anyone: posted at that
        exact price (`final`: no Jev price, no hold). A target whose ceiling cannot be set (no official value read,
        nothing left under it) gets no bid this tick, so a standing one is cancelled."""
        tick = snap.clock.tick
        found = self.buy_targets.active(self.rules, tick, snap.catalog, held)
        if not found:
            return targets
        cards = {t.card for t in found}
        bids: list[Target] = []
        for bt in found:
            official = self.values.value(bt.card, tick, held.get(bt.card, 0))
            top = buy_targets.ceiling(bt, official, self.rules)
            if top is None or official is None:
                why = "official value unread" if official is None else "no ceiling under our value and the caps"
                if self._target_notes.get(bt.card) != why:
                    self._target_notes[bt.card] = why
                    self.log(f"tick {tick} maker: buy target {bt.card}: no bid ({why})")
                continue
            self._target_notes.pop(bt.card, None)
            price = buy_targets.ladder_price(bt, top, tick, self.rules)
            reason = f"{buy_targets.describe(bt, top, tick, self.rules)}; holders {self._holders(snap, bt.card)}"
            bids.append(Target("bid", bt.card, bt.rarity, price, None, official, 1e6, reason, final=True))
        return bids + [t for t in targets if not (t.side == "bid" and t.ref in cards)]

    def _holders(self, snap: Snapshot, card: str) -> str:
        """The teams the feed and the card scan place a copy with (for the decision row; the bid is public)."""
        from bazaar_agent.supply import supply_map

        tick = snap.clock.tick
        cached = self._holder_notes.get(card)
        if cached is not None and tick - cached[0] < HOLDERS_EVERY:  # the whole feed is walked: not every tick
            return cached[1]
        try:
            supply = supply_map(snap.catalog, snap.me, snap.events, snap.scan).cards.get(card)
        except Exception as e:  # noqa: BLE001 — a hint for the record: never costs the bid
            return f"unknown ({type(e).__name__})"
        found = ", ".join(f"{team}×{n}" for team, n in supply.holders) if supply and supply.holders else "unknown"
        self._holder_notes[card] = (tick, found)
        return found

    # ------------------------------------------------------------ asks relisted after a lapse (agents.relist)

    def _relisted(self, snap: Snapshot, targets: list[Target], mine: list[OpenOffer]) -> list[Target]:
        """Each ask target at its relist price: a copy whose last ask lapsed unsold steps down, never the same
        price twice in a row, and rests (no target, so nothing is posted) once it lapsed too often. Live only:
        the history is our live decision rows (Postgres, else JSONL), so a restart keeps it."""
        asks = [t for t in targets if t.side == "ask" and t.asset_id is not None]
        if not self.live or not asks:
            return targets
        clock = snap.clock
        trails = self._ask_trails(clock.tick)
        open_prices = {o.asset_id: o.price for o in mine if o.side == "ask" and o.asset_id is not None}
        values = {
            int(a["id"]): a.get("your_value") for a in snap.me.get("assets") or [] if isinstance(a.get("id"), int)
        }
        prints, rarities = tape(snap.events), card_rarities(snap.catalog)
        out = []
        for t in targets:
            if t.side != "ask" or t.asset_id is None:
                out.append(t)
                continue
            trail = trails.get(t.asset_id, AskTrail(t.asset_id))
            your_value = values.get(t.asset_id)
            cost = max(
                ask_floor(t.value, self.rules),
                sell_floor(float(your_value), self.rules) if isinstance(your_value, int | float) else 0,
                self.rules.exception_min(t.ref),  # protect_page_exceptions: never relisted below its MIN
            )
            venue = self._venue(snap, t)
            median = market_median(prints, venue.id, t.ref, t.rarity, rarities, clock.tick, snap.us) if venue else None
            r = relist_price(
                t.price,
                trail,
                open_price=open_prices.get(t.asset_id),
                cost_floor=cost,
                median=median,
                tick=clock.tick,
                step_share=self.rules.relist_step_share,
                min_share=self.rules.relist_min_price_share,
                max_lapses=self.rules.relist_max_lapses,
                cooldown_ticks=self.rules.relist_cooldown_ticks,
            )
            if r.rest_until is not None:
                self._rest(clock.tick, t, r)
            if r.price is None:
                continue
            if r.price != t.price or r.floor:
                t = replace(t, price=r.price, min_price=r.floor, reason=f"{t.reason}; relist: {r.why}")
            out.append(t)
        return out

    def _ask_trails(self, tick: int) -> dict[int, AskTrail]:
        prices: dict[int, list[int]] = {}
        since: dict[int, int] = {}
        rests: dict[int, int] = {}
        for _, kind, asset_id, value in self.rec.decisions.ask_rows("maker", tick - MEMORY_TICKS):
            if kind == RELIST_REST:
                rests[asset_id], since[asset_id] = value if value is not None else tick, 0
            elif value is not None:
                prices.setdefault(asset_id, []).append(value)
                since[asset_id] = since.get(asset_id, 0) + 1
        return {
            a: AskTrail(a, tuple(prices.get(a, ())), since.get(a, 0), rests.get(a)) for a in set(prices) | set(rests)
        }

    def _rest(self, tick: int, t: Target, r: Relist) -> None:
        self.rec.decide(
            tick,
            RELIST_REST,
            f"rest ask {t.ref} #{t.asset_id}: {r.why}",
            inputs={"asset_id": t.asset_id, "ref": t.ref, "until_tick": r.rest_until, "target": t.price},
            reason=r.why,
            guardrail="allowed",
            chosen=True,
            status="approved",
            move={"rest": t.asset_id, "until_tick": r.rest_until},
        )

    def _sell_hooks(self, cand: Candidate) -> SellHooks:
        fresh: Context | None = None

        def context() -> Context:
            assert self._run is not None
            return fresh if fresh is not None else self._ctx(self._run)

        catalog = self._run.snap.catalog if self._run is not None else {}
        hooks = standard_hooks(
            cand, rules=self.rules, rec=self.rec, ledger=self.ledger, context=context, catalog=catalog, log=self.log
        )
        original_guard = hooks.guard

        def guarded(kind: str, price: int) -> str | None:
            nonlocal fresh
            assert self._run is not None
            if not self.live:
                return original_guard(kind, price)
            clock = self._run.snap.clock
            with trade_lock(self.ledger):
                me = self.team.me()
                offers = publication.with_pending(
                    self.ledger, me, offers_in(self.team.my_offers()), self._run.snap.us, clock.tick, clock.t_hours
                )
                own = self._dealer_promises.get(cand.asset_id)
                offers = [o for o in offers if o.get("token") != own or own is None]
                commitments = open_commitments(offers, self._run.snap.us)
                if cand.asset_id in commitments.listed:
                    return "copy already promised by another writer"
                fresh = committed_context(
                    context_from(me, clock.tick, clock.t_hours, self.ledger, self.rules, self.values), commitments
                )
                denied = original_guard(kind, price)
                if denied is None and own is None:
                    self._dealer_promises[cand.asset_id] = publication.reserve(
                        self.ledger,
                        clock.tick,
                        clock.t_hours,
                        self._run.snap.us,
                        {"assets": [cand.asset_id]},
                        {"cash": price},
                    )
                return denied

        return replace(hooks, guard=guarded)

    def _ctx(self, run: _MakerRun) -> Context:
        """/me + the shared ledger + the bid cash this tick already committed (posted or would-be), and the
        kill switch as it is now (it may go on mid-tick)."""
        trades = None if run.settled is None else self._trades(run)
        return replace(
            run.base,
            spent_last_hour=run.base.spent_last_hour + run.spent,
            stops=kill_switch(self.rules),
            trades=trades,
        )

    def _do(self, run: _MakerRun, action: MakerAction) -> None:
        if action.kind == "cancel" and action.offer is not None:
            self._cancel(run, action.offer, action.why)
        elif action.kind == "reprice" and action.target is not None and action.offer is not None:
            self._reprice(run, action.offer, action.target, action.why)
        elif action.target is not None:
            self._post(run, action.target, action.why)

    def _reprice(self, run: _MakerRun, offer: OpenOffer, t: Target, why: str) -> None:
        """Cancel, then post at the target, but only when the post would pass: a denied repost would leave
        the card without an offer for a tick. Not repostable: an offer that cannot stand is cancelled anyway
        (a cancel needs no listing slot), any other keeps its price."""
        advice: JevAdvice | None = None
        cap = maker_post_attempts(run.snap.clock.tick_seconds, run.snap.clock.limits.offers_per_team_per_tick)
        if run.listings_left <= 0:
            refused: str | None = "no listing left"
        elif self.live and run.post_attempts >= cap:
            refused = "no publication request budget left"
        else:
            hold, advice = (False, None) if t.final or t.counter else self._jev_hold(run, offer, t)
            if hold:
                return
            refused = self._refusal(self._without(run, offer), t)
        if refused is None:
            if self._cancel(run, offer, why):
                offer_id = self._post(run, t, f"reprice: {why}")
                if self.jev is not None and offer_id is not None:
                    self.jev.watch.watch(offer_id, advice, REPRICE_QUESTION, self._expires(run))
            return
        if (problem := cannot_stand(offer, t, self.rules)) is not None:
            self._cancel(run, offer, f"{problem}; not repriced: {refused}")
            return
        self.log(f"tick {run.snap.clock.tick} maker: keep {offer.ref} at {offer.price}: {refused}")

    def _without(self, run: _MakerRun, offer: OpenOffer) -> _MakerRun:
        """The tick as it would be once `offer` is cancelled: its cash, slot and (a bid's) spend given back."""
        return replace(
            run,
            offers=[o for o in run.offers if o.get("id") != offer.id],
            open_total=run.open_total - 1,
            spent=run.spent - self._refunded(run, offer),
        )

    def _refund(self, run: _MakerRun, offer: OpenOffer) -> LedgerRow:
        return self._refund_at(offer, run.snap.clock)

    def _refund_at(self, offer: OpenOffer, clock: Clock) -> LedgerRow:
        """Fallback refund date; the ledger atomically recovers a persisted exact date when known."""
        if offer.id in self._spent_at:
            tick, t_hours = self._spent_at[offer.id]
            return ("spend", tick, t_hours, -offer.price, offer.ref)
        return refund_row(offer.price, offer.ref, offer.created_tick, clock.tick, clock.t_hours, clock.max_tick_seconds)

    def _refunded(self, run: _MakerRun, offer: OpenOffer) -> int:
        """The spend a cancel gives back inside this game hour's window: a bid's, unless it was spent
        before the window (its refund is booked in the hour it was spent)."""
        if offer.side != "bid":
            return 0
        return offer.price if self._refund(run, offer)[2] > run.base.t_hours - 1.0 else 0

    def _cancel(self, run: _MakerRun, offer: OpenOffer, why: str) -> bool:
        tick = run.snap.clock.tick
        verdict = check(Action("cancel", str(offer.id)), self._ctx(run), self.rules)  # only the kill switch applies
        status: Status = "rejected" if not verdict.allowed else "approved" if run.window.open() else "expired"
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
            f"cancel {offer.side} {offer.id} {offer.ref} at {offer.price} on {offer.venue}: {why} "
            f"· guardrails {verdict}",
            inputs=inputs,
            reason=why,
            guardrail=str(verdict),
            chosen=status == "approved",
            status=status,
            move={"cancel": offer.id},
        )
        if status != "approved":
            return False
        refunded = self._refunded(run, offer) if not self.live else 0
        if self.live:
            if self.rec.send(did, tick, "cancel", {"offer": offer.id}, lambda: self.team.cancel(offer.id)) is None:
                return False
            if self.jev is not None:
                self.jev.watch.cancelled(offer.id)
            if offer.side == "bid":  # a bid's cash was counted as spend when posted: give it back
                _, spent_tick, at, _, item = self._refund(run, offer)
                refunded_at = self.ledger.refund_bid(spent_tick, at, offer.price, item)
                if refunded_at is not None and refunded_at > run.base.t_hours - 1.0:
                    refunded = offer.price
        run.spent -= refunded  # `base` was read before the refund: later checks see it here
        self._forget(offer.id)  # after `_refunded`, which dates the refund as the ledger row above
        run.offers = [o for o in run.offers if o.get("id") != offer.id]
        run.open_total -= 1
        return True

    def _with_relocations(
        self, run: _MakerRun, actions: list[MakerAction], targets: list[Target], mine: list[OpenOffer]
    ) -> list[MakerAction]:
        """Move public asks toward better crossing demand or a configured zero-fee partner.

        ponytail: reuse cancel-confirm/repost; preference alone never rotates between partners.
        """
        busy = {a.offer.id for a in actions if a.offer is not None}
        addressed = {o.get("id") for o in run.offers if o.get("to")}
        asks = {t.asset_id: t for t in targets if t.side == "ask" and t.to is None}
        venues = run.snap.venues
        if self.notices is not None:
            venues = self.notices.adjust(venues, run.snap.clock.tick, self.config.offer_ttl_ticks)
        relocations = []
        for offer in mine:
            target = asks.get(offer.asset_id)
            if (
                offer.side != "ask"
                or target is None
                or offer.id in busy | addressed
                or offer.expires_tick is None
                or offer.expires_tick <= run.snap.clock.tick
            ):
                continue
            target = replace(target, price=offer.price, final=True)
            venue = self._venue(run.snap, target, venues, better_than=offer.venue)
            why = "crossing net demand"
            if venue is None and self._preferred_sell_owners:
                chosen = self._venue(run.snap, target, venues)
                current = next((v for v in venues if v.id == offer.venue), None)
                if (
                    chosen is not None
                    and chosen.id != offer.venue
                    and chosen.owner in self._preferred_sell_owners
                    and chosen.fee(offer.price) == 0
                    and current is not None
                    and current.owner not in self._preferred_sell_owners
                ):
                    venue, why = chosen, "configured zero-fee market preference"
            if venue is not None:
                why = f"{why}: move {offer.venue} → {venue.id} at unchanged {offer.price}"
                relocations.append(MakerAction("reprice", why, target, offer))
        return [a for a in actions if a.kind != "post"] + relocations + [a for a in actions if a.kind == "post"]

    def _venue(
        self, snap: Snapshot, target: Target, venues: list[Venue] | None = None, *, better_than: str | None = None
    ) -> Venue | None:
        """Use already-loaded feed bids as routing hints; no extra per-market requests.

        The taker still reads and revalidates the actual book before accepting. A stale
        hint can only choose where our independently guarded ask is posted.
        """
        eligible = snap.venues if venues is None else venues
        demand: dict[str, int] = {}
        if target.side == "ask" and target.to is None:
            if self._liquidity_tick != snap.clock.tick:
                self._liquidity_tick = snap.clock.tick
                try:
                    self._liquidity = rivals.listings(snap.events)
                except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
                    self._liquidity = []  # unreadable hints fall back to activity; no trading authority lost
            by_id = {v.id: v for v in eligible}
            unavailable = {o.get("id") for o in offers_in(snap.offers) if o.get("status") not in (None, "open")}
            for bid in self._liquidity:
                venue = by_id.get(bid.venue)
                if (
                    venue is not None
                    and bid.side == "bid"
                    and bid.ref == target.ref
                    and bid.maker not in (snap.us, venue.owner)
                    and bid.to in (None, snap.us)
                    and bid.expires_tick is not None
                    and bid.id not in unavailable
                    and bid.open_at(snap.clock.tick)
                ):
                    net = bid.price - venue.fee(bid.price)
                    demand[bid.venue] = max(demand.get(bid.venue, 0), net)
        if better_than is not None:
            eligible = [
                v
                for v in eligible
                if demand.get(v.id, 0) >= target.price and demand.get(v.id, 0) > demand.get(better_than, 0)
            ]
        return best_venue(
            eligible,
            snap.us,
            target.price,
            to=target.to,
            demand=demand,
            preferred_owners=self._preferred_sell_owners if target.side == "ask" else (),
            spread_key=target.asset_id or sum(map(ord, target.ref)),
            avoid=self._avoided_owners(snap.us),
            team_penalty=self.rules.venue_team_penalty,
        )

    def _listing(self, run: _MakerRun, t: Target, venue: str) -> Listing:
        if t.side == "ask" and t.asset_id is not None:
            listing = sell_listing(run.snap.me, str(t.asset_id), t.price, venue, to=t.to)
            if listing.your_value is not None and t.price < sell_floor(listing.your_value, self.rules):
                raise OfferError(f"ask {t.price} is below the sell floor {sell_floor(listing.your_value, self.rules)}")
            return listing
        return bid_listing(t.ref, t.rarity, t.price, venue, to=t.to)

    def _trades(self, run: _MakerRun) -> TradeBook:
        return trade_book(run.offers, run.snap.us, run.settled or {}, book_values(run.snap.catalog))

    def _route(self, run: _MakerRun, t: Target, venue: str) -> Target:
        """With `max_counterparty_share` on: the offer for anyone when no team could pass its share by taking
        it, else addressed to the counterparty with the most room that passes. Unchanged when the cap is off
        or nothing passes (the post is then refused with the public offer's reason)."""
        if run.settled is None or t.to is not None:
            return t
        denied = self._denied(run, t, venue)
        if denied is None or "counterparty" not in denied:
            return t
        trades = self._trades(run)
        for team in sorted(t.counterparties, key=lambda c: (trades.exposure(c), c)):
            if team != run.snap.us and self._denied(run, replace(t, to=team), venue) is None:
                return replace(t, to=team, reason=f"{t.reason}; addressed to {team} (max_counterparty_share)")
        return t

    def _post(self, run: _MakerRun, t: Target, why: str) -> int | None:
        if not self.live or run.listings_left <= 0:
            return self._post_locked(run, t, why)
        clock = run.snap.clock
        cap = maker_post_attempts(clock.tick_seconds, clock.limits.offers_per_team_per_tick)
        if run.post_attempts >= cap:
            self.log(
                f"tick {clock.tick} maker: publication request budget used ({cap} attempts); remaining offers wait"
            )
            return None
        run.post_attempts += 1
        with trade_lock(self.ledger):
            me = self.team.me()
            offers = publication.with_pending(
                self.ledger, me, offers_in(self.team.my_offers()), run.snap.us, clock.tick, clock.t_hours
            )
            run.snap = replace(run.snap, me=me, offers={"offers": offers})
            run.offers = offers
            run.base = committed_context(
                context_from(me, clock.tick, clock.t_hours, self.ledger, self.rules, self.values),
                unsettled_accepts(me, self.ledger, clock.tick),
            )
            run.spent = 0  # fresh ledger already includes earlier sends this tick
            run.open_total = sum(o.get("status") in (None, "open", "queued", "accepted") for o in offers)
            run.listings_left = min(
                run.listings_left,
                max(0, clock.limits.offers_per_team_per_tick - self.ledger.count_in_tick("listing", clock.tick)),
            )
            return self._post_locked(run, t, why)

    def _post_locked(self, run: _MakerRun, t: Target, why: str) -> int | None:
        """Post one offer; the new offer's id when it went out live, else None."""
        tick = run.snap.clock.tick
        venues = run.snap.venues
        if self.notices is not None:  # a fee announced for later in the listing's life counts now
            venues = self.notices.adjust(venues, tick, self.config.offer_ttl_ticks)
        venue = self._venue(run.snap, t, venues)
        blocked = self._blocked(run)
        if venue is not None and not blocked:
            t = self._route(run, t, venue.id)
        jev = venue is not None and not blocked and not t.final and not t.counter
        t, advice, candidates = self._jev_price(run, t, venue.id) if jev and venue else (t, None, None)
        if venue is not None and not blocked:
            t = self._address(run, t, venue.id)
            venue = self._venue(run.snap, t, venues)  # price/recipient may have changed; reselect before the guard
        inputs = {
            "side": t.side,
            "ref": t.ref,
            "rarity": t.rarity,
            "price": t.price,
            "asset_id": t.asset_id,
            "value": t.value,
            "score": t.score,
            "venue": venue.id if venue else None,
            **({"to": t.to} if t.to else {}),
            **({"price_candidates": candidates} if candidates else {}),
        }
        to = f" to {t.to}" if t.to else ""
        what = f"post {t.side} {t.ref}{f' #{t.asset_id}' if t.asset_id else ''} at {t.price}{to}"
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
            addressed = {"to": listing.to} if listing.to else {}
            request = {"give": listing.give, "want": listing.want, "venue": venue.id, **addressed}
            reservation = publication.reserve(
                self.ledger, tick, run.snap.clock.t_hours, run.snap.us, listing.give, listing.want, to=listing.to
            )
            body = self.rec.send(
                did,
                tick,
                "list_offer",
                request,
                lambda: self.team.list_offer(
                    listing.give,
                    listing.want,
                    venue=venue.id,
                    expires_in_ticks=self.config.offer_ttl_ticks,
                    **addressed,
                ),
            )
            if body is None and not self.rec.maybe_landed:
                if 400 <= self.rec.last_status < 500 and self.rec.last_status != 408:
                    publication.release(self.ledger, reservation, tick, run.snap.clock.t_hours)
                return None
            if body is not None and type(body.get("id")) is int:
                publication.confirm(self.ledger, reservation, body["id"], tick, run.snap.clock.t_hours)
            # Sent, or lost on the way back (a network error): a bid that may be open counts as spend.
            self.ledger.record("listing", tick, run.snap.clock.t_hours, t.price, t.ref)
            if t.side == "bid":
                self.ledger.record("spend", tick, run.snap.clock.t_hours, t.price, t.ref)
            offer_id = body.get("id") if body is not None and isinstance(body.get("id"), int) else None
            if t.side == "bid" and offer_id is not None:
                self._remember(run, offer_id, t, body.get("expires_tick") if body is not None else None)
            if t.side == "ask" and t.asset_id is not None and t.to is not None and "buyer rank" in t.reason:
                self._tried.setdefault(t.asset_id, set()).add((t.to, t.price))
                if offer_id is not None:
                    self._ranked[offer_id] = t.to
            applied = advice is not None and (candidates or {}).get(advice.verdict) == t.price
            if self.jev is not None and offer_id is not None and applied:  # judged only on the price it set
                self.jev.watch.watch(offer_id, advice, PRICE_QUESTION, self._expires(run))
        if t.side == "bid":
            run.spent += t.price
        give = listing.give  # our open offers this tick; an ask names its card (protect_page_sets counts it)
        if listing.asset_id is not None:
            give = {**give, "assets": [{"id": listing.asset_id, "ref": listing.ref}]}
        run.offers.append(
            {"id": -1, "status": "open", "maker": run.snap.us, "give": give, "want": listing.want, "to": listing.to}
        )
        run.open_total += 1
        run.listings_left -= 1
        run.posted.append(t.ref)
        return offer_id

    # ------------------------------------------------------------ who we sell to (`buyer_rank_enabled`)

    def _refresh_ranks(self, tick: int) -> None:
        """The leaderboard's ranks, read at most once per LEADERBOARD_EVERY ticks (it refreshes every few
        minutes). A failed read keeps the last ranks; with none, every ask stays public."""
        if self._ranks_tick is not None and tick - self._ranks_tick < LEADERBOARD_EVERY:
            return
        self._ranks_tick = tick
        try:
            self._ranks = buyer_rank.leaderboard_ranks(self.public.leaderboard())
        except Exception as e:  # a read we can do without: the asks stay public
            self.log(f"tick {tick} maker: leaderboard unreadable ({type(e).__name__}); asks stay public")

    def _avoided_owners(self, us: str) -> frozenset[str]:
        """Teams whose venue the maker never lists on (`venue_avoid_rivals`): a trade on a team's venue scores
        market points for its owner (RULES.md "Scoring"), so not for the podium nor the teams just above us, the
        rivals `buyers.is_rival` names for the buyer rank. Empty with the switch off or no ranks read yet."""
        if not self.rules.venue_avoid_rivals or not self._ranks:
            return frozenset()
        cfg = buyer_rank.BuyerConfig()
        ours = self._ranks.get(us)
        rivals = (team for team, rank in self._ranks.items() if team != us and buyer_rank.is_rival(rank, ours, cfg))
        return frozenset(rivals)

    def _address(self, run: _MakerRun, t: Target, venue: str) -> Target:
        """An ask the maker already decided to post, addressed to the best buyer when that passes every guardrail.
        Public when the rank is off, the ask is already addressed, the leaderboard is unknown, this copy was
        already addressed at this price (the fallback), or no non-rival buyer passes."""
        if not self.rules.buyer_rank_enabled or t.side != "ask" or t.to is not None or t.asset_id is None or t.final:
            return t
        if run.buyer_failed or not self._ranks or any(p == t.price for _, p in self._tried.get(t.asset_id, ())):
            return t
        try:  # feed strings are hostile: a ranking that fails keeps the ask public, never costs the tick
            if run.buyer_inputs is None:
                snap = run.snap
                run.buyer_inputs = buyer_rank.market_inputs(snap.me, snap.catalog, snap.events, snap.scan)
            holders, interest = run.buyer_inputs
            rows = buyer_rank.rank_buyers(
                t.ref,
                catalog=run.snap.catalog,
                events=run.snap.events,
                holders=holders,
                interest=interest,
                ranks=self._ranks,
                us=run.snap.us,
                our_value=t.value,
                price=t.price,
            )
            team = buyer_rank.pick(rows, price=t.price)
        except Exception as e:
            run.buyer_failed = True
            self.log(f"tick {run.snap.clock.tick} maker: buyer rank failed ({type(e).__name__}); ask stays public")
            return t
        if team is None or self._denied(run, replace(t, to=team), venue) is not None:
            return t
        best = next(r for r in rows if r.team == team)
        return replace(t, to=team, reason=f"{t.reason}; buyer rank: {team} ({best.why})")

    def _with_fallbacks(
        self, actions: list[MakerAction], targets: list[Target], mine: list[OpenOffer], tick: int
    ) -> list[MakerAction]:
        """An ask the buyer rank addressed, unfilled for `buyer_rank_fallback_ticks`, is reposted for anyone at
        the same price (a reprice: cancel, then post; `_address` keeps it public). Before the new posts."""
        busy = {a.offer.id for a in actions if a.offer is not None}
        asks = {t.asset_id: t for t in targets if t.side == "ask"}
        open_ids = {o.id for o in mine}
        self._ranked = {i: team for i, team in self._ranked.items() if i in open_ids}
        fallbacks = []
        for o in mine:
            t = asks.get(o.asset_id)
            old = o.created_tick is not None and tick - o.created_tick >= self.rules.buyer_rank_fallback_ticks
            if o.id in self._ranked and o.id not in busy and t is not None and old:
                why = f"addressed to {self._ranked[o.id]} for {tick - (o.created_tick or tick)} ticks unfilled: public"
                fallbacks.append(MakerAction("reprice", why, replace(t, price=o.price, final=True), o))
        posts = [a for a in actions if a.kind == "post"]
        return [a for a in actions if a.kind != "post"] + fallbacks + posts

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
        return self._denied(run, t, venue) is None

    def _denied(self, run: _MakerRun, t: Target, venue: str) -> str | None:
        """Why the listing for `t` on `venue` is refused (the sell floor, then every guardrail with our open
        offers counted); None when it would pass. Nothing is sent or recorded."""
        try:
            listing = self._listing(run, t, venue)
        except OfferError as e:
            return f"denied: {e}"
        commitments = open_commitments(run.offers, run.snap.us)
        verdict = post(self.team, listing, self._ctx(run), self.rules, live=False, commitments=commitments).verdict
        return None if verdict.allowed else str(verdict)

    def _refusal(self, run: _MakerRun, t: Target) -> str | None:
        """Why `_post` would refuse `t` in `run` at its target price (no venue, no slot, a guardrail)."""
        venue = self._venue(run.snap, t)
        if venue is None:
            return "no venue we may trade on"
        return self._blocked(run) or self._denied(run, self._route(run, t, venue.id), venue.id)

    def _teams(self, ref: str, tick: int) -> dict[str, Any]:
        """For one card: the teams that hold it spare or miss it for a page (top 5 each), from the team matrix the
        taker's sentinel stores (`team_matrix_store.LatestMatrix`); nothing until one was read, or when it is
        older than `MAX_AGE_TICKS`."""
        m = self.latest_matrix.current(tick) if self.latest_matrix is not None else None
        return {} if m is None else {"market_teams": {"tick": m.tick, "card": {ref: m.card(ref)}}}

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
            legal = {
                label: p
                for label, p in candidates.items()
                if p >= t.min_price and self._allowed(run, replace(t, price=p), venue)
            }
            state = listing_state(
                t,
                candidates,
                legal,
                {**self._jev_context(run), "venue": venue, **self._teams(t.ref, run.snap.clock.tick)},
            )
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
        state = reprice_state(offer, t, tick, {**self._jev_context(run), **self._teams(offer.ref, tick)})
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

    # ------------------------------------------------------------ bids that lapse unfilled (bite X15)

    def _remember(self, run: _MakerRun, offer_id: int, t: Target, expires_tick: Any = None) -> None:
        """`expires_tick` is the server's answer: it may grant less than we asked (Sat 3 Oct: 40 asked, 20 given),
        and a lapse is refunded only once its tick is reached, so the asked TTL is only the fallback."""
        clock = run.snap.clock
        asked = clock.tick + self.config.offer_ttl_ticks
        expires = expires_tick if isinstance(expires_tick, int) and clock.tick < expires_tick <= asked else asked
        offer = OpenOffer(offer_id, "bid", t.ref, t.price, "", None, expires, clock.tick)
        self._bids[offer_id] = _Bid(offer, _held(run.snap.me)[t.ref], clock.tick)
        self._spent_at[offer_id] = (clock.tick, clock.t_hours)

    def _forget(self, offer_id: int) -> None:
        self._bids.pop(offer_id, None)
        self._lapsing.pop(offer_id, None)
        self._spent_at.pop(offer_id, None)

    def _lapsed_bids(self, snap: Snapshot, mine: list[OpenOffer]) -> None:
        """Give back the spend of our board bids that lapsed unfilled. A bid gone from `/api/me/offers` before
        its `expires_tick` filled or was cancelled (whoever cancelled booked the refund): nothing to do. One
        gone at or after it, while the card did not come (`/api/me`) and no settlement of it shows in the
        feed, is checked once more on the next tick (an accept settles on the next tick) and then refunded,
        dated at its spend. A missed refund over-counts (today's behaviour); a wrong one could under-count,
        so every doubt keeps the spend."""
        if not self.live:
            return
        clock, held = snap.clock, _held(snap.me)
        paused = bool(kill_switch(self.rules))  # PAUSE + `bazaar flatten` cancels (and refunds) our bids
        # A cancel by another process shows only in the feed: without this tick's live window, no refund.
        feed_ok = bool(getattr(self.feed, "window_ok", True))
        present = {
            o.get("id") for o in offers_in(snap.offers) if o.get("status") in (None, "open", "queued", "accepted")
        }
        for oid, bid in list(self._lapsing.items()):
            if bid.seen_tick >= clock.tick:
                continue
            ref = bid.offer.ref
            del self._lapsing[oid]
            if oid in present:  # listed again (a read that missed it): alive, nothing to give back
                self._bids[oid] = bid
                continue
            if (
                held[ref] <= bid.held
                and not _settled_to_us(snap.events, ref, bid.offer.created_tick, snap.us)
                and not _cancelled(snap.events, oid)
                and not paused
                and feed_ok
            ):
                _, spent_tick, at, _, item = self._refund_at(bid.offer, clock)
                if self.ledger.refund_bid(spent_tick, at, bid.offer.price, item) is not None:
                    self.log(
                        f"tick {clock.tick} maker: bid {oid} for {ref} at {bid.offer.price} lapsed unfilled: refunded"
                    )
            self._spent_at.pop(oid, None)
        for oid, bid in self._bids.items():
            if oid in present or oid in self._lapsing:
                continue
            expires = bid.offer.expires_tick
            if isinstance(expires, int) and clock.tick >= expires and held[bid.offer.ref] <= bid.held:
                self._lapsing[oid] = replace(bid, seen_tick=clock.tick)
            else:
                self._spent_at.pop(oid, None)
        known = self._bids
        self._bids = {  # the server's view of each open bid; the copies held when it was first seen
            o.id: _Bid(o, known[o.id].held if o.id in known else held[o.ref], clock.tick)
            for o in mine
            if o.side == "bid"
        }


def _held(me: dict[str, Any]) -> Counter[str]:
    return Counter(str(a.get("ref")) for a in me.get("assets") or [] if a.get("kind") == "card")


def _settled_to_us(events: Iterable[dict[str, Any]], ref: str, since_tick: int | None, us: str) -> bool:
    """A settlement in the feed that gave us a copy of `ref` since `since_tick`: the bid may have filled."""
    for e in events:
        p = e.get("payload")
        if e.get("type") != "settlement" or not isinstance(p, dict) or not isinstance(p.get("items"), list):
            continue
        tick = e.get("tick")
        if since_tick is not None and isinstance(tick, int) and tick < since_tick:
            continue
        if any(i.get("to") == us and i.get("ref") == ref for i in p["items"] if isinstance(i, dict)):
            return True
    return False


def _cancelled(events: Iterable[dict[str, Any]], offer_id: int) -> bool:
    """A cancel of the offer in the feed: whoever cancelled it (`bazaar flatten`, the desk, the taker) booked its
    refund. The live server emits no `offer.cancelled` for an expiry (the simulator does, with reason
    "expired"), so a bid that lapsed has none."""
    for e in events:
        p = e.get("payload")
        if e.get("type") == "offer.cancelled" and isinstance(p, dict) and p.get("offer") == offer_id:
            return p.get("reason") != "expired"
    return False
