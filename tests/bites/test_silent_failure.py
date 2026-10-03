"""BITE X24: an agent whose every tick raises keeps reporting healthy.

`run_per_tick` reports an exception from `on_tick` on stderr and goes on (by design: one bad tick must not stop
the loop). `StatusHub.health()` always answers `"ok": true`, and the taker stamps `last_tick_at` before it plans.
So a persistent failure (a payload shape the strategy cannot parse, a bug in a new code path after a merge) makes
the live taker trade nothing, all day, while Railway's healthcheck (`/health`) and the dashboard stay green and
`restartPolicyType ALWAYS` never fires. Only the Railway log shows the tracebacks.
"""

import pytest

from bazaar_agent.agents import taker as taker_module
from bazaar_agent.agents.status import StatusHub
from bazaar_agent.ticks import run_per_tick
from tests.agent_fakes import FakePublic, FakeTeam, clock
from tests.bites.strictness import STRICT
from tests.test_taker import taker


@pytest.mark.xfail(strict=STRICT, reason="BITE X24: /health says ok while every tick fails")
def test_health_reports_an_agent_whose_ticks_all_fail(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise KeyError("rarity")  # e.g. a catalog row without the field the strategy reads

    monkeypatch.setattr(taker_module, "build_playbook", broken)
    team = FakeTeam()
    t, _, _ = taker(tmp_path, team, FakePublic(), live=True)
    hub = StatusHub("taker", live=True)
    t.hub = hub
    ticks = iter(range(100, 110))
    errors: list[str] = []
    run_per_tick(
        lambda: clock(tick=next(ticks)).model_dump(),
        t.on_tick,
        max_ticks=10,
        sleep=lambda s: None,
        on_error=lambda stage, e: errors.append(stage),
    )
    assert len(errors) == 10  # every tick failed
    health = hub.health()
    assert health["ok"] is False or health.get("errors"), f"10 failed ticks in a row, /health says {health}"
