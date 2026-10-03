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
import time
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
    "read_once_more_after_429",
    "team_client",
]

WriteHook = Callable[[str, str, str], None]  # (method, path, "before" | "after")
TEAM_TIMEOUT_S = 4.0  # a keyed call that has not answered by then will not make its tick (the SDK waits 15 s)
TEAM_READ_RETRIES = 2  # GET network errors only, and only while ticks are slower than FAST_TICK_S
TEAM_RETRIES = TEAM_READ_RETRIES  # a /me may take (TEAM_RETRIES + 1) x TEAM_TIMEOUT_S (`holdings.ME_BUDGET_S`)
FAST_TICK_S = 15.0
RATE_LIMIT_WAIT_S = 1.2  # the wait before the one re-read of a 429 when the server names none
RATE_LIMIT_WAIT_MAX_S = 5.0  # a longer hint: the read waits for the next tick instead
RATE_LIMIT_MIN_LEFT_S = 8.0  # the re-read goes only while this much of the tick's budget is left after the wait
RETRY_HINT_KEYS = ("retry_after", "retry_after_s", "retry_in")
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

    def duel_say(self, duel_id: int, text: str = "", price: int | None = None, days: int | None = None) -> dict:
        """The SDK's `duel_say`, with `days` at the top level too. The SDK sends the price at the top level and the
        days only inside `offer`; RULES.md accepts `{"price", "days"}` or both inside `"offer"`, and a priced
        message without days is refused (`missing_days`). Sending both forms, with the same values, satisfies
        either reading of the server; no two-issue message of ours had reached the real server before Duels II."""
        body: dict[str, Any] = {"text": text}
        if price is not None:
            body["price"] = int(price)
            if days is not None:
                body["days"] = int(days)
                body["offer"] = {"price": int(price), "days": int(days)}
        return self._call("POST", f"/api/duels/{int(duel_id)}/messages", body)

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


def rate_limit_wait_s(error: BazaarError) -> float:
    """The server's wait for a `rate_limited` refusal (seconds, from its body), else RATE_LIMIT_WAIT_S."""
    for key in RETRY_HINT_KEYS:
        hint = error.extra.get(key)
        if isinstance(hint, (int, float)) and not isinstance(hint, bool) and math.isfinite(hint) and hint > 0:
            return float(hint)
    return RATE_LIMIT_WAIT_S


def read_once_more_after_429(
    read: Callable[[], Any],
    left_s: Callable[[], float],
    *,
    sleep: Callable[[float], None] | None = None,
    on_retry: Callable[[BazaarError, float], None] | None = None,
) -> Any:
    """`read()`, sent at most once more after a `rate_limited` refusal: after the server's wait (or 1.2 s), and only
    while `left_s()` (the tick's budget) still holds RATE_LIMIT_MIN_LEFT_S after that wait. Never a loop (RULES.md:
    a 429 means wait): the second refusal, any other refusal or a short tick raises as the first read would.

    For a read whose lost tick costs points (an unanswered duel scores 0); our services wake at staggered offsets
    (BAZAAR_TICK_OFFSET_S), so 1.2 s later the key's bucket has refilled."""
    try:
        return read()
    except BazaarError as error:
        if error.code != "rate_limited":
            raise
        wait = rate_limit_wait_s(error)
        if wait > RATE_LIMIT_WAIT_MAX_S or left_s() - wait < RATE_LIMIT_MIN_LEFT_S:
            raise
        if on_retry is not None:
            on_retry(error, wait)
        (sleep or time.sleep)(wait)
    return read()


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


def team_client(settings: Settings, *, track: bool = True, retries: int = TEAM_READ_RETRIES) -> Bazaar:
    """wait_on_tick=False: our tick loop owns timing, so a refused send never blocks a process. `track`:
    every send bumps the shared holdings epoch (`holdings.process_tracker`), for every process alike.
    `TeamBazaar`: no re-send of a refused call or a write, 4 s per attempt. `retries`: the GET re-sends after a
    network error (`TeamBazaar.read_retries`); 0 for loops that must stop on the first failure (the card scan)."""
    from bazaar_agent import holdings

    hook = holdings.process_tracker(settings) if track else None
    return TeamBazaar(settings.bazaar_url, settings.require_team_key(), on_write=hook, read_retries=retries)
