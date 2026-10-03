"""`bazaar dealer sell --live` against the simulator over real HTTP.

Simulator end to end: needs the #55 branch merged; copy into its tests/ and run with pytest (README.md).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from bazaar_sim import catalog
from tests.test_sim_cli import US, run, session  # noqa: F401


def test_sell_a_duplicate_to_abuela(session):  # noqa: F811
    url, sim, data_dir = session
    with sim.world.lock:
        held = sim.world.held_counts(US)
        dups = [r for r, n in held.items() if n >= 2 and catalog.cards()[r].rarity in ("common", "uncommon")]
    out = []
    for ref in dups[:2]:
        rarity = catalog.cards()[ref].rarity
        text = run("dealer", "sell", ref, "--start", "9" if rarity == "common" else "18", "--min", "3", "--live")
        with sim.world.lock:
            th = max((t for t in sim.world.state.threads.values() if t.team == US), key=lambda t: t.id)
            out.append(
                {
                    "ref": ref,
                    "rarity": rarity,
                    "status": th.status,
                    "opening_bid": th.neg.opening,
                    "limit": th.neg.limit,
                    "lines": text.strip().splitlines()[-4:],
                }
            )
    Path(os.environ["W3_OUT"]).write_text(json.dumps(out, indent=1))
