"""Bite hunt C1: the request budget on ONE team key (5 req/s, bursts of 20; Sunday 15 s ticks = 75/tick).

Every test is offline: fakes, a monkeypatched urlopen on a dead local port, a fake clock. A test FAILS
when the bug it names exists on the code under test.
"""

from __future__ import annotations

import io
import json
import types
import urllib.error
from collections import Counter
from typing import Any

import pytest

from bazaar_agent import sdk  # isort: skip  (puts vendor/bazaar-kit on sys.path)
import bazaar_sdk  # noqa: E402,I001
from bazaar_agent.agents.dealer import BidPlan, Negotiation
from bazaar_agent.agents.desk import Conversation
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.guardrails import Ledger
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask, clock, parts

SUNDAY_TICK_S = 15.0
PR78_TAKER_CEILING = 16  # rate_budget.taker(): 4 reads + 3 per dealer thread (3) + fresh clock, accept, cancel

# ---------------------------------------------------------------- helpers


class Counted:
    """Counts every public method call on the wrapped team client (each one is one keyed request)."""

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "calls", Counter())

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name.startswith("_") or not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            return attr(*args, **kwargs)

        return call

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)


class LostRaceLedger(Ledger):
    """Another process (a second taker, a `bazaar dealer buy` child) wins every reservation race: the
    quota looked free when the tick started, but `reserve_accept` refuses every time."""

    def reserve_accept(self, tick: int, t_hours: float, price: int, item: str, limit: int) -> bool:
        return False


def make_taker(tmp_path, team, public, *, ledger=None, config=None, live=True):
    kw = parts(tmp_path)
    if ledger is not None:
        kw["ledger"] = ledger
    lines: list[str] = []
    t = Taker(
        team,
        public,
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=config or TakerConfig(max_dealer_threads=0),
        **kw,
    )
    return t, lines, kw["ledger"]


def conv(dealer: str, item: str, tid: int) -> Conversation:
    return Conversation(dealer, item, "common", 20.8, "r", Negotiation(BidPlan(5, 1, 9)), tid, TICK - 1)


# ---------------------------------------------------------------- 1. taker keyed calls per tick


def test_taker_keyed_calls_per_tick_with_three_dealer_threads_fit_pr78_ceiling(tmp_path):
    """Measurement: live tick, 3 dealer conversations already open, two board candidates."""
    team = Counted(FakeTeam())
    public = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10), ask(2, "LAV-08", 20, asset=901)]})
    t, _, _ = make_taker(tmp_path, team, public, config=TakerConfig(max_dealer_threads=3))
    t.convs = {d: conv(d, "LAV-09", 700 + i) for i, d in enumerate(("abuela", "d2", "d3"))}
    t.on_tick(clock())
    keyed = sum(team.calls.values()) + 1  # + the loop's own clock read (run_per_tick reads team.clock)
    print("taker keyed calls:", dict(team.calls), "+ loop clock =", keyed)
    assert keyed <= PR78_TAKER_CEILING


def test_taker_rereads_the_clock_once_per_proposal_when_it_loses_the_reservation_race(tmp_path):
    """`_accept` loops over every ranked proposal; `_accept_one` calls `_fresh_tick` (GET /api/clock with
    the key) BEFORE `reserve_accept`. When another process holds the slot (`accepts_in_tick` read 0 at the
    start, the reservation then fails), `used` never increments, so every proposal costs a keyed clock
    read. PR #78 budgets one fresh clock per tick."""
    team = Counted(FakeTeam())
    public = FakePublic(
        boards={"rastro": [ask(1, "LAV-02", 10), ask(2, "LAV-08", 20, asset=901), ask(3, "LAV-09", 30, asset=902)]}
    )
    ledger = LostRaceLedger(tmp_path / "ledger.jsonl")
    t, lines, _ = make_taker(tmp_path, team, public, ledger=ledger)
    t.on_tick(clock())
    raced = [line for line in lines if "another process took the team's accept" in line]
    print("clock reads in on_tick:", team.calls["clock"], "lost races:", len(raced), dict(team.calls))
    assert len(raced) >= 2  # the scenario happened: several proposals each lost the race
    assert team.calls["clock"] <= 1, f"{team.calls['clock']} keyed clock reads in one tick (one per proposal)"


# ---------------------------------------------------------------- 2. a 429 on the accept burns the team's slot


class RateLimitedAccept(FakeTeam):
    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.accept_attempts: list[int] = []

    def accept(self, offer_id, assets=None):
        self.accept_attempts.append(offer_id)
        raise BazaarError("rate_limited", "more than 5 requests per second", 429)


def test_a_rate_limited_accept_does_not_burn_the_teams_accept_slot(tmp_path):
    """RULES: a refused request 'costs nothing and moves nothing'. A 429 `rate_limited` accept never
    reached the quota, yet `Recorder.send` returns None and `_accept_one` returns True ('an accept that may
    have landed is never retried'): `used += 1`, the ledger reservation stays, the next candidate is never
    tried. On Sunday that is the team's ONE accept of the tick (shared with duels)."""
    team = RateLimitedAccept()
    public = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10), ask(2, "LAV-08", 20, asset=901)]})
    t, lines, ledger = make_taker(tmp_path, team, public)
    t.on_tick(clock())
    print("accept attempts:", team.accept_attempts, "ledger accept items:", ledger.accept_items(TICK))
    released = ledger.accept_items(TICK) == []
    tried_next = len(team.accept_attempts) > 1
    assert released or tried_next, "a 429 accept kept the team's reserved accept slot for this tick"


# ---------------------------------------------------------------- 3. the vendored SDK: 429 retries and hangs


def _fake_time(monkeypatch) -> dict[str, float]:
    now = {"t": 0.0, "slept": 0.0}

    def sleep(seconds: float) -> None:
        now["t"] += seconds
        now["slept"] += seconds

    monkeypatch.setattr(bazaar_sdk, "time", types.SimpleNamespace(sleep=sleep))  # only the SDK's sleeps
    return now


def _team_client():
    settings = types.SimpleNamespace(bazaar_url="http://127.0.0.1:9", require_team_key=lambda: "tk-test-test")
    return sdk.team_client(settings)


def test_sdk_sends_a_429_refused_call_up_to_three_times(monkeypatch):
    """team_client: retries=2. A 429 is an HTTPError, so it is retried for GET AND POST (the
    'never repeat a write' guard covers only the network branch), after 0.25 s and 0.5 s."""
    now = _fake_time(monkeypatch)
    sent: list[str] = []

    def urlopen(req, timeout=None):
        sent.append(req.get_method())
        body = io.BytesIO(json.dumps({"error": "rate_limited", "message": "slow down"}).encode())
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, body)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    client = _team_client()
    for call in (client.me, lambda: client.accept(1)):
        with pytest.raises(BazaarError) as e:
            call()
        assert e.value.code == "rate_limited"
    print("requests sent:", Counter(sent), "slept:", now["slept"])
    assert Counter(sent) == Counter({"GET": 1, "POST": 1}), "each refused call was re-sent (x3) into a full bucket"


def test_one_hung_keyed_read_fits_inside_a_sunday_tick(monkeypatch):
    """team_client keeps the SDK's 15 s timeout and retries a GET network error twice (0.5 s, 1 s)."""
    now = _fake_time(monkeypatch)

    def urlopen(req, timeout=None):
        now["t"] += timeout  # the server accepted the TCP connection and never answered
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    client = _team_client()
    with pytest.raises(BazaarError) as e:
        client.me()
    get_s = now["t"]
    now["t"] = 0.0
    with pytest.raises(BazaarError):
        client.accept(1)
    post_s = now["t"]
    public_s = 3 * sdk.public_client(types.SimpleNamespace(bazaar_url="http://127.0.0.1:9")).timeout + 1.5
    print(f"worst case: keyed GET {get_s:.1f} s, keyed POST {post_s:.1f} s, keyless GET {public_s:.1f} s")
    assert e.value.code == "network"
    assert get_s <= SUNDAY_TICK_S, f"one hung GET /api/me blocks the loop {get_s:.1f} s > {SUNDAY_TICK_S:g} s tick"


def test_a_hung_read_mid_tick_never_sends_late(tmp_path):
    """Safety check (expected PASS): a thread read that hangs past the tick drops the moves, never sends late."""
    clock_now = {"t": 1000.0}

    class SlowThread(FakeTeam):
        def thread(self, tid):
            clock_now["t"] += 46.5  # the worst case above
            return super().thread(tid)

    team = SlowThread()
    public = FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]})
    kw = parts(tmp_path)
    t = Taker(
        team,
        public,
        live=True,
        log=lambda line: None,
        now=lambda: clock_now["t"],
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=1),
        **kw,
    )
    t.convs = {"abuela": conv("abuela", "LAV-09", 700)}
    t.on_tick(clock(next_tick_in=13.0, tick_seconds=15.0))
    assert team.sent == []
