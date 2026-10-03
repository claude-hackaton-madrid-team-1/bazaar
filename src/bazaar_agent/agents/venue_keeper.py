"""VENUE KEEPER: our own market, run from the maker's tick loop on Railway (no new service, no wall clock).

Every maker tick (`Maker.on_tick`, driven by /api/clock), before the maker's own offers:
  1. Which venue do we run? /api/me `venue` and the public /api/venues (owner = us, not the house, not a
     starter stall, open or closing), and the one this process opened.
  2. None, GUARDRAILS.md plans one (`allow_venue_open`), and the game hour has reached
     `venue_open_after_game_hours`: open it ONCE, a `board` venue at 0 bps with a short neutral name,
     through `guardrails.check()` (cash stays at or above `cash_floor` after the 270, never a second
     venue, never before that game hour). The key vault must be able to hold the broker key in Postgres
     before the request goes out, and the key it returns is saved there (and to the data dir) at once. A
     refused opening costs nothing and is tried again `RETRY_TICKS` later; `venue_exists` stops it for good.
  3. We run a venue and hold its key: the broker (`agents/broker.py`) reads /api/broker/book and sends
     the maximum-surplus matches (bench first) inside the maker's tick window; BAZAAR_BENCH_POLICY=edge (unset:
     exact) has it match the Market Test with the bench edge (`agents/bench_edge.py`).
  4. With `announce_every_ticks` (the maker passes ANNOUNCE_EVERY_TICKS): a notice on our venue
     (`POST /api/broker/announce`) once the broker is on, then one every that many ticks and at most
     ANNOUNCE_MAX_PER_GAME_HOUR per game hour, through `guardrails.check()` (`venue_announce`:
     `allow_venue_open` and the kill switch). It names the cards the most other teams miss (`venue_notice.py`,
     from the team matrix the maker reads, rotating through the top WANTED_POOL) or, without a current matrix,
     says our fee and what the broker does. The server takes one notice per venue every 10 ticks and refuses the
     rest `wait`: the last notice
     the feed shows for our venue counts, so a restart never retries one, and a `wait` naming a tick is honoured.
Dry run (the maker's default) opens nothing and matches nothing: it writes what it would do.

The broker key is never logged, printed, published or put in a decision or an execution row: the opening
is recorded with the venue id and the places the key was saved, never the key.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from bazaar_agent.agents.broker import BrokerAgent, BrokerConfig, bench_config_from_env, bench_text
from bazaar_agent.agents.market import Venue, _fee
from bazaar_agent.agents.runtime import Recorder, Snapshot, TickWindow
from bazaar_agent.agents.seller import offers_in, open_commitments
from bazaar_agent.agents.venue_notice import WANTED_POOL, rotated, wanted_cards, wanted_notice
from bazaar_agent.config import ConfigError, Settings
from bazaar_agent.decisions import DecisionLog, Status
from bazaar_agent.guardrails import VENUE_COST, Action, Guardrails, check, runs_venue
from bazaar_agent.sdk import BazaarError
from bazaar_agent.team_matrix import TeamMatrix
from bazaar_agent.ticks import Clock
from bazaar_agent.venue import (
    AlreadyOpened,
    Announcement,
    KeyVault,
    Opened,
    VenueSpec,
    broker_client,
    may_have_landed,
    open_venue,
    venue_context,
)

RETRY_TICKS = 10  # after a refused or failed opening (a refusal costs nothing; a network error may have opened it)
REMIND_TICKS = 20  # how often a dry run, or a venue without its key, says so again
LIST_LAG_TICKS = 3  # ticks the public list and /me may take to show the venue we just opened
FINAL_REFUSALS = frozenset({"venue_exists", "not_allowed", "forbidden"})  # never tried again by this process
# The server takes one notice per venue every 10 ticks and refuses a sooner one `wait` (Saturday, every venue: 95 of
# 297 gaps exactly 10, none below), so we post at that cadence...
ANNOUNCE_EVERY_TICKS = 10
ANNOUNCE_MAX_PER_GAME_HOUR = 24  # ...and at most this many per game hour (15 s ticks: every 10; 7.5 s: every 20)
WAIT_HINT_MAX_TICKS = 120  # a `wait` refusal naming a later tick is honoured up to this far ahead
MATRIX_GRACE_TICKS = 3  # a process's first notice waits this long for the team matrix's first read
EXAMPLE_PRICE = 20  # the notice's fee example: 5 % of it is a whole number, so no rounding hides in it

# Our market: a board (only there can our broker act), no fee (fees never score; what counts is the gains
# realised on it), and a short neutral name and line.
PLAN = VenueSpec(
    name="Team 1 market",
    fee_bps=0,
    fee_per_card=0,
    mechanism="board",
    description="Board venue, 0 % fee: crossing offers are matched every tick.",
)


def _fee_text(fee_bps: int, fee_per_card: int) -> str:
    return f"{fee_bps / 100:g} %" + (f" + {fee_per_card} P/card" if fee_per_card else "")


def announcement(plan: VenueSpec, venue: str, house: Venue | None = None) -> Announcement:
    """The notice on our venue: its name, id and fee, what the same trade costs on the house market (from the
    live `/api/venues` row, the accepting side pays it: `agents/market.py`), and what our broker does (a
    crossing bid and ask are paired every tick at the midpoint, lowered only to fit a fee: `matcher.match_price`).
    Nothing about our cards, cash or values, and no promise beyond that."""
    ours = _fee_text(plan.fee_bps, plan.fee_per_card)
    text = f"{plan.name} ({venue}): {ours} fee."
    if house is not None:
        theirs = _fee(house.fee_bps, house.fee_per_card, EXAMPLE_PRICE, 1)
        here = _fee(plan.fee_bps, plan.fee_per_card, EXAMPLE_PRICE, 1)
        if theirs > here:
            where = "El Rastro" if house.id == "rastro" else "the house market"
            text += (
                f" A {EXAMPLE_PRICE} P sale on {where} costs the side that accepts {theirs} P"
                f" ({_fee_text(house.fee_bps, house.fee_per_card)}); here it costs {here}."
            )
    where_mid = "at the midpoint" if not (plan.fee_bps or plan.fee_per_card) else "near the midpoint"
    text += f" Asks and bids welcome: our broker pairs crossing bids and asks every tick, {where_mid}."
    return Announcement(text=text)


@dataclass(frozen=True)
class OurVenue:
    venue: str
    status: str  # "open" | "closing" | "?" (named by /api/me only)


def our_venue(snap: Snapshot) -> OurVenue | None:
    """The venue we run, from the public list (owner = us, not a starter stall) and /api/me `venue`."""
    mine = [
        v
        for v in snap.venues
        if v.owner == snap.us and not v.house and not v.starter and v.status in ("open", "closing")
    ]
    if mine:  # with an auto hedge venue open too, the board venue is the one our broker runs
        mine.sort(key=lambda v: v.mechanism != "board")
        return OurVenue(mine[0].id, mine[0].status)
    if not runs_venue(snap.me):
        return None
    named = snap.me.get("venue")
    vid = str(named.get("venue") or "") if isinstance(named, dict) else str(named)
    listed = next((v for v in snap.venues if v.id == vid), None)
    if listed is not None and (listed.starter or listed.owner != snap.us):
        return None  # /me names a starter stall (or someone else's venue): not one we opened
    return OurVenue(vid, "?")  # unknown to the public list: assume ours, so we never open a second one


class VenueKeeper:
    def __init__(
        self,
        team: Any,
        *,
        settings: Settings,
        rules: Guardrails,
        vault: KeyVault,
        decisions: DecisionLog,
        live: bool,
        log: Callable[[str], None],
        hub: Any = None,
        plan: VenueSpec = PLAN,
        broker_config: BrokerConfig | None = None,
        make_broker: Callable[[SecretStr], Any] | None = None,
        stats_dir: Any = None,
        announce_every_ticks: int | None = None,
        matrix: Callable[[int], TeamMatrix | None] | None = None,
    ) -> None:
        self.team, self.settings, self.rules, self.vault = team, settings, rules, vault
        self.decisions, self.live, self.log, self.hub, self.plan = decisions, live, log, hub, plan
        self.rec = Recorder("broker", decisions, live, log, hub)
        self.quiet_rec = Recorder("broker", decisions, live, log)  # rows the public status never shows
        # BAZAAR_BENCH_POLICY / BAZAAR_BENCH_GUARD_MARGIN (Railway, set by hand; default exact) pick how the broker
        # matches the Market Test; the edge says so at start (the venue runbooks look for this line)
        self.broker_config = bench_config_from_env(broker_config or BrokerConfig(pace_s=0.2), log=log)
        if self.broker_config.bench_policy == "edge":
            log(f"venue keeper: broker bench {bench_text(self.broker_config)}")
        self.make_broker = make_broker or (lambda key: broker_client(settings, key))
        self.stats_dir = stats_dir
        self.opened: Opened | None = None  # the venue this process opened, its key kept in memory too
        self.opened_tick = 0
        self.held_claim = False  # the opening claim is still ours after a network error
        self._marked: set[str] = set()  # venues we run without a key, already marked in the vault
        self._warned = -REMIND_TICKS
        self.retry_tick = 0
        self.final: str | None = None  # a refusal that ends our attempts (venue_exists, ...)
        self.reminded = -REMIND_TICKS
        self._last: tuple[str, str] | None = None  # the last unsent opening (status, guardrail), said once
        self._broker: tuple[str, BrokerAgent] | None = None
        self._client: Any = None  # the broker connection of `_broker`: the notice goes out through it
        self.announce_every = announce_every_ticks  # None: no notice from this process
        self.matrix = matrix  # the team matrix for this tick (`LatestMatrix.current`); None: the generic notice only
        self.announced_tick: int | None = None  # our last notice (sent, refused or would-be in a dry run)
        self.announced_at: float | None = None  # its game hour
        self.announce_after = 0  # a `wait` refusal's tick: nothing before it
        self._first_try: int | None = None  # the tick of this process's first try (the matrix grace)

    # ------------------------------------------------------------ the tick

    def on_tick(self, clock: Clock, snap: Snapshot | None, window: TickWindow) -> None:
        """Never raises: a bug or an outage here must not cost the maker its tick."""
        if not self.rules.allow_venue_open:
            return  # switched off: no opening, no key vault read or mark, no broker (every match is refused anyway)
        try:
            venue = self._venue(clock, snap)
            if venue is not None and snap is not None:
                self._check_reserve(clock, snap)
            if venue is None and snap is not None:
                venue = self._maybe_open(clock, snap, window)
            if venue is not None:
                self._broker_tick(venue, clock, snap, window)
        except Exception as e:  # the type only: a message could carry a URL or a parameter
            self.log(f"tick {clock.tick} venue: {type(e).__name__}; skipped this tick")

    def _venue(self, clock: Clock, snap: Snapshot | None) -> str | None:
        if snap is not None and (ours := our_venue(snap)) is not None:
            return ours.venue
        if self.opened is None:
            return None
        # No reads this tick, or the lists lag our own opening: keep brokering it. Gone from both for longer
        # (closed by hand): stop, and never open another from this process (`_maybe_open`).
        return self.opened.venue if snap is None or clock.tick - self.opened_tick <= LIST_LAG_TICKS else None

    def _check_reserve(self, clock: Clock, snap: Snapshot) -> None:
        """We run a venue but /me does not say so (it still carries `starter_broker_key`?): every writer keeps
        the 270 P bond reserve on top of the floor. Say so, so a human sets `venue_bond_reserve` = 0."""
        if runs_venue(snap.me) or not self.rules.allow_venue_open or not self.rules.venue_bond_reserve:
            return
        if self.opened is not None and clock.tick - self.opened_tick <= LIST_LAG_TICKS:
            return  # /me may lag our own opening by a tick or two
        if clock.tick - self._warned >= REMIND_TICKS:
            self._warned = clock.tick
            self.log(
                f"tick {clock.tick} venue: we run a venue but /api/me does not show it as ours, so every purchase "
                f"still keeps cash_floor + venue_bond_reserve: set venue_bond_reserve = 0 in GUARDRAILS.md"
            )

    # ------------------------------------------------------------ opening it, once

    def _maybe_open(self, clock: Clock, snap: Snapshot, window: TickWindow) -> str | None:
        rules = self.rules
        if not rules.allow_venue_open or clock.t_hours < rules.venue_open_after_game_hours:
            return None
        if self.opened is not None or self.final is not None or clock.tick < self.retry_tick or not window.open():
            return None
        if self.held_claim:  # our last try timed out and no venue showed up since: it did not open
            self.vault.release()
            self.held_claim = False
        clock_view = {"tick": clock.tick, "t_hours": clock.t_hours}
        # The bond is judged like a purchase: on the cash our open offers do not already promise.
        promised = open_commitments(offers_in(snap.offers), snap.us).cash
        me = {**snap.me, "cash": int(snap.me.get("cash") or 0) - promised}
        if runs_venue(me):  # `our_venue` found it is a starter stall (or not ours): say so to the guardrail
            me = {**me, "venue": {**(me["venue"] if isinstance(me["venue"], dict) else {}), "starter": True}}
        try:
            outcome, opened = open_venue(
                self.team, self.plan, rules, live=self.live, vault=self.vault, durable=True, me=me, clock=clock_view
            )
        except AlreadyOpened as e:  # a venue was opened on this target before (closed since?): a human decides
            self.final = "opened_before"
            self.log(f"tick {clock.tick} venue: {e}; not opening (open one by hand if we must)")
            return None
        except ConfigError as e:  # the vault cannot hold the key, or another process is opening: nothing sent
            self._quiet(clock, "rejected", f"denied: {e}", False)
            self.retry_tick = clock.tick + RETRY_TICKS
            return None
        except BazaarError as e:
            self._refused(clock, e)
            return None
        if not outcome.sent:  # refused by the guardrails, or a dry run: nothing went out
            allowed = outcome.verdict.allowed
            self._quiet(clock, "approved" if allowed else "rejected", str(outcome.verdict), allowed)
            return None
        if opened is not None:  # first, before any write that could fail: the key lives in memory at least
            self.opened, self.opened_tick = opened, clock.tick
        else:
            self.final = "no_key"
        did = self._record_open(clock, "approved", str(outcome.verdict), True)
        response = {k: v for k, v in (outcome.response or {}).items() if k != "broker_key"}
        venue = opened.venue if opened is not None else str(response.get("venue") or "")
        saved = opened.saved if opened is not None else ()
        self.rec.executed(did, clock.tick, "open_venue", self._request(), {**response, "saved": list(saved)}, None)
        if opened is None:
            self.log(f"tick {clock.tick} venue: opened {venue or '?'} but NO broker key came back: ask the desk")
            return venue or None
        where = " + ".join(saved) if saved else "NOWHERE: kept in this process only (a restart loses it)"
        self.log(f"tick {clock.tick} venue: OPENED {venue} ({self.plan.mechanism}, 0 bps); broker key saved to {where}")
        return venue

    def _quiet(self, clock: Clock, status: Status, guardrail: str, chosen: bool) -> None:
        """A move that sent nothing: one decision row when it changes, then one every REMIND_TICKS."""
        if (status, guardrail) == self._last and clock.tick - self.reminded < REMIND_TICKS:
            return
        self._last, self.reminded = (status, guardrail), clock.tick
        self._record_open(clock, status, guardrail, chosen, publish=False)

    def _request(self) -> dict[str, Any]:
        spec = self.plan
        return {"name": spec.name, "fee_bps": spec.fee_bps, "fee_per_card": spec.fee_per_card, "venue": "new"}

    def _record_open(self, clock: Clock, status: Status, guardrail: str, chosen: bool, publish: bool = True) -> int:
        spec = self.plan
        verb = "open" if chosen else "skip opening"
        line = (
            f"{verb} our venue {spec.name!r} ({spec.mechanism}, {spec.fee_bps} bps) for {VENUE_COST} P "
            f"at game hour {clock.t_hours:g}"
        )
        # An opening that sent nothing stays off the public status: it would tell rivals we plan one, or lack cash.
        return (self.rec if publish else self.quiet_rec).decide(
            clock.tick,
            "venue_open",
            line if chosen else f"{line}: {guardrail}",
            inputs={"name": spec.name, "mechanism": spec.mechanism, "fee_bps": spec.fee_bps, "t_hours": clock.t_hours},
            reason=f"allow_venue_open, venue_open_after_game_hours {self.rules.venue_open_after_game_hours}",
            guardrail=guardrail,
            chosen=chosen,
            status=status,
            move=self._request() if chosen else None,
        )

    def _refused(self, clock: Clock, e: BazaarError) -> None:
        # Kept off the public status: a published try with no venue after it says we were refused.
        did = self._record_open(clock, "approved", "allowed", True, publish=False)
        self.quiet_rec.executed(did, clock.tick, "open_venue", self._request(), None, e.code)
        self.decisions.settle(did, "failed")
        if e.code in FINAL_REFUSALS:
            self.final = e.code
            self.log(f"tick {clock.tick} venue: opening refused {e.code}; not trying again")
            return
        self.retry_tick = clock.tick + RETRY_TICKS
        self.held_claim = may_have_landed(e)  # it may have opened: the lists will say before the retry
        self.log(f"tick {clock.tick} venue: opening refused {e.code}; trying again at tick {self.retry_tick}")

    # ------------------------------------------------------------ the broker

    def _key(self, venue: str) -> SecretStr | None:
        if self.opened is not None and self.opened.venue == venue:
            return self.opened.key
        stored = self.vault.load(venue)
        return stored.key if stored is not None else None

    def _may_mark(self, venue: str, clock: Clock, snap: Snapshot | None) -> bool:
        """Only a venue the public list shows as ours (never one /me alone names: it may be the free stall),
        live, and from the opening hour on: a wrong mark would stop every future opening on this target."""
        if not self.live or snap is None or clock.t_hours < self.rules.venue_open_after_game_hours:
            return False
        return any(v.id == venue and v.owner == snap.us and not v.starter and not v.house for v in snap.venues)

    def _bench_books(self, venue: str) -> Any:
        """The Market Test book recorder of our broker: Postgres `bench_books` for the real game (its own short
        connection, off the tick), the JSONL alone on a simulator or without a DATABASE_URL."""
        from bazaar_agent import db
        from bazaar_agent.agents.bench_capture import BenchBooks
        from bazaar_agent.holdings import scope_of

        connect = (
            None if self.settings.simulator else (lambda: db.connect(app="bazaar-bench-books", connect_timeout_s=3))
        )
        return BenchBooks(connect, self.stats_dir, self.log, world=scope_of(self.settings).world, venue=venue)

    def _broker_tick(self, venue: str, clock: Clock, snap: Snapshot | None, window: TickWindow) -> None:
        if self._broker is None or self._broker[0] != venue:
            key = self._key(venue)
            if key is None:
                # it counts as opened from now on; a mark Postgres did not take is tried again next tick
                fresh = venue not in self._marked and self._may_mark(venue, clock, snap)
                if fresh and self.vault.mark(venue, clock.tick):
                    self._marked.add(venue)
                if clock.tick - self.reminded >= REMIND_TICKS:
                    self.reminded = clock.tick
                    self.log(f"tick {clock.tick} venue: we run {venue} but hold NO broker key for it: ask the desk")
                return
            self._client = self.make_broker(key)
            agent = BrokerAgent(
                self._client,
                None,
                us=snap.us if snap is not None else None,
                rules=self.rules,
                decisions=self.decisions,
                live=self.live,
                log=self.log,
                stats_dir=self.stats_dir,
                config=self.broker_config,
                hub=self.hub,
                books=self._bench_books(venue),
            )
            self._broker = (venue, agent)
            self.log(
                f"tick {clock.tick} venue: broker on for {venue} ({'LIVE' if self.live else 'dry run'}), "
                f"bench {bench_text(self.broker_config)}"
            )
        agent = self._broker[1]
        if snap is not None:
            agent.us = snap.us
        agent.on_tick(
            clock,
            window=window,
            our_offers=snap.offers if snap is not None else None,
            events=snap.events if snap is not None else None,
        )
        self._maybe_announce(venue, clock, window, snap)

    # ------------------------------------------------------------ the notice on our venue

    def _maybe_announce(self, venue: str, clock: Clock, window: TickWindow, snap: Snapshot | None) -> None:
        """Once the broker is on, then every `announce_every` ticks (at most ANNOUNCE_MAX_PER_GAME_HOUR per game
        hour); only inside the tick window and only when the guardrails allow `venue_announce` (a refusal is tried
        again next tick, quietly)."""
        if (
            self.announce_every is None
            or self._client is None
            or not window.open()
            or not self._due(venue, clock, snap)
        ):
            return
        us, held = (snap.us, _held(snap.me)) if snap is not None else ("", set())
        pool = wanted_cards(self.matrix(clock.tick), us, WANTED_POOL, held) if self.matrix is not None else []
        cards = rotated(pool, clock.tick // self.announce_every)
        if self._first_try is None:
            self._first_try = clock.tick
        if not cards and self.matrix is not None and clock.tick - self._first_try < MATRIX_GRACE_TICKS:
            return  # the maker's first matrix read lands a tick or two after a start
        house = next((v for v in snap.venues if v.house), None) if snap is not None else None
        note = wanted_notice(self.plan, venue, cards, house) or announcement(self.plan, venue, house)
        verdict = check(
            Action("venue_announce"),
            venue_context(self.rules, {"tick": clock.tick, "t_hours": clock.t_hours}),
            self.rules,
        )
        if not verdict.allowed:
            return
        # sent or not, the next one waits its turn: never a notice per tick
        self.announced_tick, self.announced_at = clock.tick, clock.t_hours
        did = self.rec.decide(
            clock.tick,
            "venue_announce",
            f"announce on our venue {venue}: {note.text!r}",
            inputs={"venue": venue, "text": note.text, "cards": cards},
            reason=f"our venue's notice, one every {self.announce_every} ticks"
            + (": the cards the most other teams miss (team matrix)" if cards else ""),
            guardrail=str(verdict),
            chosen=True,
            status="approved",
            move={"announce": venue},
        )
        if not self.live:
            return
        client, request = self._client, {"text": note.text}
        if self.rec.send(did, clock.tick, "broker_announce", request, lambda: client.announce(note.text)) is not None:
            self.log(f"tick {clock.tick} venue: announced {venue}" + (f" ({', '.join(cards)})" if cards else ""))
        elif self.rec.last_code == "wait":
            self.announce_after = _wait_tick(self.rec.last_error, clock.tick)

    def _due(self, venue: str, clock: Clock, snap: Snapshot | None) -> bool:
        """Every `announce_every` ticks after our last notice, the one this process sent or the newest the feed
        shows for our venue (a restart, or a notice from a laptop), and never sooner than a game hour allows."""
        if self.announced_tick is not None and clock.tick < self.announced_tick:  # a simulator reset: start over
            self.announced_tick, self.announced_at, self.announce_after, self._first_try = None, None, 0, None
        if clock.tick < self.announce_after or self.announce_every is None:
            return False
        feed = _last_notice(venue, snap.events if snap is not None else (), clock.tick)
        last = max(-1 if self.announced_tick is None else self.announced_tick, feed)
        if last >= 0 and clock.tick - last < self.announce_every:
            return False
        if last > (-1 if self.announced_tick is None else self.announced_tick):  # only the feed knows it: its hour
            self.announced_tick = last
            self.announced_at = clock.t_hours - (clock.tick - last) * clock.tick_seconds / 3600
        hour_gap = 1 / ANNOUNCE_MAX_PER_GAME_HOUR - 1e-9
        return self.announced_at is None or clock.t_hours - self.announced_at >= hour_gap


def _held(me: dict[str, Any]) -> set[str]:
    """The cards we hold a copy of (/api/me): the only ones our notice may name."""
    return {str(a.get("ref")) for a in me.get("assets") or [] if isinstance(a, dict) and a.get("kind") == "card"}


def _last_notice(venue: str, events: Any, now: int) -> int:
    """The tick of the newest `venue.announcement` for our venue in the feed (newest last) up to `now`, -1 when
    none: an event from a tick the game has not reached (another world's row) is skipped, never a reason to wait."""
    for event in reversed(events or ()):
        if not isinstance(event, dict) or event.get("type") != "venue.announcement":
            continue
        payload, tick = event.get("payload"), event.get("tick")
        ours = isinstance(payload, dict) and payload.get("venue") == venue
        if ours and isinstance(tick, int) and not isinstance(tick, bool) and 0 <= tick <= now:
            return tick
    return -1


def _wait_tick(refused: Any, tick: int) -> int:
    """The tick a `wait` refusal names (`next_tick`, `until_tick` or `retry_tick` in its extra), within
    WAIT_HINT_MAX_TICKS; else 0, and the usual cadence decides."""
    extra = getattr(refused, "extra", None)
    if not isinstance(extra, dict):
        return 0
    for key in ("next_tick", "until_tick", "retry_tick"):
        value = extra.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and tick < value <= tick + WAIT_HINT_MAX_TICKS:
            return value
    return 0
