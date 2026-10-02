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
