#!/usr/bin/env python3
"""Render docs/architecture.html from docs/architecture.status.json (Python stdlib only).

The page is a static, deterministic view of the architecture: one SVG box per entry of the JSON,
a timed roadmap, three status lists, live links and the task index of .ai/specs/02-plan.md. It carries no timestamp,
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
from pathlib import Path
from string import Template
from typing import Any

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
    "runtime": (1588, 70, 380, 252),
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


def _roadmap_item(it: dict[str, Any]) -> str:
    status, prio = _pill_status(it["status"]), it["priority"]
    if prio not in PRIORITIES:
        raise SystemExit(f"architecture.status.json: unknown priority {prio!r} (use {', '.join(PRIORITIES)})")
    owner = f'<span class="owner"> · {html.escape(it["owner"])}</span>' if it.get("owner") else ""
    return (
        f'<li><span class="prio {prio}">{prio}</span>'
        f'<span class="pill {PILL_CLASS[status]}">{STATUS_WORD[status]}</span>'
        f"<span>{_inline(it['text'])}{owner}</span></li>"
    )


def render_roadmap(phases: list[dict[str, Any]]) -> str:
    """One card per time slot, in order: when, title, then its items with priority, status and owner."""
    if not phases:
        return '<div class="item"><p>No roadmap yet in docs/architecture.status.json.</p></div>'
    cards = []
    for ph in phases:
        items = "".join(_roadmap_item(it) for it in ph["items"])
        cards.append(
            f'<div class="item phase"><span class="mono">{html.escape(ph["when"])}</span>'
            f"<b>{html.escape(ph['title'])}</b><ul>{items}</ul></div>"
        )
    return "\n      ".join(cards)


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
        roadmap=render_roadmap(data.get("roadmap", [])),
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
