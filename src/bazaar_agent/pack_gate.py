"""Pack slot gate: explicit inventory restocking, otherwise holding-value proposals plus Jev.

Packs are capped per game hour (`max_packs_per_game_hour` in GUARDRAILS.md, and each dealer's own
`per_team_per_hour` in `/api/dealers`). Explicit restocking buys inventory within those quotas;
otherwise the strategy proposes holding-value gains and Jev weighs alternatives. `no` or `undecided`
keeps the slot on that legacy path. Pure: the judge is injected, so tests use a fake.
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
    judge: PackJudge | None,
    slots: PackSlots,
    used_by_pack: Mapping[str, int],
    rules: Guardrails,
    t_hours: float,
) -> Playbook:
    """Restocking uses the explicit policy; other pack proposals require Jev yes.
    Neither path spends a slot beyond the shared or dealer quota."""

    def gated(mv: Move) -> Move:
        if not mv.command:
            return mv
        left = slots_left(book, mv.ref, slots, used_by_pack)
        if left == 0:
            return replace(mv, command="", reason=f"{mv.reason}; no pack slot left this game hour")
        if rules.pack_restock_enabled and mv.strategy == "pack_restock":
            return replace(mv, jev="not required: inventory restock")
        if judge is None:
            return replace(mv, command="", reason=f"{mv.reason}; no pack judge: keep the slot")
        verdict, probability = judge(pack_state(mv, book, left, slots, rules, t_hours))
        jev = f"{verdict} {probability:.2f}"
        if verdict == "yes":
            return replace(mv, jev=jev)
        return replace(mv, command="", jev=jev, reason=f"{mv.reason}; Jev {verdict}: keep the slot")

    return replace(book, packs=tuple(map(gated, book.packs)), pack_slots=slots)


PACK_QUESTION = "spend_pack_slot_now"  # questions/packs.json


def jev_pack_judge(settings: Any, timeout_s: float, cache_ticks: int = 0) -> PackJudge:
    """Jev `spend_pack_slot_now` (questions/packs.json): (verdict, probability of yes) for one pack state.

    Shared by `bazaar strategy` and the runtime's `strategy` tool. Without TYPESAFE_API_KEY Jev answers
    undecided, and an undecided verdict keeps the slot. `cache_ticks` (the taker: GUARDRAILS.md
    `jev_cache_ticks`) reuses an answer for the same pack state, tick and game hour aside, for that many
    ticks (`agents.jev_cache`); a failed call is never reused."""
    from bazaar_agent.agents.jev_cache import CACHED_REASONS, VerdictCache, state_key, state_tick
    from bazaar_agent.config import REPO_ROOT
    from bazaar_agent.jev import judge, load_questions

    questions = load_questions(REPO_ROOT / "questions" / "packs.json")
    key = settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else None
    cache: VerdictCache[tuple[str, float]] = VerdictCache(cache_ticks)

    def ask(state: dict[str, Any]) -> tuple[str, float]:
        tick, cache_key = state_tick(state), state_key(PACK_QUESTION, state)
        cached = cache.get(cache_key, tick)
        if cached is not None:
            return cached
        verdict = judge(state, questions, api_key=key, timeout_s=timeout_s).verdicts[PACK_QUESTION]
        if verdict.reason in CACHED_REASONS:
            cache.put(cache_key, tick, (verdict.verdict, verdict.value))
        return verdict.verdict, verdict.value

    return ask
