"""Time-split evaluation of the rival affinity map (`bazaar_agent.affinity`): how W4 chose its defaults.

Fit the map on the feed before a split tick, then predict, for each team with enough later evidence, the
released set it pushed hardest afterwards (the largest signed interest from its later signals). Prints log
loss and Brier score of P(top among the released sets) against the uniform guess, the hit rate of the most
likely set, and the AUC of the expected multiplier on later buy-side vs sell-side signals.

Caveats: the target measures persistence of revealed interest, not the true (private) multipliers; β and
damping were chosen on these same splits (in-sample); the only ground truth is our own team.

    uv run python scripts/affinity_eval.py --events feed.jsonl --me me.json --catalog catalog.json
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from bazaar_agent import affinity as af


def load(path: str) -> Any:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("body"), dict):
        return data["body"]
    if isinstance(data, dict) and isinstance(data.get("payload"), dict) and "type" in data:
        return data["payload"]
    return data


def evaluate(
    events: list[dict[str, Any]],
    me: dict[str, Any],
    catalog: dict[str, Any],
    mp: af.ModelParams,
    splits: tuple[int, ...],
    released: tuple[str, ...],
) -> dict[str, float]:
    sets, mult, us = af.catalog_sets(catalog), af.multipliers_from(me), str(me.get("id") or "")
    briers, losses, aucs = [], [], []
    hits = n = 0
    for split in splits:
        train = [e for e in events if int(e.get("tick") or 0) < split]
        test = af.signals([e for e in events if int(e.get("tick") or 0) >= split], catalog)
        amap = af.affinity_map(train, sets, mult, catalog, mp, exclude=[us])
        pos = [amap.expected(s.team, s.set_code) for s in test if s.team in amap.teams and s.weight > 0]
        neg = [amap.expected(s.team, s.set_code) for s in test if s.team in amap.teams and s.weight < 0]
        if pos and neg:
            aucs.append(sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg)))
        for team, ta in amap.teams.items():
            interest: dict[str, float] = defaultdict(float)
            for s in test:
                if s.team == team:
                    interest[s.set_code] += s.weight
            if not interest or max(interest.values()) < 2:
                continue
            top = max(interest, key=lambda k: interest[k])
            if top not in released:
                continue
            total = sum(ta.p_top[x] for x in released) or 1.0
            briers += [(ta.p_top[x] / total - (x == top)) ** 2 for x in released]
            losses.append(-math.log(max(ta.p_top[top] / total, 1e-4)))
            hits += max(released, key=lambda x: ta.p_top[x]) == top
            n += 1
    return {
        "log_loss": round(sum(losses) / len(losses), 3) if losses else float("nan"),
        "brier": round(sum(briers) / len(briers), 4) if briers else float("nan"),
        "hit": round(hits / n, 3) if n else float("nan"),
        "teams": n,
        "auc": round(sum(aucs) / len(aucs), 3) if aucs else float("nan"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--events", required=True, help="feed events, JSONL (a feed_events export)")
    ap.add_argument("--me", required=True, help="our /api/me (body, fixture or agent.me event)")
    ap.add_argument("--catalog", required=True, help="/api/catalog (body or fixture)")
    ap.add_argument("--splits", default="60,80,100", help="split ticks")
    ap.add_argument("--released", default="LAV,MAL,LAT,SAL", help="sets released over the feed")
    args = ap.parse_args()
    events = [json.loads(line) for line in Path(args.events).read_text().splitlines() if line.strip()]
    me, catalog = load(args.me), load(args.catalog)
    splits = tuple(int(x) for x in args.splits.split(","))
    released = tuple(args.released.split(","))
    k = len(released)
    print(
        f"uniform: log loss {math.log(k):.3f}, Brier {(1 - 1 / k) ** 2 / k + (k - 1) / k * (1 / k) ** 2:.4f}, "
        f"hit {1 / k:.3f}, AUC 0.5"
    )
    for damp in (True, False):
        for beta in (0.25, 0.5, 1.0, 2.0):
            r = evaluate(events, me, catalog, af.ModelParams(beta=beta, damp=damp), splits, released)
            print(f"damp={damp!s:5} beta={beta:<4}: {r}")


if __name__ == "__main__":
    main()
