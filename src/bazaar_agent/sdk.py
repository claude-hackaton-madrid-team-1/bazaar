"""The bridge to the organisers' vendored SDK (`vendor/bazaar-kit/bazaar_sdk.py`). SDK first.

`public_client()` sends no key at all: a wrong or empty key counts toward the server's
`too_many_failures` limit, so keyless reads must not carry the header. `team_client()` is a
`TrackedBazaar`: every send that can move cards or cash tells the shared holdings (`holdings.py`)
before it goes and after it returns, so no process answers /api/me from a snapshot it made stale.
"""

from __future__ import annotations

import logging
import math
import sys
from collections.abc import Callable
from typing import Any

from bazaar_agent.config import REPO_ROOT, Settings

_KIT = REPO_ROOT / "vendor" / "bazaar-kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))

import bazaar_sdk  # noqa: E402
from bazaar_sdk import Bazaar, BazaarError, Broker, _Http  # noqa: E402

__all__ = [
    "Bazaar",
    "BazaarError",
    "Broker",
    "PublicBazaar",
    "TeamBazaar",
    "TrackedBazaar",
    "public_client",
    "team_client",
]

WriteHook = Callable[[str, str, str], None]  # (method, path, "before" | "after")
TEAM_TIMEOUT_S = 4.0  # a keyed call that has not answered by then will not make its tick (the SDK waits 15 s)
TEAM_READ_RETRIES = 2  # GET network errors only, and only while ticks are slower than FAST_TICK_S
TEAM_RETRIES = TEAM_READ_RETRIES  # a /me may take (TEAM_RETRIES + 1) x TEAM_TIMEOUT_S (`holdings.ME_BUDGET_S`)
FAST_TICK_S = 15.0
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


class TeamBazaar(TrackedBazaar):
    """The team client: `TrackedBazaar` (every write tells the shared holdings), sending each request once more
    at most, and never into a rate limit.

    The SDK re-sends a `429` (GET and POST alike) up to `retries` times and waits 15 s per attempt: on one key
    shared by every agent (5 req/s) a refused call re-sent twice fills the bucket further, and a hung read can
    hold a loop 46.5 s, three Sunday ticks (r2 bites X6, X2). Here: a refused request is never re-sent (it cost
    nothing; the loop decides again next tick), a write is never re-sent at all, each attempt waits
    TEAM_TIMEOUT_S, and a GET that hit a network error is tried again only while ticks are slower than
    FAST_TICK_S (read from the clock answers that pass through this client)."""

    def __init__(
        self,
        url: str,
        key: str,
        *,
        on_write: WriteHook | None = None,
        timeout: float = TEAM_TIMEOUT_S,
        read_retries: int = TEAM_READ_RETRIES,
    ) -> None:
        super().__init__(url, key, on_write=on_write, timeout=timeout, wait_on_tick=False, retries=0)
        self.read_retries = read_retries
        self.tick_seconds: float | None = None  # the pace in the last clock answer

    def _call(self, method: str, path: str, body: Any = None, query: dict[str, Any] | None = None) -> Any:
        fast = self.tick_seconds is not None and self.tick_seconds <= FAST_TICK_S
        attempts = 1 + (self.read_retries if method == "GET" and not fast else 0)
        for attempt in range(1, attempts + 1):
            try:
                result = super()._call(method, path, body, query)  # retries=0: one request
            except BazaarError as e:
                if e.code != "network" or attempt >= attempts:
                    raise
                bazaar_sdk.time.sleep(0.5 * attempt)  # the SDK's back-off for a network error
                continue
            if path == "/api/clock" and isinstance(result, dict):
                self.tick_seconds = _pace(result.get("tick_seconds")) or self.tick_seconds
            return result
        raise AssertionError("unreachable")  # pragma: no cover

    def value(self, card: str) -> Any:
        """GET /api/me/value, one request and no retry: a buy check waits at most TEAM_TIMEOUT_S on it, and a
        failure refuses the buy (`official_values`), never re-asks inside the tick."""
        return TrackedBazaar._call(self, "GET", "/api/me/value", query={"card": card})


def _pace(value: object) -> float | None:
    """A sane tick length in seconds (the rules say 5-60), else None: never a bool, NaN, inf or a huge value."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value) if 1 <= value <= 3600 else None


def team_client(settings: Settings, *, track: bool = True) -> Bazaar:
    """wait_on_tick=False: our tick loop owns timing, so a refused send never blocks a process. `track`:
    every send bumps the shared holdings epoch (`holdings.process_tracker`), for every process alike.
    `TeamBazaar`: no re-send of a refused call or a write, 4 s per attempt."""
    from bazaar_agent import holdings

    hook = holdings.process_tracker(settings) if track else None
    return TeamBazaar(settings.bazaar_url, settings.require_team_key(), on_write=hook)
