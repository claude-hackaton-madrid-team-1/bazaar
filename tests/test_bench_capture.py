"""The bench book capture: every raw offer is kept once per tick, off the tick, failing open. No network."""

import json
import time
from copy import deepcopy

import psycopg
import pytest

from bazaar_agent.agents.bench_capture import EVIDENCE_FILE, FILE_NAME, BenchBooks, rows_for, run_of, side_and_quote
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import clock
from tests.test_broker_agent import FakeBroker, agent
from tests.test_matcher import bench_buy, bench_sell

SELL = {
    "id": "b35-17",
    "maker": "bench",
    "give": {"cash": 0, "assets": []},
    "want": {"cash": 28, "types": []},
    "expires_tick": 460,
}
BUY = {"id": "b35-4", "give": {"cash": 76}, "want": {"cash": 0, "types": []}, "created_tick": 441, "extra": {"x": 1}}


class FakeConn:
    closed = False
    autocommit = False

    def __init__(self, sink, fail=False):
        self.sink, self.fail = sink, fail

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        if self.fail:
            raise psycopg.OperationalError("password=hunter2 host=secret")

    def cursor(self):
        return self

    def executemany(self, sql, rows):
        self.sink.extend(rows)


def test_side_quote_and_run():
    assert side_and_quote(SELL) == ("sell", 28)
    assert side_and_quote(BUY) == ("buy", 76)
    assert run_of("b35-17") == "b35"


def test_rows_keep_every_raw_field_and_skip_junk():
    rows = rows_for("real", 442, [SELL, BUY, {"nope": 1}, "x"], "v19", 100, 2)
    assert [r[3] for r in rows] == ["b35-17", "b35-4"]
    assert rows[1][:9] == ("real", "b35", 442, "b35-4", "buy", 76, "v19", 100, 2)
    assert rows[1][9]["extra"] == {"x": 1} and rows[0][9]["expires_tick"] == 460


def test_record_writes_jsonl_and_rows(tmp_path):
    sink: list = []
    lines: list[str] = []
    books = BenchBooks(lambda: FakeConn(sink), tmp_path, lines.append, venue="v19", inline=True)
    books.record(442, [SELL, BUY], 100, 2)
    books.record(443, [], 100, 2)  # no Market Test: nothing written
    written = [json.loads(x) for x in (tmp_path / FILE_NAME).read_text().splitlines()]
    assert len(written) == 1 and written[0]["tick"] == 442 and written[0]["offers"][1]["extra"] == {"x": 1}
    assert len(sink) == 2 and books.stored == 2 and not lines


def test_lone_surrogate_and_nul_do_not_break_the_capture(tmp_path):
    sink: list = []
    odd = {**SELL, "note": "a\ud83d\x00b"}
    books = BenchBooks(lambda: FakeConn(sink), tmp_path, inline=True)
    books.record(1, [odd])
    assert len(sink) == 1 and "\x00" not in sink[0][9].obj["note"]
    assert (tmp_path / FILE_NAME).read_text()


def test_a_dead_database_fails_open_once_and_never_leaks_its_text(tmp_path):
    lines: list[str] = []

    def dead():
        raise psycopg.OperationalError("postgresql://u:hunter2@secret-host/db")

    books = BenchBooks(dead, tmp_path, lines.append, inline=True)
    for tick in range(5):
        books.record(tick, [SELL])
    assert len(lines) == 1 and "hunter2" not in lines[0] and "OperationalError" in lines[0]
    assert len((tmp_path / FILE_NAME).read_text().splitlines()) == 5  # the file goes on


def test_a_failed_write_is_logged_once_and_reconnects(tmp_path):
    sink: list = []
    lines: list[str] = []
    conns = [FakeConn(sink, fail=True), FakeConn(sink)]
    books = BenchBooks(lambda: conns.pop(0), tmp_path, lines.append, inline=True)
    books.record(1, [SELL])
    books.record(2, [SELL])
    assert len(lines) == 2 and "hunter2" not in "".join(lines)  # the failure, then "writes are back"
    assert "back" in lines[1] and len(sink) == 1


def test_the_worker_thread_writes_off_the_tick(tmp_path):
    sink: list = []
    books = BenchBooks(lambda: FakeConn(sink), tmp_path, venue="v19")
    books.record(5, [SELL, BUY])
    for _ in range(200):
        if sink:
            break
        time.sleep(0.01)
    assert len(sink) == 2


def test_no_file_dir_no_database_is_a_noop():
    BenchBooks(None, None).record(1, [SELL])


def test_evidence_preserves_full_payload_in_file_and_database(tmp_path):
    sink = []
    books = BenchBooks(lambda: FakeConn(sink), tmp_path, venue="v19", inline=True)
    payload = {"settlement": {"sell": "b35-17", "details": "x" * 1500}}
    books.evidence(450, "settlement", payload)
    saved = json.loads((tmp_path / EVIDENCE_FILE).read_text())
    assert saved["kind"] == "settlement" and saved["payload"] == payload
    assert sink[0][0:4] == ("real", "v19", 450, "settlement")
    assert sink[0][-1].obj == payload


def test_exact_broker_captures_settlement_empty_book_and_official_session_result(tmp_path):
    broker = FakeBroker(bench=[bench_sell("b1-1", 20), bench_buy("b1-2", 40)])
    result = {
        "id": 17,
        "type": "bench.finished",
        "tick": 8,
        "payload": {"session": 1, "venue": "v19", "efficiency": 0.854, "auto_baseline": 0.854},
    }
    a = agent(tmp_path, broker, live=True, allow_venue_open=True)
    a.on_tick(clock(7))
    settlement = {"sell": "b1-1", "buy": "b1-2", "price": 30, "detail": "x" * 1500}
    original = broker.book
    broker.book = lambda: {**original(), "settlements": [settlement]}
    a.on_tick(clock(8), events=[result])
    a.on_tick(clock(9), events=[result])
    evidence = [json.loads(line) for line in (tmp_path / "agents" / EVIDENCE_FILE).read_text().splitlines()]
    assert [row["payload"]["offers"] for row in evidence if row["kind"] == "book"][-1] == []
    assert [row["payload"] for row in evidence if row["kind"] == "settlement"] == [{"settlement": settlement}]
    assert [row["payload"] for row in evidence if row["kind"] == "session"] == [result]
    response = next(row["payload"] for row in evidence if row["kind"] == "match_response")
    assert response["request"] == {"sell": "b1-1", "buy": "b1-2", "price": 30}
    assert response["state"] == "queued_or_acknowledged"


def test_the_broker_records_the_book_it_read_without_a_second_request(tmp_path):
    broker = FakeBroker(bench=[deepcopy(bench_sell("b1-1", 20)), bench_buy("b1-2", 40)])
    reads = []
    orig = broker.book
    broker.book = lambda: reads.append(1) or orig()  # type: ignore[method-assign]
    a = agent(tmp_path, broker)
    a.on_tick(clock(7))
    assert len(reads) == 1
    row = json.loads((tmp_path / "agents" / FILE_NAME).read_text().splitlines()[0])
    assert row["tick"] == 7 and {o["id"] for o in row["offers"]} == {"b1-1", "b1-2"}


@pytest.mark.parametrize("status,state", [(400, "refused"), (408, "unknown"), (503, "unknown")])
def test_match_evidence_distinguishes_refusal_from_uncertainty(tmp_path, status, state):
    class FailingBroker(FakeBroker):
        def match(self, sell, buy, price):
            raise BazaarError("failed", "no definite result", status)

    broker = FailingBroker(bench=[bench_sell("b1-1", 20), bench_buy("b1-2", 40)])
    agent(tmp_path, broker, live=True, allow_venue_open=True).on_tick(clock(7))
    evidence = [json.loads(line) for line in (tmp_path / "agents" / EVIDENCE_FILE).read_text().splitlines()]
    response = next(row["payload"] for row in evidence if row["kind"] == "match_response")
    assert response["state"] == state
    assert response["http_status"] == status


def test_an_unwritable_stats_dir_never_stops_the_tick(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("a file, not a directory")
    lines: list[str] = []
    broker = FakeBroker(bench=[bench_sell("b1-1", 20), bench_buy("b1-2", 40)])
    a = agent(tmp_path, broker, lines=lines)
    a.books = BenchBooks(None, blocker / "agents", lines.append)
    a.on_tick(clock(7))
    assert any("bench books: file failed" in x for x in lines)
