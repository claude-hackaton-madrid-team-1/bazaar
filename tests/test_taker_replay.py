"""B28: the taker's counterfactual. The CURRENT taker, live against fake clients built from a feed, settling into
a simulated wallet: what it buys, what each guardrail refuses, the duel slot, and the what-ifs."""

import json
from copy import deepcopy

from typer.testing import CliRunner

from bazaar_agent import taker_replay as tr
from bazaar_agent.cli import app
from bazaar_agent.guardrails import Guardrails
from tests.test_arb import an_ask
from tests.test_strategy import CATALOG, ME, PARAMS


def feed():
    return [
        an_ask(1, 1, 10, "t05", "LAV-02", 10, 501),  # missing page card: 10 + fee 2 against 16 (+ bonus share)
        an_ask(2, 1, 11, "t06", "LAV-01", 2, 502),  # we hold LAV-01: block_buying_held_cards
        an_ask(3, 2, 12, "t07", "LAV-08", 26, 503, rarity="uncommon"),  # 26 + fee 3 > max_price_uncommon 26
        an_ask(4, 3, 13, "t08", "LAT-09", 5, 504, rarity="rare"),  # held (LAT-09): refused too
        {"id": 5, "tick": 6, "type": "clock", "payload": {}},  # the day runs to tick 6
    ]


def me(cash=400):
    return {**deepcopy(ME), "cash": cash}


def test_it_buys_what_the_taker_would_and_settles_it_into_the_wallet():
    r = tr.run(feed(), me(), CATALOG, Guardrails(), PARAMS, label="t")
    (b,) = r.bought
    assert (b.tick, b.ref, b.ask, b.fee, b.copy_value) == (1, "LAV-02", 10, 2, 16.0)
    assert b.surplus == 4.0 and b.strategy_value > b.copy_value  # the taker also counts the page-bonus share
    assert (r.cash_start, r.cash_end, r.spent) == (400, 388, 12)
    assert r.blocked["max_price_uncommon"] == 1


def test_the_cash_floor_refuses_once_the_wallet_reaches_it():
    r = tr.run(feed(), me(cash=281), CATALOG, Guardrails(), PARAMS)
    assert r.bought == () and r.blocked.get("cash_floor") == 2  # LAV-02: 281 − 12 < 270 (and LAV-08)


def test_a_duel_takes_the_slot_and_the_taker_buys_the_next_tick():
    r = tr.run(feed(), me(), CATALOG, Guardrails(), PARAMS, duel_ticks=[1])
    assert [b.tick for b in r.bought] == [2] and r.duel_slots == 1


def test_variants_change_one_thing_at_a_time_and_render():
    labels = [label for label, _, _ in tr.variants(Guardrails(), PARAMS)]
    assert labels[0] == "current rules and settings" and "no cash_floor" in labels
    results = [tr.run(feed(), me(), CATALOG, r, p, label=label) for label, r, p in tr.variants(Guardrails(), PARAMS)]
    by = {r.label: r for r in results}
    assert by["no price caps"].blocked.get("max_price_uncommon") is None and len(by["no price caps"].bought) == 2
    assert len(by["min_buy_surplus 6"].bought) < len(by["current rules and settings"].bought) + 1
    text = tr.render(results, "x")
    assert "| current rules and settings | 1 | 12 | +4 |" in text


def test_the_album_at_the_start_of_the_day():
    events = [
        {"id": 1, "tick": 5, "type": "settlement", "payload": {"items": [{"id": 1, "ref": "LAV-01", "to": "t01"}]}},
    ]
    start = tr.start_of_day(me(), events, 400)
    assert all(a.get("id") != 1 for a in start["assets"]) and start["cash"] == 400


def test_the_cli(tmp_path):
    stream = tmp_path / "stream.jsonl"
    stream.write_text("\n".join(json.dumps(e) for e in feed()))
    (tmp_path / "me.json").write_text(json.dumps({"body": me()}))
    (tmp_path / "catalog.json").write_text(json.dumps({"body": CATALOG}))
    args = ["taker-replay", str(stream), "--me", str(tmp_path / "me.json"), "--catalog", str(tmp_path / "catalog.json")]
    out = CliRunner().invoke(app, [*args, "--current-only", "-v"])
    assert out.exit_code == 0, out.output
    assert "| current rules and settings | 1 | 12 |" in out.stdout and "tick 1 LAV-02" in out.stdout
