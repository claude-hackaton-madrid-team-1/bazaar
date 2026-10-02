"""Who "us" is: our team id, so every view tags our own activity instead of counting it as competition.

Resolved once and cached: `BAZAAR_TEAM_ID` (env or `.env`) wins, then the id cached in
`.local/team_id`, then `GET /api/me` (needs `BAZAAR_KEY`), whose answer is cached for next time.
Every source is validated: a malformed id is ignored, never trusted.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bazaar_agent.sdk import BazaarError

TEAM_ID = re.compile(r"t\d{1,3}")
CACHE_FILE = "team_id"


def valid_team_id(value: object) -> str | None:
    text = str(value).strip() if isinstance(value, str) else ""
    return text if TEAM_ID.fullmatch(text) else None


def cached_team_id(data_dir: Path) -> str | None:
    path = data_dir / CACHE_FILE
    try:
        return valid_team_id(path.read_text(encoding="utf-8")) if path.is_file() else None
    except OSError:
        return None


def remember_team_id(data_dir: Path, team_id: str) -> None:
    if valid_team_id(team_id) is None or cached_team_id(data_dir) == team_id:
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / CACHE_FILE).write_text(team_id + "\n", encoding="utf-8")


def resolve_team_id(
    override: str | None,
    data_dir: Path,
    read_me: Callable[[], dict[str, Any]] | None,
    warn: Callable[[str], None] = lambda message: None,
) -> str | None:
    """Our team id, or None when no source knows it (no override, no cache, no key or /me refused)."""
    if team := valid_team_id(override):
        return team
    if team := cached_team_id(data_dir):
        return team
    if read_me is None:
        return None
    try:
        team = valid_team_id(read_me().get("id"))
    except BazaarError as e:
        warn(f"our team id is unknown: /api/me refused {e.code} (set BAZAAR_TEAM_ID to skip the read)")
        return None
    if team is None:
        warn("our team id is unknown: /api/me carried no valid `id` (set BAZAAR_TEAM_ID)")
        return None
    remember_team_id(data_dir, team)
    return team
