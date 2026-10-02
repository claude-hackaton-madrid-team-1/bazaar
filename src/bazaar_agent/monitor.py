"""The monitoring agent: one tick-driven reader that keeps our memory current and raises alerts.

Every tick: capture the feed (JSONL first, so nothing is lost if Postgres is down), load new events
into Postgres, sync every trader (dealers from /api/dealers + /api/levels, teams from the feed),
snapshot our /api/me, and alert on anything new: a dealer appearing, an announced level going
active, a menu change, an organiser announcement. New traders show up without warning, so this
must run all game.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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


def team_snapshots(events: Iterable[Event]) -> dict[str, TraderSnapshot]:
    teams: dict[str, TraderSnapshot] = {}
    for e in events:
        p = e.get("payload") or {}
        candidates = [p.get("team"), e.get("actor")] if e.get("type") != "settlement" else p.get("parties") or []
        for team in candidates:
            if isinstance(team, str) and team.startswith("t") and team[1:].isdigit() and team not in teams:
                name = p.get("name") if e.get("type") == "team.joined" and p.get("team") == team else team
                teams[team] = TraderSnapshot(team, "team", str(name), "active", p.get("level"), "{}", {}, {})
    return teams


def detect_changes(
    tick: int,
    before: dict[str, TraderSnapshot],
    after: dict[str, TraderSnapshot],
    levels_before: list[Any],
    levels_after: list[Any],
) -> list[Alert]:
    alerts = []
    for tid, now in after.items():
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


def event_alerts(events: Iterable[Event]) -> list[Alert]:
    out = []
    for e in events:
        kind = str(e.get("type") or "")
        if kind.startswith(ALERT_EVENT_TYPES):
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
