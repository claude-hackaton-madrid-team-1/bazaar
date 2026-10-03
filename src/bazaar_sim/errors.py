"""Refusals: every one becomes `{"error": "<code>", "message": "<why>"}` with an HTTP status, like the real API."""

from __future__ import annotations

from typing import Any


class SimError(Exception):
    """A refused request: it costs nothing and moves nothing."""

    def __init__(self, code: str, message: str, status: int = 400, **extra: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


def not_found(what: str) -> SimError:
    return SimError("not_found", f"{what} not found", 404)


def invalid(message: str) -> SimError:
    return SimError("invalid", message, 400)


def wait_for_tick(message: str, next_tick: int) -> SimError:
    return SimError("wait_for_tick", message, 429, next_tick=next_tick)
