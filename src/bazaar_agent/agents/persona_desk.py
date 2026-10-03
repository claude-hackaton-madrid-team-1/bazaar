"""The taker's use of the persona model (`persona_model.py`): every dealer buy is shaped by that dealer's
published behaviour before a thread opens. Pure: the inputs are this tick's snapshot (the `/api/dealers`
personas the taker already reads, the feed window) and the learned curves; no request of its own.

Per dealer buy, behind GUARDRAILS `persona_model_enabled`:
- budget: a dealer whose `deals_per_team_per_hour` we already used in the rolling hour gets no new thread
  (the server would refuse it `persona_quota`, and the thread slot is spent for nothing);
- prior: a (dealer, price class) with fewer than `MIN_LEARNED_FILLS` informative fills and no learned policy
  (a new L4/L5 dealer) whose persona publishes traits gets the trait prior's ladder, applied only downward
  (`persona_model.plan_with_prior`): never above the strategy's start, top or step, so every cap and
  `guardrails.check` stay as they are;
- unlock: the dealers whose deals unlock a locked dealer early (`early_deals_with`, `early_min_deals`) go
  first among this tick's moves (a stable reorder: nothing is added, no price changes).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from bazaar_agent.evals.dealers import price_class
from bazaar_agent.learn.curves import CurveStats
from bazaar_agent.persona_model import (
    MIN_LEARNED_FILLS,
    NegotiationParams,
    Persona,
    deals_left,
    derive,
    plan_with_prior,
    unlock_targets,
)
from bazaar_agent.strategy import Move, dealer_command

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class Shaped:
    moves: list[Move]
    skipped: list[tuple[Move, str]]  # (move, why): one row per dealer and reason, by the caller
    notes: dict[tuple[str, str], str]  # (dealer, ref) -> what the persona prior changed
    params: dict[tuple[str, str], NegotiationParams]  # (dealer, ref) -> the params used (tone, waits, ...)


def persona_item(mv: Move) -> str:
    """The menu line a move buys from: the pack id for a pack, else the card's rarity."""
    return mv.ref if mv.side == "pack" else mv.rarity


def deal_ticks(events: Iterable[Mapping[str, Any]], us: str) -> dict[str, list[int]]:
    """dealer -> the ticks of our settlements with it (the feed's `settlement.parties`)."""
    out: dict[str, list[int]] = {}
    if not us:
        return out
    for e in events:
        if e.get("type") != "settlement":
            continue
        p = e.get("payload")
        if not isinstance(p, Mapping):
            continue
        parties = [x for x in p.get("parties") or [] if isinstance(x, str)]
        if us not in parties:
            continue
        tick = p.get("tick") if isinstance(p.get("tick"), int) else e.get("tick")
        for other in parties:
            if other != us and isinstance(tick, int):
                out.setdefault(other, []).append(tick)
    return out


def ticks_per_hour(tick_seconds: float) -> float:
    """Ticks in one game hour at today's pace (a slower pace earlier only over-counts the hour: fail safe)."""
    return SECONDS_PER_HOUR / max(1.0, tick_seconds)


def shape(
    moves: Sequence[Move],
    personas: Mapping[str, Persona],
    curves: Mapping[tuple[str, str], CurveStats],
    learned: Iterable[tuple[str, str]],
    events: Sequence[Mapping[str, Any]],
    us: str,
    unlocked: Iterable[str],
    tick: int,
    tick_seconds: float,
) -> Shaped:
    """This tick's dealer buys under the persona model. `learned`: (dealer, class) keys with a ladder policy
    (the policy already shapes those: the prior stays out of its way)."""
    policy_keys = set(learned)
    ours = deal_ticks(events, us)
    hour = ticks_per_hour(tick_seconds)
    unlocked = list(unlocked)
    first = unlock_targets(personas, unlocked, {d: len(ts) for d, ts in ours.items()})
    kept: list[Move] = []
    skipped: list[tuple[Move, str]] = []
    notes: dict[tuple[str, str], str] = {}
    used: dict[tuple[str, str], NegotiationParams] = {}
    for mv in moves:
        persona = personas.get(mv.source)
        if persona is None:
            kept.append(mv)
            continue
        left = deals_left(persona, ours.get(mv.source, ()), tick, hour)
        if left == 0:
            cap = persona.deals_per_team_per_hour
            skipped.append((mv, f"persona budget: {cap} deals per hour with {mv.source} used"))
            continue
        cls = price_class(mv.ref)
        key = (mv.source, cls or "")
        params = derive(persona, persona_item(mv), curves.get(key))
        used[(mv.source, mv.ref)] = params
        curve = curves.get(key)
        thin = curve is None or len(curve.informative_fills) < MIN_LEARNED_FILLS
        if mv.ladder is None or key in policy_keys or not thin or not persona.traits_published:
            kept.append(mv)
            continue
        ladder, why = plan_with_prior(mv.ladder, params)
        if why is not None and mv.completes_page and ladder[1] < mv.ladder[1]:  # a page completer keeps its top
            ladder, why = (ladder[0], mv.ladder[1], ladder[2]), f"{why}; top {mv.ladder[1]} kept: completes the page"
        if why is None or ladder == mv.ladder:
            kept.append(mv)
            continue
        start, top, step = ladder
        command = dealer_command(mv.ref, mv.source, start, top, step)
        kept.append(replace(mv, ladder=ladder, limit=top, reason=f"{mv.reason}; {why}", command=command))
        notes[(mv.source, mv.ref)] = why
    kept.sort(key=lambda mv: mv.source not in first)  # stable: today's order within each group
    return Shaped(kept, skipped, notes, used)
