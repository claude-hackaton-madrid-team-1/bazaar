"""The per-dealer plan (N14a): what the taker bids one dealer for one item, built from recall.

Inputs, all recalled and all numbers (never a dealer's words):
- the learned ladder policy for (dealer, price class): a `learnings` row (kind `policy`) the outcome learner
  writes (`learn.evolve`), applied as before: it only lowers, narrows or skips a ladder;
- the dealer's curve for that class (`learn.curves.CurveStats`): patience, opening ask, a bid it ignored;
- GUARDRAILS `dealer_final_lift`: how far above the rarity cap a dealer's FINAL may be taken (0 = today).
Blockers (cooloff, quota, sold out, locks) are applied before this, in `Taker._unblocked`.

The patience play. A dealer's final is its limit: when its patience runs out it names one final offer, and
it walks if that is refused (RULES.md). When a final may close above our top bid, the ladder has to last
until the final comes, with step 1 and at least `patience + PATIENCE_MARGIN` distinct bids up to the top. It
never starts lower than a bid the dealer ignored or `MIN_OPEN_SHARE` of its opening ask. On Friday's feed,
t03 used it on Chato's uncommons (start 13, step 1) and got finals of 28, 29 and 29; the teams that stepped
by 3 paid 31-32. Our bids stay at or under the cap: only the final is taken above it.

Every input that changed a number becomes a `Note`, so each `dealer_open` and `dealer_bid` row records which
learning moved the bid (`changed_by`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from bazaar_agent.evals.dealers import price_class
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.learn.curves import CurveStats
from bazaar_agent.learn.evolve import DEFAULT_PATIENCE, LadderPolicy
from bazaar_agent.strategy import Move, dealer_command, final_reach

__all__ = ["DealerPlan", "Note", "final_reach", "patience_ladder", "plan_dealer_buy"]

# Distinct bids beyond the dealer's median patience before our top, so the final comes first. Chato's finals on
# Friday came after 4-8 bids (median 6) and Abuela's after 4-9; repeating our top price brought a final in only
# 1 of 11 threads, so the ladder must be long enough rather than hold at the top.
PATIENCE_MARGIN = 3
MIN_PLAY_BIDS = 9  # at least this many distinct bids: Chato's slowest Friday final came after 8 (the simulator's: 9)
MIN_OPEN_SHARE = 0.4  # never open below this share of the dealer's opening ask (t03 opened 13 of 33: answered)
MIN_TOP_SHARE = 0.5  # with no opening ask known (no curve yet), never open below this share of our own top
LIFTED_FINAL_MIN_BIDS = 4  # a final above our top is taken only after this many bids (Friday's earliest: 4)
MIN_FINAL_SHARE = 0.2  # a learned skip is lifted only when this share of the fills sits at or under final_max
Ladder3 = tuple[int, int, int]  # (start, top, step), as `strategy.Move.ladder`


@dataclass(frozen=True)
class Note:
    """One recalled input that changed a number of the plan."""

    source: str  # "policy" | "curve" | "final_lift"
    ref: str  # what was recalled, e.g. "learning policy chato card:uncommon @t150"
    effect: str  # what it changed
    text: str = ""  # the learning's own sentence (quoted data), when it has one

    def __str__(self) -> str:
        return f"{self.ref}: {self.effect}"


@dataclass(frozen=True)
class DealerPlan:
    move: Move | None  # None: skip this buy (see `skip`)
    final_max: int | None = None  # the most we take for the dealer's final; None = the top bid, as today
    notes: tuple[Note, ...] = ()
    skip: str | None = None

    @property
    def changed_by(self) -> list[str]:
        return [str(n) for n in self.notes]

    @property
    def lessons(self) -> list[str]:
        return [n.text for n in self.notes if n.text]


def fmt(ladder: Ladder3) -> str:
    start, top, step = ladder
    return f"{start}→{top} step {step}"


def patience_ladder(
    ladder: Ladder3, patience: float | None, opening: float | None, silent: int | None, max_bids: int | None = None
) -> Ladder3:
    """A ladder that lasts until the dealer's final: step 1, `patience + PATIENCE_MARGIN` distinct bids up to the
    top (at least `MIN_PLAY_BIDS`, at most `max_bids`: the thread's tick limit), never starting below a bid the
    dealer ignored or `MIN_OPEN_SHARE` of its opening ask (`MIN_TOP_SHARE` of our top when no curve says it), and
    never above the start we already had (a plan only lowers a start)."""
    start, top, _ = ladder
    need = max(MIN_PLAY_BIDS, math.ceil(patience or DEFAULT_PATIENCE) + PATIENCE_MARGIN)
    need = min(need, max_bids) if max_bids is not None else need
    anchor = math.ceil(opening * MIN_OPEN_SHARE) if opening else math.ceil(top * MIN_TOP_SHARE)  # never a 1 P insult
    floor = max(1, (silent + 1) if silent is not None else 1, anchor)
    return (min(start, max(floor, top - (need - 1))), top, 1)


def _policy_ref(policy: LadderPolicy) -> str:
    return f"learning policy {policy.dealer} {policy.price_class} @t{policy.tick}"


def _patience_ref(cls: str, mv: Move, curve: CurveStats | None, policy: LadderPolicy | None) -> tuple[str, float]:
    """Where the patience comes from: the curve, else the policy, else the default."""
    if curve is not None and curve.patience is not None:
        opens = f", opens {curve.opening:g}" if curve.opening is not None else ""
        return f"curve {mv.source} {cls} (final after ~{curve.patience:g} bids{opens})", curve.patience
    if policy is not None:
        return f"{_policy_ref(policy)} (final after ~{policy.patience:g} bids)", policy.patience
    return f"default patience for {mv.source} ({DEFAULT_PATIENCE:g} bids)", DEFAULT_PATIENCE


def _reach(mv: Move, top: int, rules: Guardrails, min_surplus: float, room: int | None) -> int | None:
    """`final_reach`, never above what we may still commit; None when that leaves nothing above our top."""
    reach = final_reach(mv.rarity, mv.value, top, rules, min_surplus)
    if reach is None or room is None:
        return reach
    return min(reach, room) if min(reach, room) > top else None


def plan_dealer_buy(
    mv: Move,
    policy: LadderPolicy | None,
    curve: CurveStats | None,
    rules: Guardrails,
    min_surplus: float,
    room: int | None = None,
) -> DealerPlan:
    """The strategy's dealer buy, evolved by its learned policy and, with `dealer_final_lift` on, by the patience
    play. With the lift off and no policy, the move comes back unchanged (today's behaviour). `room`: the primas
    the guardrails still let us commit (cash above the floor, the hour's spend left); a lifted final never goes
    above it, and a lifted plan whose dealer fills above both it and our top is skipped (a thread slot and a
    quota spent on a final we could not take)."""
    cls = price_class(mv.ref)
    if mv.ladder is None or cls is None:
        return DealerPlan(mv)
    # The lift only for a dealer and class with price history (fills seen): an unknown dealer (an L4 trickster)
    # never gets a final above the cap on its first conversations.
    history = bool(curve is not None and curve.fills) or bool(policy is not None and policy.fills)
    ladder, notes, reasons = mv.ladder, list[Note](), list[str]()
    if policy is not None:
        planned, why = policy.plan(ladder)
        if planned is None:
            final_max = _reach(mv, ladder[1], rules, min_surplus, room) if history else None
            if final_max is None or policy.deal_share(final_max) < MIN_FINAL_SHARE:
                return DealerPlan(None, skip=why)
            under = sum(1 for f in policy.fills if f <= final_max)
            rescued = f"skip lifted: {under} of {len(policy.fills)} fills at or under the final cap {final_max}"
            notes.append(Note("policy", _policy_ref(policy), rescued, policy.text()))
        else:
            if planned != ladder:
                changed = f"ladder {fmt(ladder)} → {fmt(planned)}"
                notes.append(Note("policy", _policy_ref(policy), changed, policy.text()))
            ladder = planned
            reasons.append(why)
    final_max = _reach(mv, ladder[1], rules, min_surplus, room) if history else None
    lifted = history and final_reach(mv.rarity, mv.value, ladder[1], rules, min_surplus) is not None
    if rules.dealer_final_lift > 0 and mv.price > max(ladder[1], final_max or 0):
        # The strategy kept this buy only for a final above our top that we may not take now: a thread there just
        # walks (the simulator opened Chato 7 times at 80→80 with no price history).
        why = "cash: what we may still commit is below" if lifted else "no price history for a final above our top:"
        return DealerPlan(None, skip=f"{why} {mv.source} {cls} fills ~{mv.price:g}")
    if final_max is not None and mv.price > ladder[1]:
        # The patience play only where we need the final: the dealer fills above our top (Chato). Where its fills
        # sit inside our top (Abuela), today's ladder closes in 1-2 ticks; the simulator's patience play there won
        # 1 P a deal and cost 8 ticks each (2 deals instead of 7 in the same run).
        ref, patience = _patience_ref(cls, mv, curve, policy)
        opening, silent = (curve.opening, curve.silent_below) if curve is not None else (None, None)
        played = patience_ladder(ladder, patience, opening, silent, max(1, rules.dealer_max_ticks_per_thread - 2))
        if played != ladder:
            notes.append(Note("curve", ref, f"ladder {fmt(ladder)} → {fmt(played)}"))
            ladder = played
    if final_max is not None:
        lift = f"dealer_final_lift {rules.dealer_final_lift:g}"
        effect = (
            f"take a final up to {final_max} after {LIFTED_FINAL_MIN_BIDS} bids (our bids stay at or under {ladder[1]})"
        )
        notes.append(Note("final_lift", lift, effect))
        reasons.append(f"a final up to {final_max} ({lift})")
    if ladder == mv.ladder and not reasons:
        return DealerPlan(mv, final_max, tuple(notes))
    start, top, step = ladder
    command = dealer_command(mv.ref, mv.source, start, top, step)
    evolved = replace(mv, ladder=ladder, limit=top, reason="; ".join([mv.reason, *reasons]), command=command)
    return DealerPlan(evolved, final_max, tuple(notes))
