"""Scenarios: a calibrated replay of a real game day, opt-in with `SIM_SCENARIO=sunday`.

With the variable unset nothing here runs: the simulator behaves exactly as before. The `sunday` scenario is the
organisers' Sunday (15 s ticks) driven by `data/sunday.json`, which `scripts/sim_calibrate.py` writes from the real
Friday+Saturday data (shared Postgres, the keyless API). It adds, on top of the plain world:

- the schedule as tick-driven events (Market Tests, Round 3 with the Chamberí release and a ladder restart, the
  Sunday allowance, Duels III, the finale), at `(at_hours - open_hours) * 3600 / game_tick_seconds` ticks;
- Radio Rastro news at the observed cadence (the headlines of Saturday's feed) and `/api/news`;
- all five dealers, with the measured openings, floors, patience and final behaviour (`dealers.calibrated`);
- dealer fevers (Pilar's Salamanca +25 %, Abuela's uncommons);
- 16 rival teams fitted to Saturday's feed (`rival_params`);
- a per-request latency (lognormal around the measured median) and the real rate limits.

`SIM_TICK_SECONDS` sets the real pace; the game clock keeps adding 15 s per tick (`game_tick_seconds`), so a replay at
2 s ticks is the same Sunday in 48 minutes. Events fire on tick numbers, never on wall-clock time.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, replace
from functools import cache
from typing import TYPE_CHECKING, Any

from bazaar_sim import bench, catalog, dealers

if TYPE_CHECKING:
    from bazaar_sim.world import SimConfig, World

NEVER = 10**9  # a periodic start that never comes: the scenario starts Market Tests and duels itself
SUNDAY_FIRST_SESSION_GAP = 17  # catch-up Market Tests that were due before the doors open run one after another
FEVER_SIZE_ABUELA = 1.15  # ASSUMPTION: the feed shows Abuela's uncommon fever, not its size (persona.updated has none)
# ASSUMED fevers for Sunday: the real data has Saturday's only. They replay it in Sunday's frame so the agents meet one.
ASSUMED_FEVERS: tuple[dict[str, Any], ...] = (
    {"dealer": "pilar", "set": "SAL", "rarity": None, "mult": 1.25, "from_tick": 240, "until_tick": 720},
    {
        "dealer": "abuela",
        "set": None,
        "rarity": "uncommon",
        "mult": FEVER_SIZE_ABUELA,
        "from_tick": 360,
        "until_tick": 600,
    },
)
STOCK_CARDS = 18  # extra cards a rival holds when Sunday opens (the feed's ~32 assets a team, minus the 15 dealt)
STOCK_ODDS = {"common": 0.7, "uncommon": 0.22, "rare": 0.08}
KNOWN_ACTIONS = (
    "bench",
    "set_release",
    "round",
    "grant_all",
    "duels",
    "announce",
    "persona",
    "end_round",
    "day_opens",
    "day_closes",
)


@dataclass(frozen=True)
class ScenarioEvent:
    tick: int
    action: str
    note: str
    params: dict[str, Any]
    at_hours: float


@cache
def load(name: str) -> Scenario:
    path = catalog.DATA / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"unknown scenario {name!r}: no {path.name} in {catalog.DATA}")
    return Scenario.from_data(name, json.loads(path.read_text(encoding="utf-8")))


class Scenario:
    def __init__(self, name: str, data: dict[str, Any]) -> None:
        self.name = name
        self.data = data
        clock = data["clock"]
        self.open_hours = float(clock["open_hours"])
        self.game_tick_seconds = float(clock["game_tick_seconds"])
        self.sunday_ticks = int(clock["sunday_ticks"])
        self.dealer_ids = tuple(data["dealers"])
        self.initially_released = ("RET",)  # El Retiro came out on Saturday; Chamberí is the scenario's own release
        self.events = self._events(data["schedule"]["upcoming"])
        self._by_tick: dict[int, list[ScenarioEvent]] = {}
        for e in self.events:
            self._by_tick.setdefault(e.tick, []).append(e)
        self.news_items: list[dict[str, Any]] = [dict(i) for i in data["news"]["items"]]
        self.news_every = max(1, round(float(data["news"]["cadence_hours"]) * 3600 / self.game_tick_seconds))

    @classmethod
    def from_data(cls, name: str, data: dict[str, Any]) -> Scenario:
        for key in ("clock", "schedule", "news", "dealers", "rivals"):
            if key not in data:
                raise ValueError(f"scenario data lacks {key!r}")
        return cls(name, data)

    # ------------------------------------------------------------------ schedule

    def _events(self, upcoming: list[dict[str, Any]]) -> tuple[ScenarioEvent, ...]:
        """The organisers' entries as tick events. Entries dated before the doors open (the hard Market Test h14.65,
        the Market Test h15.0 are Saturday leftovers) run at the start, one session after another."""
        out: list[ScenarioEvent] = []
        catch_up = 0
        for e in upcoming:
            action = str(e["action"])
            if action not in KNOWN_ACTIONS:
                continue
            tick = int(e["ticks_from_open"])
            if e.get("before_open") or tick < 1:
                if action == "bench" and e.get("before_open"):
                    tick = 2 + catch_up * SUNDAY_FIRST_SESSION_GAP
                    catch_up += 1
                else:
                    tick = 1
            out.append(
                ScenarioEvent(tick, action, str(e.get("note", "")), dict(e.get("params") or {}), float(e["at_hours"]))
            )
        return tuple(sorted(out, key=lambda x: x.tick))

    def schedule_view(self, w: World) -> dict[str, Any]:
        upcoming = [
            {"at_hours": e.at_hours, "action": e.action, "note": e.note, "params": e.params}
            for e in self.events
            if e.tick > w.tick
        ]
        return {"now_hours": round(self.open_hours + w.t_hours, 3), "upcoming": upcoming}

    # ------------------------------------------------------------------ dealers

    def style(self, base: dealers.Style) -> dealers.Style:
        measured = self.data["dealers"].get(base.dealer)
        return dealers.calibrated(base, measured) if isinstance(measured, dict) else base

    def fever_mult(self, w: World, dealer_id: str, set_code: str, rarity: str) -> float:
        mult = 1.0
        for f in ASSUMED_FEVERS:
            if f["dealer"] != dealer_id or not int(f["from_tick"]) <= w.tick < int(f["until_tick"]):
                continue
            if f["set"] not in (None, set_code) or f["rarity"] not in (None, rarity):
                continue
            mult = max(mult, float(f["mult"]))
        return mult

    # ------------------------------------------------------------------ rivals

    def rival_params(self) -> dict[str, Any]:
        """What the synthetic rivals take from the measured Saturday feed (see `rivals.on_tick`)."""
        r = self.data["rivals"]
        teams = max(1, int(r.get("n_teams") or 16))
        life = (r.get("listing_lifetime_ticks") or {}).get("p50") or 20
        per_tick = float((r.get("listings_per_tick") or {}).get("mean") or 5.8)
        settle = r.get("settlements_per_tick") or {}
        trades = float(settle.get("rastro") or 0.07) + float(settle.get("team_venues") or 0.03)
        per_team = per_tick / teams
        return {
            "list_prob": min(0.9, per_team),
            "max_listings": max(3, round(per_team * float(life))),
            "listing_ticks": int(life),
            "cancel_prob": float(r.get("cancel_rate") or 0.38) / float(life),
            "take_prob": min(1.0, trades / teams),
            "ask_band": _bands(r.get("ask_over_book") or {}),
            "bid_band": _bands(r.get("bid_over_book") or {}),
            "partners": _partners(r.get("reciprocal_pairs") or []),
        }

    def give_saturday_stock(self, w: World, team_id: str, rng: random.Random) -> None:
        """A team that played Saturday holds far more than the starting hand: the feed has ~32 assets a team and
        listings relisted every ~20 ticks, which only works with stock. Mints `STOCK_CARDS` more, by rarity odds."""
        pools = {
            rarity: [
                c.ref for c in catalog.cards().values() if c.rarity == rarity and c.set_code in catalog.released_sets()
            ]
            for rarity in STOCK_ODDS
        }
        for _ in range(STOCK_CARDS):
            rarity = rng.choices(list(STOCK_ODDS), weights=list(STOCK_ODDS.values()))[0]
            ref = rng.choice(pools[rarity])
            if w.mintable(ref):
                w.mint(ref, team_id, "saturday stock")

    # ------------------------------------------------------------------ the tick

    def on_tick(self, w: World) -> None:
        self._news(w)
        for e in self._by_tick.get(w.tick, []):
            self._fire(w, e)

    def _news(self, w: World) -> None:
        if not self.news_items or w.tick % self.news_every != 0:
            return
        item = self.news_items[(w.tick // self.news_every - 1) % len(self.news_items)]
        entry = {
            "id": len(w.state.news) + 1,
            "at_hours": w.t_hours,
            "tick": w.tick,
            "source": item["source"],
            "source_name": item["source_name"],
            "headline": item["headline"],
            "body": item.get("body", ""),
        }
        w.state.news.append(entry)
        del w.state.news[:-50]
        w.emit(
            "news.posted",
            {
                "id": entry["id"],
                "body": entry["body"],
                "text": f"{entry['source_name']}: {entry['headline']}",
                "source": entry["source"],
                "headline": entry["headline"],
                "source_name": entry["source_name"],
            },
            actor="news",
        )

    def _fire(self, w: World, e: ScenarioEvent) -> None:
        from bazaar_sim import broker, duels

        if e.action == "bench":
            hard = "hard" in e.note.lower() or int(e.params.get("traders", 10)) >= 12
            base = bench.HARD if hard else bench.NORMAL
            broker.start_bench(w, base.with_ticks(int(e.params.get("ticks", 16))))
        elif e.action == "set_release":
            code = str(e.params["set"])
            if code not in w.state.released:
                w.state.released.append(code)
                catalog.configure(extra_released=tuple(w.state.released), scenario_dealers=True)
                w.emit(
                    "set.released",
                    {"set": code, "name": catalog.set_name(code), "cards": len(catalog.page_cards(code))},
                )
        elif e.action == "round":
            w.state.round = w.state.round + 1 if w.state.round else 3
            for team in w.state.teams.values():
                team.round_start_deal = len(team.deals)  # the dealer ladder restarts
            w.emit(
                "round.started",
                {
                    "name": str(e.params.get("name", "")),
                    "reset": True,
                    "round": w.state.round,
                    "weight": e.params.get("weight", 1),
                },
            )
        elif e.action == "grant_all":
            cash = int(e.params.get("cash", 0))
            for team in w.state.teams.values():
                team.cash += cash
            w.emit("announcement", {"text": e.note}, actor="calendar")
        elif e.action == "duels":
            issues = e.params.get("issues") or ["price"]
            duels.start_session(
                w,
                two_issues=len(issues) > 1,
                decay_per_round=float(e.params.get("decay", 0.1)),
                duel_ticks=int(e.params.get("duel_ticks", 12)),
                name=str(e.params.get("name", "")) or None,
            )
        elif e.action == "persona" and not e.params.get("enabled", True):
            dealer = str(e.params["id"])
            if dealer not in w.state.disabled_dealers:
                w.state.disabled_dealers.append(dealer)
                w.emit(
                    "persona.updated",
                    {
                        "name": catalog.raw_dealers().get(dealer, {}).get("name", dealer),
                        "persona": dealer,
                        "version": 1,
                    },
                )
        elif e.action == "announce":
            w.emit("announcement", {"text": e.note}, actor="calendar")
        elif e.action == "end_round":
            w.emit("round.ended", {"round": w.state.round or 3})
        w.emit("schedule.fired", {"note": e.note, "action": e.action})


def _bands(raw: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Per rarity, the p25-p75 of a price over book (the middle half of what rivals really posted)."""
    out: dict[str, tuple[float, float]] = {}
    for rarity, q in raw.items():
        if isinstance(q, dict) and q.get("p25") is not None and q.get("p75") is not None:
            lo, hi = float(q["p25"]), float(q["p75"])
            out[str(rarity)] = (min(lo, hi), max(lo, hi))
    return out


def _partners(pairs: list[dict[str, Any]]) -> dict[tuple[int, int], int]:
    """Reciprocal trading pairs by rival index: the real team ids t02..t18 (t01 is us) map onto our rivals in order."""
    out: dict[tuple[int, int], int] = {}
    for p in pairs:
        a, b = _index(str(p["a"])), _index(str(p["b"]))
        if a is not None and b is not None:
            out[(a, b)] = out[(b, a)] = int(p.get("trades", 1))
    return out


def _index(team: str) -> int | None:
    digits = team.lstrip("t")
    return int(digits) - 2 if digits.isdigit() and int(digits) >= 2 else None


def configure(config: SimConfig, name: str) -> SimConfig:
    """The `SimConfig` of a scenario run: env still wins where the plain simulator reads it."""
    s = load(name)
    pace = float(os.environ.get("SIM_TICK_SECONDS") or s.data["clock"]["tick_seconds"])
    latency = s.data["clock"]["latency_ms"]
    pace_seconds = max(0.2, min(60.0, pace))
    # latency compresses with the pace: 40 ms of a 15 s tick is 5 ms of a 2 s tick (agent compute does not compress)
    scale = float(os.environ.get("SIM_LATENCY_SCALE") or 1.0) * min(1.0, pace_seconds / s.game_tick_seconds)
    return replace(
        config,
        scenario=name,
        tick_seconds=max(0.2, min(60.0, pace)),
        game_tick_seconds=s.game_tick_seconds,
        player_teams=int(os.environ.get("SIM_PLAYER_TEAMS") or 2),
        rivals=max(0, min(16, int(os.environ.get("SIM_RIVALS") or s.data["rivals"].get("n_teams") or 16))),
        chato_open_ticks=0,
        pilar_open_ticks=0,
        duel_first_tick=NEVER,
        bench_first_tick=NEVER,
        limits={**config.limits, **{k: int(v) for k, v in s.data["clock"]["limits"].items()}},
        latency_p50_ms=float(latency["p50"]) * scale,
        latency_p95_ms=float(latency["p95"]) * scale,
        venue_live_ticks=0,
    )


# ---------------------------------------------------------------- the Workshop (El Taller)


def craft(w: World, team_id: str, raw_ids: Any) -> dict[str, Any]:
    """`POST /api/taller {"assets": [a, b, c]}`: three copies of one rarity become one random card of the next
    rarity up (a released set's). The team keeps at least one copy of each card it gives. Scenario worlds only."""
    from bazaar_sim import market, validate
    from bazaar_sim.errors import SimError, invalid
    from bazaar_sim.scoring import asset_view

    if w.scenario is None:
        raise SimError("not_found", "route /api/taller", 404)
    ids = validate.int_list(raw_ids, "assets")
    if len(ids) != 3 or len(set(ids)) != 3:
        raise invalid("the Workshop takes three different assets")
    locked = market.locked_assets(w)
    assets = [w.asset(i) for i in ids]
    for a in assets:
        if a.owner != team_id:
            raise SimError("not_owner", f"asset {a.id} is not yours", 403)
        if a.kind != "card":
            raise invalid(f"asset {a.id} is a pack, not a card")
        if a.id in locked:
            raise SimError("asset_locked", f"asset {a.id} is promised in an accepted offer", 400)
    rarities = {catalog.cards()[a.ref].rarity for a in assets}
    if len(rarities) != 1:
        raise invalid("the three copies must share one rarity")
    rarity = next(iter(rarities))
    up = _next_rarity_up(rarity)
    if up is None:
        raise invalid(f"nothing is above {rarity}")
    held = w.held_counts(team_id)
    for ref, n in {ref: sum(1 for a in assets if a.ref == ref) for ref in {a.ref for a in assets}}.items():
        if held.get(ref, 0) - n < 1:
            raise SimError("keep_one", f"keep at least one copy of {ref}", 400)
    released = catalog.released_sets()
    rng = w.rng("taller", team_id, w.state.counters.get("asset", 0))
    pool = [c.ref for c in catalog.cards().values() if c.rarity == up and c.set_code in released and w.mintable(c.ref)]
    if not pool:
        raise SimError("sold_out", f"every {up} is out of print", 400)
    for a in assets:
        a.owner = "crafted"
        a.history.append({"from": team_id, "to": "crafted", "tick": w.tick, "why": "workshop"})
    made = w.mint(rng.choice(pool), team_id, "workshop")
    team = w.team(team_id)
    w.emit("taller.crafted", {"team": team_id, "name": team.name, "from": rarity, "to": up, "card": made.ref})
    return {"ok": True, "card": asset_view(w, team, dict(w.held_counts(team_id)), made), "from": rarity, "to": up}


def _next_rarity_up(rarity: str) -> str | None:
    order = catalog.RARITY_ORDER
    i = order.index(rarity)
    return order[i + 1] if i + 1 < len(order) else None
