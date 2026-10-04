"""The calibration script's pure sections, on tiny fixtures (no database, no network)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sim_calibrate", ROOT / "scripts" / "sim_calibrate.py")
assert spec and spec.loader
cal = importlib.util.module_from_spec(spec)
sys.modules["sim_calibrate"] = cal
spec.loader.exec_module(cal)

CARDS = {
    "LAV-03": {"rarity": "common", "book": 10, "set": "LAV"},
    "SAL-09": {"rarity": "rare", "book": 70, "set": "SAL"},
}


def msg(i: int, thread: int, sender: str, with_: str, cash: int, final: bool = False, ref: str = "LAV-03") -> dict:
    asset = {"id": 1, "ref": ref, "rarity": CARDS[ref]["rarity"], "kind": "card"}
    if sender == with_:
        offer = {"give": {"cash": 0, "assets": [asset]}, "want": {"cash": cash, "assets": []}, "final": final}
    else:
        offer = {"give": {"cash": cash, "assets": []}, "want": {"cash": 0, "assets": [asset]}, "final": False}
    return {
        "id": i,
        "tick": i,
        "actor": sender,
        "payload": {"thread": thread, "sender": sender, "with": with_, "offer": offer},
    }


def test_quantiles_and_empty():
    q = cal.quantiles([1, 2, 3, 4, 5])
    assert (q["n"], q["min"], q["p50"], q["max"]) == (5, 1, 3, 5)
    assert cal.quantiles([])["p50"] is None


def test_plus_hours_and_schedule_ticks():
    assert cal._plus_hours("+2.63h") == 2.63 and cal._plus_hours(None) is None
    s = cal.schedule_section([{"at_hours": 14.65, "action": "bench"}, {"at_hours": 13.0, "action": "x"}])
    first, second = s["upcoming"]
    assert first["ticks_from_open"] == round((14.65 - cal.OPEN_HOURS) * 240) and first["before_open"]
    assert second["before_open"]
    later = cal.schedule_section([{"at_hours": 18.65, "action": "duels"}])["upcoming"][0]
    assert later["ticks_from_open"] == 480 and not later["before_open"]


def test_news_cadence_is_the_median_gap():
    n = cal.news_section([{"at_hours": 1.0, "headline": "a"}, {"at_hours": 2.0, "headline": "b"}, {"at_hours": 4.0}])
    assert n["cadence_hours"] == 1.5 and [i["at_hours"] for i in n["items"]] == [1.0, 2.0, 4.0]


def test_dealer_curve_and_fake_final_detection():
    opened = [
        {
            "payload": {
                "kind": "persona",
                "team": "t02",
                "with": "picaros",
                "topic": {"buy": {"card": "LAV-03"}},
                "thread": 7,
            }
        }
    ]
    msgs = [
        msg(1, 7, "picaros", "picaros", 12),
        msg(2, 7, "t02", "picaros", 6),
        msg(3, 7, "picaros", "picaros", 10, True),
        msg(4, 7, "picaros", "picaros", 8),
    ]
    infos = cal.thread_infos(opened, msgs)
    dyn = cal.thread_dynamics(msgs, infos)
    assert dyn[7]["final_at"] == 1 and dyn[7]["after_final"] == [8]
    curves = [{"thread_id": 7, "dealer": "picaros", "opening_ask": 12, "outcome": "deal", "fill_price": 8, "steps": 3}]
    api = {
        "picaros": {"menu": {"sells": [{"rarity": "common", "list_price": 10}]}, "unlock": {"open_to_all_at": "+8.67h"}}
    }
    d = cal.dealer_section(curves, infos, dyn, CARDS, api)["picaros"]
    assert d["open_mult"] == {"common": 1.2} and d["fill_ratio"]["common"]["p50"] == 0.8
    assert d["fake_final"]["rate"] == 1.0 and d["unlock"]["open_to_all_at_hours"] == 8.67
    assert d["walk_rate"] == 0.0


def test_rivals_rates_exclude_us_and_friday():
    def listed(i, tick, who, give, want):
        offer = {"id": i, "give": give, "want": want, "created_tick": tick, "expires_tick": tick + 20}
        return {"id": i, "tick": tick, "actor": who, "payload": {"offer": offer, "venue": "rastro"}}

    ask_give = {"cash": 0, "assets": [{"ref": "LAV-03", "rarity": "common"}]}
    listings = [
        listed(1, 200, "t05", ask_give, {"cash": 12, "types": []}),
        listed(2, 201, "t01", ask_give, {"cash": 12, "types": []}),
        listed(3, 50, "t05", ask_give, {"cash": 12, "types": []}),
        listed(4, 201, "t06", {"cash": 5, "assets": []}, {"cash": 0, "types": ["card:LAV-03"]}),
    ]
    sett = [
        {
            "id": 9,
            "tick": 202,
            "actor": "",
            "payload": {"persona": None, "venue": "rastro", "items": [{"frm": "t05", "to": "t06"}]},
        }
    ]
    out = cal.rivals_section(listings, [], sett, [], [], CARDS, [])
    assert out["ask_over_book"]["common"]["p50"] == 1.2 and out["bid_over_book"]["common"]["p50"] == 0.5
    assert out["listings_per_tick"]["ticks"] == 42 and out["n_teams"] == 2
    assert out["reciprocal_pairs"][0] == {"a": "t05", "b": "t06", "trades": 1, "a_to_b": 1, "b_to_a": 0}
    assert out["accept_rate"] == round(1 / 2, 3)


def test_output_is_deterministic_and_secret_free():
    doc = {"b": 1.23456, "a": [1, 2]}
    assert cal.dumps(doc) == cal.dumps(json.loads(cal.dumps(doc)))
    committed = (ROOT / "src" / "bazaar_sim" / "data" / "sunday.json").read_text(encoding="utf-8")
    assert "postgres" not in committed and "X-Team-Key" not in committed
    data = json.loads(committed)
    assert data["version"] == 1 and {"clock", "schedule", "dealers", "rivals", "duels"} <= set(data)
