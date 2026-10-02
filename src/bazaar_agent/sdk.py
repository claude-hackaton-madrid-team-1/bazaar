"""The bridge to the organisers' vendored SDK (`vendor/bazaar-kit/bazaar_sdk.py`). SDK first.

`public_client()` sends no key at all: a wrong or empty key counts toward the server's
`too_many_failures` limit, so keyless reads must not carry the header.
"""

from __future__ import annotations

import sys
from typing import Any

from bazaar_agent.config import REPO_ROOT, Settings

_KIT = REPO_ROOT / "vendor" / "bazaar-kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))

from bazaar_sdk import Bazaar, BazaarError, Broker, _Http  # noqa: E402

__all__ = ["Bazaar", "BazaarError", "Broker", "PublicBazaar", "public_client", "team_client"]


class PublicBazaar(Bazaar):
    """The SDK's team client without the X-Team-Key header, for the public routes only."""

    def __init__(self, url: str, *, timeout: float = 10.0, retries: int = 2) -> None:
        _Http.__init__(self, url, {}, timeout, False, retries)
        self.key = ""

    def feed_window(self, limit: int) -> list[dict[str, Any]]:
        events = self.feed(limit=limit).get("events") or []
        return [e for e in events if isinstance(e, dict) and isinstance(e.get("id"), int)]


def public_client(settings: Settings) -> PublicBazaar:
    return PublicBazaar(settings.bazaar_url)


def team_client(settings: Settings) -> Bazaar:
    """wait_on_tick=False: our tick loop owns timing, so a refused send never blocks a process."""
    return Bazaar(settings.bazaar_url, settings.require_team_key(), wait_on_tick=False, retries=2)
