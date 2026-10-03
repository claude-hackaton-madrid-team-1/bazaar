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

import bazaar_sdk  # noqa: E402
from bazaar_sdk import Bazaar, BazaarError, Broker, _Http  # noqa: E402

__all__ = ["Bazaar", "BazaarError", "Broker", "PublicBazaar", "TeamBazaar", "public_client", "team_client"]

TEAM_TIMEOUT_S = 4.0  # a keyed call that has not answered by then will not make its tick (the SDK waits 15 s)
TEAM_READ_RETRIES = 2  # GET network errors only, and only while ticks are slower than FAST_TICK_S
FAST_TICK_S = 15.0


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


class TeamBazaar(Bazaar):
    """The SDK's team client: a write is sent once, a read at most once more after a refusal.

    The SDK re-sends a `429` (GET and POST alike) up to `retries` times and waits 15 s per attempt: on one key
    shared by every agent (5 req/s) a refused call re-sent twice fills the bucket further, and a hung read can
    hold a loop 46.5 s, three Sunday ticks (r2 bites X6, X2). Here: a write is never re-sent (the loop decides
    again next tick); a read refused by the rate limit is sent once more after 0.25 s (one 429 on a snapshot read
    must not cost the whole tick); each attempt waits TEAM_TIMEOUT_S; and a GET that hit a network error is tried
    again only while ticks are slower than FAST_TICK_S (read from the clock answers that pass through here)."""

    def __init__(self, url: str, key: str, *, timeout: float = TEAM_TIMEOUT_S, read_retries: int = TEAM_READ_RETRIES):
        super().__init__(url, key, timeout=timeout, wait_on_tick=False, retries=0)
        self.read_retries = read_retries
        self.tick_seconds: float | None = None  # the pace in the last clock answer

    def _call(self, method: str, path: str, body: Any = None, query: dict[str, Any] | None = None) -> Any:
        fast = self.tick_seconds is not None and self.tick_seconds <= FAST_TICK_S
        attempts = 1 + (self.read_retries if method == "GET" and not fast else 0)
        limited, attempt = False, 0
        while True:
            attempt += 1
            try:
                result = super()._call(method, path, body, query)  # retries=0: one request
            except BazaarError as e:
                if method == "GET" and e.code == "rate_limited" and not limited:
                    limited = True
                    bazaar_sdk.time.sleep(0.25)  # the SDK's back-off for a 429
                    continue
                if e.code != "network" or attempt >= attempts:
                    raise
                bazaar_sdk.time.sleep(0.5 * attempt)  # the SDK's back-off for a network error
                continue
            if (
                path == "/api/clock"
                and isinstance(result, dict)
                and isinstance(result.get("tick_seconds"), int | float)
            ):
                self.tick_seconds = float(result["tick_seconds"])
            return result


def team_client(settings: Settings) -> Bazaar:
    """wait_on_tick=False: our tick loop owns timing, so a refused send never blocks a process. `TeamBazaar`:
    a write sent once, a read once more after a 429, 4 s per attempt."""
    return TeamBazaar(settings.bazaar_url, settings.require_team_key())
