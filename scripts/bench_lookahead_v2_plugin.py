"""`--plugin` for bench_tournament.py: lookahead v2's `slack` variants, loaded from commit af794985 (branch
feat/bench-lookahead-v2) with `git show`, so nothing under src/ changes here.

    uv run python scripts/bench_tournament.py --plugin scripts/bench_lookahead_v2_plugin.py --only slack1,slack2,slack4

The v2 module is imported under its own name; the wall-clock budget is switched off (a loaded benchmark box must not
change the number of rollouts).
"""

from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import replace
from pathlib import Path
from typing import Any

SRC = "af794985:src/bazaar_agent/agents/bench_lookahead.py"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bazaar_agent.agents.bench_edge import expiries_in  # noqa: E402
from bazaar_agent.agents.matcher import BrokerBook, Fee, plan_matches, quotes_from  # noqa: E402


def _load() -> types.ModuleType:
    code = subprocess.run(["git", "show", SRC], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    module = types.ModuleType("bench_lookahead_v2")
    module.__dict__["__name__"] = "bench_lookahead_v2"
    sys.modules["bench_lookahead_v2"] = module  # dataclasses look the module up by name
    exec(compile(code, "bench_lookahead_v2.py", "exec"), module.__dict__)  # noqa: S102
    return module


V2 = _load()


class SlackPolicy:
    def __init__(self, per_side: int, slack: float, samples: int = 128, seed: int = 0) -> None:
        prior = replace(V2.LookaheadPrior(), per_side=per_side)
        config = V2.LookaheadConfig(samples=samples, prior=prior, slack=slack, budget_s=1e9)
        self.planner = V2.BenchLookahead(config, seed=seed)

    def __call__(self, book: dict[str, Any]) -> list[tuple[str, str, int]]:
        parsed = BrokerBook.model_validate(book)
        bench_q = [q for q in quotes_from(parsed).quotes if q.bench]
        fee = Fee(parsed.fee_bps, parsed.fee_per_card)
        exact = plan_matches(bench_q, fee, 15)
        expires = max(expiries_in(parsed.bench_offers, int(book["tick"])).values(), default=None)
        plan = self.planner.plan(bench_q, fee, int(book["tick"]), exact, expires, None)
        return [(str(m.sell.id), str(m.buy.id), m.price) for m in plan]


POLICIES = {f"slack{s}": (lambda tr, s=s: SlackPolicy(len(tr) // 2, float(s))) for s in (1, 2, 4)}
