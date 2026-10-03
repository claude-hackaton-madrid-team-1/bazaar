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
     the maximum-surplus matches (bench first) inside the maker's tick window.
Dry run (the maker's default) opens nothing and matches nothing: it writes what it would do.

The broker key is never logged, printed, published or put in a decision or an execution row: the opening
is recorded with the venue id and the places the key was saved, never the key.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from bazaar_agent.agents.broker import BrokerAgent, BrokerConfig
from bazaar_agent.agents.runtime import Recorder, Snapshot, TickWindow
from bazaar_agent.config import ConfigError, Settings
from bazaar_agent.decisions import DecisionLog, Status
from bazaar_agent.guardrails import VENUE_COST, Guardrails, runs_venue
from bazaar_agent.sdk import BazaarError
from bazaar_agent.ticks import Clock
from bazaar_agent.venue import KeyVault, Opened, VenueSpec, broker_client, open_venue

RETRY_TICKS = 10  # after a refused or failed opening (a refusal costs nothing; a network error may have opened it)
REMIND_TICKS = 20  # how often a dry run, or a venue without its key, says so again
LIST_LAG_TICKS = 3  # ticks the public list and /me may take to show the venue we just opened
FINAL_REFUSALS = frozenset({"venue_exists", "not_allowed", "forbidden"})  # never tried again by this process

# Our market: a board (only there can our broker act), no fee (fees never score; what counts is the gains
# realised on it), and a short neutral name and line.
PLAN = VenueSpec(
    name="Team 1 market",
    fee_bps=0,
    fee_per_card=0,
    mechanism="board",
    description="Board venue, 0 % fee: crossing offers are matched every tick.",
)


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
    if mine:
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
    ) -> None:
        self.team, self.settings, self.rules, self.vault = team, settings, rules, vault
        self.decisions, self.live, self.log, self.hub, self.plan = decisions, live, log, hub, plan
        self.rec = Recorder("broker", decisions, live, log, hub)
        self.broker_config = broker_config or BrokerConfig(pace_s=0.2)
        self.make_broker = make_broker or (lambda key: broker_client(settings, key))
        self.stats_dir = stats_dir
        self.opened: Opened | None = None  # the venue this process opened, its key kept in memory too
        self.opened_tick = 0
        self.retry_tick = 0
        self.final: str | None = None  # a refusal that ends our attempts (venue_exists, ...)
        self.reminded = -REMIND_TICKS
        self._last: tuple[str, str] | None = None  # the last unsent opening (status, guardrail), said once
        self._broker: tuple[str, BrokerAgent] | None = None

    # ------------------------------------------------------------ the tick

    def on_tick(self, clock: Clock, snap: Snapshot | None, window: TickWindow) -> None:
        """Never raises: a bug or an outage here must not cost the maker its tick."""
        try:
            venue = self._venue(clock, snap)
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

    # ------------------------------------------------------------ opening it, once

    def _maybe_open(self, clock: Clock, snap: Snapshot, window: TickWindow) -> str | None:
        rules = self.rules
        if not rules.allow_venue_open or clock.t_hours < rules.venue_open_after_game_hours:
            return None
        if self.opened is not None or self.final is not None or clock.tick < self.retry_tick or not window.open():
            return None
        clock_view = {"tick": clock.tick, "t_hours": clock.t_hours}
        me = snap.me
        if runs_venue(me):  # `our_venue` found it is a starter stall (or not ours): say so to the guardrail
            me = {**me, "venue": {**(me["venue"] if isinstance(me["venue"], dict) else {}), "starter": True}}
        try:
            outcome, opened = open_venue(
                self.team, self.plan, rules, live=self.live, vault=self.vault, durable=True, me=me, clock=clock_view
            )
        except ConfigError as e:  # the vault cannot hold the key: nothing was sent
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
        did = self._record_open(clock, "approved", str(outcome.verdict), True)
        response = {k: v for k, v in (outcome.response or {}).items() if k != "broker_key"}
        venue = opened.venue if opened is not None else str(response.get("venue") or "")
        saved = opened.saved if opened is not None else ()
        self.rec.executed(did, clock.tick, "open_venue", self._request(), {**response, "saved": list(saved)}, None)
        if opened is None:
            self.log(f"tick {clock.tick} venue: opened {venue or '?'} but NO broker key came back: ask the desk")
            self.final = "no_key"
            return venue or None
        self.opened, self.opened_tick = opened, clock.tick
        where = " + ".join(saved) if saved else "NOWHERE: kept in this process only (a restart loses it)"
        self.log(f"tick {clock.tick} venue: OPENED {venue} ({self.plan.mechanism}, 0 bps); broker key saved to {where}")
        return venue

    def _quiet(self, clock: Clock, status: Status, guardrail: str, chosen: bool) -> None:
        """A move that sent nothing: one decision row when it changes, then one every REMIND_TICKS."""
        if (status, guardrail) == self._last and clock.tick - self.reminded < REMIND_TICKS:
            return
        self._last, self.reminded = (status, guardrail), clock.tick
        self._record_open(clock, status, guardrail, chosen)

    def _request(self) -> dict[str, Any]:
        spec = self.plan
        return {"name": spec.name, "fee_bps": spec.fee_bps, "fee_per_card": spec.fee_per_card, "venue": "new"}

    def _record_open(self, clock: Clock, status: Status, guardrail: str, chosen: bool) -> int:
        spec = self.plan
        verb = "open" if chosen else "skip opening"
        line = (
            f"{verb} our venue {spec.name!r} ({spec.mechanism}, {spec.fee_bps} bps) for {VENUE_COST} P "
            f"at game hour {clock.t_hours:g}"
        )
        return self.rec.decide(
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
        did = self._record_open(clock, "approved", "allowed", True)
        self.rec.executed(did, clock.tick, "open_venue", self._request(), None, e.code)
        self.decisions.settle(did, "failed")
        if e.code in FINAL_REFUSALS:
            self.final = e.code
            self.log(f"tick {clock.tick} venue: opening refused {e.code}; not trying again")
            return
        self.retry_tick = clock.tick + RETRY_TICKS
        self.log(f"tick {clock.tick} venue: opening refused {e.code}; trying again at tick {self.retry_tick}")

    # ------------------------------------------------------------ the broker

    def _key(self, venue: str) -> SecretStr | None:
        if self.opened is not None and self.opened.venue == venue:
            return self.opened.key
        stored = self.vault.load(venue)
        return stored.key if stored is not None else None

    def _broker_tick(self, venue: str, clock: Clock, snap: Snapshot | None, window: TickWindow) -> None:
        if self._broker is None or self._broker[0] != venue:
            key = self._key(venue)
            if key is None:
                if clock.tick - self.reminded >= REMIND_TICKS:
                    self.reminded = clock.tick
                    self.log(f"tick {clock.tick} venue: we run {venue} but hold NO broker key for it: ask the desk")
                return
            agent = BrokerAgent(
                self.make_broker(key),
                None,
                us=snap.us if snap is not None else None,
                rules=self.rules,
                decisions=self.decisions,
                live=self.live,
                log=self.log,
                stats_dir=self.stats_dir,
                config=self.broker_config,
                hub=self.hub,
            )
            self._broker = (venue, agent)
            self.log(f"tick {clock.tick} venue: broker on for {venue} ({'LIVE' if self.live else 'dry run'})")
        agent = self._broker[1]
        if snap is not None:
            agent.us = snap.us
        agent.on_tick(
            clock,
            window=window,
            our_offers=snap.offers if snap is not None else None,
            events=snap.events if snap is not None else None,
        )
