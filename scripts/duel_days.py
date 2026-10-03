#!/usr/bin/env python3
"""Two-issue duels (B8) on the zoo: what the rival's days reveal, and each policy under each days truth. Offline only.

    uv run python scripts/duel_days.py                                   # v1 and the reference policies
    PYTHONPATH=<night/b11-endgame>/src uv run python scripts/duel_days.py --v2 --out report.md

`--v2` adds W2b's v2 and its B11 presets (needs `bazaar_agent.duel_arena`), each with `days_signed` off and on,
and with `duel_days.days_aware`. A policy with days_signed on, scored under the worst-case truth, is the case of a
switch flipped without real evidence.
"""

from __future__ import annotations

import argparse
import importlib.util
import statistics
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

from bazaar_sim import duel_zoo
from bazaar_sim.duel_zoo import Policy

ROOT = Path(__file__).resolve().parent.parent
RIVAL_DAYS = {"each picks its end": -1.0, "blind, always 0": 0.0, "blind, always 5": 5.0}


def days_module() -> ModuleType:
    """`bazaar_agent.agents.duel_days`, from this checkout even when PYTHONPATH puts another agent first."""
    try:
        from bazaar_agent.agents import duel_days

        return duel_days
    except ImportError:
        spec = importlib.util.spec_from_file_location("duel_days", ROOT / "src/bazaar_agent/agents/duel_days.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules["duel_days"] = mod
        spec.loader.exec_module(mod)
        return mod


def table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    def cell(v: Any) -> str:
        return f"{v:.3f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)

    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return "\n".join(lines + ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows])


def grid(n: int, truth: str, fixed: float, decay: float, seed: int = 7) -> list[duel_zoo.Scenario]:
    g = duel_zoo.scenarios(duel_zoo.PLAN_STYLES, n=n, decays=(decay,), two_issues=True, days_truth=truth, seed=seed)
    return g if fixed < 0 else duel_zoo.with_params(g, duel_zoo.PLAN_STYLES, days_fixed=fixed)


def estimator_section(n: int, decay: float) -> str:
    """How often `rival_days` names the end the rival's weight really prefers, by the end of the duel."""
    dd = days_module()
    silent: Policy = lambda duel, tick, started: duel_zoo.HOLD  # noqa: E731
    rows = []
    for label, fixed in RIVAL_DAYS.items():
        scs = grid(n, "signed", fixed, decay)
        named = right = 0
        conf: list[float] = []
        for sc in scs:
            est = dd.rival_days(duel_zoo.play(silent, sc)[1])
            if est.prefers is None or not sc.rival_days_weight:
                continue
            named += 1
            conf.append(est.confidence)
            right += (est.prefers == 10) == (sc.rival_days_weight > 0)
        rows.append((label, len(scs), round(named / len(scs), 3), round(right / named, 3) if named else "–",
                     round(statistics.fmean(conf), 3) if conf else "–"))  # fmt: skip
    return (
        "## What the rival's days reveal (`rival_days`, silent policy, decay "
        f"{decay})\n\n`named`: share of duels where it names an end; `right`: the named end is the one the rival's "
        "weight prefers; `confidence`: its own confidence, averaged.\n\n"
        + table(("rivals", "duels", "named", "right", "confidence"), rows)
    )


def policies(v2: bool) -> dict[str, Policy]:
    dd = days_module()
    from bazaar_agent.agents.duelist import duel_move

    out: dict[str, Policy] = {"v1": duel_move, **duel_zoo.REFERENCE_POLICIES}
    if not v2:
        return out
    from bazaar_agent.duel_arena import B11_PRESETS, single  # type: ignore[import-not-found,unused-ignore]

    for name in ("today", "eg1_share03", "eg1_share05"):
        params = B11_PRESETS[name]
        label = "v2" if name == "today" else name
        signed = replace(params, days_signed=True)
        out[label] = single(params)
        out[f"{label} signed"] = single(signed)
        out[f"{label} signed + days_aware"] = dd.days_aware(single(signed), True)
    return out


def policy_section(pols: dict[str, Policy], n: int, decay: float) -> str:
    rows = []
    for truth in ("signed", "worst"):
        for label, fixed in RIVAL_DAYS.items():
            scs = grid(n, truth, fixed, decay)
            for name, p in pols.items():
                s = duel_zoo.summarize(duel_zoo.run(p, scs))
                rows.append((truth, label, name, s.n, round(s.deal_rate, 3), round(s.mean_result, 2),
                             round(s.mean_score, 3), s.outside_limit))  # fmt: skip
    head = ("days truth", "rivals", "policy", "n", "deal rate", "mean P", "share × kept", "outside")
    return (
        f"## Two-issue duels by days truth and rival behaviour (6 plan styles, decay {decay})\n\n"
        "`signed`: our weight is a gain (+) or loss (−) per day, as the simulator says. `worst`: every day costs "
        "|weight| (#60's stance). A `signed` policy under the `worst` truth is the switch flipped without evidence.\n\n"
        + table(head, rows)
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v2", action="store_true", help="add W2b's v2 and B11 presets (bazaar_agent.duel_arena)")
    ap.add_argument("--n", type=int, default=200, help="scenarios per style × role")
    ap.add_argument("--decay", type=float, default=0.08, help="Duels II: 0.08")
    ap.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    args = ap.parse_args(argv)
    sections: list[Callable[[], str]] = [
        lambda: estimator_section(args.n, args.decay),
        lambda: policy_section(policies(args.v2), args.n, args.decay),
    ]
    text = "\n\n".join(s() for s in sections) + "\n"
    if args.out:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
