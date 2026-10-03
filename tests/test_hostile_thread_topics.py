"""Another team's malformed thread topic or offer must never stop a tick (found by the #193 audit)."""

from __future__ import annotations

from typing import Any

import pytest

from bazaar_agent import affinity, intel

GOOD: dict[str, Any] = {
    "type": "thread.opened",
    "tick": 5,
    "payload": {"team": "t07", "kind": "persona", "topic": {"buy": {"card": "LAT-09"}}},
}

HOSTILE: list[dict[str, Any]] = [
    {"type": "thread.opened", "tick": 1, "payload": {"team": "t08", "topic": {"buy": "LAT-09"}}},
    {"type": "thread.opened", "tick": 2, "payload": {"team": "t08", "topic": "buy LAT-09"}},
    {"type": "thread.opened", "tick": 3, "payload": {"team": "t08", "topic": {"buy": {"card": "LAT-09"}, "sell": "x"}}},
    {"type": "offer.listed", "tick": 4, "actor": "t09", "payload": {"offer": {"give": "cash", "want": ["LAT-09"]}}},
]


@pytest.mark.parametrize("bad", HOSTILE, ids=["buy-str", "topic-str", "sell-str", "offer-shapes"])
def test_team_flows_skips_a_malformed_event_and_keeps_the_rest(bad: dict[str, Any]) -> None:
    flows = {f.team: f for f in intel.team_flows([bad, GOOD])}
    assert flows["t07"].set_interest["LAT"] == 1


@pytest.mark.parametrize("bad", HOSTILE, ids=["buy-str", "topic-str", "sell-str", "offer-shapes"])
def test_affinity_signals_skip_a_malformed_event_and_keep_the_rest(bad: dict[str, Any]) -> None:
    sigs = affinity.signals([bad, GOOD])
    assert any(s.team == "t07" and s.kind == "topic" for s in sigs)
