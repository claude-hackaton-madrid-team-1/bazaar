#!/usr/bin/env python3
"""Score duel policies on the rival zoo and the practice replay; print the tables as Markdown. Offline only.

    uv run python scripts/duel_zoo.py                                   # v1 and the reference policies
    uv run python scripts/duel_zoo.py --policy v1 --policy pkg.mod:fn --gate pkg.mod:fn --out report.md

A policy is `v1` (bazaar_agent.agents.duelist.duel_move at today's GUARDRAILS.md values), a reference policy
of `bazaar_sim.duel_zoo.REFERENCE_POLICIES` by name, or `module:attribute` for any callable with the
`duel_move(duel, tick, started_tick)` signature; `label=module:attr` names its column. `--gate X` adds the night
plan's go/no-go of X against v1, once per `--gate-decays` pair (default 0.06,0.08).
"""

from __future__ import annotations

import argparse
import importlib
import statistics
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from bazaar_sim import duel_gate, duel_replay, duel_zoo
from bazaar_sim.duel_zoo import Policy, Record


def resolve(name: str) -> Policy:
    if name == "v1":
        from bazaar_agent.agents.duelist import duel_move

        return duel_move
    if name in duel_zoo.REFERENCE_POLICIES:
        return duel_zoo.REFERENCE_POLICIES[name]
    module, _, attr = name.partition(":")
    if not attr:
        raise SystemExit(f"unknown policy {name!r}: v1, {', '.join(duel_zoo.REFERENCE_POLICIES)} or module:attr")
    policy: Policy = getattr(importlib.import_module(module), attr)
    return policy


def table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    def cell(v: Any) -> str:
        if v is None:
            return "–"
        return f"{v:.3f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)

    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def fit_section() -> str:
    rows = [
        (f.duel, f.status, f.role, f.label, f.n, "yes" if f.played else "no", f.open_gap, f.final_gap, f.pace,
         f.cadence, f.shape, f.hold_share)
        for f in duel_replay.fit()
    ]  # fmt: skip
    head = ("duel", "status", "role", "label", "msgs", "we played", "open gap", "final gap", "pace/tick", "cadence",
            "shape k", "hold share")  # fmt: skip
    return (
        "## The 26 practice duels, labelled\n\nGaps and pace are fractions of OUR limit, positive inside our zone.\n\n"
        + table(head, rows)
    )


def _q(xs: Sequence[float | int | None]) -> str:
    vals = sorted(float(x) for x in xs if x is not None)
    if not vals:
        return "–"
    return f"{vals[0]:.2f} / {statistics.median(vals):.2f} / {vals[-1]:.2f}"


def realism_section(n: int) -> str:
    v1 = resolve("v1")
    silent: Policy = lambda duel, tick, started: duel_zoo.HOLD  # noqa: E731
    rows = []
    real = [f for f in duel_replay.fit() if f.status != "live"]
    for style in ("linear", "convex", "one_shot", "tit_for_tat", "holdout"):
        fs = [f for f in real if f.label == style]
        rows.append((f"real {style}", len(fs), _q([f.n for f in fs]), _q([f.open_gap for f in fs]),
                     _q([f.final_gap for f in fs]), _q([f.pace for f in fs])))  # fmt: skip
        policy, against = (v1, "v1") if style in ("tit_for_tat", "holdout") else (silent, "silent")
        grid = duel_zoo.scenarios((style,), n=n, decays=(0.06,))
        zs = [duel_replay.features(duel_zoo.play(policy, sc)[1]) for sc in grid]
        rows.append((f"zoo {style} vs {against}", len(zs), _q([f.n for f in zs]), _q([f.open_gap for f in zs]),
                     _q([f.final_gap for f in zs]), _q([f.pace for f in zs])))  # fmt: skip
    head = ("paths", "count", "msgs min/med/max", "open gap", "final gap", "pace/tick")
    return "## Realism: real paths vs zoo paths (min / median / max)\n\n" + table(head, rows)


def confusion_section(n: int) -> str:
    silent: Policy = lambda duel, tick, started: duel_zoo.HOLD  # noqa: E731
    labels = (*duel_zoo.STYLES, "undetermined")
    rows = []
    for name, policy in (("silent", silent), ("v1", resolve("v1"))):
        for style in duel_zoo.STYLES:
            grid = duel_zoo.scenarios((style,), n=n, decays=(0.06,))
            got = [duel_replay.classify(duel_zoo.play(policy, sc)[1]) for sc in grid]
            rows.append((f"{style} vs {name}", *(got.count(lab) for lab in labels)))
    return "## The classifier on zoo paths (counts)\n\n" + table(("zoo rival", *labels), rows)


def _summary_rows(records: Sequence[Record], keys: Sequence[str]) -> list[tuple[Any, ...]]:
    out = []
    for key, s in duel_zoo.by(records, *keys).items():
        out.append((*key, s.n, s.deal_rate, s.mean_result, s.mean_score, s.mean_rounds, s.mean_messages,
                    s.outside_limit))  # fmt: skip
    return out


METRICS = ("n", "deal rate", "mean P", "mean share×kept", "rounds/deal", "our msgs", "outside")


def tournament_section(policies: dict[str, Policy], n: int) -> str:
    parts = ["## Tournament on the zoo"]
    price = duel_zoo.scenarios(duel_zoo.STYLES, n=n, decays=duel_zoo.DECAYS, duel_ticks=duel_zoo.DUEL_TICKS)
    days = [
        sc
        for truth in ("signed", "worst")
        for sc in duel_zoo.scenarios(duel_zoo.STYLES, n=n // 2, decays=(0.08,), two_issues=True, days_truth=truth)
    ]
    records = {name: duel_zoo.run(p, price) for name, p in policies.items()}
    overall = []
    for name, rs in records.items():
        s = duel_zoo.summarize(rs)
        overall.append((name, s.n, s.deal_rate, s.mean_result, s.mean_score, s.mean_rounds, s.mean_messages,
                        s.outside_limit))  # fmt: skip
    parts.append(
        f"Price only: 7 styles × {n} × 2 roles × 3 decays × 2 lengths = {len(price)} duels per policy.\n\n"
        + table(("policy", *METRICS), overall)
    )
    by_style = [(name, *row) for name, rs in records.items() for row in _summary_rows(rs, ("style",))]
    parts.append("### By rival style\n\n" + table(("policy", "style", *METRICS), by_style))
    mixes = {
        "practice": duel_replay.practice_mix(),
        "waiting one-shots = tit-for-tat": duel_replay.practice_mix(waiting_is_tit_for_tat=True),
        "responsive rivals = linear": duel_replay.practice_mix(responsive_is_linear=True),
    }
    mix_rows = [
        (name, label, round(duel_zoo.mix_mean(rs, mix), 2), round(duel_zoo.mix_mean(rs, mix, "deal"), 3))
        for name, rs in records.items()
        for label, mix in mixes.items()
    ]
    weights = "; ".join(f"{label}: {mix}" for label, mix in mixes.items())
    parts.append(
        "### Weighted by the practice session's mix of rival styles\n\n"
        f"Style weights = how often each style was seen in the 26 practice duels ({weights}).\n\n"
        + table(("policy", "mix", "mean P", "deal rate"), mix_rows)
    )
    by_decay = [(name, *row) for name, rs in records.items() for row in _summary_rows(rs, ("decay", "duel_ticks"))]
    parts.append("### By decay and duel length\n\n" + table(("policy", "decay", "ticks", *METRICS), by_decay))
    by_role = [(name, *row) for name, rs in records.items() for row in _summary_rows(rs, ("role",))]
    parts.append("### By role\n\n" + table(("policy", "role", *METRICS), by_role))
    day_rows = []
    for name, p in policies.items():
        for truth in ("signed", "worst"):
            rs = duel_zoo.run(p, [sc for sc in days if sc.days_truth == truth])
            s = duel_zoo.summarize(rs)
            day_rows.append((name, truth, s.n, s.deal_rate, s.mean_result, s.mean_score, s.mean_rounds,
                             s.mean_messages, s.outside_limit, s.errors))  # fmt: skip
    parts.append(
        f"### Two-issue sessions (decay 0.08, {len(days) // 2} duels per truth)\n\n"
        "Our days valued `signed` (weight × days, the simulator) or `worst` (−|weight| × days, PR #60).\n\n"
        + table(("policy", "days truth", *METRICS, "errors"), day_rows)
    )
    return "\n\n".join(parts)


def seeds_section(policies: dict[str, Policy], n: int, seeds: Sequence[int] = (1, 2, 3, 4, 5)) -> str:
    """The go/no-go grid (6 plan styles × 2 roles × 2 decays) redrawn with other seeds: how much the means move,
    and each policy's lift over the first one seed by seed (the gate's `mean_result` check, seed by seed)."""
    rows, means_by = [], {}
    for name, p in policies.items():
        means = []
        for seed in seeds:
            grid = duel_zoo.scenarios(duel_zoo.PLAN_STYLES, n=n, decays=(0.06, 0.08), seed=seed)
            means.append(duel_zoo.summarize(duel_zoo.run(p, grid)).mean_result)
        means_by[name] = means
        spread = statistics.stdev(means) if len(means) > 1 else 0.0
        rows.append((name, *(round(m, 2) for m in means), round(statistics.fmean(means), 2), round(spread, 2)))
    first = next(iter(means_by))
    lifts = []
    for name, means in list(means_by.items())[1:]:
        ratio = [m / b if b > 0 else float("nan") for m, b in zip(means, means_by[first], strict=True)]
        lifts.append((f"{name} / {first}", *(round(r, 3) for r in ratio), round(statistics.fmean(ratio), 3),
                      round(statistics.stdev(ratio), 3) if len(ratio) > 1 else 0.0))  # fmt: skip
    head = ("policy", *(f"seed {s}" for s in seeds), "mean", "sd")
    return (
        f"## Seed stability: mean P per duel on the go/no-go grid, decays 0.06/0.08 "
        f"({len(duel_zoo.PLAN_STYLES) * n * 4} duels per seed)\n\n"
        + table(head, rows)
        + "\n\nLift per seed (the gate's `mean_result`, threshold 1.4):\n\n"
        + table(("ratio", *(f"seed {s}" for s in seeds), "mean", "sd"), lifts)
    )


SENSITIVITY: tuple[tuple[str, tuple[str, ...], dict[str, float]], ...] = (
    ("as fitted", (), {}),
    ("conceders end 2 % over their limit", ("linear", "convex"), {"end": 0.02}),
    ("conceders end 30 % over their limit", ("linear", "convex"), {"end": 0.3}),
    ("one-shots never listen", ("one_shot",), {"listens": 0.0}),
    ("one-shots always listen", ("one_shot",), {"listens": 1.0}),
    ("tit-for-tat ratio 0.4, no drift", ("tit_for_tat",), {"ratio": 0.4, "drift": 0.0}),
    ("tit-for-tat ratio 1.2, drift 2 %", ("tit_for_tat",), {"ratio": 1.2, "drift": 0.02}),
    ("holdout holds at 30 %", ("holdout",), {"hold": 0.3}),
    ("conceders and holdouts never accept", ("linear", "convex", "tit_for_tat", "holdout"), {"listens": 0.0}),
)


def sensitivity_section(policies: dict[str, Policy], n: int) -> str:
    """Mean P per duel on the go/no-go grid (plus holdout) when one assumption of the zoo is pinned."""
    base = duel_zoo.scenarios(duel_zoo.STYLES, n=n, decays=(0.06, 0.08))
    rows = []
    for label, styles, fixed in SENSITIVITY:
        grid = duel_zoo.with_params(base, styles, **fixed)
        means = {name: duel_zoo.summarize(duel_zoo.run(p, grid)).mean_result for name, p in policies.items()}
        first = next(iter(means.values()))
        rows.append((label, *(round(m, 2) for m in means.values()), *(round(m / first, 2) if first else None
                                                                       for m in list(means.values())[1:])))  # fmt: skip
    names = list(policies)
    head = ("assumption", *(f"{n} P" for n in names), *(f"{n} / {names[0]}" for n in names[1:]))
    return (
        f"## Sensitivity: mean P per duel when one zoo assumption is pinned ({len(base)} duels, 7 styles)\n\n"
        + table(head, rows)
    )


def order_section(policies: dict[str, Policy], n: int) -> str:
    """The within-tick order: the simulator lets us see the rival's tick-t message before we move at t."""
    base = duel_zoo.scenarios(duel_zoo.PLAN_STYLES, n=n, decays=(0.06, 0.08))
    flipped = [replace(sc, team_first=True) for sc in base]
    rows = []
    for name, p in policies.items():
        a, b = duel_zoo.summarize(duel_zoo.run(p, base)), duel_zoo.summarize(duel_zoo.run(p, flipped))
        ra = sum(r.result for r in duel_replay.replay_all(p))
        rb = sum(r.result for r in duel_replay.replay_all(p, team_first=True))
        rows.append((name, round(a.mean_result, 2), round(b.mean_result, 2), round(a.deal_rate, 3),
                     round(b.deal_rate, 3), round(ra, 2), round(rb, 2)))  # fmt: skip
    head = ("policy", "zoo P rival first", "zoo P we first", "deals rival first", "deals we first",
            "replay P rival first", "replay P we first")  # fmt: skip
    return f"## Within-tick order ({len(base)} duels on the go/no-go grid, and the replay)\n\n" + table(head, rows)


def accept_cap_section(policies: dict[str, Policy], duels: int = 1200) -> str:
    """One accept per tick for the whole team (RULES.md; GUARDRAILS `max_accepts_per_tick` = 1, duels first),
    against batches of duels sharing a deadline; and the real deadline groups replayed together."""
    mixes = {"flat 6 plan styles": {s: 1.0 for s in duel_zoo.PLAN_STYLES}, "practice mix": duel_replay.practice_mix()}
    rows = []
    for mix_name, mix in mixes.items():
        for decay in (0.06, 0.08):
            for size in (1, 3, 6):
                grid = duel_zoo.batches(size, duels // size, mix, decay=decay)
                cells = []
                for p in policies.values():
                    free = duel_zoo.summarize(duel_zoo.run_batches(p, grid, None)).mean_result
                    capped = duel_zoo.summarize(duel_zoo.run_batches(p, grid, 1)).mean_result
                    cells.append(f"{free:.2f} → {capped:.2f}")
                rows.append((mix_name, decay, size, *cells))
    replay_rows = [
        (name, round(sum(r.result for r in duel_replay.replay_groups(p, accepts_per_tick=None)), 2),
         round(sum(r.result for r in duel_replay.replay_groups(p)), 2))
        for name, p in policies.items()
    ]  # fmt: skip
    return (
        f"## One accept per tick for the whole team ({duels} duels per row, in batches sharing a deadline)\n\n"
        "Mean P per duel, no cap → one accept per tick. Duels move in `duel` order; an accept past the budget is "
        "refused and retried next tick.\n\n"
        + table(("mix", "decay", "batch", *policies), rows)
        + "\n\nThe 12 unanswered practice duels replayed by deadline group (6 share tick 132), conservative:\n\n"
        + table(("policy", "no cap P", "one accept per tick P"), replay_rows)
    )


def replay_section(policies: dict[str, Policy]) -> str:
    rows: dict[int, list[Any]] = {}
    totals = []
    for name, p in policies.items():
        tot = {}
        for cf in ("conservative", "consistent"):
            out = duel_replay.replay_all(p, counterfactual=cf)
            tot[cf] = sum(r.result for r in out)
            if cf == "conservative":
                for r in out:
                    rows.setdefault(r.duel, [r.duel, r.role, r.oracle]).append(
                        f"{r.result:g} ({r.record.rounds}r)" if r.record.deal else "0"
                    )
        totals.append((name, round(tot["conservative"], 2), round(tot["consistent"], 2)))
    head = ("duel", "role", "oracle P", *policies)
    oracle = sum(r[2] for r in rows.values())
    return (
        "## Replay on the 12 unanswered practice duels\n\n"
        f"Per duel, conservative counterfactual: P after decay (rounds). The oracle (best rival offer, accepted at "
        f"once) totals {oracle:g} P; the real result was 0 P on all twelve.\n\n"
        + table(head, list(rows.values()))
        + "\n\n"
        + table(("policy", "conservative P", "consistent P"), totals)
    )


def gate_section(name: str, candidate: Policy, n: int, decays: Sequence[float] = (0.06, 0.08)) -> str:
    gate = duel_gate.go_no_go(candidate, resolve("v1"), n=n, decays=decays)
    rows = [(c.name, c.value, c.threshold, "pass" if c.passed else "FAIL", c.detail) for c in gate.checks]
    verdict = "GO" if gate.go else "NO-GO"
    return f"## Go/no-go: {name} vs v1, decays {'/'.join(map(str, decays))} → {verdict}\n\n" + table(
        ("check", "value", "threshold", "", "detail"), rows
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", action="append", help="v1, a reference policy, or module:attr (repeatable)")
    ap.add_argument("--gate", action="append", default=[], help="a policy to put through the go/no-go vs v1")
    ap.add_argument("--gate-decays", action="append", default=[], help="decay pairs for --gate, e.g. 0.08,0.10")
    ap.add_argument("--n", type=int, default=200, help="scenarios per style × role × decay × length")
    ap.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    args = ap.parse_args(argv)
    names = args.policy or ["v1", *duel_zoo.REFERENCE_POLICIES]
    specs = [n.partition("=")[::2] if "=" in n else (n, n) for n in names]  # label=module:attr names a column
    policies = {label: resolve(spec) for label, spec in specs}
    sections = [
        fit_section(),
        realism_section(args.n),
        confusion_section(min(args.n, 100)),
        tournament_section(policies, args.n),
        seeds_section(policies, args.n),
        sensitivity_section(policies, args.n),
        order_section(policies, args.n),
        accept_cap_section(policies),
        replay_section(policies),
        *(
            gate_section(label, resolve(spec), args.n, tuple(map(float, d.split(","))))
            for label, spec in (g.partition("=")[::2] if "=" in g else (g, g) for g in args.gate)
            for d in (args.gate_decays or ["0.06,0.08"])
        ),
    ]
    text = "\n\n".join(sections) + "\n"
    if args.out:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
