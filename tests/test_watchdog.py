"""The live watchdog: pure rules over plain rows, and `run` against a throwaway Postgres schema."""

from __future__ import annotations

import json
import secrets
import time

import psycopg
import pytest
from psycopg import sql

from bazaar_agent import watchdog as wd
from bazaar_agent.guardrails import Guardrails

US = "t01"
RULES = Guardrails(
    live_watchdog_enabled=True,
    watchdog_window_ticks=120,
    watchdog_swap_cash_per_hour=40,
    watchdog_max_swaps_per_team=3,
    watchdog_repeat_price_max=3,
    watchdog_repeat_trip_ticks=20,
    watchdog_refusal_storm=50,
)


def settlement(sid, tick, items, price, persona=None, venue="rastro", fee=0):
    parties = sorted({i["frm"] for i in items} | {i["to"] for i in items})
    payload = {"settlement": sid, "tick": tick, "kind": "trade", "parties": parties, "venue": venue}
    payload |= {"persona": persona, "fee": fee, "items": items, "price": price}
    return {"id": 1000 + sid, "tick": tick, "payload": payload}


def card(aid, ref, frm, to, kind="card"):
    return {"id": aid, "kind": kind, "ref": ref, "frm": frm, "to": to}


def snap(tick, *cards):
    return {"tick": tick, "cards": [{"asset": a, "ref": r, "your_value": v} for a, r, v in cards]}


def drow(did, tick, kind, inputs=None, status="done", chosen=None, agent="taker", guardrail="allowed"):
    return {
        "id": did,
        "tick": tick,
        "agent": agent,
        "kind": kind,
        "status": status,
        "inputs": inputs or {},
        "chosen": chosen,
        "policy": {"guardrail": guardrail, "allowed": guardrail == "allowed"},
    }


# ---------------------------------------------------------------- (a) bad trades


def test_a_buy_above_the_official_value_trips_the_scope_that_made_it():
    trades = wd.trades_of(
        [
            settlement(1, 10, [card(500, "LAV-08", "abuela", US)], 30, persona="abuela", venue=None),
            settlement(2, 11, [card(501, "SAL-07", "t05", US)], 40),  # our accept of an ask
            settlement(3, 12, [card(502, "MAL-02", "t06", US)], 20),  # their accept of our bid
            settlement(4, 12, [card(503, "LAT-01", "t06", US)], 5),  # fine: below its value
        ],
        US,
    )
    snaps = [snap(13, (500, "LAV-08", 24.0), (501, "SAL-07", 32.5), (502, "MAL-02", 14.1), (503, "LAT-01", 7.0))]
    decisions = [drow(1, 11, "accept_ask", {"ref": "SAL-07"})]
    found = wd.bad_trades(trades, snaps, decisions, margin=0.0)
    assert [(f.scope, f.at) for f in found] == [("dealer_buy", 10), ("board_accept", 11), ("maker_post", 12)]
    assert "LAV-08" in found[0].reason and "30" in found[0].reason and "24" in found[0].reason
    assert all(f.level == "trip" and f.until_tick is None for f in found)


def test_a_sell_below_our_value_before_it_left_trips_and_a_fair_sell_does_not():
    trades = wd.trades_of(
        [
            settlement(1, 20, [card(600, "LAT-09", US, "abuela")], 10, persona="abuela", venue=None),
            settlement(2, 21, [card(601, "SAL-01", US, "t07")], 9),  # our accept of their bid
            settlement(3, 22, [card(602, "LAV-03", US, "t07")], 30),  # our ask filled: fine
        ],
        US,
    )
    snaps = [snap(19, (600, "LAT-09", 35.0), (601, "SAL-01", 12.0), (602, "LAV-03", 16.0)), snap(23)]
    decisions = [drow(1, 21, "accept_bid", {"asset_id": 601, "ref": "SAL-01"})]
    found = wd.bad_trades(trades, snaps, decisions, margin=0.0)
    assert [(f.scope, f.at) for f in found] == [("dealer_sell", 20), ("board_accept", 21)]


def test_a_trade_with_no_snapshot_value_or_a_pack_is_skipped_never_guessed():
    trades = wd.trades_of(
        [
            settlement(1, 10, [card(700, "sobre_barrio", "abuela", US, kind="pack")], 17, persona="abuela"),
            settlement(2, 10, [card(701, "LAV-08", "t04", US)], 99),  # no snapshot holds 701 yet
            settlement(3, 10, [card(702, "LAV-09", "t04", "t05")], 99),  # not ours
        ],
        US,
    )
    assert wd.bad_trades(trades, [snap(9)], [], margin=0.0) == []


def test_the_margin_is_the_guardrail_margin():
    trades = wd.trades_of([settlement(1, 10, [card(500, "LAV-08", "t04", US)], 24)], US)
    snaps = [snap(10, (500, "LAV-08", 24.0))]
    assert wd.bad_trades(trades, snaps, [], margin=0.0) == []
    assert [f.scope for f in wd.bad_trades(trades, snaps, [], margin=1.0)] == ["maker_post"]


def test_decision_rows_catch_a_bid_above_our_cap_an_ask_above_value_and_a_sell_at_a_loss():
    rows = [
        drow(1, 5, "dealer_bid", {"thread": 9, "max": 26, "final_max": None}, chosen={"kind": "bid", "price": 27}),
        drow(2, 5, "dealer_bid", {"thread": 9, "max": 26, "final_max": 30}, chosen={"kind": "bid", "price": 30}),
        drow(3, 6, "accept_ask", {"ref": "LAV-02", "total": 22, "value": 20.8}),
        drow(4, 6, "accept_bid", {"ref": "SAL-01", "surplus": -3.2}),
        drow(5, 6, "accept_ask", {"ref": "LAV-02", "total": 18, "value": 20.8}),
        drow(6, 6, "accept_ask", {"ref": "LAV-02", "total": 99, "value": 20.8}, status="failed"),  # never went out
    ]
    found = wd.decision_findings(rows)
    assert [(f.scope, f.at) for f in found] == [("dealer_buy", 5), ("board_accept", 6), ("board_accept", 6)]


# ---------------------------------------------------------------- (b) team swaps


def swap(sid, tick, team, gave, got):
    return settlement(sid, tick, [card(gave[0], gave[1], US, team), card(got[0], got[1], team, US)], 0)


def test_a_swap_that_gave_away_our_last_copy_trips_team_swap():
    trades = wd.trades_of([swap(1, 30, "t05", (800, "LAV-09"), (801, "LAT-02"))], US)
    found = wd.swap_rules(trades, [snap(29, (800, "LAV-09", 50)), snap(31, (801, "LAT-02", 9))], {}, RULES)
    assert [(f.scope, f.at) for f in found] == [("team_swap", 30)]
    assert "last copy" in found[0].reason and "LAV-09" in found[0].reason
    kept = [snap(31, (801, "LAT-02", 9), (805, "LAV-09", 50))]  # we still hold one: fine
    assert wd.swap_rules(trades, kept, {}, RULES) == []
    assert wd.swap_rules(trades, [snap(29)], {}, RULES) == []  # no snapshot after it yet: next tick


def test_too_many_swaps_with_one_team_trip_team_swap():
    trades = wd.trades_of(
        [swap(i, 40 + i, "t05", (900 + i, "LAV-01"), (950 + i, "LAT-0" + str(i))) for i in range(4)], US
    )
    after = [snap(50, *[(1000 + i, "LAV-01", 5) for i in range(3)])]
    found = wd.swap_rules(trades, after, {}, RULES)
    assert [f.scope for f in found] == ["team_swap"] and "t05" in found[0].reason and "4 swaps" in found[0].reason
    assert wd.swap_rules(trades[:3], after, {}, RULES) == []


def test_swap_cash_over_the_hourly_cap_trips_team_swap():
    assert wd.swap_rules([], [], {11: (12, 25), 12: (13, 15)}, RULES) == []  # 40: at the cap
    found = wd.swap_rules([], [], {11: (12, 25), 12: (13, 16)}, RULES)
    assert [(f.scope, f.at) for f in found] == [("team_swap", 13)] and "41" in found[0].reason


def say(did, tick, thread, cash):
    return {
        "decision_id": did,
        "tick": tick,
        "sdk_method": "say",
        "request": {"thread_id": thread, "swap": {"give": {"cash": cash}}},
    }


def test_swap_cash_counts_only_a_settled_swap_at_its_last_offer_and_the_accept_pay():
    decisions = [
        drow(1, 10, "team_offer", {"team": "t05", "thread": 11}),
        drow(2, 11, "team_accept", {"thread": 12}),
        drow(3, 10, "team_offer", {"team": "t06", "thread": 13}),  # posted, never settled: moved nothing
    ]
    executions = [say(1, 10, 11, 5), say(1, 12, 11, 9), say(1, 20, 11, 30), say(3, 10, 13, 25)]
    ledger = [{"tick": 11, "price": 14, "item": "team:12"}, {"tick": 11, "price": 99, "item": "team:13"}]
    trades = wd.trades_of([swap(50, 14, "t05", (1, "LAV-03"), (2, "SAL-01"))], US)
    assert wd.swap_cash(decisions, executions, ledger, trades) == {11: (14, 9), 12: (11, 14)}


def test_unsettled_swap_offers_never_trip_the_cash_rule():
    decisions = [drow(i, 10 + i, "team_offer", {"team": f"t0{i + 2}", "thread": 20 + i}) for i in range(3)]
    executions = [say(i, 10 + i, 20 + i, 15) for i in range(3)]
    cash = wd.swap_cash(decisions, executions, [], [])
    assert cash == {} and wd.swap_rules([], [], cash, RULES) == []


# ---------------------------------------------------------------- (c) the same price again


def test_the_same_price_twice_in_a_row_is_counted_per_scope_and_trips_for_a_while():
    bids = [drow(i, 10 + i, "dealer_bid", {"thread": 7}, chosen={"kind": "bid", "price": p}) for i, p in
            enumerate([20, 20, 20, 21, 21, 21])]  # fmt: skip
    for r in bids:
        r["thread_id"] = 7
    found = wd.repeat_price_rule(wd.sends_of(bids, []), tick=30, rules=RULES, since={})
    assert [(f.scope, f.until_tick) for f in found] == [("dealer_buy", 50)]
    assert "4 times" in found[0].reason
    assert wd.repeat_price_rule(wd.sends_of(bids[:5], []), tick=30, rules=RULES, since={}) == []  # 3: not more
    # Repeats at or before the scope's last breaker change are not counted again.
    assert wd.repeat_price_rule(wd.sends_of(bids, []), tick=30, rules=RULES, since={"dealer_buy": 13}) == []


def test_another_thread_in_between_still_counts_per_key_and_a_refused_send_is_not_a_send():
    rows = []
    for i, (t, p) in enumerate([(7, 20), (8, 20), (7, 20), (8, 20), (7, 20), (8, 20)]):
        r = drow(i, 10 + i, "dealer_bid", {"thread": t}, chosen={"kind": "bid", "price": p})
        r["thread_id"] = t
        rows.append(r)
    sends = wd.sends_of(rows, [])
    assert [f.scope for f in wd.repeat_price_rule(sends, 30, RULES, {})] == ["dealer_buy"]  # 2 + 2 = 4 > 3
    for r in rows:
        r["status"] = "failed"
    assert wd.sends_of(rows, []) == []


def test_maker_reposts_and_cash_free_swap_steps_are_not_spam():
    offers = [drow(i, 10 + i, "team_offer") for i in range(5)]
    execs = [
        {
            "decision_id": i,
            "tick": 10 + i,
            "sdk_method": "say",
            "request": {"thread_id": 4, "swap": {"want": {"cash": 3}}},
        }
        for i in range(5)
    ]
    posts = [
        drow(10 + i, 10 + i, "post_bid", {"ref": "LAV-02", "price": 12, "to": "t05"}, agent="maker") for i in range(5)
    ]
    sends = wd.sends_of(offers + posts, execs)
    found = wd.repeat_price_rule(sends, 30, RULES, {})
    assert found == [] and sends == []  # hint 5 is about dealers (#203 reviews: these tripped normal trading)


# ---------------------------------------------------------------- (d) duels


def duel(did, status, role, limit, price=None, rival=None, deadline=50, tick=49, days=None):
    payload = {"id": did, "role": role, "your_limit": limit}
    if rival is not None:
        payload["rival_offer"] = {"id": 1, "price": rival}
    return {"duel": did, "tick": tick, "status": status, "role": role, "your_limit": limit, "price": price,
            "days": days, "deadline_tick": deadline, "payload": payload}  # fmt: skip


def test_a_duel_deal_outside_our_limit_trips_duel_accept():
    rows = [
        duel(1, "deal", "seller", 40, price=40),  # at cost
        duel(2, "deal", "buyer", 104, price=105),  # above value
        duel(3, "deal", "seller", 40, price=55),  # fine
        duel(4, "deal", "buyer", 104, price=90),  # fine
        duel(5, "no_deal", "buyer", 104, price=None),
        duel(6, "deal", "seller", 40, price=30, days=10),  # two issues: price alone does not say
    ]
    found = wd.duel_findings(rows, [], tick=60)
    assert [(f.scope, f.level) for f in found] == [("duel_accept", "trip"), ("duel_accept", "trip")]
    assert "duel 1" in found[0].reason and "duel 2" in found[1].reason


def test_an_unanswered_acceptable_duel_at_its_last_tick_is_critical_never_a_trip():
    rows = [duel(7, "live", "seller", 40, rival=60, deadline=50, tick=49)]
    found = wd.duel_findings(rows, [], tick=49)
    assert [(f.scope, f.level) for f in found] == [(None, "critical")] and "duel 7" in found[0].reason
    moved = [drow(1, 49, "duel_offer", chosen={"duel": 7, "kind": "offer", "price": 62})]
    assert wd.duel_findings(rows, moved, tick=49) == []
    assert wd.duel_findings([duel(7, "live", "seller", 40, rival=30, deadline=50, tick=49)], [], tick=49) == []
    assert wd.duel_findings(rows, [], tick=47) == []  # not the last tick yet


# ---------------------------------------------------------------- (e) refusal storms


def test_a_refusal_storm_is_counted_by_reason_and_item_with_a_suggested_fix():
    rows = [
        drow(i, 10 + i % 50, "accept_ask", {"ref": "SAL-08"}, status="rejected",
             guardrail=f"denied: price {20 + i % 3} > official value 17 of SAL-08 (GET /api/me/value)")
        for i in range(75)
    ]  # fmt: skip
    rows += [
        drow(100 + i, 10, "accept_ask", {"ref": "LAV-01"}, status="rejected", guardrail="denied: x") for i in range(3)
    ]
    storms = wd.refusal_storms(rows, [], threshold=50)
    assert len(storms) == 1
    s = storms[0]
    assert s.count == 75 and s.item == "SAL-08" and "official value" in s.suggestion
    assert s.line().startswith("SAL-08 refused 75 times")


def test_server_refusal_codes_count_too():
    refused = [
        {"error_code": "insufficient_cash", "kind": "dealer_bid", "inputs": {"item": "LAV-09"}} for _ in range(6)
    ]
    storms = wd.refusal_storms([], refused, threshold=5)
    assert [(s.item, s.count) for s in storms] == [("LAV-09", 6)] and "cash" in storms[0].suggestion


def test_storm_warnings_are_rate_limited():
    state = wd.WatchdogState()
    storm = wd.Storm("denied: x", "SAL-08", 75, "fix it")
    assert state.should_warn(storm, 100) and not state.should_warn(storm, 101)
    assert state.should_warn(storm, 100 + wd.STORM_EVERY_TICKS)


# ---------------------------------------------------------------- Watchdog: bounded, never raises


def test_the_watchdog_swallows_any_error_and_logs_it():
    lines: list[str] = []

    def broken():
        raise RuntimeError("no database")

    w = wd.Watchdog(broken, lines.append, timeout_s=1.0)
    w.tick(10, RULES)  # must not raise
    assert any("watchdog" in line and "RuntimeError" in line for line in lines)


def test_the_watchdog_does_nothing_without_a_database():
    w = wd.Watchdog(None, lambda line: None)
    w.tick(10, RULES)


def test_a_slow_watchdog_never_holds_the_tick_longer_than_its_budget():
    lines: list[str] = []

    def slow():
        time.sleep(2.0)
        raise RuntimeError("late")

    w = wd.Watchdog(slow, lines.append, timeout_s=0.2)
    started = time.monotonic()
    w.tick(10, RULES)
    assert time.monotonic() - started < 1.0
    w.tick(11, RULES)  # the first is still running: skipped, never doubled
    assert any("still running" in line for line in lines)


# ---------------------------------------------------------------- run(): SQL against a throwaway schema


@pytest.fixture
def pg():
    from bazaar_agent import db
    from bazaar_agent.config import load_settings

    url = load_settings().database_url.get_secret_value()
    try:
        db.connect(url, app="bazaar-pytest").close()
    except (psycopg.OperationalError, db.DatabaseUrlError):
        pytest.skip("Postgres not reachable (uv run bazaar db up, or set DATABASE_URL)")
    name = sql.Identifier(f"bazaar_pytest_{secrets.token_hex(4)}")
    with db.connect(url, app="bazaar-pytest") as admin:
        admin.execute(sql.SQL("create schema {}").format(name))
    conn = db.connect(url, app="bazaar-pytest")
    conn.execute(sql.SQL("set search_path to {}, public").format(name))
    conn.commit()
    db.init_schema(conn)
    yield conn
    conn.close()
    with db.connect(url, app="bazaar-pytest") as admin:
        admin.execute(sql.SQL("drop schema if exists {} cascade").format(name))


def _decision(conn, tick, kind, inputs, status="done", chosen=None, dry_run=False, guardrail="allowed", thread=None):
    conn.execute(
        "insert into decisions (tick, kind, agent, candidates, chosen, status, dry_run, policy_checks, thread_id) "
        "values (%s, %s, 'taker', %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s)",
        (tick, kind, json.dumps(inputs), json.dumps(chosen) if chosen else None, status, dry_run,
         json.dumps({"guardrail": guardrail}), thread),
    )  # fmt: skip


def test_run_trips_on_live_rows_only_and_never_twice(pg):
    from bazaar_agent import breakers

    _decision(pg, 100, "dealer_bid", {"thread": 9, "max": 26}, chosen={"kind": "bid", "price": 40}, thread=9)
    _decision(pg, 100, "accept_ask", {"ref": "X", "total": 99, "value": 1}, dry_run=True)  # dry run: ignored
    pg.commit()
    lines: list[str] = []
    found = wd.run(pg, 101, RULES, lines.append)
    assert [(f.scope, f.level) for f in found] == [("dealer_buy", "trip")]
    active = {r.scope: r for r in breakers.rows(pg) if r.active(101)}
    assert set(active) == {"dealer_buy"} and active["dealer_buy"].source == "watchdog"
    # A human resets it: the same old evidence never trips it again.
    breakers.reset(pg, "dealer_buy", 102)
    wd.run(pg, 103, RULES, lines.append)
    assert not any(r.active(103) for r in breakers.rows(pg))


def test_run_never_shortens_a_human_trip_and_spam_trips_lapse(pg):
    from bazaar_agent import breakers

    breakers.trip(pg, "dealer_buy", "human stop", 50)
    for i in range(6):
        _decision(pg, 60 + i, "dealer_bid", {"thread": 7, "max": 30}, chosen={"kind": "bid", "price": 20}, thread=7)
    pg.commit()
    wd.run(pg, 70, RULES, lambda line: None)
    row = next(r for r in breakers.rows(pg) if r.scope == "dealer_buy")
    assert row.source == "manual" and row.until_tick is None  # the human's open-ended trip stands


def test_run_survives_a_broken_query_and_rolls_back(pg):
    pg.execute("alter table executions rename column sdk_method to gone")  # never public.executions
    pg.commit()
    lines: list[str] = []
    assert wd.run(pg, 10, RULES, lines.append) == []
    assert any("watchdog" in line for line in lines)
    pg.execute("select 1")  # the connection is usable again
