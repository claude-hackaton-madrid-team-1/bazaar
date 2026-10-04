"""Per-card spread on the public books at Saturday's close (tick 1445, doors closed, keyless GETs).

Usage: uv run python docs/market-making/reports/mm-probe/spreads.py <dir with <venue>.json>
Each file is GET /api/venues/<venue>/offers. Addressed offers (`to` set) are left out: only their addressee can act.
A bid is `give.cash` for `want.types` card:<REF>; an ask is one card asset for `want.cash`.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median


def main() -> None:
    root = Path(sys.argv[1])
    bids: dict[str, list[tuple[int, str]]] = defaultdict(list)
    asks: dict[str, list[tuple[int, str]]] = defaultdict(list)
    n = addressed = 0
    for f in sorted(root.glob("*.json")):
        for o in json.loads(f.read_text()).get("offers", []):
            n += 1
            if o.get("to"):
                addressed += 1
                continue
            give, want = o["give"], o["want"]
            if give.get("assets") and len(give["assets"]) == 1 and want.get("cash") and not want.get("types"):
                asks[give["assets"][0]["ref"]].append((want["cash"], o["venue"]))
            elif give.get("cash") and len(want.get("types") or []) == 1 and not give.get("assets"):
                bids[want["types"][0].removeprefix("card:")].append((give["cash"], o["venue"]))
    print(f"open offers {n}, addressed {addressed}; cards with asks {len(asks)}, with bids {len(bids)}")
    two = sorted(set(asks) & set(bids))
    print(f"cards quoted on both sides: {len(two)}")
    gaps = []
    for ref in two:
        a, av = min(asks[ref])
        b, bv = max(bids[ref])
        gaps.append((a - b, ref, b, bv, a, av))
    for g, ref, b, bv, a, av in sorted(gaps):
        print(f"  {ref:7} best bid {b:4} ({bv:6}) best ask {a:4} ({av:6}) gap {g:4} = {g / a:5.0%} of ask")
    if gaps:
        print(
            f"median gap {median(g for g, *_ in gaps)} P; crossing {sum(g <= 0 for g, *_ in gaps)}; "
            f"gap <= 5 P {sum(0 < g <= 5 for g, *_ in gaps)}"
        )
    one_sided_ask = sorted(set(asks) - set(bids))
    one_sided_bid = sorted(set(bids) - set(asks))
    print(f"ask-only cards {len(one_sided_ask)}; bid-only cards {len(one_sided_bid)}: {', '.join(one_sided_bid)}")
    by_rarity_note = defaultdict(list)
    for ref, lst in asks.items():
        by_rarity_note[ref.split("-")[0]].append(min(p for p, _ in lst))
    print("venues holding public offers:", sorted({v for d in (asks, bids) for lst in d.values() for _, v in lst}))


if __name__ == "__main__":
    main()
