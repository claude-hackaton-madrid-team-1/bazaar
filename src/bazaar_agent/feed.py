"""Capture the public feed to append-only JSONL, once per tick.

`GET /api/feed` has no cursor, only `limit`: once the public history outgrows the window, older
events are gone. So we capture from the start and flag a possible gap whenever a full window
no longer reaches back to the newest event we already hold.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FEED_FILE = "feed.jsonl"
DEFAULT_WINDOW = 500  # the server caps a feed read at 500 events whatever `limit` asks

Event = dict[str, Any]


@dataclass(frozen=True)
class CaptureResult:
    fetched: int
    new: int
    newest_id: int | None
    gap_possible: bool


class FeedStore:
    """One JSONL file of feed events, ordered by id, deduplicated on append."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / FEED_FILE
        self._ids: set[int] | None = None

    def _known_ids(self) -> set[int]:
        if self._ids is None:
            self._ids = {e["id"] for e in self.events()}
        return self._ids

    def events(self) -> Iterator[Event]:
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    def newest_id(self) -> int | None:
        ids = self._known_ids()
        return max(ids) if ids else None

    def append(self, window: Iterable[Event], window_limit: int) -> CaptureResult:
        batch = sorted(window, key=lambda e: e["id"])
        known = self._known_ids()
        newest_before = max(known) if known else None
        fresh = [e for e in batch if e["id"] not in known]
        if fresh:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for event in fresh:
                    handle.write(json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n")
            known.update(e["id"] for e in fresh)
        full_window = len(batch) >= window_limit
        gap = bool(full_window and newest_before is not None and batch and batch[0]["id"] > newest_before)
        return CaptureResult(len(batch), len(fresh), self.newest_id(), gap)


def load_events(store: FeedStore, live_window: list[Event] | None = None) -> list[Event]:
    """Captured events, merged with a live window when given (live wins on duplicate ids)."""
    merged = {e["id"]: e for e in store.events()}
    for event in live_window or []:
        merged[event["id"]] = event
    return [merged[i] for i in sorted(merged)]
