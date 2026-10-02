"""The monitoring agent's logic: keep our memory current and raise alerts, in real time.

Feed events arrive two ways and pass ONE pipeline exactly once (dedupe by event id, `Watcher`):
the live SSE stream (`stream.py`) delivers each within a second of it happening; the per-tick
`/api/feed` poll fills whatever the stream missed (a reconnect, a 429) and stays the source of
truth. Each new event goes to JSONL first (nothing is lost if Postgres is down), then Postgres, then
alerts: a dealer appearing, an announced level going active, a menu change, an announcement. Every
tick also syncs dealers (/api/dealers + /api/levels) and snapshots our /api/me. New traders show up
without warning, so this must run all game.

Our own team is tagged, never mistaken for competition: our trader row has status `us`, and our
own actions never raise an alert.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any

from bazaar_agent.feed import CaptureResult, FeedStore
from bazaar_agent.intel import is_ours
from bazaar_agent.stream import STREAM_ONLY_TYPES

Event = dict[str, Any]
ALERT_EVENT_TYPES = ("announcement", "level", "persona", "dealer", "venue.opened", "schedule.fired", "day.")


@dataclass(frozen=True)
class TraderSnapshot:
    trader_id: str
    kind: str  # dealer | team
    name: str
    status: str
    level: int | None
    menu: str  # canonical JSON, compared for changes
    traits: dict[str, Any]
    unlock: dict[str, Any]


@dataclass(frozen=True)
class Alert:
    tick: int
    kind: str
    subject: str
    detail: str


def dealer_snapshots(dealers_payload: dict[str, Any]) -> dict[str, TraderSnapshot]:
    personas = dealers_payload.get("personas") or dealers_payload.get("dealers") or []
    out = {}
    for p in personas:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        out[str(p["id"])] = TraderSnapshot(
            trader_id=str(p["id"]),
            kind="dealer",
            name=str(p.get("name") or p["id"]),
            status=str(p.get("status") or "unknown"),
            level=p.get("level") if isinstance(p.get("level"), int) else None,
            menu=json.dumps(p.get("menu") or {}, sort_keys=True),
            traits=p.get("traits") or {},
            unlock=p.get("unlock") or {},
        )
    return out


def team_snapshots(events: Iterable[Event], ours: str | None = None) -> dict[str, TraderSnapshot]:
    """Every team the events mention. Our own row has status `us`: tagged, never counted as competition."""
    teams: dict[str, TraderSnapshot] = {}
    for e in events:
        p = e.get("payload") or {}
        candidates = [p.get("team"), e.get("actor")] if e.get("type") != "settlement" else p.get("parties") or []
        for team in candidates:
            if isinstance(team, str) and team.startswith("t") and team[1:].isdigit() and team not in teams:
                name = p.get("name") if e.get("type") == "team.joined" and p.get("team") == team else team
                status = "us" if team == ours else "active"
                teams[team] = TraderSnapshot(team, "team", str(name), status, p.get("level"), "{}", {}, {})
    return teams


def detect_changes(
    tick: int,
    before: dict[str, TraderSnapshot],
    after: dict[str, TraderSnapshot],
    levels_before: list[Any],
    levels_after: list[Any],
    ours: str | None = None,
) -> list[Alert]:
    alerts = []
    for tid, now in after.items():
        if ours and tid == ours:
            continue  # our own trader row changing is never news
        old = before.get(tid)
        if old is None:
            if before:  # the first sync is a baseline, not news
                alerts.append(Alert(tick, f"new_{now.kind}", tid, f"{now.name} appeared (status {now.status})"))
            continue
        if old.status != now.status:
            alerts.append(Alert(tick, "status_change", tid, f"{old.status} → {now.status}"))
        if now.level is not None and old.level != now.level:
            alerts.append(Alert(tick, "level_change", tid, f"level {old.level} → {now.level}"))
        if old.menu != now.menu:
            alerts.append(Alert(tick, "menu_change", tid, "menu changed (prices or quotas)"))
    if not before and not levels_before:
        return alerts  # baseline

    def by_id(levels: list[Any]) -> dict[str, dict[str, Any]]:
        return {str(x.get("id") or x.get("name")): x for x in levels if isinstance(x, dict)}

    old_levels, new_levels = by_id(levels_before), by_id(levels_after)
    for lid, level in new_levels.items():
        state = level.get("state") or level.get("status") or ""
        teaser = level.get("teaser") or level.get("how") or ""
        if lid not in old_levels:
            alerts.append(Alert(tick, "level_announced", lid, f"{level.get('name', lid)} {state}: {teaser}".strip()))
        else:
            was = old_levels[lid].get("state") or old_levels[lid].get("status") or ""
            if was != state:
                alerts.append(Alert(tick, "level_state_change", lid, f"{was} → {state}: {teaser}".strip()))
    return alerts


def event_alerts(events: Iterable[Event], ours: str | None = None) -> list[Alert]:
    """Alerts for announcements, levels, dealers and venues in the feed; never for our own actions."""
    out = []
    for e in events:
        kind = str(e.get("type") or "")
        if kind.startswith(ALERT_EVENT_TYPES) and not is_ours(e, ours):
            p = e.get("payload") or {}
            text = p.get("text") or p.get("note") or p.get("name") or json.dumps(p)[:160]
            out.append(Alert(int(e.get("tick") or 0), f"feed:{kind}", str(e.get("actor") or ""), str(text)))
    return out


def append_alerts(path: Path, alerts: Iterable[Alert]) -> None:
    alerts = list(alerts)
    if not alerts:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for a in alerts:
            handle.write(json.dumps(asdict(a), ensure_ascii=False) + "\n")


def read_alerts(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines[-limit:]]


# ---------------------------------------------------------------- one pipeline for stream and poll


@dataclass(frozen=True)
class Lead:
    """How far the live stream ran ahead of the per-tick poll, over the events this poll confirmed."""

    streamed: int  # events the poll found the stream had already delivered
    poll_only: int  # events only the poll delivered (stream down, refused, or not started yet)
    median_s: float | None
    max_s: float | None

    def describe(self) -> str:
        if not self.streamed:
            return f"stream ahead on 0 events, {self.poll_only} poll-only"
        return (
            f"stream ahead on {self.streamed} events by median {self.median_s:.1f} s "
            f"(max {self.max_s:.1f} s), {self.poll_only} poll-only"
        )


@dataclass(frozen=True)
class Ingested:
    """What one batch (a stream burst or a poll window) added: new events and the alerts they raise."""

    source: str  # "stream" | "poll"
    fresh: list[Event]  # new public feed events: feed.jsonl, then feed_events and tape
    private: list[Event]  # new events scoped to our team (stream only): team_events.jsonl, never feed_events
    alerts: list[Alert]


class Watcher:
    """Every feed event passes here exactly once, from the live stream or the per-tick poll.

    Dedupe is by event id against the JSONL store, so whichever path delivers an event first wins
    and the other is a no-op. Team trader rows are tracked as events arrive: a new team alerts at
    once (after the first poll, which is the baseline), and our own team never alerts.
    """

    def __init__(
        self,
        store: FeedStore,
        ours: str | None,
        private_store: FeedStore | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store, self.private_store, self.ours = store, private_store, ours
        self.teams: dict[str, TraderSnapshot] = {}
        self.baselined = False
        self.stream_only_skipped = 0  # `tick` events: the feed never carries them, so neither do we
        self._now = now
        self._streamed_at: dict[int, float] = {}  # id -> arrival, until a poll confirms it
        self._polled_to = store.newest_id()

    def from_stream(self, events: Iterable[Event]) -> Ingested:
        public: list[Event] = []
        private: list[Event] = []
        for e in events:
            if e.get("type") in STREAM_ONLY_TYPES:
                self.stream_only_skipped += 1
            else:
                (public if e.get("scope", "public") == "public" else private).append(e)
        fresh = self.store.add(public)
        arrived = self._now()
        self._streamed_at.update((e["id"], arrived) for e in fresh)
        kept = self.private_store.add(private) if self.private_store is not None else []
        return self._ingest("stream", fresh, kept)

    def from_poll(self, window: list[Event], window_limit: int) -> tuple[CaptureResult, Ingested, Lead]:
        polled = self._now()
        leads = [polled - self._streamed_at.pop(e["id"]) for e in window if e["id"] in self._streamed_at]
        result, fresh = self.store.capture(window, window_limit, reach=self._polled_to)
        if window:
            newest, oldest = max(e["id"] for e in window), min(e["id"] for e in window)
            self._polled_to = max(self._polled_to or newest, newest)
            # a streamed id below the window will never be confirmed (it left the window): forget it
            self._streamed_at = {i: t for i, t in self._streamed_at.items() if i >= oldest}
        if not self.baselined:  # the first poll is the baseline: every team known so far is not news
            self.teams = {**team_snapshots(self.store.events(), self.ours), **self.teams}
        ingested = self._ingest("poll", fresh, [])
        self.baselined = True
        lead = Lead(len(leads), len(fresh), float(median(leads)) if leads else None, max(leads) if leads else None)
        return result, ingested, lead

    def _ingest(self, source: str, fresh: list[Event], private: list[Event]) -> Ingested:
        alerts = event_alerts(fresh + private, self.ours)
        tick = max((int(e.get("tick") or 0) for e in fresh), default=0)
        for tid, snap in team_snapshots(fresh, self.ours).items():
            if tid in self.teams:
                continue
            self.teams[tid] = snap
            if self.baselined and tid != self.ours:
                alerts.append(Alert(tick, "new_team", tid, f"{snap.name} appeared (status {snap.status})"))
        return Ingested(source, fresh, private, alerts)
