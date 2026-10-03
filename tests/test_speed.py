"""SP1: the Jev answer cache and the concurrent reads make ticks shorter, never different.

Every "identical moves" test runs the same ticks twice, once with GUARDRAILS.md's speed rules off (today's
behaviour: `jev_cache_ticks = 0`, `parallel_reads = false`) and once with them on, and compares what was
sent and what was decided.
"""

import threading
from types import SimpleNamespace

import pytest

from bazaar_agent.agents.jev_cache import VerdictCache, state_key, state_tick
from bazaar_agent.agents.runtime import JevAdvice, MarketFeed, read_snapshot, read_together
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.guardrails import load_guardrails
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask, clock, parts, rows

SPEED_ON = {"jev_cache_ticks": 4, "parallel_reads": True}
SPEED_OFF = {"jev_cache_ticks": 0, "parallel_reads": False}

# ---------------------------------------------------------------- the cache


def test_the_state_key_ignores_the_clock_fields_and_nothing_else():
    state = {"offer": {"item": "LAV-02", "total_cost": 12}, "cash": 300, "tick": 100, "game_hour": 1.5}
    later = {**state, "tick": 101, "game_hour": 1.52}
    assert state_key("q", state) == state_key("q", later)
    assert state_key("q", state) != state_key("q", {**state, "cash": 299})  # a new state is a new call
    assert state_key("q", state) != state_key("other", state)  # and so is another question
    assert state_tick(state) == 100 and state_tick({"tick": True}) is None and state_tick({}) is None


def test_an_answer_lives_for_jev_cache_ticks_from_the_tick_it_was_asked_in():
    cache: VerdictCache[str] = VerdictCache(4)
    cache.put("k", 100, "yes")
    assert [cache.get("k", t) for t in (100, 101, 103)] == ["yes", "yes", "yes"]
    assert cache.get("k", 104) is None and cache.get("k", 101) is None  # expired, and gone
    cache.put("k", 110, "no")
    assert cache.get("k", 109) is None  # an older tick never reads a newer answer
    assert cache.hits == 3


def test_zero_ticks_or_no_tick_keeps_nothing():
    off: VerdictCache[str] = VerdictCache(0)
    off.put("k", 100, "yes")
    assert off.get("k", 100) is None
    on: VerdictCache[str] = VerdictCache(4)
    on.put("k", None, "yes")
    assert on.get("k", None) is None and on.get("k", 100) is None


def test_a_full_cache_drops_the_expired_answers_first_then_everything():
    cache: VerdictCache[int] = VerdictCache(2, max_entries=2)
    cache.put("old", 100, 1)
    cache.put("fresh", 105, 2)
    cache.put("new", 105, 3)  # full: "old" expired at 102 and goes
    assert cache.get("fresh", 105) == 2 and cache.get("new", 105) == 3
    cache.put("newer", 105, 4)  # full of live answers: start over
    assert cache.get("newer", 105) == 4 and cache.get("fresh", 105) is None


def test_guardrails_md_turns_both_speed_rules_on():
    rules = load_guardrails().rules
    assert rules.jev_cache_ticks == 4 and rules.parallel_reads is True


# ---------------------------------------------------------------- concurrent reads


def test_read_together_returns_the_same_answers_in_order_either_way():
    reads = {"a": lambda: 1, "b": lambda: {"x": 2}, "c": lambda: [3]}
    assert read_together(reads, False) == read_together(reads, True) == {"a": 1, "b": {"x": 2}, "c": [3]}
    assert list(read_together(reads, True)) == ["a", "b", "c"]


def test_parallel_reads_are_in_flight_at_the_same_time():
    met = threading.Barrier(3, timeout=5)  # each read waits for the other two: one after the other would fail
    reads = {name: (lambda n=name: (met.wait(), n)[1]) for name in ("me", "offers", "catalog")}
    assert read_together(reads, True) == {"me": "me", "offers": "offers", "catalog": "catalog"}


def test_a_failed_read_raises_the_first_error_in_order_after_the_others_finished():
    done: list[str] = []

    def fails(code):
        def read():
            raise BazaarError(code, code, 429)

        return read

    reads = {"me": fails("rate_limited"), "slow": lambda: done.append("slow"), "later": fails("bad_key")}
    for parallel in (False, True):
        with pytest.raises(BazaarError) as err:
            read_together(reads, parallel)
        assert err.value.code == "rate_limited"
    assert done == ["slow"]  # in order nothing after the failure ran; in parallel everything finished


def test_a_parallel_snapshot_equals_the_sequential_one(tmp_path):
    team, public = FakeTeam(offers=[ask(1, "LAV-02", 10, maker="t01")]), FakePublic()
    feed = MarketFeed(public.feed_window)
    extra = {"threads": lambda: team.my_threads("open")}
    one = read_snapshot(team, public, feed, clock(), extra=extra)
    two = read_snapshot(team, public, feed, clock(), parallel=True, extra=extra)
    assert one == two and one.extra == {"threads": {"threads": []}}
    assert team.reads.count("me") == 2 and team.reads.count("my_threads") == 2  # one of each per snapshot


# ---------------------------------------------------------------- the taker: identical moves


class CountingJev:
    """A deterministic Jev: `yes` below a total cost of 15, else `no`; counts its calls."""

    def __init__(self, reason=None):
        self.calls, self.reason = 0, reason

    def __call__(self, state):
        self.calls += 1
        verdict = "yes" if state["offer"]["total_cost"] < 15 else "no"
        return JevAdvice(verdict, 0.9 if verdict == "yes" else 0.2, {"yes": 0.9}, self.reason)


def run_taker(tmp_path, speed, ticks, *, live, jev=None, config=None, boards=None, dealers=None):
    team = FakeTeam()
    public = FakePublic(boards=boards or {}, **({"dealers": dealers} if dealers is not None else {}))
    lines: list[str] = []
    t = Taker(
        team,
        public,
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=config or TakerConfig(max_dealer_threads=0),
        **({"jev": jev} if jev is not None else {}),
        **parts(tmp_path, **speed),
    )
    for n in range(ticks):
        team.now = clock(tick=TICK + n)
        t.on_tick(team.now)
    decided = [
        (r["tick"], r["kind"], r["status"], r["chosen"], r["guardrail"], r["move"], (r.get("jev") or {}).get("verdict"))
        for r in rows(tmp_path)
        if "kind" in r
    ]
    return SimpleNamespace(team=team, taker=t, lines=lines, decided=decided)


def test_a_standing_ask_is_judged_once_and_every_tick_decides_the_same(tmp_path):
    boards = {"rastro": [ask(1, "LAV-02", 10), ask(2, "LAV-08", 20, asset=901)]}
    off_jev, on_jev = CountingJev(), CountingJev()
    off = run_taker(tmp_path / "off", SPEED_OFF, 6, live=False, jev=off_jev, boards=boards)
    on = run_taker(tmp_path / "on", SPEED_ON, 6, live=False, jev=on_jev, boards=boards)
    assert on.decided == off.decided and on.team.sent == off.team.sent == []
    # Two asks judged every tick: 12 calls in 6 ticks; cached, each is asked at ticks 100 and 104 only.
    assert off_jev.calls == 12 and on_jev.calls == 4 and on.taker.jev_cache.hits == 8


def test_live_the_cache_and_the_parallel_reads_send_the_same_writes(tmp_path):
    boards = {"rastro": [ask(1, "LAV-02", 10)], "v02": [ask(3, "LAV-08", 20, venue="v02", asset=901)]}
    config = TakerConfig(max_dealer_threads=3)
    off = run_taker(tmp_path / "off", SPEED_OFF, 4, live=True, jev=CountingJev(), boards=boards, config=config)
    on = run_taker(tmp_path / "on", SPEED_ON, 4, live=True, jev=CountingJev(), boards=boards, config=config)
    assert on.team.sent == off.team.sent and on.decided == off.decided
    assert ("accept", 1) in on.team.sent and any(s[0] == "open_thread" for s in on.team.sent)
    assert sorted(on.team.reads) == sorted(off.team.reads)  # the same reads, only at once


def test_a_failed_jev_call_is_asked_again_next_tick(tmp_path):
    boards = {"rastro": [ask(1, "LAV-02", 10)]}
    timeout = CountingJev(reason="timeout")
    run_taker(tmp_path, SPEED_ON, 3, live=False, jev=timeout, boards=boards)
    assert timeout.calls == 3


def test_a_cached_answer_carries_no_digest_and_says_it_was_cached(tmp_path):
    boards = {"rastro": [ask(1, "LAV-02", 10)]}
    run = run_taker(tmp_path, SPEED_ON, 2, live=False, jev=CountingJev(), boards=boards)
    jevs = [r["jev"] for r in rows(tmp_path) if r.get("kind") == "accept_ask"]
    assert [j["verdict"] for j in jevs] == ["yes", "yes"] and jevs[1]["reason"] == "cached"
    assert "digest" not in jevs[1] and run.taker.jev_cache.hits == 1


def test_the_pack_gate_asks_jev_once_for_an_unchanged_pack_state(tmp_path, monkeypatch):
    import bazaar_agent.jev as jev_mod
    from bazaar_agent.pack_gate import PACK_QUESTION, jev_pack_judge

    asked: list[int] = []
    reason: list[str | None] = [None]

    def judge(state, questions, api_key=None, timeout_s=None):
        asked.append(state["tick"])
        verdict = SimpleNamespace(verdict="no", value=0.3, reason=reason[0])
        return SimpleNamespace(verdicts={PACK_QUESTION: verdict})

    monkeypatch.setattr(jev_mod, "judge", judge)
    settings = SimpleNamespace(typesafe_api_key=None)
    pack = {"pack": "sobre_barrio", "pack_slots_left_this_game_hour": 3, "cash": 300}
    cached, uncached = jev_pack_judge(settings, 3.0, 4), jev_pack_judge(settings, 3.0)
    answers = [cached({**pack, "tick": t, "game_hour": 1.5 + t / 100}) for t in (100, 101, 102)]
    assert answers == [("no", 0.3)] * 3 and asked == [100]
    assert cached({**pack, "pack_slots_left_this_game_hour": 2, "tick": 103}) == ("no", 0.3) and asked == [100, 103]
    assert [uncached({**pack, "tick": t}) for t in (100, 101)] == [("no", 0.3)] * 2 and asked[-2:] == [100, 101]
    reason[0] = "timeout"  # a failure is never kept
    failing = jev_pack_judge(settings, 3.0, 4)
    failing({**pack, "tick": 200})
    failing({**pack, "tick": 201})
    assert asked[-2:] == [200, 201]


def test_the_taker_reads_its_open_threads_with_the_snapshot(tmp_path):
    run = run_taker(tmp_path, SPEED_ON, 1, live=False)
    assert run.team.reads.count("my_threads") == 1 and run.team.reads.count("me") == 1


def test_the_maker_posts_the_same_offers_with_parallel_reads(tmp_path):
    from bazaar_agent.agents.maker import Maker

    sent = []
    for name, speed in (("off", SPEED_OFF), ("on", SPEED_ON)):
        team = FakeTeam()
        m = Maker(
            team, FakePublic(), live=True, log=lambda line: None, now=lambda: 1000.0, **parts(tmp_path / name, **speed)
        )
        for n in range(3):
            team.now = clock(tick=TICK + n)
            m.on_tick(team.now)
        sent.append(team.sent)
    assert sent[0] == sent[1] and any(s[0] == "list_offer" for s in sent[0])


def test_parallel_reads_see_the_callers_context():
    import contextvars

    current = contextvars.ContextVar("current", default="none")
    current.set("tick 100 span")
    assert read_together({"a": current.get, "b": current.get}, True) == {"a": "tick 100 span", "b": "tick 100 span"}


class FakeHoldings:
    """The shared /me snapshot (#105): answers from 'Postgres', never calls /me itself."""

    def __init__(self, me):
        self.me_payload, self.calls, self.catalogs = me, 0, []

    def me(self, clock, *, clock_read_at=None):
        self.calls += 1
        return SimpleNamespace(me=self.me_payload, source="db")

    def observe_catalog(self, tick, catalog):
        self.catalogs.append(tick)


def test_a_parallel_snapshot_with_holdings_reads_me_once_from_the_snapshot():
    for parallel in (False, True):
        team, public = FakeTeam(), FakePublic()
        holdings = FakeHoldings(team.me())
        team.reads.clear()
        snap = read_snapshot(team, public, MarketFeed(public.feed_window), clock(), holdings, parallel=parallel)
        assert holdings.calls == 1 and "me" not in team.reads  # the snapshot answered: no live /me
        assert snap.holdings is not None and snap.me == holdings.me_payload and holdings.catalogs == [TICK]


def test_a_refused_dealer_thread_skips_only_that_conversation(tmp_path):
    from bazaar_agent.agents.dealer import BidPlan, Negotiation
    from bazaar_agent.agents.desk import Conversation

    class OneThreadRefused(FakeTeam):
        def thread(self, tid):
            if tid == 51:
                raise BazaarError("not_found", "no such thread", 404)
            return super().thread(tid)

    for name, speed in (("off", SPEED_OFF), ("on", SPEED_ON)):
        team = OneThreadRefused()
        lines: list[str] = []
        t = Taker(
            team,
            FakePublic(),
            live=True,
            log=lines.append,
            now=lambda: 1000.0,
            sleep=lambda s: None,
            config=TakerConfig(max_dealer_threads=0),
            **parts(tmp_path / name, **speed),
        )
        for dealer, tid in (("abuela", 50), ("chato", 51)):
            t.convs[dealer] = Conversation(
                dealer, "LAV-08", "uncommon", 52, "r", Negotiation(BidPlan(18, 1, 22)), tid, TICK
            )
        t.on_tick(clock(tick=TICK + 1))
        assert (t.convs["abuela"].ticks, t.convs["chato"].ticks) == (1, 0)  # abuela moved on, chato waits a tick
        assert ("say", 50, 18) in team.sent and not [s for s in team.sent if s[1] == 51]
        assert any("thread 51 with chato refused not_found; it waits a tick" in line for line in lines)


def test_the_card_of_an_unreadable_dealer_thread_is_not_bought_on_a_board_that_tick(tmp_path):
    """Our bid may still stand in a thread we could not read (and she may take it): its card waits a tick."""
    from bazaar_agent.agents.dealer import BidPlan, Negotiation
    from bazaar_agent.agents.desk import Conversation

    class Refused(FakeTeam):
        def thread(self, tid):
            raise BazaarError("not_found", "no such thread", 404)

    for name, speed in (("off", SPEED_OFF), ("on", SPEED_ON)):
        team = Refused()
        boards = {"rastro": [ask(1, "LAV-02", 10), ask(2, "LAV-08", 20, asset=901)]}
        t = Taker(
            team,
            FakePublic(boards=boards),
            live=True,
            log=lambda line: None,
            now=lambda: 1000.0,
            sleep=lambda s: None,
            config=TakerConfig(max_dealer_threads=0),
            **parts(tmp_path / name, **speed),
        )
        t.convs["abuela"] = Conversation(
            "abuela", "LAV-08", "uncommon", 52, "r", Negotiation(BidPlan(18, 1, 22)), 50, TICK
        )
        team.now = clock(tick=TICK + 1)  # the taker re-reads the clock before an accept
        t.on_tick(team.now)
        assert team.sent == [("accept", 1)]  # LAV-02 only: LAV-08 (the best score) waits for its thread


def test_a_cached_answer_is_served_on_a_tick_with_no_time_to_ask_jev(tmp_path):
    """The one declared behaviour change: the same state Jev answered a tick ago gets that answer even when this
    tick has too little time left to ask (without the cache: `undecided`, no budget). On a dealer thread a cached
    yes may close the deal early; it is still inside our max and the guardrails still run before the send."""
    from bazaar_agent.agents.runtime import TickWindow

    state = {"offer": {"item": "LAV-08", "total_cost": 12}, "cash": 300, "tick": TICK}
    answers = {}
    for name, speed in (("off", SPEED_OFF), ("on", SPEED_ON)):
        jev = CountingJev()
        t = run_taker(tmp_path / name, speed, 0, live=False, jev=jev).taker
        with_time = SimpleNamespace(
            snap=SimpleNamespace(clock=clock(tick=TICK)), jev_calls=0, window=TickWindow(TICK, 1010.0, lambda: 1000.0)
        )
        no_time = SimpleNamespace(
            snap=SimpleNamespace(clock=clock(tick=TICK + 1)),
            jev_calls=0,
            window=TickWindow(TICK + 1, 1002.0, lambda: 1000.0),
        )  # 2 s < jev_min_budget_s
        first = t._ask_jev(with_time, state)
        second = t._ask_jev(no_time, {**state, "tick": TICK + 1})
        answers[name] = (first.verdict, second.verdict, second.reason, jev.calls)
    assert answers == {"off": ("yes", "undecided", "no tick budget for jev", 1), "on": ("yes", "yes", "cached", 1)}


def test_keyed_reads_go_one_at_a_time_in_order_beside_the_public_ones():
    import time

    lock, state = threading.Lock(), {"now": 0, "peak": 0, "order": []}
    met = threading.Barrier(2, timeout=5)  # the first keyed read meets the public one: they overlap

    def keyed(name, wait=False):
        def read():
            if wait:
                met.wait()
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
                state["order"].append(name)
            time.sleep(0.02)
            with lock:
                state["now"] -= 1
            return name

        return read

    reads = {"me": keyed("me", wait=True), "catalog": lambda: (met.wait(), "catalog")[1]}
    reads |= {"offers": keyed("offers"), "threads": keyed("threads")}
    got = read_together(reads, True, keyed=("me", "offers", "threads"))
    assert got == {"me": "me", "catalog": "catalog", "offers": "offers", "threads": "threads"}
    assert state["peak"] == 1 and state["order"] == ["me", "offers", "threads"]  # one keyed request in flight


def test_a_failed_keyed_read_stops_the_keyed_ones_after_it():
    ran: list[str] = []

    def refused():
        raise BazaarError("rate_limited", "slow down", 429)

    reads = {"me": lambda: ran.append("me"), "offers": refused, "threads": lambda: ran.append("threads")}
    reads |= {"catalog": lambda: ran.append("catalog")}
    with pytest.raises(BazaarError) as err:
        read_together(reads, True, keyed=("me", "offers", "threads"))
    assert err.value.code == "rate_limited" and sorted(ran) == ["catalog", "me"]  # as in order: threads never ran
