"""The duel policy go/no-go of the night plan (W2b), computed on the zoo and the replay.

    6 styles × `n` scenarios × 2 roles × 2 decays, price only:
      mean_result     the candidate's mean result (P after decay) ≥ 1.4 × the baseline's
      deals_conceders its deal rate ≥ the baseline's against conceders (linear, convex, tit_for_tat, sim)
      deals_one_shot  its deal rate ≥ 0.9 × the baseline's against one-shot rivals
    outside_limit     0 closes outside our limit, over the grid above plus the same grid in two-issue
                      sessions with days valued signed and at the worst case (3 × 4,800 = 14,400 duels at n=200)
    replay            its conservative replay on the 12 unanswered practice duels beats the baseline's

Every check is reported with its value and threshold, so a no-go says by how much.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from bazaar_sim import duel_replay, duel_zoo
from bazaar_sim.duel_zoo import Policy, Record

CONCEDERS = ("linear", "convex", "tit_for_tat", "sim")
ONE_SHOT = ("one_shot",)
RESULT_LIFT = 1.4
ONE_SHOT_DEALS = 0.9


@dataclass(frozen=True)
class Check:
    name: str
    value: float
    threshold: float
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class Gate:
    checks: tuple[Check, ...]
    candidate: tuple[Record, ...]
    baseline: tuple[Record, ...]

    @property
    def go(self) -> bool:
        return all(c.passed for c in self.checks)


def _rate(records: Sequence[Record], styles: Sequence[str]) -> float:
    picked = [r for r in records if r.style in styles]
    return sum(r.deal for r in picked) / len(picked) if picked else 0.0


def go_no_go(
    candidate: Policy,
    baseline: Policy,
    *,
    n: int = 200,
    decays: Sequence[float] = (0.06, 0.08),
    styles: Sequence[str] = duel_zoo.PLAN_STYLES,
    seed: int = 7,
) -> Gate:
    grid = duel_zoo.scenarios(styles, n=n, decays=decays, seed=seed)
    cand, base = duel_zoo.run(candidate, grid), duel_zoo.run(baseline, grid)
    c_all, b_all = duel_zoo.summarize(cand), duel_zoo.summarize(base)
    lift = c_all.mean_result / b_all.mean_result if b_all.mean_result else float("inf")
    c_con, b_con = _rate(cand, CONCEDERS), _rate(base, CONCEDERS)
    c_one, b_one = _rate(cand, ONE_SHOT), _rate(base, ONE_SHOT)
    outside = sum(r.outside_limit for r in cand)
    duels = len(cand)
    for truth in ("signed", "worst"):
        days_grid = duel_zoo.scenarios(styles, n=n, decays=decays, seed=seed, two_issues=True, days_truth=truth)
        days_records = duel_zoo.run(candidate, days_grid)
        outside += sum(r.outside_limit for r in days_records)
        duels += len(days_records)
    c_rep = sum(r.result for r in duel_replay.replay_all(candidate))
    b_rep = sum(r.result for r in duel_replay.replay_all(baseline))
    checks = (
        Check(
            "mean_result",
            round(lift, 3),
            RESULT_LIFT,
            lift >= RESULT_LIFT,
            f"{c_all.mean_result:.2f} P vs {b_all.mean_result:.2f} P per duel",
        ),
        Check("deals_conceders", round(c_con, 3), round(b_con, 3), c_con >= b_con, "deal rate vs the baseline's"),
        Check(
            "deals_one_shot",
            round(c_one, 3),
            round(ONE_SHOT_DEALS * b_one, 3),
            c_one >= ONE_SHOT_DEALS * b_one,
            f"baseline {b_one:.3f}",
        ),
        Check("outside_limit", float(outside), 0.0, outside == 0, f"over {duels} duels (price only + two-issue)"),
        Check("replay", round(c_rep, 2), round(b_rep, 2), c_rep > b_rep, "P on the 12 unanswered practice duels"),
    )
    return Gate(checks, tuple(cand), tuple(base))
