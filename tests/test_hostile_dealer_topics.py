"""A dealer thread topic of any shape never breaks the dealer-thread reader (#198 review follow-up)."""

from __future__ import annotations

from typing import Any

import pytest

from bazaar_agent import intel

DEALER_HOSTILE: list[Any] = [{"sell": {"assets": 12}}, {"buy": {"assets": None}}, "buy LAT-09", ["LAT-09"]]


@pytest.mark.parametrize("topic", DEALER_HOSTILE, ids=["assets-int", "assets-null", "topic-str", "topic-list"])
def test_dealer_threads_read_any_topic_shape_as_unknown(topic: Any) -> None:
    bad = {
        "type": "thread.opened",
        "tick": 1,
        "payload": {"thread": 9, "kind": "persona", "team": "t08", "with": "abuela", "topic": topic},
    }
    good = {
        "type": "thread.opened",
        "tick": 2,
        "payload": {
            "thread": 10,
            "kind": "persona",
            "team": "t07",
            "with": "abuela",
            "topic": {"buy": {"card": "LAT-09"}},
        },
    }
    threads = {t.thread: t for t in intel.dealer_threads([bad, good])}
    assert threads[10].item == "LAT-09"
    assert threads[9].item in ("?", "assets:")
