"""The simulator's clock loop: one failed tick (or save) never stops the shared simulator (#178 review P2)."""

import asyncio
from types import SimpleNamespace

from bazaar_sim import app


class FlakyWorld:
    """A world whose first tick raises; the next ones succeed."""

    def __init__(self) -> None:
        self.calls = 0

    def next_tick_in(self) -> float:
        return 0.0

    def advance_if_due(self) -> bool:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("a rival broke the tick")
        return True


def test_a_failed_tick_is_logged_and_the_clock_keeps_going(caplog):
    world, saved = FlakyWorld(), []
    sim = SimpleNamespace(world=world, persist=lambda: saved.append(world.calls))

    async def run() -> None:
        task = asyncio.create_task(app._clock_loop(sim))  # type: ignore[arg-type]
        await asyncio.sleep(0.3)
        assert not task.done(), "the clock loop died on the first failed tick"
        task.cancel()

    with caplog.at_level("ERROR", logger="bazaar_sim"):
        asyncio.run(run())
    assert world.calls >= 3 and saved  # it kept ticking and saving after the failure
    assert any("clock" in r.getMessage() and r.exc_info for r in caplog.records)
