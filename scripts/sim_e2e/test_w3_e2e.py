"""The ladder plans through our real CLI `dealer buy` against the local simulator.

Simulator end to end: needs the #55 branch merged; copy into its tests/ and run with pytest (README.md).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from bazaar_sim import catalog
from tests.test_sim_cli import US, run, session  # noqa: F401  (fixture)


def missing(sim, rarity):
    with sim.world.lock:
        held = sim.world.held_counts(US)
    refs = [c.ref for s in catalog.released_sets() for c in catalog.page_cards(s) if c.rarity == rarity]
    return [r for r in refs if held.get(r, 0) == 0]


def test_ladder_plans_end_to_end(session):  # noqa: F811
    url, sim, data_dir = session
    out = []
    for rarity, (start, top), n in (("common", (8, 12), 4), ("uncommon", (21, 25), 3)):
        for ref in missing(sim, rarity)[:n]:
            text = run("dealer", "buy", ref, "--start", str(start), "--max", str(top), "--live")
            with sim.world.lock:
                th = max(
                    (t for t in sim.world.state.threads.values() if t.team == US and t.with_ == "abuela"),
                    key=lambda t: t.id,
                )
                neg = th.neg
                deal = next((d for d in sim.world.team(US).deals if d.item == ref), None)
            out.append(
                {
                    "ref": ref,
                    "rarity": rarity,
                    "plan": [start, top],
                    "opening": neg.opening,
                    "limit": neg.limit,
                    "price": deal.price if deal else None,
                    "negotiated": deal.negotiated if deal else None,
                    "last_line": text.strip().splitlines()[-1][:160],
                }
            )
    Path(os.environ.get("W3_E2E_OUT", "/dev/null")).write_text(json.dumps(out, indent=1))
    assert all(o["price"] is not None for o in out)
