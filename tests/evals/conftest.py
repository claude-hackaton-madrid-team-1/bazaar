"""Real data from the practice evening (2026-10-02), exported from our Postgres and `/api/duels?done=true`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bazaar_agent.evals.dealers import CurveRow

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "evals"
OURS = "t01"
LEVELS = {"abuela": 1, "chato": 2}


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="session")
def done_duels() -> dict[int, dict[str, Any]]:
    """Our 26 practice duels as `/api/duels?done=true` returned them (6 still `live`: doors closed)."""
    return {d["duel"]: d for d in load("duels_done.json")["duels"]}


@pytest.fixture(scope="session")
def feed_ours() -> list[dict[str, Any]]:
    """Our dealer threads 85/99/101/110/115/187, our settlements, day.opened and our duel.closed events."""
    events: list[dict[str, Any]] = load("feed_ours.json")["events"]
    return events


@pytest.fixture(scope="session")
def curve_rows() -> list[CurveRow]:
    """Every team's Abuela and Chato card threads (`dealer_curves`, tick 159)."""
    return [CurveRow(*row) for row in load("dealer_curves.json")["rows"]]
