"""pack_value + Jev: a pack is bought only when Jev says it is worth one of our scarce pack slots now.

Packs are capped per game hour (`max_packs_per_game_hour` in GUARDRAILS.md, and each dealer's own
`per_team_per_hour` in `/api/dealers`). The strategy engine finds the packs worth more to us than their
price; Jev weighs that against the slots left, the best alternative buy and our cash. `no` or
`undecided` keeps the slot. Pure: the judge is injected, so tests use a fake.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from bazaar_agent.guardrails import Guardrails
from bazaar_agent.strategy import Move, PackSlots, Playbook

PackJudge = Callable[[dict[str, Any]], tuple[str, float]]  # state -> (verdict, probability of yes)


def slots_left(book: Playbook, pack: str, slots: PackSlots, used_by_pack: Mapping[str, int]) -> int:
    """Our guardrail slots left, further capped by the selling dealer's own quota for that pack."""
    quota = book.pack_quotas.get(pack)
    left = slots.left if quota is None else min(slots.left, quota - used_by_pack.get(pack, 0))
    return max(0, left)


def pack_state(
    mv: Move, book: Playbook, left: int, slots: PackSlots, rules: Guardrails, t_hours: float
) -> dict[str, Any]:
    """What Jev reads to decide whether this pack is worth one of our scarce pack slots now."""
    top = book.buys[0] if book.buys else None
    alternative = None
    if top is not None:
        alternative = {"card": top.ref, "price": top.price, "value_to_us": top.value, "surplus": top.surplus}
    return {
        "pack": mv.ref,
        "pack_expected_value_to_us": mv.value,
        "pack_learned_price": mv.price,
        "pack_surplus": mv.surplus,
        "how_the_value_was_computed": mv.reason,
        "pack_slots_left_this_game_hour": left,
        "pack_slots_per_game_hour": slots.limit,
        "best_alternative_buy": alternative,
        "cash": book.cash,
        "cash_floor": rules.cash_floor,
        "cash_above_floor": max(0, book.cash - rules.cash_floor),
        "game_hour": round(t_hours, 2),
        "tick": book.tick,
    }


def gate_packs(
    book: Playbook,
    judge: PackJudge,
    slots: PackSlots,
    used_by_pack: Mapping[str, int],
    rules: Guardrails,
    t_hours: float,
) -> Playbook:
    """pack_value + Jev: a pack move keeps its command only when Jev decides yes to spending a slot now.
    `no` or `undecided` keeps the slot; with no slot left, Jev is not asked at all."""

    def gated(mv: Move) -> Move:
        if not mv.command:
            return mv
        left = slots_left(book, mv.ref, slots, used_by_pack)
        if left == 0:
            return replace(mv, command="", reason=f"{mv.reason}; no pack slot left this game hour")
        verdict, probability = judge(pack_state(mv, book, left, slots, rules, t_hours))
        jev = f"{verdict} {probability:.2f}"
        if verdict == "yes":
            return replace(mv, jev=jev)
        return replace(mv, command="", jev=jev, reason=f"{mv.reason}; Jev {verdict}: keep the slot")

    return replace(book, packs=tuple(map(gated, book.packs)), pack_slots=slots)
