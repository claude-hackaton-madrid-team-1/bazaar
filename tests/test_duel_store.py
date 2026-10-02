"""The duel runner's Postgres writer, without a database: rows, JSONL import, ended duels, outages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg
import pytest

from bazaar_agent.duel_store import DuelStore, _row, duel_list, jsonl_duels

DONE = {
    "duel": 85,
    "session": 1,
    "status": "deal",
    "role": "seller",
    "item": "Taxi Blanco",
    "your_limit": 109,
    "rival": "Rival Azul",
    "deadline_tick": 156,
    "rounds": 7,
    "decay_per_round": 0.06,
    "price": 138,
    "days": None,
    "result": 18.8,
    "messages": [{"from": "Rival Azul", "text": "a key tk-team1-very-secret-0042 for you", "price": 114}],
}


def test_a_row_keeps_the_columns_the_evals_read_and_scrubs_the_payload() -> None:
    row = _row(DONE, None)
    assert row is not None
    assert row[:14] == (85, 1, 156, "deal", "seller", "Taxi Blanco", 109, "Rival Azul", 156, 7, 0.06, 138, None, 18.8)
    assert "tk-team1-very-secret-0042" not in row[14]  # a team-key shape in rival text never lands in Postgres
    assert json.loads(row[14])["price"] == 138
    assert _row({"status": "live"}, 3) is None  # no duel id


def test_a_live_payload_is_stored_at_the_tick_it_was_seen() -> None:
    row = _row({"duel": 5, "status": "live", "deadline_tick": 132}, 120)
    assert row is not None and (row[2], row[3]) == (120, "live")


def test_jsonl_logs_yield_each_response_and_skip_moves_and_junk(tmp_path: Path) -> None:
    log = tmp_path / "duels.jsonl"
    lines = [
        {"tick": 120, "response": {"duels": [{"duel": 5, "status": "live"}, "junk"]}},
        {"tick": 121, "duel": 5, "move": {"kind": "offer", "price": 70}},
        {"tick": 122, "response": {"duels": []}},
        {"tick": 123, "response": {"duels": 1}},
        {"tick": True, "response": {"duels": []}},
        ["not", "an", "object"],
    ]
    log.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8")
    assert list(jsonl_duels(log)) == [(120, [{"duel": 5, "status": "live"}]), (122, []), (123, [])]


def test_finished_duels_are_read_after_a_restart_and_when_one_leaves_the_live_list() -> None:
    store = DuelStore(None, lambda m: None)
    assert store.read_finished([{"duel": 1}, {"duel": 2}]) is True  # first tick: catch up on downtime
    assert store.read_finished([{"duel": 1}, {"duel": 2}]) is False
    assert store.read_finished([{"duel": 2}]) is True  # duel 1 ended
    assert store.read_finished([{"duel": 2}, {"duel": 3}]) is False  # a new duel is not an ended one


@pytest.mark.parametrize("response", [{"duels": 1}, {"duels": None}, {"duels": "x"}, None, []])
def test_a_malformed_duel_list_reads_as_no_duels(response: object) -> None:
    assert duel_list(response) == []


def test_a_duel_list_keeps_only_objects() -> None:
    assert duel_list({"duels": [1, {"duel": 2}]}) == [{"duel": 2}]


def test_without_postgres_the_store_writes_nothing_and_never_raises() -> None:
    logs: list[str] = []
    calls = {"n": 0}

    def broken() -> psycopg.Connection:
        calls["n"] += 1
        raise psycopg.OperationalError("no route")

    store = DuelStore(broken, logs.append)
    assert store.save(100, [DONE]) == 0
    assert store.save(101, [DONE]) == 0  # skipped: retried only after a few ticks
    assert store.save(105, [DONE]) == 0
    assert calls["n"] == 2 and len(logs) == 1 and "JSONL only" in logs[0]
    store.close()


class _FakeConn:
    def __init__(self, fail_on_write: bool) -> None:
        self.fail, self.closed, self.rows = fail_on_write, False, []

    def execute(self, *args: Any) -> None:
        return None

    def commit(self) -> None:
        return None

    def cursor(self) -> _FakeConn:
        return self

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def executemany(self, query: str, rows: list[Any]) -> None:
        if self.fail:
            raise psycopg.errors.UndefinedTable("duels does not exist")
        self.rows.extend(rows)

    def close(self) -> None:
        self.closed = True


def test_a_failed_write_drops_the_connection_and_the_loop_goes_on() -> None:
    logs: list[str] = []
    conn = _FakeConn(fail_on_write=True)
    store = DuelStore(lambda: conn, logs.append)  # type: ignore[arg-type, return-value]
    assert store.save(100, [DONE]) == 0
    assert conn.closed and "write failed" in logs[0]


def test_a_write_stores_one_row_per_duel() -> None:
    conn = _FakeConn(fail_on_write=False)
    store = DuelStore(lambda: conn, lambda m: None)  # type: ignore[arg-type, return-value]
    assert store.save(160, [DONE, {"duel": 86, "status": "live"}], finished=False) == 2
    assert [r[0] for r in conn.rows] == [85, 86]
    assert store.save(160, [DONE], finished=True) == 1 and conn.rows[-1][2] == 156  # finished: its deadline
