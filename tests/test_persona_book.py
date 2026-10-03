"""agents.persona_book: personas parsed every tick, stored off the tick on change."""

from __future__ import annotations

import copy
from typing import Any

from bazaar_agent.agents.persona_book import PersonaBook
from bazaar_agent.monitor import TraderSnapshot

ABUELA: dict[str, Any] = {
    "id": "abuela",
    "status": "active",
    "level": 1,
    "traits": {"patience": 0.85, "generosity": 0.8, "shrewdness": 0.2},
    "menu": {"sells": [{"rarity": "uncommon", "list_price": 25}], "deals_per_team_per_hour": 8},
    "unlock": {"always": True},
    "open_to_all": True,
}
CHATO: dict[str, Any] = {
    "id": "chato",
    "status": "active",
    "level": 2,
    "traits": {"shrewdness": 0.85, "strictness": 0.85},
    "menu": {"sells": [{"rarity": "rare", "list_price": 77}]},
    "unlock": {"early_deals_with": "abuela", "early_min_deals": 3},
}


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[list[TraderSnapshot], int]] = []
        self.fail = fail

    def __call__(self, snaps: list[TraderSnapshot], tick: int) -> None:
        self.calls.append((snaps, tick))
        if self.fail:
            raise RuntimeError("db down")


def join(book: PersonaBook) -> None:
    if book._worker is not None:
        book._worker.join(1)


def observe(book: PersonaBook, dealers: list[Any], tick: int) -> None:
    book.observe(dealers, tick)
    join(book)


def changed_menu() -> list[dict[str, Any]]:
    a = copy.deepcopy(ABUELA)
    a["menu"]["sells"][0]["list_price"] = 24
    return [a]


def test_observe_parses_the_personas() -> None:
    book = PersonaBook(None, lambda _m: None)
    out = book.observe([ABUELA, "junk", CHATO], 0)
    assert sorted(out) == ["abuela", "chato"]
    assert out["abuela"].traits.patience == 0.85
    assert book.personas is out


def test_first_observation_is_stored_once() -> None:
    rec = Recorder()
    book = PersonaBook(rec, lambda _m: None)
    observe(book, [ABUELA], 5)
    assert len(rec.calls) == 1
    snaps, tick = rec.calls[0]
    assert tick == 5 and [s.trader_id for s in snaps] == ["abuela"]


def test_same_payload_is_not_stored_again() -> None:
    rec = Recorder()
    book = PersonaBook(rec, lambda _m: None)
    for tick in (0, 1, 15, 40):
        observe(book, [ABUELA], tick)
    assert len(rec.calls) == 1


def test_a_menu_change_waits_for_the_store_interval() -> None:
    rec = Recorder()
    book = PersonaBook(rec, lambda _m: None)
    observe(book, [ABUELA], 0)
    for tick in (3, 9):
        observe(book, changed_menu(), tick)
    assert len(rec.calls) == 1
    assert book.personas["abuela"].list_price("uncommon") == 24  # parsed at once, only the store waits
    observe(book, changed_menu(), 10)
    assert [t for _, t in rec.calls] == [0, 10]


def test_a_new_persona_is_stored_at_once() -> None:
    rec = Recorder()
    logs: list[str] = []
    book = PersonaBook(rec, logs.append)
    observe(book, [ABUELA], 0)
    observe(book, [ABUELA, CHATO], 1)
    assert [t for _, t in rec.calls] == [0, 1]
    assert any("chato" in m for m in logs)


def test_a_failing_writer_only_logs() -> None:
    rec = Recorder(fail=True)
    logs: list[str] = []
    book = PersonaBook(rec, logs.append)
    out = book.observe([ABUELA], 3)
    join(book)
    assert "abuela" in out
    assert len(rec.calls) == 1
    assert any("not stored" in m and "RuntimeError" in m for m in logs)
    assert not any("db down" in m for m in logs)


def test_no_writer_never_spawns_a_thread() -> None:
    book = PersonaBook(None, lambda _m: None)
    book.observe([ABUELA], 0)
    book.observe([ABUELA, CHATO], 20)
    assert book._worker is None
