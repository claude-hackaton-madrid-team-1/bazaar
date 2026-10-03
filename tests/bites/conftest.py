"""Mark the duel bite tests that fail on main as strict xfails, from `known_bites_main.txt`.

Those tests are parametrized across payload shapes and policies, so the list names exact node ids instead
of decorators. A fix makes a listed test XPASS, and strict turns that into a failure: delete its line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

KNOWN = Path(__file__).with_name("known_bites_main.txt")


def _known() -> set[str]:
    lines = KNOWN.read_text(encoding="utf-8").splitlines() if KNOWN.is_file() else []
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    known = _known()
    for item in items:
        if item.nodeid in known:
            item.add_marker(pytest.mark.xfail(strict=True, reason="BITE (duels): fails on main, see _night/BITES.md"))
