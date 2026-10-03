"""scripts/architecture_page.py: deterministic render, stale detection, status -> CSS mapping."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "scripts/templates/architecture.html.tmpl").read_text(encoding="utf-8")

PLAN = """# Plan

## Task index

| Task id | Title | Phase | Status |
|---|---|---|---|
| [#21](https://github.com/o/r/issues/21) | Feed capture | 0 | ✅ `bazaar monitor` <b> |
| N3 (new) | Learner | 1 | ⬜ not started |

## Next
"""


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("architecture_page", ROOT / "scripts/architecture_page.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["architecture_page"] = mod
    spec.loader.exec_module(mod)
    return mod


ap = _load()

DATA = {
    "boxes": {
        "state": {
            "title": "STATE",
            "mark": "✓",
            "status": "done",
            "lines": [{"text": "CLI monitor"}, {"text": "album first", "note": True}],
        },
        "guardrails": {
            "title": "Guardrails",
            "status": "partial",
            "lines": [{"text": "checked", "mark": "✓", "lead": True}],
        },
    },
    "waiting_on_you": [{"status": "todo", "label": "blocked", "title": "Token <x>", "body": "Set `A=1` & go"}],
    "being_built": [{"status": "wip", "label": "worker", "title": "Evals", "body": "Online."}],
    "not_started": [{"status": "part", "label": "gap", "title": "Learner", "body": "Fills tables."}],
    "links": [{"name": "Taker", "urls": ["https://a.example", "wss://a.example/events"]}],
    "timeline": {
        "start": "2026-10-03T09:00",
        "end": "2026-10-03T21:00",
        "closed": [["2026-10-03T19:00", "2026-10-03T21:00"]],
        "markers": [{"at": "2026-10-03T18:00", "kind": "deadline", "label": "Deadline <18>"}],
        "events": [{"at": "2026-10-03T10:00", "label": "MT", "title": "Market <Test>"}],
        "lanes": [
            {
                "name": "Trading <live>",
                "bars": [
                    {
                        "title": "Fix `#61` <now>",
                        "start": "2026-10-03T09:00",
                        "end": "2026-10-03T12:00",
                        "status": "wip",
                        "priority": "P0",
                        "owner": "Marius",
                    },
                    {
                        "title": "Overlap",
                        "start": "2026-10-03T10:00",
                        "end": "2026-10-03T11:00",
                        "status": "done",
                        "priority": "P2",
                    },
                    {
                        "title": "After",
                        "start": "2026-10-03T12:00",
                        "end": "2026-10-03T15:00",
                        "status": "todo",
                        "priority": "P1",
                    },
                ],
            }
        ],
    },
}


def test_render_is_deterministic_and_complete() -> None:
    first = ap.render_page(DATA, PLAN, TEMPLATE)
    assert first == ap.render_page(json.loads(json.dumps(DATA)), PLAN, TEMPLATE)
    assert "$" not in re.sub(r"<style>.*?</style>", "", first, flags=re.S)  # no unfilled placeholder
    assert "STATE ✓" in first and "✓ checked" in first
    assert 'fill="var(--done)"' in first and 'fill="var(--part)"' in first
    assert "Token &lt;x&gt;" in first and "<code>A=1</code> &amp; go" in first
    assert '<a href="https://github.com/o/r/issues/21">#21</a>' in first
    assert "&lt;b&gt;" in first  # plan text is escaped
    assert "N3 (new)" in first


def test_note_lines_are_spaced_below_the_body() -> None:
    svg = ap.render_box("state", DATA["boxes"]["state"])
    ys = [int(y) for y in re.findall(r'y="(\d+)" class="[sn]"', svg)]
    assert ys == [370 + 78, 370 + 78 + 30]


@pytest.mark.parametrize("status", ap.STATUSES)
def test_every_status_maps_to_a_css_class(status: str) -> None:
    css = TEMPLATE
    pill = ap.PILL_CLASS[status]
    assert f".{pill} " in css, f"no CSS rule for pill class {pill}"
    token = ap.FILL[status].removeprefix("var(--").removesuffix(")")
    assert f"--{token}:" in css, f"no CSS token for {token}"


def test_unknown_status_and_box_are_rejected() -> None:
    with pytest.raises(SystemExit):
        ap.render_box("state", {"title": "X", "status": "nope", "lines": []})
    with pytest.raises(SystemExit):
        ap.render_box("ghost", {"title": "X", "status": "done", "lines": []})


def test_every_geometry_box_is_seeded_and_every_seeded_box_is_known() -> None:
    seeded = json.loads((ROOT / "docs/architecture.status.json").read_text(encoding="utf-8"))["boxes"]
    assert set(seeded) == set(ap.GEOMETRY)


def test_check_detects_a_stale_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "architecture.html"
    monkeypatch.setattr(ap, "OUTPUT", out)
    assert ap.main(["--check"]) == 1  # missing counts as stale
    assert ap.main([]) == 0 and out.exists()
    assert ap.main(["--check"]) == 0
    out.write_text(out.read_text(encoding="utf-8") + "<!-- edited -->", encoding="utf-8")
    assert ap.main(["--check"]) == 1
    assert ap.main([]) == 0 and ap.main(["--check"]) == 0


def test_committed_page_is_current() -> None:
    assert ap.main(["--check"]) == 0


def test_timeline_places_bars_on_the_axis_and_escapes_text() -> None:
    page = ap.render_page(DATA, PLAN, TEMPLATE)
    assert 'class="tl-bar wip" style="left:0.0%;width:25.0%;top:4px"' in page  # 09:00-12:00 of 09:00-21:00
    assert 'class="tl-bar done" style="left:8.333%;width:8.334%;top:34px"' in page  # overlap -> second row
    assert 'class="tl-bar todo" style="left:25.0%;width:25.0%;top:4px"' in page  # starts when the first ends
    assert "Trading &lt;live&gt;" in page
    lane = {
        "name": "x",
        "bars": [
            {
                "title": "Fix `#61` <now>",
                "start": "2026-10-03T09:00",
                "end": "2026-10-03T21:00",
                "status": "todo",
                "priority": "P1",
            }
        ],
    }
    one = ap.render_timeline({"start": "2026-10-03T09:00", "end": "2026-10-03T21:00", "lanes": [lane]})
    assert "Fix <code>#61</code> &lt;now&gt;" in one
    assert "Deadline &lt;18&gt;" in page and "Market &lt;Test&gt;" in page
    assert 'class="tl-closed" style="left:83.333%;width:16.667%"' in page
    assert "<script>" not in re.sub(r"<script>\s*\(function \(\) \{.*?</script>", "", page, flags=re.S)


def test_timeline_rejects_bad_input() -> None:
    lane = {
        "name": "x",
        "bars": [
            {"title": "t", "start": "2026-10-03T09:00", "end": "2026-10-03T10:00", "status": "todo", "priority": "P9"}
        ],
    }
    base = {"start": "2026-10-03T09:00", "end": "2026-10-03T21:00", "lanes": [lane]}
    with pytest.raises(SystemExit):  # unknown priority
        ap.render_timeline(base)
    lane["bars"][0]["priority"] = "P1"
    lane["bars"][0]["end"] = "2026-10-04T10:00"
    with pytest.raises(SystemExit):  # outside the axis
        ap.render_timeline(base)
    lane["bars"][0]["end"] = "2026-10-03T08:00"
    with pytest.raises(SystemExit):  # ends before it starts (and outside)
        ap.render_timeline(base)
    lane["bars"][0]["end"] = "2026-10-03T10:00"
    with pytest.raises(SystemExit):
        ap.render_timeline({**base, "markers": [{"at": "2026-10-03T10:00", "kind": "party", "label": "x"}]})
    with pytest.raises(SystemExit):
        ap.render_timeline({**base, "end": "2026-10-03T08:00"})


def test_a_narrow_bar_label_pushes_the_next_bar_down() -> None:
    bars = [
        {
            "title": "A long title that cannot fit",
            "start": "2026-10-03T09:00",
            "end": "2026-10-03T09:30",
            "status": "todo",
            "priority": "P1",
        },
        {"title": "Next", "start": "2026-10-03T09:45", "end": "2026-10-03T20:00", "status": "todo", "priority": "P1"},
    ]
    out = ap.render_timeline(
        {"start": "2026-10-03T09:00", "end": "2026-10-03T21:00", "lanes": [{"name": "x", "bars": bars}]}
    )
    assert 'class="tl-bar todo out"' in out and "top:34px" in out  # the label of the first occupies row 1


def test_timeline_is_optional() -> None:
    without = {k: v for k, v in DATA.items() if k != "timeline"}
    assert "No timeline yet" in ap.render_page(without, PLAN, TEMPLATE)
