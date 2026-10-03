"""Keys, tokens and throttles. A presented key is never logged, echoed or stored (only its SHA-256).

- Team key (`X-Team-Key`): must start with `sim-` and be one of the simulator's teams
  (`sim-team1` ... `sim-team<N>`). Anything else, a real `tk-...` key included, is 401 `bad_key`.
- Broker key (`X-Broker-Key`): `simbk-...`, returned once by `POST /api/venues`.
- Admin token (`X-Admin-Token`): `SIM_ADMIN_TOKEN` from the environment, compared in constant time.
- Rate limit: 5 requests/s per key with bursts of 20 (`rate_limited`); keyless reads 60/s per address.
- Wrong keys: 20 in a burst per address, then one every two seconds (`too_many_failures`).
"""

from __future__ import annotations

import hmac
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from bazaar_sim.errors import SimError

TEAM_PREFIX = "sim-"
BROKER_PREFIX = "simbk-"
MAX_KEY_CHARS = 128


@dataclass
class Bucket:
    rate: float
    burst: float
    tokens: float
    stamp: float


@dataclass
class Throttle:
    """Token buckets by name. `take` returns False when the bucket is empty."""

    rate: float
    burst: float
    clock: Callable[[], float] = time.monotonic
    buckets: dict[str, Bucket] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self, name: str) -> bool:
        if self.rate <= 0:
            return True
        now = self.clock()
        with self.lock:
            b = self.buckets.get(name) or Bucket(self.rate, self.burst, self.burst, now)
            b.tokens = min(b.burst, b.tokens + (now - b.stamp) * b.rate)
            b.stamp = now
            ok = b.tokens >= 1
            if ok:
                b.tokens -= 1
            self.buckets[name] = b
            if len(self.buckets) > 10_000:
                self.buckets.clear()
            return ok


@dataclass
class Gate:
    keyed: Throttle
    keyless: Throttle
    failures: Throttle

    @classmethod
    def from_rates(cls, per_key: float = 5.0, burst: float = 20.0, keyless: float = 60.0) -> Gate:
        return cls(Throttle(per_key, burst), Throttle(keyless, keyless), Throttle(0.5, 20.0))

    def rate(self, key_name: str | None, address: str) -> None:
        ok = self.keyed.take(key_name) if key_name else self.keyless.take(address)
        if not ok:
            raise SimError("rate_limited", "more than 5 requests per second per key (bursts of 20)", 429)

    def failed(self, address: str) -> None:
        """Count a wrong key; past the burst every wrong-key request from the address is refused."""
        if not self.failures.take(address):
            raise SimError("too_many_failures", "too many wrong keys from this address: one every two seconds", 429)


def looks_like_team_key(key: str | None) -> bool:
    return bool(key) and len(key or "") <= MAX_KEY_CHARS and (key or "").startswith(TEAM_PREFIX)


def looks_like_broker_key(key: str | None) -> bool:
    return bool(key) and len(key or "") <= MAX_KEY_CHARS and (key or "").startswith(BROKER_PREFIX)


def admin_ok(expected: str | None, presented: str | None) -> bool:
    if not expected or not presented:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), presented.encode("utf-8"))
