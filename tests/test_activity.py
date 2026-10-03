"""The activity watchdog (UB1): pure rules over plain rows, the hook's emissions, and the taker's wiring."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bazaar_agent import activity as act
from bazaar_agent.agents.status import StatusHub
from bazaar_agent.guardrails import Guardrails

RULES = Guardrails(activity_stall_seconds=30, deploy_guard_bench_ticks=10)
TICK = 500


def sent(agent, tick, kind="dealer_bid", status="done", dry_run=False):
    return {"agent": agent, "kind": kind, "status": status, "tick": tick, "dry_run": dry_run}


def denied(agent, tick, text, ref="MAL-09", price=60, kind="dealer_bid"):
    inputs = {"ref": ref, "price": price}
    return {"agent": agent, "kind": kind, "status": "rejected", "tick": tick, "inputs": inputs,
            "policy": {"guardrail": f"denied: {text}"}, "reason": "ladder"}  # fmt: skip


def clock(tick_seconds=30.0, doors="open", paused=False, t_hours=5.0):
    return SimpleNamespace(tick_seconds=tick_seconds, doors=doors, paused=paused, t_hours=t_hours)


# ---------------------------------------------------------------- the window


@pytest.mark.parametrize(
    ("seconds", "tick_s", "ticks"), [(30, 30, 1), (30, 15, 2), (15, 15, 1), (30, 60, 1), (45, 30, 2)]
)
def test_the_window_is_the_threshold_in_ticks_at_the_clocks_pace(seconds, tick_s, ticks):
    assert act.window_ticks(seconds, tick_s) == ticks


def test_an_unknown_pace_is_one_tick():
    assert act.window_ticks(30, 0) == 1 and act.window_ticks(30, float("nan")) == 1


# ---------------------------------------------------------------- what counts as activity


def test_done_sends_count_and_bookkeeping_dry_runs_and_guard_rows_do_not():
    assert act.is_activity(sent("maker", 1, "post_ask"))
    assert act.is_activity(sent("taker", 1, "some_new_send_kind"))  # an exclusion list: new sends count
    assert act.is_activity(sent("duels", 1, "duel_hold", status="approved"))  # holding inside a live duel
    assert not act.is_activity(sent("taker", 1, "dealer_bid", status="approved"))  # not sent yet
    assert not act.is_activity(sent("taker", 1, "dealer_bid", status="rejected"))
    assert not act.is_activity(sent("taker", 1, dry_run=True))
    assert not act.is_activity(sent("guard", 1, "approval_needed"))
    for kind in ("process_started", "dealer_opened", "dealer_closed", "approval_granted", "breaker_trip",
                 "activity_stall", "ladder_probe", "strategy_gate", "dealer_skip", "hold_duel"):  # fmt: skip
        assert not act.is_activity(sent("taker", 1, kind)), kind


def test_last_activity_is_per_agent():
    rows = [
        sent("taker", 10),
        sent("taker", 12, "accept_ask"),
        sent("maker", 7, "post_ask"),
        sent("taker", 13, "dealer_skip"),
    ]
    assert act.last_activity(rows) == {"taker": 12, "maker": 7}


# ---------------------------------------------------------------- stall detection


def test_no_agent_sent_in_the_window_is_a_stall_with_ticks_since_the_last_send():
    rows = [sent("taker", TICK - 5), sent("maker", TICK - 3, "post_ask")]
    report = act.assess(TICK, 1, 60, rows, [])
    assert report.activity == "stalled" and report.stalled_for_ticks == 3
    by = {a.agent: a.stalled_for_ticks for a in report.agents}
    assert by == {"taker": 5, "maker": 3}


def test_any_agent_that_sent_in_the_window_means_ok():
    rows = [sent("taker", TICK - 40), sent("duels", TICK - 1, "duel_offer")]
    report = act.assess(TICK, 1, 60, rows, [])
    assert report.activity == "ok" and report.stalled_for_ticks == 1 and report.agents == ()


def test_the_window_at_15_s_ticks_waits_two_ticks():
    rows = [sent("maker", TICK - 2, "post_ask")]
    assert act.assess(TICK, 2, 60, rows, []).activity == "ok"
    assert act.assess(TICK, 1, 60, rows, []).activity == "stalled"


def test_an_open_dealer_conversation_is_the_taker_negotiating():
    report = act.assess(TICK, 1, 60, [], [], taker_busy=True)
    assert report.activity == "ok" and report.stalled_for_ticks == 0


def test_nothing_at_all_in_the_read_window_is_a_stall_of_unknown_length():
    report = act.assess(TICK, 1, 60, [], [])
    assert report.activity == "stalled" and report.stalled_for_ticks is None
    assert [a.agent for a in report.agents] == ["taker"]
    assert "60+ ticks" in report.warn_lines()[0]


def test_an_unreadable_database_is_unknown_not_ok():
    assert act.assess(TICK, 1, 60, None).activity == "unknown"
    assert act.assess(TICK, 1, 60, None, idle="kill_switch").activity == "idle"
    assert act.assess(TICK, 1, 60, None, taker_busy=True).activity == "ok"


# ---------------------------------------------------------------- expected idle


def test_kill_switch_and_pause_file_are_idle():
    assert act.idle_reason(stops=("trading_enabled = false",)) == "kill_switch"
    assert act.idle_reason(stops=("pause file .local/PAUSE exists",)) == "pause_file"
    report = act.assess(TICK, 1, 60, [sent("taker", TICK - 9)], [], idle="kill_switch")
    assert report.activity == "idle" and report.idle_reason == "kill_switch" and report.warn_lines()


def test_doors_closed_or_a_paused_clock_are_idle():
    assert act.idle_reason(doors="closed") == "doors_closed"
    assert act.idle_reason(paused=True) == "clock_paused"
    assert act.idle_reason() is None


def test_a_market_test_bench_running_or_starting_soon_is_idle():
    rows = [{"id": 9, "tick": TICK - 3, "type": "bench.started", "payload": {"ticks": 10}}]
    assert act.bench_running(rows, TICK)
    assert not act.bench_running([*rows, {"id": 10, "tick": TICK - 1, "type": "bench.finished"}], TICK)
    assert not act.bench_running(rows, TICK + 20)  # past its length
    assert act.idle_reason(bench=True) == "market_test"
    soon = [{"event_id": "bench:5.1", "action": "bench", "at_hours": 5.05}]  # 6 ticks at 30 s
    assert act.idle_reason(upcoming=soon, t_hours=5.0, tick_seconds=30, lead_ticks=10) == "market_test"
    assert act.idle_reason(upcoming=soon, t_hours=5.0, tick_seconds=30, lead_ticks=3) is None


def test_a_live_duel_session_is_idle():
    live = [{"status": "live", "deadline_tick": TICK + 4, "tick": TICK - 1}]
    assert act.duel_session_live(live, TICK)
    assert not act.duel_session_live([{**live[0], "tick": TICK - 30}], TICK)  # the runner stopped refreshing it
    assert not act.duel_session_live([{**live[0], "status": "deal"}], TICK)
    assert act.idle_reason(duels=True) == "duel_session"


# ---------------------------------------------------------------- blockers


def test_the_top_blocker_is_the_most_frequent_refusal_numbers_ignored_with_an_example():
    refusals = [
        denied("taker", TICK - 1, "cash 300 - 60 < cash_floor 270"),
        denied("taker", TICK, "cash 290 - 61 < cash_floor 270", ref="MAL-10", price=61),
        denied("taker", TICK, "price 90 > max_price_rare 80", ref="LAT-10", price=90),
        denied("taker", TICK - 30, "price 90 > max_price_rare 80"),  # before the stall began: not counted
        denied("taker", TICK, "price 90 > max_price_rare 80"),
    ]
    report = act.assess(TICK, 1, 60, [sent("taker", TICK - 3)], refusals)
    top = report.top_blocker
    assert top is not None and top.rule == "cash_floor" and top.count == 2 and top.item == "MAL-10"
    assert top.line() == "cash_floor on MAL-10 at 61 (2x: cash 290 - 61 < cash_floor 270)"
    line = report.warn_lines()[0]
    assert line.startswith(f"tick {TICK} WARN activity: taker STALLED 3 ticks: cash_floor on MAL-10 at 61")


def test_rule_categories_are_coarse_ids():
    assert act.rule_of("price 30 > max_price_uncommon 26") == "max_price_uncommon"
    assert act.rule_of("jev: undecided (0.61 < 0.75)") == "jev_undecided"
    assert act.rule_of("refused insufficient_cash") == "cash_floor"
    assert act.rule_of("refused wait_for_tick") == "server_refusal"
    assert act.rule_of("something new") == "other"


def test_the_public_part_carries_no_price_card_or_text():
    refusals = [denied("taker", TICK, "price 90 > max_price_rare 80", ref="LAT-10", price=90)]
    public = act.assess(TICK, 1, 60, [], refusals).public()
    assert public == {"activity": "stalled", "stalled_for_ticks": None, "idle_reason": None,
                      "top_blocker": "max_price_rare"}  # fmt: skip
    assert "LAT-10" not in str(public) and "90" not in str(public)


def test_no_warn_line_ever_says_refused():
    refusals = [
        {"agent": "maker", "kind": "post_ask", "status": "rejected", "tick": TICK, "inputs": {"ref": "SAL-01"},
         "policy": {"guardrail": "allowed"}, "reason": "refused wait_for_tick (server refused it)"},
    ]  # fmt: skip
    report = act.assess(TICK, 1, 60, [], refusals)
    assert report.warn_lines() and all(" refused " not in f" {w} " for w in report.warn_lines())
    assert "refused" not in act.learning_of(report, TICK, "t01").text


# ---------------------------------------------------------------- the hook


class Watch(act.ActivityWatch):
    """The real hook with the Postgres read replaced by fixed rows (None: the read failed)."""

    def __init__(self, rows, **kw):
        super().__init__(None, self._log, **kw)
        self.lines: list[str] = []
        self.rows = rows

    def _log(self, line):
        self.lines.append(line)

    def _read(self, tick, read_ticks):
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


def stalled_rows():
    return act.Rows([sent("taker", TICK - 4)], [denied("taker", TICK, "cash 300 - 60 < cash_floor 270")], [], [])


def test_a_stall_logs_one_warn_per_agent_one_row_per_tick_and_one_learning_per_episode():
    decided, learned = [], []
    w = Watch(stalled_rows(), decide=lambda *a, **k: decided.append((a, k)), record=learned.append)
    for tick in (TICK, TICK + 1, TICK + 2):
        report = w.tick(tick, RULES, clock=clock(), us="t01")
        assert report is not None and report.activity == "stalled"
    assert sum("WARN activity: taker STALLED" in line for line in w.lines) == 3
    assert [a[1] for a, _ in decided] == ["activity_stall"] * 3
    assert all(k["chosen"] is False and k["status"] == "rejected" for _, k in decided)
    assert len(learned) == 1 and learned[0][0].kind == "activity_stall"
    assert learned[0][0].detail["event_id"] == f"activity_stall:{TICK}"


def test_an_ok_tick_ends_the_episode_and_writes_nothing():
    decided = []
    w = Watch(act.Rows([sent("maker", TICK, "post_ask")], [], [], []), decide=lambda *a, **k: decided.append(a))
    assert w.tick(TICK, RULES, clock=clock()).activity == "ok"
    assert decided == [] and w.lines == []


def test_idle_is_labelled_and_not_warned():
    w = Watch(stalled_rows(), decide=lambda *a, **k: pytest.fail("no row while idle"))
    report = w.tick(TICK, RULES, clock=clock(doors="closed"))
    assert report.activity == "idle" and report.idle_reason == "doors_closed" and w.lines == []
    assert w.tick(TICK, RULES, clock=clock(), stops=("pause file x",)).idle_reason == "pause_file"
    bench = act.Rows([], [], [{"id": 1, "tick": TICK - 1, "type": "bench.started", "payload": {"ticks": 5}}], [])
    assert Watch(bench).tick(TICK, RULES, clock=clock()).idle_reason == "market_test"
    duel = act.Rows([], [], [], [{"status": "live", "deadline_tick": TICK + 3, "tick": TICK}])
    assert Watch(duel).tick(TICK, RULES, clock=clock()).idle_reason == "duel_session"


def test_the_hook_never_raises_when_the_read_fails():
    w = Watch(RuntimeError("db gone"))
    assert w.tick(TICK, RULES, clock=clock()) is None  # nothing reported yet, nothing raised
    assert any("activity: check skipped (RuntimeError)" in line for line in w.lines)


def test_a_postgres_connect_failure_reads_as_unknown():
    lines: list[str] = []

    def boom():
        raise OSError("refused")

    w = act.ActivityWatch(boom, lines.append)
    report = w.tick(TICK, RULES, clock=clock())
    assert report is not None and report.activity == "unknown"
    assert any("Postgres read failed (OSError)" in line for line in lines)


def test_a_failing_decide_or_record_never_breaks_the_hook():
    def bad(*a, **k):
        raise RuntimeError("x")

    w = Watch(stalled_rows(), decide=bad, record=bad)
    assert w.tick(TICK, RULES, clock=clock()).activity == "stalled"


def test_off_when_the_rule_is_zero():
    w = Watch(stalled_rows())
    assert w.tick(TICK, Guardrails(activity_stall_seconds=0), clock=clock()) is None and w.lines == []


def test_the_pace_is_remembered_when_a_tick_has_no_clock():
    rows = act.Rows([sent("maker", TICK - 2, "post_ask")], [], [], [])
    w = Watch(rows)
    assert w.tick(TICK, RULES, clock=clock(tick_seconds=15)).activity == "ok"  # 2 ticks at 15 s
    assert w.tick(TICK, RULES, clock=None).window == 2


# ---------------------------------------------------------------- /health and /state


def test_health_and_state_carry_only_the_four_coarse_fields():
    hub = StatusHub("taker", True)
    report = act.assess(TICK, 1, 60, [], [denied("taker", TICK, "price 90 > max_price_rare 80", ref="LAT-10")])
    hub.activity({**report.public(), "top_blocker_text": "LAT-10 at 90", "stalled_for_ticks": True})
    health, state = hub.health(), hub.state()
    for view in (health, state):
        assert view["activity"] == "stalled" and view["top_blocker"] == "max_price_rare"
        assert "top_blocker_text" not in view and "stalled_for_ticks" not in view  # a bool is not a tick count
    hub.activity({"activity": "stalled", "top_blocker": "cash 300 < 270"})  # not a coarse id: dropped
    assert "top_blocker" not in hub.health()
    hub.activity(None)
    assert "activity" not in hub.state()


# ---------------------------------------------------------------- the taker


def test_the_live_taker_runs_the_check_after_its_sends_and_publishes_it(tmp_path, monkeypatch):
    from bazaar_agent.agents import taker as taker_mod
    from tests.agent_fakes import FakePublic, FakeTeam, rows
    from tests.agent_fakes import clock as fake_clock
    from tests.test_taker import taker

    monkeypatch.setattr(taker_mod, "kill_switch", lambda rules, path=None: ())
    t, lines, _ = taker(tmp_path, FakeTeam(), FakePublic(), live=True, live_watchdog_enabled=True,
                        activity_stall_seconds=30)  # fmt: skip
    t.watchdog = SimpleNamespace(tick=lambda tick, rules: None)
    t.activity = Watch(stalled_rows(), decide=t.rec.decide)
    published = []
    t.hub = SimpleNamespace(activity=published.append, tick=lambda *a: None, view=lambda **k: None,
                            decision=lambda row: None, execution=lambda row: None)  # fmt: skip
    t.rec.hub = None
    t.on_tick(fake_clock(tick=TICK))
    assert any("WARN activity: taker STALLED" in line for line in t.activity.lines)
    assert published and published[-1]["activity"] == "stalled" and published[-1]["top_blocker"] == "cash_floor"
    assert [r["kind"] for r in rows(tmp_path) if r.get("kind") == "activity_stall"] == ["activity_stall"]
    assert all(" refused " not in line for line in lines)
