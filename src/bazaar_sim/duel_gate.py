"""The duel policy go/no-go of the night plan (W2b), computed on the zoo and the replay.

    6 styles × `n` scenarios × 2 roles × 2 decays, price only:
      mean_result     the candidate's mean result (P after decay) ≥ 1.4 × the baseline's
      deals_conceders its deal rate ≥ the baseline's against conceders (linear, convex, tit_for_tat, sim)
      deals_one_shot  its deal rate ≥ 0.9 × the baseline's against one-shot rivals
    outside_limit     0 closes outside our limit, over the grid above plus two two-issue grids, one with our days
                      valued signed and one at the worst case, each drawn apart (3 × 4,800 = 14,400 distinct duels
                      at n=200, above the plan's 10,000)
    replay            its conservative replay on the 12 unanswered practice duels beats the baseline's

Every check is reported with its value and threshold, so a no-go says by how much.

The checks score each duel alone, as the plan defines them: none applies the team's one accept per tick across
duels that share a deadline. Run `duel_zoo.run_batches` / `duel_replay.replay_groups` (the report script's accept
section) for that: a policy that waits for the endgame passes here and halves there.
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


def _rate(records: Sequence[Record], styles: Sequence[str]) -> float | None:
    """The deal rate against `styles`, or None when the grid has none of them (the check did not run)."""
    picked = [r for r in records if r.style in styles]
    return duel_zoo.summarize(picked).deal_rate if picked else None


def _rate_check(name: str, cand: float | None, base: float | None, factor: float) -> Check:
    if cand is None or base is None:
        return Check(name, float("nan"), float("nan"), False, "not run: no duels of these styles in the grid")
    return Check(name, round(cand, 3), round(factor * base, 3), cand >= factor * base, f"baseline {base:.3f}")


def _lift_check(cand: float, base: float) -> Check:
    """`cand ≥ 1.4 × base` only means something when the baseline earns: at or below 0 it fails unless the
    candidate earns more and something at all."""
    detail = f"{cand:.2f} P vs {base:.2f} P per duel"
    if base <= 0:
        return Check("mean_result", float("nan"), RESULT_LIFT, cand > max(base, 0.0), detail + " (baseline ≤ 0)")
    lift = cand / base
    return Check("mean_result", round(lift, 3), RESULT_LIFT, lift >= RESULT_LIFT, detail)


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
        _lift_check(c_all.mean_result, b_all.mean_result),
        _rate_check("deals_conceders", _rate(cand, CONCEDERS), _rate(base, CONCEDERS), 1.0),
        _rate_check("deals_one_shot", _rate(cand, ONE_SHOT), _rate(base, ONE_SHOT), ONE_SHOT_DEALS),
        Check("outside_limit", float(outside), 0.0, outside == 0, f"over {duels} duels (price only + two-issue)"),
        Check("replay", round(c_rep, 2), round(b_rep, 2), c_rep > b_rep, "P on the 12 unanswered practice duels"),
    )
    return Gate(checks, tuple(cand), tuple(base))
