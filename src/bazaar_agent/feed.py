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
    """One JSONL file of feed events, deduplicated by id. Poll windows append in id order; the live
    stream appends each event as it arrives, so a file may be locally out of order (readers sort)."""

    def __init__(self, directory: Path, filename: str = FEED_FILE) -> None:
        self.path = directory / filename
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

    def add(self, events: Iterable[Event]) -> list[Event]:
        """Append the events whose id we do not hold yet; returns them (the new ones), in id order."""
        known = self._known_ids()
        fresh: dict[int, Event] = {}
        for e in events:
            if e["id"] not in known:
                fresh.setdefault(e["id"], e)
        out = [fresh[i] for i in sorted(fresh)]
        if out:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for event in out:
                    handle.write(json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n")
            known.update(fresh)
        return out

    def append(self, window: Iterable[Event], window_limit: int) -> CaptureResult:
        result, _ = self.capture(window, window_limit)
        return result

    def capture(
        self, window: Iterable[Event], window_limit: int, reach: int | None = None
    ) -> tuple[CaptureResult, list[Event]]:
        """A poll window: the new events, and whether a full window failed to reach back to `reach`
        (default: the newest id held). The live stream passes the previous poll's newest id: events
        it delivered after a drop must not hide a hole the stream left behind."""
        batch = sorted(window, key=lambda e: e["id"])
        reach = self.newest_id() if reach is None else reach
        fresh = self.add(batch)
        full_window = len(batch) >= window_limit
        gap = bool(full_window and reach is not None and batch and batch[0]["id"] > reach)
        return CaptureResult(len(batch), len(fresh), self.newest_id(), gap), fresh


def load_events(store: FeedStore, live_window: list[Event] | None = None) -> list[Event]:
    """Captured events, merged with a live window when given (live wins on duplicate ids)."""
    merged = {e["id"]: e for e in store.events()}
    for event in live_window or []:
        merged[event["id"]] = event
    return [merged[i] for i in sorted(merged)]
