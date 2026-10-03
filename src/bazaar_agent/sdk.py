"""The bridge to the organisers' vendored SDK (`vendor/bazaar-kit/bazaar_sdk.py`). SDK first.

`public_client()` sends no key at all: a wrong or empty key counts toward the server's
`too_many_failures` limit, so keyless reads must not carry the header. `team_client()` is a
`TrackedBazaar`: every send that can move cards or cash tells the shared holdings (`holdings.py`)
before it goes and after it returns, so no process answers /api/me from a snapshot it made stale.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any

from bazaar_agent.config import REPO_ROOT, Settings

_KIT = REPO_ROOT / "vendor" / "bazaar-kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))

from bazaar_sdk import Bazaar, BazaarError, Broker, _Http  # noqa: E402

__all__ = ["Bazaar", "BazaarError", "Broker", "PublicBazaar", "TrackedBazaar", "public_client", "team_client"]

WriteHook = Callable[[str, str, str], None]  # (method, path, "before" | "after")
TEAM_TIMEOUT_S = 15.0  # one team request's HTTP timeout
TEAM_RETRIES = 2  # retries after a network error: a /me may take (TEAM_RETRIES + 1) x TEAM_TIMEOUT_S
log = logging.getLogger(__name__)


class PublicBazaar(Bazaar):
    """The SDK's team client without the X-Team-Key header, for the public routes only."""

    def __init__(self, url: str, *, timeout: float = 10.0, retries: int = 2) -> None:
        _Http.__init__(self, url, {}, timeout, False, retries)
        self.key = ""

    def feed_window(self, limit: int) -> list[dict[str, Any]]:
        events = self.feed(limit=limit).get("events") or []
        return [e for e in events if isinstance(e, dict) and isinstance(e.get("id"), int)]


class TrackedBazaar(Bazaar):
    """The SDK's team client; every request that is not a GET calls `on_write` before and after it.
    A hook that fails is logged and ignored: it never blocks, delays past its own timeout, or breaks a send."""

    def __init__(self, url: str, key: str, *, on_write: WriteHook | None = None, **kwargs: Any) -> None:
        super().__init__(url, key, **kwargs)
        self.on_write = on_write

    def _call(self, method: str, path: str, body: Any = None, query: dict[str, Any] | None = None) -> Any:
        hook = self.on_write
        if hook is None or method.upper() == "GET":
            return super()._call(method, path, body, query)
        _tell(hook, method, path, "before")
        try:
            return super()._call(method, path, body, query)
        finally:
            _tell(hook, method, path, "after")


def _tell(hook: WriteHook, method: str, path: str, phase: str) -> None:
    try:
        hook(method, path, phase)
    except Exception as e:  # the holdings are a cache: losing one bump costs at most holdings_max_age_s
        log.warning("holdings: write hook failed (%s)", type(e).__name__)


def public_client(settings: Settings) -> PublicBazaar:
    return PublicBazaar(settings.bazaar_url)


def team_client(settings: Settings, *, track: bool = True, retries: int = TEAM_RETRIES) -> Bazaar:
    """wait_on_tick=False: our tick loop owns timing, so a refused send never blocks a process. `track`:
    every send bumps the shared holdings epoch (`holdings.process_tracker`), for every process alike.
    `retries=0`: the first refusal (a 429 too) is raised at once, for loops that must stop on one."""
    from bazaar_agent import holdings

    hook = holdings.process_tracker(settings) if track else None
    key = settings.require_team_key()
    return TrackedBazaar(
        settings.bazaar_url, key, on_write=hook, wait_on_tick=False, retries=retries, timeout=TEAM_TIMEOUT_S
    )
