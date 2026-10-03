#!/usr/bin/env python3
"""Render docs/architecture.html from docs/architecture.status.json (Python stdlib only).

The page is a static, deterministic view of the architecture: one SVG box per entry of the JSON,
a Linear-style timeline (lanes of bars on a Madrid-time axis), three status lists, live links
and the task index of .ai/specs/02-plan.md. It carries no timestamp,
so it changes only when an input changes. Run by the same hook and CI job as scripts/readme_status.py.

    python3 scripts/architecture_page.py          # rewrite docs/architecture.html
    python3 scripts/architecture_page.py --check  # exit 1 when it is stale

Git hooks and CI cannot publish claude.ai artifacts: after a merge that changes the page, the
coordinator republishes it (see "Working as a team" in README.md).
"""

from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from string import Template
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "docs/architecture.status.json"
TEMPLATE = ROOT / "scripts/templates/architecture.html.tmpl"
OUTPUT = ROOT / "docs/architecture.html"
PLAN = ROOT / ".ai/specs/02-plan.md"

STATUSES = ("done", "wip", "partial", "todo")
# Box status -> CSS colour token; list pills reuse the same four names as `.p-<status>` classes.
FILL = {s: f"var(--{s if s != 'partial' else 'part'})" for s in STATUSES}
PILL_CLASS = {s: f"p-{'part' if s == 'partial' else s}" for s in STATUSES}
# Pill statuses used by the lists (the original page spelled them wip/todo/part).
PILL_ALIASES = {"part": "partial"}
# Roadmap items: priority badge (Jev's P0-P3 scale) and the word shown in the status pill.
PRIORITIES = ("P0", "P1", "P2", "P3")
STATUS_WORD = {"done": "done", "wip": "doing", "partial": "partial", "todo": "todo"}

# box id -> (x, y, width, height) in the 2000x1320 SVG viewBox
GEOMETRY: dict[str, tuple[int, int, int, int]] = {
    "guardrails": (470, 70, 400, 220),
    "state": (110, 370, 262, 190),
    "llm_proposer": (450, 340, 332, 238),
    "jev": (862, 300, 410, 292),
    "runtime": (1588, 70, 380, 280),
    "llm_runtime": (1588, 376, 380, 200),
    "buy_sell": (1420, 760, 400, 250),
    "pg_vectors": (74, 770, 320, 140),
    "db_cards": (430, 770, 390, 120),
    "db_traders_behaviors": (430, 910, 390, 140),
    "db_learnings": (430, 1070, 390, 120),
    "also_stored": (74, 930, 320, 250),
    "observability": (880, 760, 480, 230),
}
TITLE_DY, FIRST_DY, LINE_STEP, NOTE_GAP, NOTE_STEP = 42, 78, 28, 30, 26


def _pill_status(raw: str) -> str:
    status = PILL_ALIASES.get(raw, raw)
    if status not in STATUSES:
        raise SystemExit(f"architecture.status.json: unknown status {raw!r} (use {', '.join(STATUSES)})")
    return status


def _inline(text: str) -> str:
    """Escape text, then turn `code` spans into <code>."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(text, quote=False))


def _with_mark(entry: dict[str, Any], key: str) -> str:
    text, mark = str(entry[key]), entry.get("mark", "")
    if not mark:
        return text
    return f"{mark} {text}" if entry.get("lead") else f"{text} {mark}"


def render_box(box_id: str, box: dict[str, Any]) -> str:
    if box_id not in GEOMETRY:
        raise SystemExit(f"architecture.status.json: unknown box {box_id!r}")
    x, y, w, h = GEOMETRY[box_id]
    status = _pill_status(box["status"])
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{FILL[status]}" '
        'stroke="var(--ink)" stroke-width="2"/>',
        f'<text x="{x + 20}" y="{y + TITLE_DY}" class="t">{html.escape(_with_mark(box, "title"))}</text>',
    ]
    ty, prev_note = y + FIRST_DY, False
    for i, line in enumerate(box.get("lines", [])):
        note = bool(line.get("note"))
        if i:
            ty += NOTE_STEP if (note and prev_note) else NOTE_GAP if note else LINE_STEP
        cls = "n" if note else "s"
        out.append(f'<text x="{x + 20}" y="{ty}" class="{cls}">{html.escape(_with_mark(line, "text"))}</text>')
        prev_note = note
    return "\n      ".join(out)


def render_items(items: list[dict[str, Any]]) -> str:
    rows = []
    for it in items:
        status = _pill_status(it["status"])
        rows.append(
            f'<div class="item"><span class="pill {PILL_CLASS[status]}">{html.escape(it["label"])}</span>'
            f"<b>{html.escape(it['title'])}</b><p>{_inline(it['body'])}</p></div>"
        )
    return "\n        ".join(rows)


MADRID = ZoneInfo("Europe/Madrid")
MARKER_KINDS = ("freeze", "deadline")
BAR_ROW_PX, BAR_TOP_PX = 30, 4
# Layout heuristics for labels (the track is ~TRACK_PX wide at the timeline's min-width).
TRACK_PX, LABEL_PAD_PX, LABEL_CHAR_PX, LABEL_GAP_PX = 1490.0, 38.0, 6.6, 8.0


def _at(stamp: str) -> datetime:
    """A naive Madrid wall-clock stamp (YYYY-MM-DDTHH:MM); an explicit offset or a bad stamp is an input error."""
    try:
        parsed = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        raise SystemExit(f"architecture.status.json: bad timeline time {stamp!r} (use YYYY-MM-DDTHH:MM)") from None
    if parsed.tzinfo is not None:
        raise SystemExit(f"architecture.status.json: timeline time {stamp!r} must be Madrid wall time, no offset")
    return parsed.replace(tzinfo=MADRID)


class _Axis:
    """Madrid wall-clock stamps -> % positions across [start, end]; anything outside is an input error."""

    def __init__(self, start: str, end: str) -> None:
        self.start, self.end = _at(start), _at(end)
        self.total = (self.end - self.start).total_seconds()
        if self.total <= 0:
            raise SystemExit("architecture.status.json: timeline end must be after start")

    def pct(self, stamp: str) -> float:
        x = round((_at(stamp) - self.start).total_seconds() / self.total * 100, 3)
        if not 0 <= x <= 100:
            raise SystemExit(f"architecture.status.json: timeline time {stamp!r} is outside the axis")
        return x


def _minutes(axis: _Axis, stamp: str) -> float:
    return (_at(stamp) - axis.start).total_seconds() / 60


def _label_px(bar: dict[str, Any]) -> float:
    """Rough width of a bar's badge + title at 12 px, for layout only."""
    return LABEL_PAD_PX + LABEL_CHAR_PX * len(bar["title"])


def _stack(axis: _Axis, bars: list[dict[str, Any]]) -> list[tuple[int, bool, dict[str, Any]]]:
    """Greedy rows inside one lane. A bar too narrow for its title shows it outside, to its right,
    and that label counts as occupied space, so labels never cover the next bar either."""
    px_per_min = TRACK_PX / (axis.total / 60)
    ends: list[float] = []
    placed = []
    for bar in sorted(bars, key=lambda x: (x["start"], x["end"])):
        start, end = _minutes(axis, bar["start"]), _minutes(axis, bar["end"])
        outside = (end - start) * px_per_min < _label_px(bar)
        occupied = end + (_label_px(bar) + LABEL_GAP_PX) / px_per_min if outside else end
        row = next((i for i, last in enumerate(ends) if last <= start), len(ends))
        if row == len(ends):
            ends.append(occupied)
        else:
            ends[row] = occupied
        placed.append((row, outside, bar))
    return placed


def _bar(axis: _Axis, row: int, outside: bool, bar: dict[str, Any]) -> str:
    status, prio = _pill_status(bar["status"]), bar["priority"]
    if prio not in PRIORITIES:
        raise SystemExit(f"architecture.status.json: unknown priority {prio!r} (use {', '.join(PRIORITIES)})")
    left, right = axis.pct(bar["start"]), axis.pct(bar["end"])
    if right <= left:
        raise SystemExit(f"architecture.status.json: bar {bar['title']!r} ends before it starts")
    owner = f" · {bar['owner']}" if bar.get("owner") else ""
    when = f"{_at(bar['start']).strftime('%a %H:%M')} → {_at(bar['end']).strftime('%a %H:%M')}"
    tip = html.escape(f"{prio} · {STATUS_WORD[status]} · {bar['title']}{owner} ({when})")
    out = " out" if outside else ""
    return (
        f'<div class="tl-bar {PILL_CLASS[status][2:]}{out}" style="left:{left}%;width:{round(right - left, 3)}%;'
        f'top:{BAR_TOP_PX + row * BAR_ROW_PX}px" title="{tip}"><b class="prio {prio}">{prio}</b>'
        f"<span>{_inline(bar['title'])}</span></div>"
    )


def _overlays(axis: _Axis, tl: dict[str, Any], labels: bool) -> str:
    """Closed-door bands, hour ticks, deadline markers and the now line, repeated in every track."""
    out = []
    for a, b in tl.get("closed", []):
        if axis.pct(b) <= axis.pct(a):
            raise SystemExit(f"architecture.status.json: closed band {a!r}..{b!r} ends before it starts")
        out.append(
            f'<div class="tl-closed" style="left:{axis.pct(a)}%;width:{round(axis.pct(b) - axis.pct(a), 3)}%"></div>'
        )
    hours = tl.get("tick_hours", 3)
    if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours < 1:
        raise SystemExit(f"architecture.status.json: tick_hours must be a number >= 1, got {hours!r}")
    t, step = axis.start, timedelta(hours=hours)
    while t <= axis.end:
        x = round((t - axis.start).total_seconds() / axis.total * 100, 3)
        text = f"<span>{t.strftime('%a %H:%M')}</span>" if labels else ""
        out.append(f'<div class="tl-tick" style="left:{x}%">{text}</div>')
        t += step
    for m in tl.get("markers", []):
        if m["kind"] not in MARKER_KINDS:
            raise SystemExit(f"architecture.status.json: unknown marker kind {m['kind']!r}")
        text = f"<span>{html.escape(m['label'])}</span>" if labels else ""
        x = axis.pct(m["at"])
        if m.get("side", "right") not in ("left", "right"):
            raise SystemExit(f"architecture.status.json: marker side must be left or right, got {m['side']!r}")
        # Near the right edge, or when asked (a close neighbour on the right), the label goes on the left.
        flip = " end" if x > 85 or m.get("side") == "left" else ""
        out.append(f'<div class="tl-marker {m["kind"]}{flip}" style="left:{x}%">{text}</div>')
    out.append('<div class="tl-now" hidden></div>')
    return "".join(out)


def _event(axis: _Axis, i: int, e: dict[str, Any]) -> str:
    tip = html.escape(f"{_at(e['at']).strftime('%a %H:%M')} · {e['title']}")
    low = " low" if i % 2 else ""  # alternate heights so neighbouring labels do not collide
    low += " last" if axis.pct(e["at"]) > 97 else ""  # keep the last labels inside the track
    return (
        f'<div class="tl-event{low}" style="left:{axis.pct(e["at"])}%" title="{tip}">'
        f"<span>{html.escape(e['label'])}</span></div>"
    )


def render_timeline(tl: dict[str, Any]) -> str:
    """A Linear-style roadmap: one lane per stream, bars on a Madrid-time axis, game events on top."""
    if not tl:
        return '<div class="item"><p>No timeline yet in docs/architecture.status.json.</p></div>'
    axis = _Axis(tl["start"], tl["end"])
    start_ms, end_ms = int(axis.start.timestamp() * 1000), int(axis.end.timestamp() * 1000)
    head = _overlays(axis, tl, labels=True)
    events = "".join(_event(axis, i, e) for i, e in enumerate(tl.get("events", [])))
    rows = [
        f'<div class="tl-row tl-axis"><div class="tl-name"></div><div class="tl-track">{head}</div></div>',
        f'<div class="tl-row"><div class="tl-name">Game events</div><div class="tl-track tl-events">'
        f"{_overlays(axis, tl, labels=False)}{events}</div></div>",
    ]
    for lane in tl["lanes"]:
        placed = _stack(axis, lane["bars"])
        height = BAR_TOP_PX * 2 + (max((r for r, _, _ in placed), default=0) + 1) * BAR_ROW_PX
        bars = "".join(_bar(axis, r, o, b) for r, o, b in placed)
        rows.append(
            f'<div class="tl-row"><div class="tl-name">{html.escape(lane["name"])}</div>'
            f'<div class="tl-track" style="height:{height}px">{_overlays(axis, tl, labels=False)}{bars}</div></div>'
        )
    body = "\n      ".join(rows)
    opening = f'<div class="tl-wrap"><div class="tl" id="tl" data-start="{start_ms}" data-end="{end_ms}">'
    return f"{opening}\n      {body}\n    </div></div>"


def render_links(links: list[dict[str, Any]]) -> str:
    return "\n      ".join(
        f'<div class="item"><b>{html.escape(link["name"])}</b>'
        f'<span class="mono">{" · ".join(html.escape(u) for u in link["urls"])}</span></div>'
        for link in links
    )


def _cell(text: str) -> str:
    link = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", text.strip())
    if link and link.group(2).startswith("https://"):
        return f'<a href="{html.escape(link.group(2))}">{html.escape(link.group(1))}</a>'
    return _inline(text.strip())


def task_rows(plan: str) -> list[list[str]]:
    match = re.search(r"## Task index\n\n(\|.*?)\n\n", plan, re.S)
    if not match:
        return []
    lines = match.group(1).strip().splitlines()[2:]  # skip the header and the |---| rule
    return [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]


def render_backlog(plan: str) -> str:
    rows = task_rows(plan)
    if not rows:
        return '<div class="item"><p>No task index in .ai/specs/02-plan.md.</p></div>'
    return "\n      ".join(
        f'<div class="item"><span class="mono">{_cell(r[0])}</span><b>{_inline(r[1])}</b><p>{_inline(r[3])}</p></div>'
        for r in rows
        if len(r) >= 4
    )


def render_page(data: dict[str, Any], plan: str, template: str) -> str:
    boxes = "\n      ".join(render_box(k, v) for k, v in data["boxes"].items())
    return Template(template).substitute(
        boxes=boxes,
        timeline=render_timeline(data.get("timeline", {})),
        waiting=render_items(data["waiting_on_you"]),
        built=render_items(data["being_built"]),
        notstarted=render_items(data["not_started"]),
        links=render_links(data["links"]),
        backlog=render_backlog(plan),
    )


def render() -> str:
    data = json.loads(STATUS.read_text(encoding="utf-8"))
    plan = PLAN.read_text(encoding="utf-8") if PLAN.exists() else ""
    return render_page(data, plan, TEMPLATE.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    updated = render()
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
    if "--check" in argv:
        if updated != current:
            print("docs/architecture.html is stale: run python3 scripts/architecture_page.py", file=sys.stderr)
            return 1
        return 0
    if updated != current:
        OUTPUT.write_text(updated, encoding="utf-8")
        print("docs/architecture.html updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
