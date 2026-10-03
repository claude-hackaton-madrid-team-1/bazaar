"""Duel policy v2: silence is free, every priced message we send costs a round, one accept per tick.

Verified on the 8 practice deals (shared DB `duels`, 2026-10-02): `result = |price − limit| × (1 − decay)^rounds`
with `rounds = min(our priced messages, the rival's priced messages)`, and an accept adds no round. v1 counters
every tick (7–8 rounds a deal, ~30 % of the surplus gone at 6 %). v2, behind `duel_policy` = v2 in GUARDRAILS.md:

  1. Anchor once (`duel_open_wait_ticks` after the start), then hold while the rival keeps conceding.
  2. Spend at most `duel_max_own_offers` rounds: the anchor, a stall-counter when the rival has not moved for
     `duel_stall_ticks` ticks, and a last offer at our floor before the endgame when nothing inside our limit is on
     the table. A rival that never priced, or went quiet and ignores us, gets v1's descending offers for free
     (they add a round only if it answers with a price), up to `duel_free_offers` messages.
  3. Accept a rival offer strictly inside our limit when it meets our target, when the rival has stalled and a
     counter is not worth one more round (`step × (1 − decay) < surplus × decay`), or in the endgame.
  4. The team accepts one offer per tick (RULES.md), shared by every duel: `plan_moves` gives the slot to the
     most urgent duel, earliest deadline first, so six duels that end on the same tick all get their accept.

Every price is valued as v1 values it (`duelist.worth`, |weight| per day against us) unless `duel_days_signed`
is on; our offers stay strictly inside our limit (PR #60), and `guardrails.check` re-checks every move.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from bazaar_agent.agents.duelist import (
    ROUNDING_SLACK,
    DuelMove,
    _two_issue,
    duel_deadline,
    duel_done,
    duel_id,
    inside_limit,
    our_target,
    worth,
)
from bazaar_agent.guardrails import duel_days_ok

OUR_SENDER = "you"  # how /api/duels names our own messages (verified, practice session)
SIGNED_DAYS_MAX = 10


@dataclass(frozen=True)
class V2Params:
    anchor: float = 0.6  # duel_anchor
    floor: float = 0.05  # duel_floor_margin
    endgame_ticks: int = 2  # duel_endgame_ticks
    max_own_offers: int = 3  # duel_max_own_offers
    stall_ticks: int = 3  # duel_stall_ticks
    open_wait_ticks: int = 0  # duel_open_wait_ticks
    free_offers: int = 16  # duel_free_offers
    answer_share: float = 0.2  # duel_answer_share
    accept_margin: int = 1  # duel_accept_margin_ticks
    days_signed: bool = False  # duel_days_signed

    @classmethod
    def from_rules(cls, rules: Any, anchor: float | None = None, floor: float | None = None) -> V2Params:
        """The GUARDRAILS.md values, with today's steered anchor and floor when the caller has them."""
        return cls(
            anchor=rules.duel_anchor if anchor is None else anchor,
            floor=rules.duel_floor_margin if floor is None else floor,
            endgame_ticks=rules.duel_endgame_ticks,
            max_own_offers=rules.duel_max_own_offers,
            stall_ticks=rules.duel_stall_ticks,
            open_wait_ticks=rules.duel_open_wait_ticks,
            free_offers=rules.duel_free_offers,
            answer_share=rules.duel_answer_share,
            accept_margin=rules.duel_accept_margin_ticks,
            days_signed=rules.duel_days_signed,
        )


DEFAULTS = V2Params()


@dataclass(frozen=True)
class V2Plan:
    """One duel's v2 move, with what the cross-duel accept planner needs."""

    move: DuelMove
    acceptable: DuelMove | None  # accepting the rival's standing offer, when it is strictly inside our limit
    value: float  # our surplus in that offer (0 without one)
    stalled: bool
    ticks_left: int
    pace: float = 0.0  # what the rival's offer gained us per tick lately (0 = nothing to wait for)


# ---------------------------------------------------------------- reading a duel


def _number(value: object) -> float | None:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return float(value)


def value_of(duel: Mapping[str, Any], price: int, days: object, signed: bool) -> float | None:
    """A price with its days as a price for us. Worst case (`duelist.worth`) unless `signed`: then
    `your_days_weight` is primas we gain (+) or lose (−) per day, as the simulator's `days_meaning` says."""
    if not signed or not _two_issue(duel):
        return worth(duel, price, days)
    n_days, weight = _number(days), _number(duel.get("your_days_weight"))
    if n_days is None or weight is None or not duel_days_ok(n_days):
        return None
    return price + weight * n_days if duel.get("role") == "seller" else price - weight * n_days


def surplus(value: float, limit: int, role: str) -> float:
    return value - limit if role == "seller" else limit - value


def _priced(duel: Mapping[str, Any], ours: bool) -> list[dict[str, Any]]:
    rival = duel.get("rival")
    return [
        m
        for m in duel.get("messages") or []
        if isinstance(m, dict)
        and _number(m.get("price")) is not None
        and ((m.get("from") == OUR_SENDER) if ours else (m.get("from") != OUR_SENDER and m.get("from") == rival))
    ]


def own_offers(duel: Mapping[str, Any]) -> int:
    """Priced messages we sent: each one is a round once the rival has priced as many."""
    sent = len(_priced(duel, ours=True))
    return 1 if sent == 0 and isinstance(duel.get("your_offer"), dict) else sent


def rounds_spent(duel: Mapping[str, Any]) -> int:
    """Rounds of decay so far: min(our priced messages, the rival's). What `duel_max_own_offers` caps."""
    return min(own_offers(duel), len(_priced(duel, ours=False)))


def ignored(duel: Mapping[str, Any]) -> bool:
    """True when the rival has priced nothing since our last priced message: it is not answering us."""
    ours = [m["tick"] for m in _priced(duel, ours=True) if isinstance(m.get("tick"), int)]
    theirs = [m["tick"] for m in _priced(duel, ours=False) if isinstance(m.get("tick"), int)]
    return bool(ours) and bool(theirs) and max(ours) >= max(theirs)


def quiet(duel: Mapping[str, Any], tick: int, silent_ticks: int) -> bool:
    """We have priced at least as often as the rival, it has not answered our last offer, and it has priced
    nothing for `silent_ticks`: our next offer adds a round only if it answers with a price (a listening
    one-shot does not; a time-based rival that is merely slow to start would)."""
    ours = [m["tick"] for m in _priced(duel, ours=True) if isinstance(m.get("tick"), int)]
    theirs = [m["tick"] for m in _priced(duel, ours=False) if isinstance(m.get("tick"), int)]
    if not ours or not theirs or len(ours) < len(theirs) or not ignored(duel):
        return False
    return tick > max(ours) and tick - max(theirs) >= silent_ticks


def rival_values(duel: Mapping[str, Any], signed: bool) -> list[tuple[int, float]]:
    """(tick, our surplus) of every rival priced message we can value, oldest first."""
    limit, role = duel.get("your_limit"), str(duel.get("role"))
    out = []
    for m in _priced(duel, ours=False):
        value = value_of(duel, int(m["price"]), m.get("days"), signed)
        tick = m.get("tick")
        if value is not None and isinstance(limit, int) and isinstance(tick, int):
            out.append((tick, surplus(value, limit, role)))
    return out


def last_gain_tick(history: list[tuple[int, float]]) -> int | None:
    """The last tick the rival moved in our favour (its first offer counts as a move)."""
    best, when = -math.inf, None
    for tick, s in history:
        if s > best + 1e-9:
            best, when = s, tick
    return when


def recent_pace(history: list[tuple[int, float]], tick: int, ticks: int) -> float:
    """Our surplus gained per tick from the rival's offers over the last `ticks` ticks (0 when it has not moved)."""
    if not history:
        return 0.0
    before = [s for t, s in history if t <= tick - ticks]
    start = before[-1] if before else history[0][1]
    return max(0.0, history[-1][1] - start) / max(1, ticks)


def mean_step(history: list[tuple[int, float]]) -> float:
    """The rival's average concession per priced move in our favour (0 when it never conceded)."""
    gains = [b - a for (_, a), (_, b) in zip(history, history[1:], strict=False) if b > a]
    return sum(gains) / len(gains) if gains else 0.0


# ---------------------------------------------------------------- our offers


def offer_price(duel: Mapping[str, Any], target: int, days: int, signed: bool) -> int:
    """The price that keeps `target` after the cost of our `days`, rounded to our side."""
    weight = _number(duel.get("your_days_weight")) or 0.0
    cost = (-weight if signed else abs(weight)) * days  # signed: a positive weight is a gain per day
    if duel.get("role") == "seller":
        return math.ceil(target + cost - ROUNDING_SLACK)
    return math.floor(target - cost + ROUNDING_SLACK)


def our_days(duel: Mapping[str, Any], signed: bool) -> int | None:
    """None in a price-only duel. Worst case: 0 (every day may cost us). Signed: the end of 0–10 that pays."""
    if not _two_issue(duel):
        return None
    weight = _number(duel.get("your_days_weight")) or 0.0
    return SIGNED_DAYS_MAX if signed and weight > 0 else 0


def _offer(duel: Mapping[str, Any], target: int, signed: bool, reason: str) -> DuelMove | None:
    """Our priced message at `target`, or None when it would not stay strictly inside our limit."""
    limit, role = duel["your_limit"], duel["role"]
    days = our_days(duel, signed)
    price = offer_price(duel, target, days, signed) if days is not None else target
    value = value_of(duel, price, days, signed)
    if price < 1 or value is None or not inside_limit(value, limit, role):
        return None
    return DuelMove("offer", price, days, reason)


def _beats(duel: Mapping[str, Any], move: DuelMove, rival_surplus: float, signed: bool) -> bool:
    """True when our offer asks for more than the rival already gives (else accepting dominates it)."""
    value = value_of(duel, int(move.price or 0), move.days, signed)
    if value is None:
        return False
    return surplus(value, duel["your_limit"], duel["role"]) > rival_surplus + 1e-9


# ---------------------------------------------------------------- one duel


def duel_plan(duel: Mapping[str, Any], tick: int, started_tick: int, params: V2Params = DEFAULTS) -> V2Plan:
    """v2's move for one duel, before the cross-duel accept planner. Strictly inside our limit always."""
    limit, role = duel.get("your_limit"), duel.get("role")
    deadline = duel_deadline(duel)
    left = (deadline - tick) if isinstance(deadline, int) else 12
    if duel_done(duel) or not isinstance(limit, int) or isinstance(limit, bool) or role not in ("seller", "buyer"):
        return V2Plan(DuelMove("hold", reason="done or unreadable duel"), None, 0.0, False, left)
    if _two_issue(duel) and _number(duel.get("your_days_weight")) is None:
        unvalued = DuelMove("hold", reason="two-issue duel without your_days_weight: cannot value days")
        return V2Plan(unvalued, None, 0.0, False, left)
    signed = params.days_signed
    total = max(1, (deadline - started_tick) if isinstance(deadline, int) else 12)
    elapsed = tick - started_tick
    ours, theirs = own_offers(duel), len(_priced(duel, ours=False))
    history = rival_values(duel, signed)
    moved = last_gain_tick(history)
    timed = all(isinstance(m.get("tick"), int) for m in _priced(duel, True) + _priced(duel, False))
    # Without every message's tick we cannot tell a stalled rival from a conceding one: never call it stalled.
    stalled = timed and elapsed >= params.stall_ticks and (moved is None or tick - moved >= params.stall_ticks)
    decay = _number(duel.get("decay_per_round")) or 0.0
    target = our_target(limit, str(role), elapsed / total, params.anchor, params.floor)
    target_surplus = surplus(target, limit, str(role))
    endgame = left <= params.endgame_ticks

    acceptable, on_table = _acceptable(duel, signed)
    if acceptable is not None:
        pace = recent_pace(history, tick, params.stall_ticks)
        plan = lambda move: V2Plan(move, acceptable, on_table, stalled, left, pace)  # noqa: E731
        step = mean_step(history)
        if left <= params.accept_margin + 1 or (endgame and stalled):
            return plan(replace(acceptable, reason="endgame, inside limit"))
        if endgame and ours <= theirs:  # still conceding, and the last safe accept tick is still ahead
            return plan(DuelMove("hold", reason=f"endgame, rival still conceding ({on_table:g}): accept later"))
        if not stalled and ours <= theirs:
            return plan(DuelMove("hold", reason=f"rival still conceding ({on_table:g} on the table): silence is free"))
        if not stalled:  # each rival message now adds a round: wait only while its steps beat the decay
            if step * (1 - decay) > on_table * decay:
                return plan(DuelMove("hold", reason=f"rival concedes {step:.1f} a step, more than a round costs"))
            return plan(replace(acceptable, reason="rival still conceding, but slower than a round costs"))
        if on_table >= target_surplus:
            return plan(replace(acceptable, reason="rival stalled at or above our target"))
        counter = _offer(duel, target, signed, "stall-counter: the rival stopped conceding")
        free = quiet(duel, tick, params.stall_ticks) and ours < params.free_offers  # a round only if it answers
        if free and counter is not None and _beats(duel, counter, on_table, signed):
            return plan(replace(counter, reason="the rival went quiet: step down for free"))
        if ignored(duel):
            return plan(replace(acceptable, reason="rival stalled and ignored our last offer: take it"))
        prior = params.answer_share * max(0.0, target_surplus - on_table)  # before the rival has shown a step
        worth_a_round = (on_table + max(step, prior, 1.0)) * (1 - decay) > on_table
        spare = params.max_own_offers - rounds_spent(duel)
        if spare > 0 and counter is not None and _beats(duel, counter, on_table, signed) and worth_a_round:
            return plan(counter)
        return plan(replace(acceptable, reason="rival stalled: another round is not worth it"))

    def send(price: int, reason: str) -> V2Plan:
        move = _offer(duel, price, signed, reason) or DuelMove("hold", reason="no offer strictly inside our limit")
        return V2Plan(move, None, 0.0, stalled, left)

    wait = V2Plan(DuelMove("hold", reason="nothing inside our limit yet: wait"), None, 0.0, stalled, left)
    if theirs == 0:  # the rival never priced: our offers cost no round until it does
        first = max(params.open_wait_ticks, params.stall_ticks)  # give it time to open first
        if ours >= params.free_offers or (ours == 0 and elapsed < first):
            return wait
        return send(target, "the rival has not priced: our offers cost no round yet")
    if quiet(duel, tick, params.stall_ticks) and ours < params.free_offers and left > params.endgame_ticks + 1:
        return send(target, "the rival went quiet: step down for free")
    if 2 <= left <= params.endgame_ticks + 1:  # the rival's last chances to take a deal from us: no deal scores 0
        # Said twice (D − 3 and D − 2) so it is still the rival's freshest offer in its endgame, whichever of us
        # moves first within a tick; it costs a round only in a duel that would otherwise score nothing.
        return send(our_target(limit, str(role), 1.0, params.anchor, params.floor), "last offer at our floor")
    if endgame:
        return wait
    spare = params.max_own_offers - rounds_spent(duel)
    if spare <= 0:
        return wait
    if ours == 0 and elapsed >= params.open_wait_ticks:
        return send(target, "anchor (once)")
    if stalled and spare > 1:  # keep the last offer for the last call
        return send(target, "stall-counter: the rival stopped conceding")
    return wait


def _acceptable(duel: Mapping[str, Any], signed: bool) -> tuple[DuelMove | None, float]:
    """Accepting the rival's standing offer when it is strictly inside our limit (rounded as #60 rounds it)."""
    offer = duel.get("rival_offer")
    if not isinstance(offer, dict) or _number(offer.get("price")) is None:
        return None, 0.0
    price = int(offer["price"])
    value = value_of(duel, price, offer.get("days"), signed)
    limit, role = duel["your_limit"], duel["role"]
    if value is None or not inside_limit(round(value), limit, role):
        return None, 0.0
    return DuelMove("accept", price, reason="inside our limit"), surplus(value, limit, role)


def may_counter(duel: Mapping[str, Any], params: V2Params) -> bool:
    """Whether one more priced message stays within v2's caps: rounds spent below `duel_max_own_offers` and
    our priced messages below `duel_free_offers`."""
    return rounds_spent(duel) < params.max_own_offers and own_offers(duel) < params.free_offers


def counter_offer(duel: Mapping[str, Any], tick: int, started_tick: int, params: V2Params = DEFAULTS) -> DuelMove:
    """Our offer at today's target (v2's valuation of days), for Jev's `counter`; a hold when out of reach."""
    limit, role, deadline = duel.get("your_limit"), duel.get("role"), duel_deadline(duel)
    if not isinstance(limit, int) or isinstance(limit, bool) or role not in ("seller", "buyer"):
        return DuelMove("hold", reason="unreadable duel")
    if _two_issue(duel) and _number(duel.get("your_days_weight")) is None:
        return DuelMove("hold", reason="two-issue duel without your_days_weight: cannot value days")
    total = max(1, (deadline - started_tick) if isinstance(deadline, int) else 12)
    target = our_target(limit, str(role), (tick - started_tick) / total, params.anchor, params.floor)
    move = _offer(duel, target, params.days_signed, "counter at our target")
    return move or DuelMove("hold", reason=f"no offer strictly inside our limit {limit}")


# ---------------------------------------------------------------- every duel, one accept per tick


def plan_moves(
    duels: Iterable[Mapping[str, Any]],
    tick: int,
    first_seen: Mapping[int, int],
    params: V2Params = DEFAULTS,
    slots: int = 1,
) -> dict[int, DuelMove]:
    """Every live duel's move this tick, with at most `slots` accepts across them (RULES.md: one per team).

    A duel holding an acceptable offer counts toward the queue of accepts still to make: when the duels that
    end by tick D need as many accept ticks as are left before D's endgame, the earliest-deadline one takes
    the slot now. Otherwise the slot goes to the most valuable duel that wants to accept. A duel that wanted
    the slot and did not get it holds (no counter: its offer is already good enough to take)."""
    plans: dict[int, V2Plan] = {}
    for d in duels:
        did = duel_id(d)
        if did is None:
            continue
        try:
            plans[did] = duel_plan(d, tick, first_seen.get(did, tick), params)
        except Exception as e:  # one malformed row holds that duel only, never the others
            plans[did] = V2Plan(DuelMove("hold", reason=f"unreadable duel ({type(e).__name__})"), None, 0.0, False, 0)
    queue = sorted((p.ticks_left, -p.value, did) for did, p in plans.items() if p.acceptable is not None)
    chosen: list[int] = []
    for position, (left, _, _) in enumerate(queue, start=1):
        room = max(1, left - params.accept_margin) * slots  # accept ticks left, keeping the margin
        if position >= room:  # the duels up to here need every remaining slot: accept the most urgent now
            urgent = [did for _, _, did in queue[:position] if did not in chosen]
            chosen.extend(_by_urgency(urgent, plans)[: slots - len(chosen)])
            break
    wanting = [did for did, p in plans.items() if p.move.kind == "accept" and did not in chosen]
    chosen.extend(sorted(wanting, key=lambda did: -plans[did].value)[: max(0, slots - len(chosen))])
    moves = {}
    for did, p in plans.items():
        if did in chosen and p.acceptable is not None:
            moves[did] = p.move if p.move.kind == "accept" else replace(p.acceptable, reason="accept queue: due now")
        elif p.move.kind == "accept":
            moves[did] = DuelMove("hold", reason="accept queued: another duel has the team's accept this tick")
        else:
            moves[did] = p.move
    return moves


def _by_urgency(dids: list[int], plans: Mapping[int, V2Plan]) -> list[int]:
    """Earliest deadline first; then the slowest rival (least to wait for: a stalled one first); then the biggest
    surplus. The fast conceders keep conceding while the slots go to the others."""
    return sorted(dids, key=lambda did: (plans[did].ticks_left, plans[did].pace, -plans[did].value))


@functools.cache
def _guardrails_params() -> V2Params:
    from bazaar_agent.guardrails import load_guardrails

    return V2Params.from_rules(load_guardrails().rules)


def single_duel_move(duel: dict[str, Any], tick: int, started_tick: int) -> DuelMove:
    """v2 at GUARDRAILS.md's knobs for ONE duel, with `duelist.duel_move`'s signature (the W2a zoo's policy shape,
    `scripts/duel_zoo.py --gate bazaar_agent.agents.duel_v2:single_duel_move`). No other duel competes for the
    accept here: the live loops, and a batch harness, call `plan_moves` with every live duel."""
    did = duel_id(duel)
    if did is None:
        return DuelMove("hold", reason="duel without an id")
    return plan_moves([duel], tick, {did: started_tick}, _guardrails_params())[did]
