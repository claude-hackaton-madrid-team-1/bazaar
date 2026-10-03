"""The desk (agent taker) in the simulator with ladder_floor_quantile = 0.5.

Simulator end to end: needs the #55 branch merged; copy into its tests/ and run with pytest (README.md).
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from tests.test_sim_cli import US, run, session  # noqa: F401  (fixture)

from bazaar_agent import cli

MERGED = Path(os.environ["W3_MERGED"])


def test_desk_opens_dealer_threads_from_the_floor_table(session, monkeypatch):  # noqa: F811
    url, sim, data_dir = session
    q = float(os.environ.get("W3_Q", "0.5"))
    feed_dir = data_dir / "feed"
    feed_dir.mkdir(exist_ok=True)
    with (feed_dir / "feed.jsonl").open("w") as f:  # Friday's public feed, ids moved clear of the simulator's
        for line in MERGED.read_text().splitlines():
            e = json.loads(line)
            e["id"] = int(e["id"]) + 10_000_000
            f.write(json.dumps(e) + "\n")
    loaded = cli._strategy()
    patched = replace(loaded, params=loaded.params.model_copy(update={"ladder_floor_quantile": q}))
    monkeypatch.setattr(cli, "_strategy", lambda: patched)
    from bazaar_agent import strategy as st

    real_db = st.dealer_buy
    seen = []

    def spy(m, case, quote, params, rules):
        seen.append((case.card.ref, params.ladder_floor_quantile, sorted(m.floors)[:3], len(m.floors)))
        return real_db(m, case, quote, params, rules)

    monkeypatch.setattr(st, "dealer_buy", spy)
    print(
        "load_settings data_dir", __import__("bazaar_agent.config", fromlist=["x"]).load_settings().data_dir, data_dir
    )
    Path(os.environ["W3_LOG"]).write_text(
        run("agent", "taker", "--live", "--max-ticks", "40", "--no-jev", "--threads", "1")
    )
    with sim.world.lock:
        threads = [t for t in sim.world.state.threads.values() if t.team == US and t.with_ == "abuela"]
        out = []
        for t in threads:
            offers = sim.world.state.offers
            seq = []
            for m in t.messages:
                o = offers.get(m.offer) if m.offer is not None else None
                price = None
                if o is not None:
                    price = (o.give.cash or o.want.cash) if hasattr(o.give, "cash") else None
                seq.append(("T" if m.sender == US else "D", price))
            out.append(
                {
                    "id": t.id,
                    "topic": t.topic,
                    "status": t.status,
                    "opening": t.neg.opening,
                    "limit": t.neg.limit,
                    "rarity": t.neg.rarity,
                    "item_kind": t.neg.item_kind,
                    "last_team_price": t.neg.last_team_price,
                    "rounds": t.neg.rounds,
                    "seq": seq,
                }
            )
        deals = [{"item": d.item, "price": d.price, "negotiated": d.negotiated} for d in sim.world.team(US).deals]
    Path(os.environ.get("W3_OUT", "/dev/null")).write_text(
        json.dumps({"threads": out, "deals": deals}, indent=1, default=str)
    )
    print("SPY", seen[:3], len(seen))
    assert threads
