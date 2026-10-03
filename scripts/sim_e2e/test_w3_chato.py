"""Chato uncommons through `dealer buy` with dealer_price_caps = chato:uncommon=31.

Simulator end to end: needs the #55 branch merged; copy into its tests/ and run with pytest (README.md).
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_sim import catalog
from tests.simkit import QUIET, running_sim
from tests.test_sim_cli import NOWHERE_DB, US


@pytest.fixture
def chato_session(tmp_path, monkeypatch):
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
        yield sim


def test_chato_uncommons_under_a_dealer_cap(chato_session, monkeypatch):
    sim = chato_session
    import time

    time.sleep(2)  # chato opens at tick 2
    caps = os.environ.get("W3_CAPS", "chato:uncommon=31")
    loaded = cli._rules()
    monkeypatch.setattr(
        cli, "_rules", lambda: replace(loaded, rules=loaded.rules.model_copy(update={"dealer_price_caps": caps}))
    )
    with sim.world.lock:
        held = sim.world.held_counts(US)
    refs = [
        c.ref
        for s in catalog.released_sets()
        for c in catalog.page_cards(s)
        if c.rarity == "uncommon" and held.get(c.ref, 0) == 0
    ][:3]
    out = []
    for ref in refs:
        r = CliRunner().invoke(
            cli.app, ["dealer", "buy", ref, "--start", "27", "--max", "31", "--dealer", "chato", "--live"]
        )
        with sim.world.lock:
            th = max(
                (t for t in sim.world.state.threads.values() if t.team == US and t.with_ == "chato"),
                key=lambda t: t.id,
                default=None,
            )
            deal = next((d for d in sim.world.team(US).deals if d.item == ref), None)
            out.append(
                {
                    "ref": ref,
                    "exit": r.exit_code,
                    "last": r.output.strip().splitlines()[-1][:200] if r.output.strip() else "",
                    "limit": th.neg.limit if th else None,
                    "opening": th.neg.opening if th else None,
                    "price": deal.price if deal else None,
                    "negotiated": deal.negotiated if deal else None,
                }
            )
    Path(os.environ["W3_OUT"]).write_text(json.dumps(out, indent=1))
