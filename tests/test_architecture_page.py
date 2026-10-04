"""The generated architecture stays deterministic, linkable and safe to render."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("architecture_page", ROOT / "scripts/architecture_page.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["architecture_page"] = mod
    spec.loader.exec_module(mod)
    return mod


ap = _load()


class Page(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.links: list[str] = []
        self.ids: list[str] = []
        self.tags: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if tag == "a" and values.get("href"):
            self.links.append(str(values["href"]))


def test_committed_page_is_current():
    assert ap.render() == ap.OUTPUT.read_text(encoding="utf-8")
    assert ap.main(["--check"]) == 0


def test_render_is_deterministic_without_git_or_backlog():
    assert ap.render() == ap.render()
    page = Page(ap.render())
    assert page.tags.count("h1") == 1
    assert page.tags.count("section") == 6
    assert "script" not in page.tags
    assert len(page.ids) == len(set(page.ids))


def test_navigation_and_repository_sources_resolve():
    page = Page(ap.render())
    assert page.links
    for href in page.links:
        target = urlsplit(href)
        if target.scheme:
            assert target.scheme == "https"
            continue
        if target.path:
            path = (ap.OUTPUT.parent / unquote(target.path)).resolve()
            assert path.is_relative_to(ROOT), href
            assert path.exists(), href
        elif target.fragment:
            assert target.fragment in page.ids, href


def test_source_text_is_escaped_before_inline_markup():
    data = json.loads(ap.STATUS.read_text())
    data = copy.deepcopy(data)
    data["flow"][0]["title"] = '<img src=x onerror="alert(1)">'
    data["flow"][0]["body"] = 'Use `<script>` & "quotes"'
    data["services"][0]["command"] = '<script>alert("x")</script>'
    data["references"][0]["label"] = '<svg onload="alert(1)">'
    rendered = ap.render_page(data, ap.TEMPLATE.read_text())
    assert '<img src="x"' not in rendered
    assert "<img src=x" not in rendered
    assert "<script>" not in rendered
    assert "<svg onload=" not in rendered
    assert "<code>&lt;script&gt;</code> &amp; &quot;quotes&quot;" in rendered
    assert "&lt;svg onload=&quot;alert(1)&quot;&gt;" in rendered


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "//example.com/x",
        "http://example.com",
        "https:/x",
        "",
        "\nhttps://x",
        "../x\ny",
        r"..\x",
    ],
)
def test_unsafe_source_links_are_rejected(href: str):
    with pytest.raises(ValueError, match="Unsupported architecture link"):
        ap.link({"href": href, "label": "source"})


def test_link_attribute_is_escaped():
    assert ap.link({"href": 'https://example.com/?q="&x=1', "label": "source"}) == (
        '<a href="https://example.com/?q=&quot;&amp;x=1">source</a>'
    )


def test_check_detects_and_repairs_stale_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    output = tmp_path / "architecture.html"
    output.write_text("old")
    monkeypatch.setattr(ap, "OUTPUT", output)
    assert ap.main(["--check"]) == 1
    assert output.read_text() == "old"
    assert ap.main([]) == 0
    assert ap.main(["--check"]) == 0
    assert output.read_text() == ap.render()
