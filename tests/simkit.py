"""Test harness for the simulator: a world with a hand-turned clock, or a real server in this process.

`manual_world()` gives a `World` whose clock moves only when the test calls `step()`: unit tests of
the rules. `running_sim()` serves the real FastAPI app with uvicorn on a free local port, on a
background thread, so the vendored SDK and our CLI talk to it over real HTTP. No test reaches the
real game or a real database.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace

import uvicorn

from bazaar_sim.app import Sim, create_app, server_config
from bazaar_sim.auth import Gate
from bazaar_sim.store import MemoryStore
from bazaar_sim.world import SimConfig, World

QUIET = SimConfig(rivals=0, duel_first_tick=10_000, bench_first_tick=10_000)  # no background activity


@dataclass
class ManualClock:
    now: float = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Manual:
    world: World
    clock: ManualClock

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self.clock.now += self.world.config.tick_seconds
            self.world.advance()


def manual_world(config: SimConfig = QUIET, **overrides: object) -> Manual:
    clock = ManualClock()
    cfg = replace(config, **overrides)  # type: ignore[arg-type]
    return Manual(World.create(cfg, now=clock), clock)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def running_sim(
    config: SimConfig,
    *,
    admin_token: str | None = "test-admin-token",
    rate: float = 0.0,
    burst: float = 20.0,
    keepalive_s: float = 0.5,
    run_clock: bool = True,
) -> Iterator[tuple[str, Sim]]:
    """Serve the simulator on 127.0.0.1:<free port>. `rate=0` turns the per-key throttle off."""
    sim = Sim(
        world=World.create(config),
        store=MemoryStore(),
        gate=Gate.from_rates(rate, burst, keyless=rate * 12 if rate else 0.0),
        admin_token=admin_token,
        keepalive_s=keepalive_s,
    )
    port = free_port()
    server = uvicorn.Server(server_config(create_app(sim, run_clock=run_clock), "127.0.0.1", port))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the simulator did not start"
    try:
        yield f"http://127.0.0.1:{port}", sim
    finally:
        server.should_exit = True
        thread.join(timeout=10)
