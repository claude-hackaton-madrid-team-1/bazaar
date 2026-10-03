"""The simulated world: one `WorldState`, one lock, and the clock that drives everything.

Rule-bearing actions live in focused modules that take a `World`: `threads.py` (dealer and team
conversations), `market.py` (offers, settlement, venues, brokers, the bench), `duels.py`,
`rivals.py` (synthetic teams), `scoring.py`. `views.py` renders the API shapes.

Ticks: `advance()` runs one heartbeat in a fixed order. Anything accepted during tick N settles at
the start of tick N+1, all at once or not at all (RULES.md: "structure binds").
"""

from __future__ import annotations

import hashlib
import os
import random
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bazaar_sim import catalog
from bazaar_sim.errors import SimError, not_found, wait_for_tick
from bazaar_sim.models import Asset, ClockState, Event, Team, Venue, WorldState

MAX_EVENTS = 2000  # kept in the snapshot; the public feed serves the last 500
FEED_CAP = 500
STREAM_ONLY_KEEP = 200
PRUNE_AFTER_TICKS = 600  # finished threads and offers older than this leave the snapshot
LIMITS = {
    "accepts_per_team_per_tick": 1,
    "max_open_offers_per_team": 30,
    "max_open_threads_per_team": 6,
    "messages_per_side_per_tick": 1,
    "offers_per_team_per_tick": 12,
    "messages_per_team_thread": 200,  # RULES.md: a conversation between two teams ends in a deal or after 200 messages
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class SimConfig:
    tick_seconds: float = 10.0
    seed: int = 7
    player_teams: int = 8
    rivals: int = 6
    chato_open_ticks: int = 360  # El Chato opens to everyone after this many ticks (earlier: 3 deals)
    pilar_open_ticks: int = 720  # Doña Pilar opens to everyone after this many ticks (earlier: 3 Chato deals)
    duel_first_tick: int = 6
    duel_every_ticks: int = 90
    duel_ticks: int = 12
    bench_first_tick: int = 30
    bench_every_ticks: int = 120
    bench_ticks: int = 16
    idle_ticks: int = 40
    venue_live_ticks: int = 0
    rivals_enabled: bool = True
    rival_inbound_threads: bool = False  # one rival opens a silent team thread to each player (slot pressure)
    limits: dict[str, int] = field(default_factory=lambda: dict(LIMITS))

    @classmethod
    def from_env(cls) -> SimConfig:
        return cls(
            tick_seconds=max(0.2, min(60.0, _env_float("SIM_TICK_SECONDS", 10.0))),
            seed=_env_int("SIM_SEED", 7),
            player_teams=max(1, min(12, _env_int("SIM_PLAYER_TEAMS", 8))),
            rivals=max(0, min(8, _env_int("SIM_RIVALS", 6))),
            chato_open_ticks=_env_int("SIM_CHATO_OPEN_TICKS", 360),
            pilar_open_ticks=_env_int("SIM_PILAR_OPEN_TICKS", 720),
            duel_first_tick=_env_int("SIM_DUEL_FIRST_TICK", 6),
            duel_every_ticks=max(5, _env_int("SIM_DUEL_EVERY_TICKS", 90)),
            duel_ticks=max(3, _env_int("SIM_DUEL_TICKS", 12)),
            bench_first_tick=_env_int("SIM_BENCH_FIRST_TICK", 30),
            bench_every_ticks=max(5, _env_int("SIM_BENCH_EVERY_TICKS", 120)),
            bench_ticks=max(2, _env_int("SIM_BENCH_TICKS", 16)),
            idle_ticks=max(5, _env_int("SIM_IDLE_TICKS", 40)),
            venue_live_ticks=max(0, _env_int("SIM_VENUE_LIVE_TICKS", 0)),
            rival_inbound_threads=_env_int("SIM_RIVAL_INBOUND_THREADS", 0) == 1,
        )


def player_team_id(n: int) -> str:
    return f"t{n:02d}"


def rival_team_id(i: int) -> str:
    return f"t{11 + i:02d}"


def key_for(n: int) -> str:
    """The team key of player team `n` (documented in the README; not a secret, it is a simulator)."""
    return f"sim-team{n}"


def key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class World:
    """The whole simulated game. Every public method takes the lock: HTTP threads and the tick task share it."""

    def __init__(self, state: WorldState, config: SimConfig, now: Callable[[], float] = time.time) -> None:
        self.state = state
        self.config = config
        self.now = now
        self.lock = threading.RLock()
        self.stream_only: list[Event] = []  # `tick` events: on the live stream, never on /api/feed
        self.keys = {key_digest(key_for(n)): player_team_id(n) for n in range(1, config.player_teams + 1)}

    # ---------------------------------------------------------------- creation

    @classmethod
    def create(cls, config: SimConfig, now: Callable[[], float] = time.time) -> World:
        state = WorldState(seed=config.seed)
        state.clock = ClockState(tick_seconds=config.tick_seconds, tick_started_at=now(), booted_at=now())
        state.chato_open_tick = config.chato_open_ticks
        state.pilar_open_tick = config.pilar_open_ticks
        world = cls(state, config, now)
        world._found()
        return world

    def _found(self) -> None:
        self.state.venues["rastro"] = Venue(
            venue="rastro",
            name="El Rastro",
            owner="world",
            owner_name="The house",
            fee_bps=500,
            fee_per_card=1,
            house=True,
            description="The house market. Posted offers only; no broker.",
        )
        self.emit("day.opened", {"day": "sim", "name": "Simulator", "tick_seconds": self.config.tick_seconds})
        teams = [(player_team_id(n), f"Team {n}", False) for n in range(1, self.config.player_teams + 1)]
        teams += [(rival_team_id(i), f"Team {11 + i}", True) for i in range(self.config.rivals)]
        for team_id, name, bot in teams:
            self._join(team_id, name, bot)
        self.emit(
            "announcement",
            {"text": f"The simulated Bazaar is open: one tick every {self.config.tick_seconds:g} s."},
            actor="calendar",
        )

    def _join(self, team_id: str, name: str, bot: bool) -> None:
        rng = self.rng("join", team_id)
        values = list(catalog.AFFINITY_VALUES)
        rng.shuffle(values)
        team = Team(id=team_id, name=name, bot=bot, affinity=dict(zip(catalog.set_codes(), values, strict=True)))
        self.state.teams[team_id] = team
        hand = [("common", 11), ("uncommon", 3), ("rare", 1)]
        released = catalog.released_sets()
        for rarity, n in hand:
            pool = [c.ref for c in catalog.cards().values() if c.rarity == rarity and c.set_code in released]
            for _ in range(n):
                self.mint(rng.choice(pool), team_id, "starting grant")
        self.emit("team.joined", {"team": team_id, "name": name, "level": 1})

    # ---------------------------------------------------------------- ids, randomness, events

    def next_id(self, kind: str) -> int:
        value = self.state.counters.get(kind, 0) + 1
        self.state.counters[kind] = value
        return value

    def rng(self, purpose: str, *keys: object) -> random.Random:
        """Deterministic per (seed, tick, purpose, keys): no RNG state to persist, same world every replay."""
        seed = f"{self.state.seed}:{self.state.clock.tick}:{purpose}:" + ":".join(map(str, keys))
        return random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())

    @property
    def tick(self) -> int:
        return self.state.clock.tick

    @property
    def t_hours(self) -> float:
        return round(self.state.clock.t_seconds / 3600.0, 4)

    @property
    def hour(self) -> int:
        """The game hour a quota counts in."""
        return int(self.state.clock.t_seconds // 3600)

    def open_to_all_tick(self, dealer_id: str) -> int | None:
        """The tick a gated dealer opens to every team (None: always open, or never scheduled)."""
        if dealer_id == "chato":
            return self.state.chato_open_tick
        if dealer_id == "pilar":
            tick = self.state.pilar_open_tick
            return self.config.pilar_open_ticks if tick is None else tick
        return None

    def open_to_all(self, dealer_id: str) -> bool:
        at = self.open_to_all_tick(dealer_id)
        return at is not None and self.tick >= at

    def emit(self, kind: str, payload: dict[str, Any], *, actor: str = "", scope: str = "public") -> Event:
        event = Event(
            id=self.next_id("event"),
            tick=self.tick,
            t=self.t_hours,
            type=kind,
            scope=scope,
            actor=actor,
            payload=payload,
        )
        self.state.events.append(event)
        if len(self.state.events) > MAX_EVENTS:
            del self.state.events[: len(self.state.events) - MAX_EVENTS]
        return event

    def emit_stream_only(self, kind: str, payload: dict[str, Any]) -> None:
        event = Event(id=self.next_id("event"), tick=self.tick, t=self.t_hours, type=kind, payload=payload)
        self.stream_only.append(event)
        del self.stream_only[:-STREAM_ONLY_KEEP]

    # ---------------------------------------------------------------- teams and assets

    def team_for_key(self, key: str) -> str | None:
        return self.keys.get(key_digest(key))

    def team(self, team_id: str) -> Team:
        team = self.state.teams.get(team_id)
        if team is None:
            raise not_found(f"team {team_id}")
        return team

    def asset(self, asset_id: int) -> Asset:
        asset = self.state.assets.get(asset_id)
        if asset is None:
            raise SimError("unknown_asset", f"asset {asset_id} does not exist", 404)
        return asset

    def holdings(self, owner: str) -> list[Asset]:
        return [a for a in self.state.assets.values() if a.owner == owner]

    def held_counts(self, owner: str) -> Counter[str]:
        return Counter(a.ref for a in self.state.assets.values() if a.owner == owner and a.kind == "card")

    def mint(self, ref: str, owner: str, why: str) -> Asset:
        """A new numbered copy (`#serial/print_run`). Packs have no print run; cards stop at theirs."""
        serial = self.state.minted.get(ref, 0) + 1
        card = catalog.card(ref)
        if card is not None and serial > card.print_run:
            raise SimError("sold_out", f"{ref} is out of print ({card.print_run} copies)", 400)
        self.state.minted[ref] = serial
        asset = Asset(
            id=self.next_id("asset"),
            kind="card" if card is not None else "pack",
            ref=ref,
            serial=serial,
            owner=owner,
            history=[{"from": "world", "to": owner, "tick": self.tick, "why": why}],
        )
        self.state.assets[asset.id] = asset
        return asset

    def mintable(self, ref: str) -> bool:
        card = catalog.card(ref)
        return card is None or self.state.minted.get(ref, 0) < card.print_run

    def transfer(self, asset: Asset, to: str, why: str) -> None:
        asset.history.append({"from": asset.owner, "to": to, "tick": self.tick, "why": why})
        asset.owner = to

    # ---------------------------------------------------------------- per-tick caps

    def limit(self, name: str) -> int:
        return int(self.config.limits.get(name, LIMITS[name]))

    def use(self, team_id: str, what: str, cap: int, message: str) -> None:
        """Count one use of a per-tick allowance; the one past the cap is a 429 with `next_tick`."""
        key = f"{team_id}:{what}:{self.tick}"
        used = self.state.usage.get(key, 0)
        if used >= cap:
            raise wait_for_tick(message, self.tick + 1)
        self.state.usage[key] = used + 1

    def used(self, team_id: str, what: str) -> int:
        return self.state.usage.get(f"{team_id}:{what}:{self.tick}", 0)

    # ---------------------------------------------------------------- clock

    def next_tick_in(self) -> float:
        c = self.state.clock
        if c.paused:
            return c.tick_seconds
        return round(max(0.0, c.tick_started_at + c.tick_seconds - self.now()), 2)

    def due(self) -> bool:
        c = self.state.clock
        return not c.paused and self.now() >= c.tick_started_at + c.tick_seconds

    def resume_after_restart(self) -> None:
        """The doors were shut while the process was down: the clock restarts now, it never replays ticks."""
        self.state.clock.tick_started_at = self.now()
        self.state.clock.tick_seconds = self.config.tick_seconds

    def advance(self) -> None:
        """One heartbeat, in a fixed order (see the module docstring)."""
        from bazaar_sim import duels, market, rivals, scoring, threads

        with self.lock:
            c = self.state.clock
            c.tick += 1
            c.t_seconds += c.tick_seconds
            c.tick_started_at = self.now()
            self.emit_stream_only("tick", {"tick": c.tick, "tick_seconds": c.tick_seconds})
            market.settle_due(self)
            market.expire(self)
            threads.dealer_turn(self)
            threads.housekeeping(self)
            duels.on_tick(self)
            market.bench_tick(self)
            market.venue_tick(self)
            if self.config.rivals_enabled:
                rivals.on_tick(self)
            threads.levels_tick(self)
            scoring.snapshot_if_due(self)
            self._prune()

    def advance_if_due(self) -> bool:
        with self.lock:
            if not self.due():
                return False
            self.advance()
            return True

    def _prune(self) -> None:
        keep = self.tick - 2
        self.state.usage = {k: v for k, v in self.state.usage.items() if int(k.rsplit(":", 1)[1]) >= keep}
        old = self.tick - PRUNE_AFTER_TICKS
        stale = [t.id for t in self.state.threads.values() if t.status != "open" and t.last_activity_tick < old]
        for tid in stale:
            del self.state.threads[tid]
        live_threads = set(self.state.threads)
        for o in list(self.state.offers.values()):
            done = o.status not in ("open", "accepted") and o.created_tick < old
            if done and (o.thread is None or o.thread not in live_threads):
                del self.state.offers[o.id]
