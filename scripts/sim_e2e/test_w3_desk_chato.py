"""The desk with Chato first: ladder_level_deals 3, floors 0.5, chato:uncommon=31.

Simulator end to end: needs the #55 branch merged; copy into its tests/ and run with pytest (README.md).
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from tests.simkit import QUIET, running_sim
from tests.test_sim_cli import NOWHERE_DB, US, run

from bazaar_agent import cli

MERGED = Path(os.environ["W3_MERGED"])


@pytest.fixture
def desk_session(tmp_path, monkeypatch):
    config = replace(QUIET, tick_seconds=0.5, rivals=0, chato_open_ticks=2)
    with running_sim(config) as (url, sim):
        monkeypatch.setattr("bazaar_agent.config.SIM_URL", url)
        for name in ("BAZAAR_URL", "BAZAAR_KEY", "BAZAAR_SIM_KEY"):
            monkeypatch.delenv(name, raising=False)
        env = {
            "BAZAAR_SIM": "1",
            "BAZAAR_TEAM_ID": US,
            "BAZAAR_DATA_DIR": str(tmp_path),
            "DATABASE_URL": NOWHERE_DB,
            "BAZAAR_SIM_DATABASE_URL": NOWHERE_DB,
            "COLUMNS": "200",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        yield sim, tmp_path


def test_desk_chato_first(desk_session, monkeypatch):
    sim, data_dir = desk_session
    feed_dir = data_dir / "feed"
    feed_dir.mkdir(exist_ok=True)
    with (feed_dir / "feed.jsonl").open("w") as f:
        for line in MERGED.read_text().splitlines():
            e = json.loads(line)
            e["id"] = int(e["id"]) + 10_000_000
            f.write(json.dumps(e) + "\n")
    import time

    time.sleep(1.5)
    loaded, rules = cli._strategy(), cli._rules()
    params = loaded.params.model_copy(update={"ladder_floor_quantile": 0.5, "ladder_level_deals": 3})
    monkeypatch.setattr(cli, "_strategy", lambda: replace(loaded, params=params))
    monkeypatch.setattr(
        cli,
        "_rules",
        lambda: replace(rules, rules=rules.rules.model_copy(update={"dealer_price_caps": "chato:uncommon=31"})),
    )
    log = run("agent", "taker", "--live", "--max-ticks", "50", "--no-jev", "--threads", "2")
    Path(os.environ["W3_LOG"]).write_text(log)
    out = []
    with sim.world.lock:
        offers = sim.world.state.offers
        for t in sorted(sim.world.state.threads.values(), key=lambda t: t.id):
            if t.team != US:
                continue
            seq = []
            for m in t.messages:
                o = offers.get(m.offer) if m.offer is not None else None
                seq.append(("T" if m.sender == US else "D", (o.give.cash or o.want.cash) if o is not None else None))
            deal = next((d for d in sim.world.team(US).deals if d.item == t.neg.item and d.dealer == t.with_), None)
            out.append(
                {
                    "dealer": t.with_,
                    "item": t.neg.item,
                    "rarity": t.neg.rarity,
                    "status": t.status,
                    "opening": t.neg.opening,
                    "limit": t.neg.limit,
                    "price": deal.price if deal else None,
                    "seq": seq,
                }
            )
        unlocked = list(sim.world.team(US).unlocked)
    Path(os.environ["W3_OUT"]).write_text(json.dumps({"threads": out, "unlocked": unlocked}, indent=1))
