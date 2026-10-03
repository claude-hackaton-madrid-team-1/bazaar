from rich.console import Console

from bazaar_agent import intel, render
from bazaar_agent.ticks import Clock
from tests.test_intel import EVENTS


def text(table) -> str:
    console = Console(width=200, record=True)
    console.print(table)
    return console.export_text()


def test_every_table_renders_real_rows():
    threads = intel.dealer_threads(EVENTS)
    assert "sobre_barrio" in text(render.curves_table(intel.curve_summary(threads)))
    assert "12 15" in text(render.threads_table(threads, 5))
    assert "LAT-03" in text(render.tape_table(intel.tape(EVENTS), 10))
    assert "t06" in text(render.teams_table(intel.team_flows(EVENTS)))
    book = intel.order_book(
        [{"id": 123, "maker": "x", "give": {"assets": [{"ref": "LAT-05"}]}, "want": {"cash": 10}}],
        intel.listed_makers(EVENTS),
    )
    assert "t06" in text(render.book_table(book, "rastro"))
    clock = Clock.model_validate({"tick": 5, "next_tick_in": 30, "tick_seconds": 60})
    assert "action budget now" in text(render.clock_table(clock))
    dealer = {
        "id": "abuela",
        "name": "Abuela Carmen",
        "status": "active",
        "level": 1,
        "traits": {"patience": 0.85},
        "menu": {
            "sells": [{"pack": "sobre_barrio", "list_price": 26, "opening_ask": 30}],
            "deals_per_team_per_hour": 8,
        },
    }
    assert "sobre_barrio@26 (open 30)" in text(render.dealers_table([dealer]))
    me = {
        "name": "Team 1",
        "cash": 400,
        "level": 1,
        "score": {"score": 3.2, "rank": 4},
        "assets": [
            {
                "id": 9,
                "kind": "card",
                "ref": "LAV-01",
                "name": "Lavapiés 1",
                "rarity": "common",
                "serial": 4,
                "print_run": 300,
                "your_value": 16.0,
            }
        ],
    }
    assert "400" in text(render.status_table(me))
    assert "LAV-01" in text(render.cards_table(me))
    shown = text(render.status_table(me, "target: SIMULATOR https://bazaar-sim.example (BAZAAR_SIM, key sim-...)"))
    assert "target" in shown and "SIMULATOR https://bazaar-sim.example" in shown


def test_status_shows_the_score_parts_the_saturday_gates_read():
    me = {"cash": 353, "level": 2, "score": {"score": 8.34, "ladder_points": 0.058, "bench_points": None}}
    shown = text(render.status_table(me))
    assert "ladder_points" in shown and "0.058" in shown and "bench_points" in shown
    assert "duel_points" not in shown  # only the parts /me sends
