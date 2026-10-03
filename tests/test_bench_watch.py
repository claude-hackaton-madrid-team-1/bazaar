"""The read-only Market Test watcher (B20): the stall's key, what one read logs, and the calibration."""

from __future__ import annotations

import json

from bazaar_agent import telemetry as tm
from bazaar_agent.agents.bench_watch import BenchWatch, calibrate, read_rows, snapshot, starter_key
from bazaar_agent.ticks import Clock


def sell(oid: str, quote: int, run: int = 3, **extra: object) -> dict:
    return {"id": oid, "run": run, "give": {"cash": 0, "assets": [{"kind": "card"}]}, "want": {"cash": quote}, **extra}


def buy(oid: str, quote: int, run: int = 3) -> dict:
    return {"id": oid, "run": run, "give": {"cash": quote}, "want": {"cash": 0, "types": ["card:BENCH"]}}


class FakeBroker:
    def __init__(self, books: list[dict]) -> None:
        self.books, self.calls = books, 0

    def book(self) -> dict:
        self.calls += 1
        return self.books.pop(0)

    def match(self, *a: object) -> None:  # pragma: no cover - the watcher must never call it
        raise AssertionError("the watcher sent a match")


def test_the_starter_key_comes_from_me_and_is_scrubbed_from_telemetry():
    key = starter_key({"starter_broker_key": "bk_starter_secret_value_123"})
    assert key is not None and key.get_secret_value() == "bk_starter_secret_value_123"
    assert "bk_starter_secret_value_123" not in tm.scrub("key bk_starter_secret_value_123 here")
    assert starter_key({"venue": None}) is None and starter_key({"starter_broker_key": ""}) is None


def test_a_read_keeps_the_bench_offers_and_any_unknown_field_and_skips_junk():
    row = snapshot(
        {"tick": 300, "bench_offers": [sell("b3-0", 40, expires_tick=305), buy("b3-1", 35), {"id": 7}, "junk"]},
        Clock(tick=300, t_hours=5.0),
    )
    assert row is not None and row["tick"] == 300 and row["t_hours"] == 5.0
    assert row["offers"][0] == {"id": "b3-0", "run": 3, "side": "sell", "quote": 40, "extra": {"expires_tick": 305}}
    assert row["offers"][1] == {"id": "b3-1", "run": 3, "side": "buy", "quote": 35}
    assert snapshot({"bench_offers": []}, Clock(tick=1)) is None


def test_the_watcher_reads_once_a_tick_writes_only_during_a_test_and_never_matches(tmp_path):
    broker = FakeBroker(
        [{"bench_offers": []}, {"bench_offers": [sell("b3-0", 40)]}, {"bench_offers": []}, {"bench_offers": []}]
    )
    lines: list[str] = []
    watch = BenchWatch(broker, tmp_path / "bench_book.jsonl", lines.append)
    for tick in (1, 2, 3, 4):
        watch.on_tick(Clock(tick=tick))
    assert (watch.reads, watch.rows, broker.calls) == (4, 2, 4)
    rows = read_rows(tmp_path / "bench_book.jsonl")
    assert [(r["tick"], len(r["offers"])) for r in rows] == [(2, 1), (3, 0)] and len(lines) == 1
    assert calibrate(rows)["3"]["stays_of_leavers"] == [1]  # the empty read closes the session


def test_calibration_tells_crossed_from_left_and_measures_arrivals_firmness_and_relax(tmp_path):
    def s(oid: str, q: int) -> dict:
        return {"id": oid, "run": 3, "side": "sell", "quote": q}

    def b(oid: str, q: int) -> dict:
        return {"id": oid, "run": 3, "side": "buy", "quote": q}

    reads = [  # rows as `snapshot` logs them
        {"tick": 10, "offers": [s("b3-0", 40), b("b3-1", 30), s("b3-2", 50)]},
        {"tick": 11, "offers": [s("b3-0", 38), b("b3-1", 30), s("b3-2", 50), b("b3-3", 45)]},
        # tick 11's crossing: b3-0 (38) with b3-3 (45); both gone at 12. b3-2 never moves, b3-1 leaves at 13.
        {"tick": 12, "offers": [b("b3-1", 30), s("b3-2", 50)]},
        {"tick": 13, "offers": [s("b3-2", 50), s("b3-4", 40), b("b3-5", 37)]},
        # on the stall a crossed pair is never seen crossing: 40 and 37 vanish together one relax step apart
        {"tick": 14, "offers": [s("b3-2", 50)]},
    ]
    path = tmp_path / "bench_book.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in reads) + "\nnot json\n")
    out = calibrate(read_rows(path))["3"]
    assert out["reads"] == 5 and out["traders"] == 6 and out["sells"] == 3
    assert out["arrivals"] == [0, 0, 0, 1, 3, 3]
    assert out["crossed"] == 4 and out["stays_of_leavers"] == [3]
    assert out["firm_share"] == round(2 / 3, 3)  # b3-1 and b3-2 never moved; b3-0 relaxed 40 -> 38
    assert calibrate(read_rows(path), tolerance=0.0)["3"]["stays_of_leavers"] == [1, 1, 3]
    assert out["mean_relax_step"] == 2.0 and out["extra_fields"] == []
