"""A trickster Abuela in the simulator: `bazaar dealer buy --live` over real HTTP refuses the swap and flags it
only when GUARDRAILS.md allows flags.

Simulator end to end: needs the #55 branch merged; copy into its tests/ and run with pytest (README.md).
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from bazaar_sim import catalog, market, threads
from bazaar_sim.models import Side
from tests.test_sim_cli import US, run, session  # noqa: F401

from bazaar_agent import cli


@pytest.mark.parametrize("allow", [False, True])
def test_a_trickster_is_refused_and_flagged_only_when_allowed(session, monkeypatch, allow):  # noqa: F811
    url, sim, data_dir = session
    real_create, real_post = market.create_offer, threads.post_message

    def lesser(w, maker, to, venue, thread, give, want, *args, **kw):
        if maker == "abuela" and give.types and give.types[0].startswith("card:"):
            give = Side(types=["card:LAT-01"])  # a common slipped in for the uncommon we asked
        return real_create(w, maker, to, venue, thread, give, want, *args, **kw)

    def claims(w, th, sender, text, offer_id, *, public_text):
        if sender == "abuela" and offer_id is not None:
            name = catalog.cards()[th.neg.item].name
            text = f"Tu {name}, la {th.neg.item}, solo para ti."
        return real_post(w, th, sender, text, offer_id, public_text=public_text)

    monkeypatch.setattr(market, "create_offer", lesser)
    monkeypatch.setattr(threads, "post_message", claims)
    loaded = cli._rules()
    rules = loaded.rules.model_copy(update={"allow_flags": allow, "flag_trusted_dealers": "none"})
    monkeypatch.setattr(cli, "_rules", lambda: replace(loaded, rules=rules))
    with sim.world.lock:
        held = sim.world.held_counts(US)
    ref = next(
        c.ref
        for s in catalog.released_sets()
        for c in catalog.page_cards(s)
        if c.rarity == "uncommon" and held.get(c.ref, 0) == 0
    )
    text = run("dealer", "buy", ref, "--start", "3", "--max", "8", "--live")
    with sim.world.lock:
        deals = [d for d in sim.world.team(US).deals if d.item == ref]
        flags = list(getattr(sim.world.state, "flags", []) or [])
    Path(os.environ["W3_OUT"] + f".{allow}").write_text(
        json.dumps(
            {"ref": ref, "out": text.splitlines(), "deals": len(deals), "flags": [str(f) for f in flags]}, indent=1
        )
    )
    assert deals == []
    if allow:
        assert "flagged message" in text
    else:
        assert "would flag message" in text and "allow_flags" in text
