"""Duels: scheduled 1v1 sessions against an aliased rival, in the real payload shape (2026-10-02, ticks 120-146).

Each session gives every player team two duels on one item, once as seller (`your_limit` is a cost)
and once as buyer (a value). You see only your limit. The rival bot has its own, concedes toward it
over the session's clock, and accepts an offer that beats where it would be next. A deal outside
your limit loses points, no deal scores zero, and the deal's value shrinks with every round of
talk: `rounds` is the fewer priced messages of the two sides, and a deal keeps (1 - decay) ** rounds of
its value, as in the real practice payloads. Even-numbered sessions negotiate price and delivery day
(`your_days_weight`, P per day); a priced message there without `days` is `missing_days`. Session 1 is
practice and does not score.

`SIM_DUEL_STYLES` (comma list of `duel_zoo.STYLES` and the exploiters `squeezer`, `oracle_squeezer`; default `sim`)
gives each duel a rival drawn from the zoo, fixed per duel; `sim` is the bot below. `SIM_DUEL_DECAY` sets the decay
per round (default 0.06). `SIM_DUEL_PAIRS` (1-6, default 1) gives each team that many seller/buyer pairs per
session, all on one deadline (6 concurrent duels at 3).
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
from typing import Any

from bazaar_sim import catalog, duel_exploit, duel_zoo, validate
from bazaar_sim.errors import SimError, invalid, not_found, wait_for_tick
from bazaar_sim.models import Duel, DuelOffer
from bazaar_sim.world import World

RIVALS = ("Rival Azul", "Rival Verde", "Rival Oro", "Rival Rojo", "Rival Noche", "Rival Plata")
DECAY = 0.06
OUTSIDE_LIMIT_POINTS = -5.0
POINTS_PER_PIE = 10.0
RIVAL_START, RIVAL_END = 0.9, 0.35  # share of the pie the rival asks for at the start and at the deadline
ENDGAME_TICKS = 2
LIMIT_MEANING = {"seller": "never sell below your cost", "buyer": "never pay above your value"}
DAYS_MEANING = "primas you gain (+) or lose (-) per delivery day, 0-10"
OPENERS = (
    "Hello, and thank you for meeting me. I would propose {p} P for this one.",
    "Fair is fair: {p} P, and we can close now.",
)
COUNTERS = (
    "Thanks for the offer. I can do {p} P.",
    "That's a real step from me: {p} P.",
    "I appreciate it. Let us try to close quickly: {p} P.",
)
ACCEPTS = ("Deal at {p} P. Pleasure doing business.",)
STYLES_ENV, DECAY_ENV, PAIRS_ENV = "SIM_DUEL_STYLES", "SIM_DUEL_DECAY", "SIM_DUEL_PAIRS"
EXPLOITERS = ("squeezer", "oracle_squeezer")  # duel_exploit's live-playable rivals (the mirror needs a pair memory)
LIVE_STYLES = (*duel_zoo.STYLES, *EXPLOITERS)
LIVE_RIVALS = {**duel_zoo.RIVALS, **{s: duel_exploit.RIVALS[s] for s in EXPLOITERS}}
log = logging.getLogger(__name__)
_warned: set[str] = set()


def _warn_once(message: str, exc_info: bool = False) -> None:
    """Settings are read every tick: say what is wrong once, not once per duel per tick."""
    if message not in _warned:
        _warned.add(message)
        log.warning(message, exc_info=exc_info)


def styles() -> tuple[str, ...]:
    """The rival styles duels draw from (`SIM_DUEL_STYLES`). Unset means `sim`; a bad value also means `sim`, with
    a warning: this runs inside the tick, and an exception there would leave the tick half done."""
    raw = [s.strip() for s in os.environ.get(STYLES_ENV, "").split(",") if s.strip()]
    unknown = [s for s in raw if s not in LIVE_STYLES]
    if unknown:
        _warn_once(f"{STYLES_ENV}: unknown rival style {unknown} (one of {LIVE_STYLES}): using sim")
        return ("sim",)
    return tuple(raw) or ("sim",)


def pairs() -> int:
    """Seller/buyer duel pairs per team per session (`SIM_DUEL_PAIRS`, 1-6, default 1: two duels as before)."""
    raw = os.environ.get(PAIRS_ENV)
    try:
        value = int(raw) if raw not in (None, "") else 1
    except ValueError:
        value = 0
    if not 1 <= value <= 6:
        _warn_once(f"{PAIRS_ENV}={raw!r} is not 1-6: using 1")
        return 1
    return value


def decay() -> float:
    """The decay per round (`SIM_DUEL_DECAY`, default 0.06); a value outside [0, 1) falls back to the default."""
    raw = os.environ.get(DECAY_ENV)
    try:
        value = float(raw) if raw not in (None, "") else DECAY
    except ValueError:
        value = -1.0
    if not 0 <= value < 1:
        _warn_once(f"{DECAY_ENV}={raw!r} is not in [0, 1): using {DECAY}")
        return DECAY
    return value


def _style_rng(w: World, duel: Duel) -> random.Random:
    """Fixed per duel across ticks (`World.rng` changes every tick)."""
    seed = f"{w.state.seed}:duel_style:{duel.duel}"
    return random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())


def rival_style(w: World, duel: Duel) -> tuple[str, dict[str, float]]:
    """The duel's rival style and its parameters, drawn per duel id from `SIM_DUEL_STYLES`. The draw is a pure
    function of (seed, duel id, pool), recomputed each tick (no model field): change the pool between sessions,
    not during one, or live duels switch rivals."""
    pool = styles()
    if pool == ("sim",):
        return "sim", {}
    rng = _style_rng(w, duel)
    style = rng.choice(pool)
    if style in EXPLOITERS:
        return style, duel_exploit.exploit_params(style, rng)
    return style, duel_zoo.style_params(style, rng)


def on_tick(w: World) -> None:
    cfg = w.config
    if w.tick >= cfg.duel_first_tick and (w.tick - cfg.duel_first_tick) % cfg.duel_every_ticks == 0:
        start_session(w)
    for duel in list(w.state.duels.values()):
        if duel.status == "live":
            _tick_duel(w, duel)
    finished = sorted(d.duel for d in w.state.duels.values() if d.status != "live")
    for did in finished[:-400]:
        del w.state.duels[did]


def start_session(
    w: World,
    *,
    two_issues: bool | None = None,
    decay_per_round: float | None = None,
    duel_ticks: int | None = None,
    name: str | None = None,
) -> int:
    """Start a duel session. A scenario passes its own issues, decay and clock; the plain simulator passes none."""
    w.state.duel_session += 1
    session = w.state.duel_session
    per_round = decay() if decay_per_round is None else decay_per_round
    two_issues = session % 2 == 0 if two_issues is None else two_issues
    length = w.config.duel_ticks if duel_ticks is None else duel_ticks
    issues = ["price", "days"] if two_issues else ["price"]
    rng = w.rng("duels", session)
    players = [t for t in w.state.teams.values() if not t.bot]
    created = 0
    for team, _ in ((t, i) for t in players for i in range(pairs())):  # each pair: one scenario, both roles
        item = rng.choice([c.name for c in catalog.cards().values()])
        cost = rng.randint(30, 110)
        value = cost + rng.randint(20, 80)
        for role in ("seller", "buyer"):
            ours, theirs = (cost, value) if role == "seller" else (value, cost)
            weights = (round(rng.uniform(-4, 4), 1), round(rng.uniform(-4, 4), 1)) if two_issues else (None, None)
            duel = Duel(
                duel=w.next_id("duel"),
                session=session,
                team=team.id,
                role=role,  # type: ignore[arg-type]
                item=item,
                issues=issues,
                your_limit=ours,
                rival=rng.choice(RIVALS),
                rival_limit=theirs,
                your_days_weight=weights[0],
                rival_days_weight=weights[1],
                deadline_tick=w.tick + length,
                decay_per_round=per_round,
                started_tick=w.tick,
                practice=session == 1 and name is None,
            )
            w.state.duels[duel.duel] = duel
            created += 1
    w.emit(
        "duels.scheduled",
        {
            "session": session,
            "name": name or ("Practice duels" if session == 1 else f"Sim duels {session}"),
            "duels": created,
            "rounds": 1,
            "duel_ticks": length,
            "decay": per_round,
            "issues": issues,
        },
    )
    return session


# ---------------------------------------------------------------- the team's moves


def _team_duel(w: World, team_id: str, did: int) -> Duel:
    duel = w.state.duels.get(did)
    if duel is None or duel.team != team_id:
        raise not_found(f"duel {did}")
    if duel.status != "live" or duel.accepted is not None:
        raise SimError("duel_closed", f"duel {did} is {duel.status}", 400)
    return duel


def say(w: World, team_id: str, did: int, body: dict[str, Any]) -> dict[str, Any]:
    duel = _team_duel(w, team_id, did)
    offer: dict[str, Any] = body["offer"] if isinstance(body.get("offer"), dict) else {}
    price = validate.optional_price(body.get("price", offer.get("price")))
    days_raw = body.get("days", offer.get("days"))
    if days_raw is not None and (
        isinstance(days_raw, bool) or not isinstance(days_raw, int) or not 0 <= days_raw <= 10
    ):
        raise invalid("days must be a whole number from 0 to 10")
    if price is not None and "days" in duel.issues and days_raw is None:
        raise SimError("missing_days", "this session negotiates delivery days: send days (0-10) with every price", 400)
    if w.used(team_id, f"duel:{did}") >= 1:
        raise wait_for_tick("one message per duel per tick", w.tick + 1)
    w.use(team_id, f"duel:{did}", 1, "one message per duel per tick")
    text = validate.clean_text(body.get("text"))
    days = int(days_raw) if days_raw is not None else 0
    if price is not None:
        duel.your_offer = DuelOffer(id=w.next_id("duel_offer"), price=price, days=days, tick=w.tick)
    message = {"tick": w.tick, "from": "you", "text": text, "price": price, "days": days_raw}
    duel.messages.append(message)
    duel.rounds = rounds(duel)
    duel.last_message_tick = w.tick
    w.emit("duel.message", {"duel": did, **message}, scope=f"team:{team_id}", actor=team_id)
    return {"ok": True, "duel": did, "offer": duel.your_offer.model_dump() if duel.your_offer else None}


def accept(w: World, team_id: str, did: int) -> dict[str, Any]:
    duel = _team_duel(w, team_id, did)
    if duel.rival_offer is None:
        raise SimError("no_offer", f"the rival has no standing offer in duel {did}", 400)
    cap = w.limit("accepts_per_team_per_tick")  # RULES.md: one accept per team per tick, duels and market alike
    if w.used(team_id, "accepts") >= cap:
        raise wait_for_tick("accepts per tick", w.tick + 1)
    w.use(team_id, "accepts", cap, "accepts per tick")  # `_team_duel` already refuses a duel accepted once
    duel.accepted, duel.accepted_tick = "team", w.tick
    return {"ok": True, "duel": did, "settles_tick": w.tick + 1}


# ---------------------------------------------------------------- the rival and the clock


def _progress(w: World, duel: Duel) -> float:
    total = max(1, duel.deadline_tick - duel.started_tick)
    return min(1.0, max(0.0, (w.tick - duel.started_tick) / total))


def _rival_price(w: World, duel: Duel) -> int:
    """The rival's price now: it starts asking for most of the pie and concedes toward a third of it."""
    pie = abs(duel.rival_limit - duel.your_limit)
    keep = RIVAL_START - (RIVAL_START - RIVAL_END) * _progress(w, duel)
    if duel.role == "seller":  # the rival buys: it bids low, rising toward its value
        return max(1, round(duel.your_limit + pie * (1 - keep)))
    return max(1, round(duel.your_limit - pie * (1 - keep)))  # the rival sells: it asks high, falling


def _utility(duel: Duel, price: int, days: int, *, rival: bool) -> float:
    """Gain over the limit, delivery days included: a seller gains from price, a buyer from its value."""
    if rival:
        weight = duel.rival_days_weight or 0.0
        base = price - duel.rival_limit if duel.role == "buyer" else duel.rival_limit - price
    else:
        weight = duel.your_days_weight or 0.0
        base = price - duel.your_limit if duel.role == "seller" else duel.your_limit - price
    return base + weight * days


def _tick_duel(w: World, duel: Duel) -> None:
    if duel.accepted is not None and (duel.accepted_tick or 0) < w.tick:
        _close_deal(w, duel)
        return
    if w.tick >= duel.deadline_tick:
        _close(w, duel, "no_deal", None)
        return
    if duel.accepted is not None:
        return
    style, params = rival_style(w, duel)
    if style != "sim":
        _zoo_turn(w, duel, style, params)
        return
    rng = w.rng("duel", duel.duel)
    target = _rival_price(w, duel)
    days = _rival_days(duel)
    ours = duel.your_offer
    if ours is not None and (duel.rival_offer is None or ours.tick >= duel.rival_offer.tick):
        mine = _utility(duel, ours.price, ours.days, rival=True)
        wanted = _utility(duel, target, days, rival=True)
        endgame = duel.deadline_tick - w.tick <= ENDGAME_TICKS
        if mine >= 0 and (mine >= wanted or endgame):
            duel.accepted, duel.accepted_tick = "rival", w.tick
            _rival_says(w, duel, rng.choice(ACCEPTS).format(p=ours.price), ours.price, ours.days)
            return
    if duel.rival_offer is None or duel.rival_offer.price != target:
        lines = OPENERS if duel.rival_offer is None else COUNTERS
        duel.rival_offer = DuelOffer(id=w.next_id("duel_offer"), price=target, days=days, tick=w.tick)
        _rival_says(w, duel, rng.choice(lines).format(p=target), target, days)


def _offer(raw: DuelOffer | None) -> duel_zoo.Offer | None:
    return None if raw is None else duel_zoo.Offer(raw.price, raw.days, raw.tick)


def _zoo_turn(w: World, duel: Duel, style: str, params: dict[str, float]) -> None:
    """A zoo rival's move: it sees only its own limit, the clock and the messages (`duel_zoo.RivalView`)."""
    rng = w.rng("duel", duel.duel)
    view = duel_zoo.RivalView(
        tick=w.tick,
        started_tick=duel.started_tick,
        deadline_tick=duel.deadline_tick,
        role="buyer" if duel.role == "seller" else "seller",
        limit=duel.rival_limit,
        days_weight=duel.rival_days_weight,
        two_issues="days" in duel.issues,
        decay=duel.decay_per_round,
        params=params,
        messages=tuple(duel.messages),  # our messages are "from": "you" here too (duel_zoo.US)
        our_offer=_offer(duel.your_offer),
        its_offer=_offer(duel.rival_offer),
        rng=rng,
        other_limit=duel.your_limit,
    )
    try:
        act = _checked(LIVE_RIVALS[style](view))
    except Exception as e:  # a broken rival holds: the shared clock never stops (#151 review)
        _warn_once(f"duel rival style {style!r} raised {type(e).__name__}: it holds instead", exc_info=True)
        return
    ours = duel.your_offer
    if act.kind == "accept" and ours is not None:
        duel.accepted, duel.accepted_tick = "rival", w.tick
        _rival_says(w, duel, rng.choice(ACCEPTS).format(p=ours.price), ours.price, ours.days)
    elif act.kind == "offer" and act.price is not None:
        days = act.days if act.days is not None and "days" in duel.issues else 0
        lines = OPENERS if duel.rival_offer is None else COUNTERS
        duel.rival_offer = DuelOffer(id=w.next_id("duel_offer"), price=act.price, days=days, tick=w.tick)
        _rival_says(w, duel, rng.choice(lines).format(p=act.price), act.price, days)


def _checked(act: duel_zoo.Act) -> duel_zoo.Act:
    """A rival's move as the game would take it: an offer needs a whole price in the game's range and days 0-10 (or
    none); anything else raises here, inside the guard, so it is a hold (#178 security P3)."""
    if act.kind not in ("accept", "offer", "hold"):
        raise ValueError(f"move {act.kind!r}")
    if act.kind == "offer":
        price, days = act.price, act.days
        if (
            not isinstance(price, int)
            or isinstance(price, bool)
            or not duel_zoo.MIN_PRICE <= price <= duel_zoo.MAX_PRICE
        ):
            raise ValueError(f"offer price {price!r}")
        if days is not None and (not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= 10):
            raise ValueError(f"offer days {days!r}")
    return act


def _rival_days(duel: Duel) -> int:
    """The rival's delivery day: the end of the range that suits it (a neutral 5 without a days issue)."""
    if "days" not in duel.issues:
        return 0
    weight = duel.rival_days_weight or 0.0
    return 10 if weight > 0 else 0 if weight < 0 else 5


def _rival_says(w: World, duel: Duel, text: str, price: int, days: int) -> None:
    message = {
        "tick": w.tick,
        "from": duel.rival,
        "text": text,
        "price": price,
        "days": days if "days" in duel.issues else None,
    }
    duel.messages.append(message)
    duel.rounds = rounds(duel)
    w.emit("duel.message", {"duel": duel.duel, **message}, scope=f"team:{duel.team}", actor="duel")


def rounds(duel: Duel) -> int:
    """The real game's count (all 26 practice payloads): the fewer priced messages of the two sides."""
    ours = sum(1 for m in duel.messages if m["from"] == "you" and m.get("price") is not None)
    theirs = sum(1 for m in duel.messages if m["from"] != "you" and m.get("price") is not None)
    return min(ours, theirs)


def _close_deal(w: World, duel: Duel) -> None:
    offer = duel.rival_offer if duel.accepted == "team" else duel.your_offer
    assert offer is not None
    _close(w, duel, "deal", offer)


def _close(w: World, duel: Duel, status: str, offer: DuelOffer | None) -> None:
    duel.status = status  # type: ignore[assignment]
    points, gain, share = 0.0, 0.0, 0.0
    pie = float(abs(duel.rival_limit - duel.your_limit))
    if offer is not None:
        gain = _utility(duel, offer.price, offer.days, rival=False)
        share = gain / pie if pie else 0.0
        decay = (1 - duel.decay_per_round) ** duel.rounds  # the real result: surplus × (1 - decay) ** rounds
        points = OUTSIDE_LIMIT_POINTS if gain < 0 else round(POINTS_PER_PIE * share * decay, 2)
        duel.price, duel.days = offer.price, offer.days if "days" in duel.issues else None
    duel.result = {
        "status": status,
        "price": duel.price,
        "days": duel.days,
        "your_gain": round(gain, 2),
        "pie": pie,
        "share": round(share, 3),
        "points": 0.0 if duel.practice else points,
        "practice": duel.practice,
    }
    if not duel.practice:
        team = w.team(duel.team)
        team.duel_points = round(team.duel_points + points, 2)
    w.emit("duel.closed", {"duel": duel.duel, "session": duel.session, "status": status, "item": duel.item})
    w.emit("duel.result", {"duel": duel.duel, **duel.result}, scope=f"team:{duel.team}")


# ---------------------------------------------------------------- the view


def duel_view(duel: Duel) -> dict[str, Any]:
    return {
        "duel": duel.duel,
        "session": duel.session,
        "status": duel.status,
        "role": duel.role,
        "item": duel.item,
        "issues": list(duel.issues),
        "your_days_weight": duel.your_days_weight,
        "days_meaning": DAYS_MEANING if "days" in duel.issues else None,
        "your_limit": duel.your_limit,
        "limit_meaning": LIMIT_MEANING[duel.role],
        "rival": duel.rival,
        "deadline_tick": duel.deadline_tick,
        "decay_per_round": duel.decay_per_round,
        "rounds": duel.rounds,
        "your_offer": duel.your_offer.model_dump() if duel.your_offer else None,
        "rival_offer": duel.rival_offer.model_dump() if duel.rival_offer else None,
        "messages": [dict(m) for m in duel.messages],
        "result": duel.result,
        "price": duel.price,
        "days": duel.days,
    }


def duels_view(w: World, team_id: str, done: bool) -> dict[str, Any]:
    rows = [d for d in w.state.duels.values() if d.team == team_id and (done or d.status == "live")]
    return {"duels": [duel_view(d) for d in sorted(rows, key=lambda d: d.duel)]}
