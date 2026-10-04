#!/usr/bin/env python3
"""Render the source-backed architecture overview using only the Python standard library.

Edit docs/architecture.status.json and scripts/templates/architecture.html.tmpl, then run:
    python3 scripts/architecture_page.py
    python3 scripts/architecture_page.py --check

The overview describes implemented paths and declared deployment resources, not live health.
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "docs/architecture.status.json"
TEMPLATE = ROOT / "scripts/templates/architecture.html.tmpl"
OUTPUT = ROOT / "docs/architecture.html"


def inline(text: str) -> str:
    """Escape source text before adding its optional inline code markup."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(text))


def link(item: dict[str, str]) -> str:
    """Allow repository-relative paths and HTTPS references; reject executable URLs."""
    href = item["href"]
    parts = urlsplit(href)
    if (
        not href
        or href != href.strip()
        or any(ord(c) < 32 for c in href)
        or "\\" in href
        or (parts.scheme and (parts.scheme != "https" or not parts.netloc))
        or (not parts.scheme and href.startswith("/"))
    ):
        raise ValueError(f"Unsupported architecture link: {href!r}")
    return f'<a href="{html.escape(href, quote=True)}">{inline(item["label"])}</a>'


def links(items: list[dict[str, str]]) -> str:
    return '<div class="sources">' + " · ".join(link(item) for item in items) + "</div>"


def cards(items: list[dict[str, Any]], *, ordered: bool = False) -> str:
    out = []
    for index, item in enumerate(items, 1):
        tag = "li" if ordered else "article"
        number = f'<span class="step" aria-hidden="true">0{index}</span>' if ordered else ""
        out.append(
            f'<{tag} class="card">{number}<h3>{inline(item["title"])}</h3>'
            f"<p>{inline(item['body'])}</p>{links(item['sources'])}</{tag}>"
        )
    return "\n".join(out)


def service_rows(items: list[dict[str, Any]]) -> str:
    return "\n".join(
        f'<tr><th scope="row">{inline(item["name"])}</th>'
        f"<td><code>{html.escape(item['command'])}</code></td>"
        f"<td>{inline(item['role'])}{links(item['sources'])}</td></tr>"
        for item in items
    )


def render_page(data: dict[str, Any], template: str) -> str:
    # ponytail: a static overview needs no browser app; add runtime health only in the operations UI.
    return Template(template).substitute(
        flow=cards(data["flow"], ordered=True),
        intelligence=cards(data["intelligence"]),
        services=service_rows(data["services"]),
        storage=cards(data["storage"]),
        boundaries=cards(data["boundaries"]),
        development=cards(data["development"]),
        references=links(data["references"]),
    )


def render() -> str:
    return render_page(json.loads(STATUS.read_text(encoding="utf-8")), TEMPLATE.read_text(encoding="utf-8"))


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
