"""`bazaar deploy-guard`: never merge to main (a Railway redeploy) near a duel deadline or a scheduled event.

Shapes come from the vendored SDK and the recorded fixtures: /api/clock (tests/fixtures/api/get_api_clock.anon.json),
/api/duels (`duel`, `status`, `deadline_tick`; tests/test_duel_jev.py LIVE), /api/schedule (`now_hours`, `upcoming`
of `{action, at_hours, note, params}`; tests/fixtures/api/get_api_schedule.anon.json).
"""

from __future__ import annotations

import json

import pytest
import typer

from bazaar_agent import deploy_guard as dg
from bazaar_agent.guardrails import load_guardrails
from bazaar_agent.sdk import BazaarError

RULES = load_guardrails().rules.model_copy(update={"deploy_guard_duel_ticks": 4, "deploy_guard_bench_ticks": 10})
TICK = 400
TICK_S = 30.0
NOW_H = 5.0  # t_hours at TICK


def clock(**kw):
    return {"tick": TICK, "tick_seconds": TICK_S, "t_hours": NOW_H, "doors": "open", "paused": False, **kw}


def hours_in(ticks: int) -> float:
    return round(NOW_H + ticks * TICK_S / 3600.0, 4)


def duel(did=95, deadline=TICK + 20, status="live", **kw):
    return {"duel": did, "status": status, "deadline_tick": deadline, "your_limit": 104, **kw}


def event(action, in_ticks, **params):
    return {"action": action, "at_hours": hours_in(in_ticks), "note": f"{action} soon", "params": params}


def schedule(*events, now=NOW_H):
    return {"now_hours": now, "upcoming": list(events)}


# Benches every 2 h (RULES.md): the next one 3 h from now is far, and the previous one ended long ago.
FAR_BENCHES = (event("bench", 360, ticks=16, traders=10), event("bench", 600, ticks=16, traders=10))


def verdict(duels=(), events=FAR_BENCHES, clk=None, **kw):
    return dg.verdict({"duels": list(duels)}, schedule(*events), clk or clock(), RULES, **kw)


# ---------------------------------------------------------------- duels


def test_a_live_duel_at_the_threshold_blocks_the_merge():
    v = verdict([duel(deadline=TICK + 4)])
    assert not v.safe and any("duel 95" in r and "deadline" in r for r in v.reasons)
    assert v.next_safe_tick == TICK + 5  # the duel closes at its deadline; safe the tick after


def test_a_live_duel_beyond_the_threshold_is_safe():
    v = verdict([duel(deadline=TICK + 5)])
    assert v.safe and v.reasons == () and v.next_safe_tick == TICK


def test_finished_duels_are_ignored():
    v = verdict([duel(deadline=TICK + 1, status="deal", result=12.0), duel(96, deadline=TICK, status="no_deal")])
    assert v.safe


def test_a_live_duel_without_a_deadline_fails_closed():
    v = verdict([{"duel": 95, "status": "live"}])
    assert not v.safe and any("could not read" in r for r in v.reasons) and v.next_safe_tick is None


# ---------------------------------------------------------------- the Market Test and other events


def test_a_bench_starting_within_the_window_blocks_until_it_ends():
    v = verdict(events=(event("bench", 10, ticks=16), event("bench", 250, ticks=16)))
    assert not v.safe and any("Market Test" in r for r in v.reasons)
    assert v.next_safe_tick == TICK + 10 + 16 + 1


def test_a_bench_beyond_the_window_is_safe():
    assert verdict(events=(event("bench", 11, ticks=16), event("bench", 251, ticks=16))).safe


def test_a_running_bench_still_listed_blocks():
    v = verdict(events=(event("bench", -3, ticks=16), event("bench", 237, ticks=16)))
    assert not v.safe and any("running" in r for r in v.reasons) and v.next_safe_tick == TICK - 3 + 16 + 1


def test_a_running_bench_no_longer_listed_is_inferred_from_the_cadence():
    # Listed benches at +235 and +475 ticks (2 h apart at 30 s): the previous one started 5 ticks ago, 16 long.
    v = verdict(events=(event("bench", 235, ticks=16), event("bench", 475, ticks=16)))
    assert not v.safe and any("running" in r and "inferred" in r for r in v.reasons)
    assert v.next_safe_tick == TICK - 5 + 16 + 1


def test_any_other_scheduled_event_within_the_window_blocks():
    v = verdict(events=(*FAR_BENCHES, event("set_release", 7, set="CHA")))
    assert not v.safe and any("set_release" in r for r in v.reasons) and v.next_safe_tick == TICK + 8


def test_a_duel_session_that_starts_soon_blocks_and_its_first_deadline_closes_the_window():
    # Mid-duel is safe (the runner acts on its first tick after a restart); the first wave's deadline is not.
    v = verdict(events=(*FAR_BENCHES, event("duels", 2, duel_ticks=12)))
    assert not v.safe and any("duels" in r for r in v.reasons)
    assert v.next_safe_tick == TICK + 3 and f"until tick {TICK + 2 + 12 - 4 - 1}" in v.window


def test_past_non_bench_events_are_ignored():
    assert verdict(events=(*FAR_BENCHES, event("announce", -50))).safe


# ---------------------------------------------------------------- nothing near, and the next window


def test_nothing_near_is_safe_and_names_the_window():
    v = verdict(events=(*FAR_BENCHES, event("persona", 100, id="pilar")))
    assert v.safe and v.next_safe_tick == TICK
    assert f"until tick {TICK + 100 - 10 - 1}" in v.window and "then scheduled persona starts" in v.window


def test_blockers_chain_into_one_next_safe_tick():
    v = verdict([duel(deadline=TICK + 3)], events=(*FAR_BENCHES, event("announce", 9)))
    assert not v.safe and len(v.reasons) == 2 and v.next_safe_tick == TICK + 10


def test_closed_doors_are_safe_while_the_opening_is_far():
    v = verdict(
        [duel(deadline=TICK + 1)],
        clk=clock(doors="closed", paused=True, next_opens="2026-10-04T09:00:00+02:00"),
        now=dg._epoch("2026-10-03T23:30:00+02:00"),
    )
    assert v.safe and "closed" in v.window


def test_closed_doors_near_the_opening_block():
    v = verdict(
        clk=clock(doors="closed", paused=True, next_opens="2026-10-04T09:00:00+02:00"),
        now=dg._epoch("2026-10-04T08:58:00+02:00"),
    )
    assert not v.safe and any("open" in r for r in v.reasons)


# ---------------------------------------------------------------- malformed data fails closed


@pytest.mark.parametrize(
    "duels, sched, clk",
    [
        ({"duels": []}, schedule(*FAR_BENCHES), {"tick": "x"}),
        ({"duels": []}, schedule(*FAR_BENCHES), None),
        ({"duels": "nope"}, schedule(*FAR_BENCHES), clock()),
        (None, schedule(*FAR_BENCHES), clock()),
        ({"duels": []}, {"now_hours": NOW_H}, clock()),
        ({"duels": []}, schedule({"action": "bench", "at_hours": "soon"}), clock()),
        ({"duels": []}, schedule(event("bench", 300)), clock()),  # a bench without its length
        ({"duels": []}, schedule(*FAR_BENCHES), clock(tick_seconds=0)),
        ({"duels": []}, schedule(*FAR_BENCHES), clock(doors="closed", next_opens=None)),
    ],
)
def test_malformed_payloads_fail_closed(duels, sched, clk):
    v = dg.verdict(duels, sched, clk, RULES)
    assert not v.safe and any(r.startswith("could not read") for r in v.reasons)


# ---------------------------------------------------------------- run() and the CLI function


class FakeClient:
    def __init__(self, duels=(), events=FAR_BENCHES, fail=None):
        self.calls: list[str] = []
        self._duels, self._events, self._fail = list(duels), events, fail

    def _read(self, path, value):
        self.calls.append(path)
        if self._fail == path:
            raise BazaarError("rate_limited", "slow down", 429, {})
        return value

    def clock(self):
        return self._read("/api/clock", clock())

    def duels(self, done=False):
        assert not done, "the guard reads live duels only"
        return self._read("/api/duels", {"duels": self._duels})

    def schedule(self):
        return self._read("/api/schedule", schedule(*self._events))


def test_run_makes_exactly_three_reads():
    client = FakeClient()
    v = dg.run(client, RULES)
    assert v.safe and sorted(client.calls) == ["/api/clock", "/api/duels", "/api/schedule"]


def test_run_fails_closed_on_a_refused_read():
    v = dg.run(FakeClient(fail="/api/schedule"), RULES)
    assert not v.safe and any("could not read /api/schedule" in r and "rate_limited" in r for r in v.reasons)


def invoke(monkeypatch, capsys, client, json_out):
    monkeypatch.setattr(dg, "_client", lambda: client)
    monkeypatch.setattr(dg, "_load_rules", lambda: RULES)
    with pytest.raises(typer.Exit) as exit_:
        dg.deploy_guard_cmd(json_out=json_out)
    return exit_.value.exit_code, capsys.readouterr()


def test_the_cli_exits_0_when_safe_and_prints_pure_json(monkeypatch, capsys):
    code, out = invoke(monkeypatch, capsys, FakeClient(), True)
    body = json.loads(out.out)
    assert code == 0 and body["safe"] is True and body["next_safe_tick"] == TICK


def test_the_cli_exits_1_when_unsafe_and_says_why(monkeypatch, capsys):
    rival = duel(deadline=TICK + 2, rival="[/red]Rival")  # untrusted text must not break rich
    code, out = invoke(monkeypatch, capsys, FakeClient([rival]), False)
    assert code == 1 and "DO NOT MERGE" in out.out and "duel 95" in out.out and f"tick {TICK + 3}" in out.out


def test_the_cli_exits_1_when_the_key_is_missing(monkeypatch, capsys):
    from bazaar_agent.config import ConfigError

    def no_key():
        raise ConfigError("BAZAAR_KEY is not set")

    monkeypatch.setattr(dg, "_client", no_key)
    monkeypatch.setattr(dg, "_load_rules", lambda: RULES)
    with pytest.raises(typer.Exit) as exit_:
        dg.deploy_guard_cmd(json_out=False)
    assert exit_.value.exit_code == 1
