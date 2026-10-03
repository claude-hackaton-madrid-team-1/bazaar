"""The evals end to end on Postgres (a throwaway schema, like test_db.py), seeded with our real practice data.

Skipped when Postgres is unreachable. No game API and no real Phoenix: annotations go to a fake one.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest
from typer.testing import CliRunner

from bazaar_agent import db
from bazaar_agent.duel_store import save_duels
from bazaar_agent.evals import cli as evals_cli
from bazaar_agent.evals.phoenix import PhoenixAnnotator
from bazaar_agent.evals.run import run_once
from tests import test_db
from tests.evals.conftest import LEVELS, OURS, load
from tests.evals.test_phoenix import FakePhoenix, client, span
from tests.test_db import open_in

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def private_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Advisory locks are database-wide: a per-test namespace keeps other sessions' evals out of these tests."""
    from bazaar_agent.evals import inline

    namespace = secrets.token_hex(4)
    monkeypatch.setattr(inline, "lock_key", lambda agent: f"pytest-{namespace}:bazaar-evals:{agent}")


def lock(conn: psycopg.Connection, agent: str) -> bool:
    from bazaar_agent.evals.inline import lock_key

    row = conn.execute("select pg_try_advisory_lock(hashtext(%s))", (lock_key(agent),)).fetchone()
    return bool(row and row[0])


# The throwaway-schema fixtures of test_db.py: every test gets its own schema, dropped afterwards.
database_url, schema, conn = test_db.database_url, test_db.schema, test_db.conn

TRADE_SETTLEMENT = {
    "id": 900001,
    "tick": 170,
    "type": "settlement",
    "actor": "",
    "payload": {
        "settlement": 500,
        "tick": 170,
        "kind": "trade",
        "venue": "rastro",
        "persona": None,
        "parties": ["t06", OURS],
        "price": 12,
        "fee": 2,
        "items": [{"id": 300, "to": OURS, "frm": "t06", "ref": "LAV-02", "kind": "card"}],
    },
}


def seed(conn: psycopg.Connection) -> None:
    db.load_events(conn, [*load("feed_ours.json")["events"], TRADE_SETTLEMENT])
    with conn.cursor() as cur:
        cur.executemany(
            "insert into dealer_curves (thread_id, dealer, item, opening_ask, fill_price) values (%s, %s, %s, %s, %s)",
            [(10_000 + i, *row) for i, row in enumerate(load("dealer_curves.json")["rows"])],
        )
        cur.executemany("insert into traders (id, kind, level) values (%s, 'dealer', %s)", list(LEVELS.items()))
        score = json.dumps({"team": OURS, "duel_points": 0.0, "ladder_points": 0.058, "bench_efficiency": None})
        cur.execute(
            "insert into snapshots (tick, cash, assets, score) values (168, 353, '[]', %s), (171, 339, %s, %s)",
            (score, json.dumps([{"id": 300, "ref": "LAV-02", "your_value": 20.0}]), score),
        )
        cur.execute(
            "insert into decisions (tick, agent, kind, status, dry_run, candidates, jev) values "
            "(169, 'taker', 'accept_ask', 'done', false, %s, %s)",
            (json.dumps({"ref": "LAV-02"}), json.dumps({"verdict": "yes", "value": 0.81})),
        )
    conn.commit()
    save_duels(conn, load("duels_done.json")["duels"], None)


@pytest.fixture
def seeded(conn: psycopg.Connection) -> psycopg.Connection:
    seed(conn)
    return conn


def rows(conn: psycopg.Connection, query: str) -> list[tuple[Any, ...]]:
    found = conn.execute(query).fetchall()  # type: ignore[arg-type]
    conn.commit()
    return found


def test_a_pass_scores_every_target_and_a_second_pass_changes_nothing(seeded: psycopg.Connection) -> None:
    first = run_once(seeded, OURS)
    assert first.scored == {"duel": 20, "dealer": 6, "trade": 1}  # 6 practice duels were still live
    assert first.changed == 27
    assert run_once(seeded, OURS).changed == 0
    trade = rows(
        seeded,
        "select score::float, realized_surplus::float, jev_question, jev_right, decision_id from outcomes "
        "where subject = 'settlement:500'",
    )
    assert trade == [(pytest.approx(0.3), 6, "offer_is_worth_accepting", True, 1)]


def test_the_scorecard_and_ladder_views_read_like_the_rules(seeded: psycopg.Connection) -> None:
    run_once(seeded, OURS)
    card = {r[0]: r for r in rows(seeded, "select target, day, outcomes, scored, bad, worst from eval_scorecard")}
    assert card["duel"][1:5] == ("fri", 20, 20, 8)  # 8 practice duels with the rival inside our limit, unplayed
    assert card["duel"][5][0] == "duel:119"  # worst first: missed deals score 0, ties by subject
    assert card["dealer"][1:3] == ("fri", 6)
    ladder = rows(
        seeded, "select level, dealers, threads, deals, best3_share::float, best3 from eval_ladder order by level"
    )
    assert ladder == [
        (1, "abuela", 5, 4, pytest.approx(0.733), ["thread:99", "thread:101", "thread:110"]),
        (2, "chato", 1, 0, 0, ["thread:187"]),
    ]
    calibration = rows(seeded, "select question, decided, n_right, n_wrong from eval_jev_calibration")
    assert calibration == [("offer_is_worth_accepting", 1, 1, 0)]


def test_a_better_learned_floor_rescores_and_requeues_the_annotation(seeded: psycopg.Connection) -> None:
    run_once(seeded, OURS)
    seeded.execute("update outcomes set annotated_at = now()")
    seeded.execute(
        "insert into dealer_curves (thread_id, dealer, item, opening_ask, fill_price) values "
        "(99999, 'abuela', 'SAL-01', 12, 6)"
    )
    seeded.commit()
    again = run_once(seeded, OURS)
    assert again.changed == 3  # 99, 101 and 110 are commons: their floor moved from 7 to 6
    requeued = rows(seeded, "select subject, score::float from outcomes where annotated_at is null order by subject")
    assert requeued == [
        ("thread:101", pytest.approx(0.5)),
        ("thread:110", pytest.approx(0.5)),
        ("thread:99", pytest.approx(0.8333)),
    ]


def test_outcomes_land_on_their_phoenix_traces(seeded: psycopg.Connection) -> None:
    fake = FakePhoenix({"bazaar.duel.id:85": [span("a" * 32, "1" * 16)]})
    summary = run_once(seeded, OURS, annotator=PhoenixAnnotator(client(fake), "bazaar"))
    assert (summary.annotated, summary.annotation_ids, summary.no_span) == (1, ("ann-0",), 26)
    assert rows(seeded, "select trace_id, span_id from outcomes where subject = 'duel:85'") == [("a" * 32, "1" * 16)]
    posted = [json.loads(r.content) for r in fake.requests if r.method == "POST"]
    assert posted[0]["data"][0]["result"]["score"] == pytest.approx(0.94**7, abs=1e-4)
    for _ in range(3):  # a span that never lands stops being searched after three passes
        run_once(seeded, OURS, annotator=PhoenixAnnotator(client(fake), "bazaar"))
    searches = sum(1 for r in fake.requests if r.method == "GET")
    run_once(seeded, OURS, annotator=PhoenixAnnotator(client(fake), "bazaar"))
    assert sum(1 for r in fake.requests if r.method == "GET") == searches


def test_a_lagging_live_payload_never_overwrites_a_finished_duel(seeded: psycopg.Connection) -> None:
    stale = {"duel": 85, "status": "live", "role": "seller", "your_limit": 109, "deadline_tick": 156}
    save_duels(seeded, [stale], 999)
    assert rows(seeded, "select status, price from duels where duel = 85") == [("deal", 138)]
    newer = {"duel": 125, "status": "live", "role": "seller", "your_limit": 68, "rounds": 3}
    save_duels(seeded, [newer], 999)
    assert rows(seeded, "select tick, rounds from duels where duel = 125") == [(999, 3)]


def test_the_old_outcomes_table_migrates_in_place(conn: psycopg.Connection) -> None:
    conn.execute("drop view eval_scorecard, eval_ladder, eval_jev_calibration")
    conn.execute("drop table outcomes")
    conn.execute(  # the shape that shipped before the evals
        "create table outcomes (decision_id bigint primary key references decisions(id), realized_surplus numeric, "
        "ladder_share numeric, jev_right bool, recorded_tick int)"
    )
    conn.commit()
    db.init_schema(conn)
    db.init_schema(conn)
    keys = rows(conn, "select count(*) from pg_constraint where conrelid = 'outcomes'::regclass and contype = 'p'")
    assert keys == [(0,)]
    assert rows(conn, "select count(*) from eval_scorecard") == [(0,)]


@pytest.fixture
def cli_db(database_url: str, schema: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    setup = open_in(database_url, schema)
    db.init_schema(setup)
    seed(setup)
    setup.close()
    monkeypatch.setattr(evals_cli, "_connect", lambda: open_in(database_url, schema))
    monkeypatch.setenv("BAZAAR_TEAM_ID", OURS)
    yield


def test_the_cli_runs_and_reports(cli_db: None) -> None:
    runner = CliRunner()
    ran = runner.invoke(evals_cli.evals_app, ["run", "--no-phoenix"])
    assert ran.exit_code == 0, ran.output
    assert "scored dealer 6, duel 20, trade 1" in ran.output and "27 new/changed" in ran.output
    report = runner.invoke(evals_cli.evals_app, ["report", "--json"])
    assert report.exit_code == 0, report.output
    data = json.loads(report.output)
    assert {r["target"] for r in data["scorecard"]} == {"duel", "dealer", "trade"}
    assert data["official"]["ladder_points"] == 0.058
    assert data["jev_calibration"][0]["right"] == 1
    assert len([w for w in data["worst"] if w["target"] == "duel"]) == 5
    shown = runner.invoke(evals_cli.evals_app, ["report"])
    assert shown.exit_code == 0 and "Dealer ladder" in shown.output


def test_the_cli_imports_a_duel_runner_log(cli_db: None, tmp_path: Any) -> None:
    log = tmp_path / "duels.jsonl"
    log.write_text(json.dumps({"tick": 300, "response": {"duels": [{"duel": 777, "status": "live"}]}}) + "\n")
    out = CliRunner().invoke(evals_cli.evals_app, ["import-duels", str(log), str(tmp_path / "missing.jsonl")])
    assert out.exit_code == 0 and "1 duel snapshot(s) upserted" in out.output and "no such file" in out.output


def clock(tick: int) -> Any:
    from bazaar_agent.ticks import Clock

    return Clock.model_validate({"tick": tick, "next_tick_in": 20, "tick_seconds": 30})


def test_the_loop_runs_on_game_ticks_every_n_and_only_when_an_input_moved(
    database_url: str, schema: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = open_in(database_url, schema)
    db.init_schema(setup)
    setup.close()
    monkeypatch.setattr(evals_cli, "_connect", lambda: open_in(database_url, schema))
    passes: list[int] = []
    # (inputs, pending spans) per due tick: new · nothing new · a span pending · nothing · a `duel done` duel
    states = iter([((10, None), 0), ((10, None), 0), ((10, None), 2), ((10, None), 0), ((10, "duel 85"), 0)])
    current: dict[str, Any] = {}
    monkeypatch.setattr(evals_cli, "_inputs_state", lambda conn: current["inputs"])
    monkeypatch.setattr(evals_cli, "_pending", lambda conn: current["pending"])
    gate = evals_cli.TickGate(3, lambda conn: passes.append(current["tick"]), phoenix=True)
    for tick in (100, 101, 102, 103, 106, 107, 109, 112):
        due = gate.last_tick is None or tick - gate.last_tick >= 3
        if due:
            current["inputs"], current["pending"] = next(states)
        current["tick"] = tick
        gate(clock(tick))
    assert passes == [100, 106, 112]  # due at 100, 103, 106, 109, 112; 103 and 109 saw nothing new
    assert gate.conn is not None
    gate.conn.close()


def test_a_postgres_error_drops_the_connection_and_waits_for_the_next_due_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        closed = False

        def close(self) -> None:
            self.closed = True

    opened: list[Broken] = []

    def connect() -> Broken:
        opened.append(Broken())
        return opened[-1]

    def down(conn: Any) -> Any:
        raise psycopg.OperationalError("down")

    monkeypatch.setattr(evals_cli, "_connect", connect)
    monkeypatch.setattr(evals_cli, "_inputs_state", down)
    gate = evals_cli.TickGate(2, lambda conn: None, phoenix=False)
    with pytest.raises(psycopg.OperationalError):
        gate(clock(10))  # run_per_tick reports it and goes on with the next tick
    assert gate.conn is None and opened[0].closed
    gate(clock(11))  # not due yet: no reconnect storm during an outage
    assert len(opened) == 1


def test_the_cli_loop_reads_the_keyless_public_clock(cli_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import bazaar_agent.sdk as sdk
    import bazaar_agent.ticks as ticks

    seen: dict[str, Any] = {}

    def fake_run_per_tick(read_clock: Any, on_tick: Any, **kwargs: Any) -> int:
        seen["reader"], seen["gate"] = read_clock, on_tick
        return 0

    monkeypatch.setattr(ticks, "run_per_tick", fake_run_per_tick)
    result = CliRunner().invoke(evals_cli.evals_app, ["run", "--every-ticks", "6", "--no-phoenix"])
    assert result.exit_code == 0, result.output
    assert isinstance(seen["reader"].__self__, sdk.PublicBazaar)  # no team key on this client
    assert seen["gate"].every_ticks == 6 and "every 6 game ticks" in result.output


def test_the_gate_reads_inputs_and_pending_spans(seeded: psycopg.Connection) -> None:
    before = evals_cli._inputs_state(seeded)
    assert before[0] == 170 and before[2] == 171  # newest feed tick, newest /me snapshot
    run_once(seeded, OURS)
    assert evals_cli._pending(seeded) == 27  # nothing annotated yet: every outcome waits for its span
    save_duels(seeded, [{"duel": 4242, "status": "no_deal", "role": "seller", "your_limit": 5}], None)
    assert evals_cli._inputs_state(seeded) != before  # a duel stored by `duel done` moves the gate


def test_a_cash_change_the_trade_cannot_explain_falls_back_to_the_price(seeded: psycopg.Connection) -> None:
    seeded.execute("update snapshots set cash = 339 - 270 where tick = 171")  # a venue bond in between
    seeded.commit()
    run_once(seeded, OURS)
    details = rows(
        seeded, "select details->>'cash_from', realized_surplus::float from outcomes where subject = 'settlement:500'"
    )
    assert details == [("price", 6.0)]  # 20 - (12 + 2), not 20 - 284


def test_phoenix_down_mid_pass_keeps_the_scores(seeded: psycopg.Connection) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gone", request=request)

    warnings: list[str] = []
    annotator = PhoenixAnnotator(httpx.Client(base_url="http://x", transport=httpx.MockTransport(down)), "bazaar")
    summary = run_once(seeded, OURS, annotator=annotator, warn=warnings.append)
    assert summary.changed == 27 and summary.annotated == 0 and "retrying next pass" in warnings[0]


def test_jev_calls_are_counted_per_question_each_agent_asks(seeded: psycopg.Connection) -> None:
    from bazaar_agent.evals.inputs import jev_calls

    seeded.execute(
        "insert into decisions (tick, agent, kind, status, jev) values "
        "(150, 'duels', 'duel_offer', 'done', '{\"verdict\": \"counter\", \"digest\": \"d1\"}'), "
        "(151, 'duels', 'duel_offer', 'done', '{\"verdict\": \"counter\", \"digest\": \"d1\"}'), "
        "(152, 'duels', 'duel_accept', 'done', '{\"verdict\": \"undecided\", \"digest\": \"d2\"}'), "
        "(153, 'duels', 'duel_offer', 'done', '{\"verdict\": \"undecided\", \"reason\": \"no tick budget for jev\"}'), "
        "(154, 'maker', 'post_ask', 'approved', '{\"verdict\": \"aggressive\", \"digest\": \"m1\"}'), "
        "(155, 'maker', 'post_ask', 'approved', '{\"verdict\": \"aggressive\", \"reason\": \"cached\"}'), "
        "(156, 'maker', 'hold_ask', 'approved', '{\"verdict\": \"yes\", \"digest\": \"m2\"}'), "
        "(157, 'taker', 'accept_ask', 'approved', '{\"verdict\": \"no\", \"reason\": \"cached (below_threshold)\"}')"
    )
    seeded.commit()
    assert jev_calls(seeded) == {
        "duel_move": (2, 1),  # d1 recorded on two ticks is one call; no budget is no call
        "list_price_choice": (1, 1),  # the cached reuse is not a call
        "reprice_or_hold": (1, 1),
        "offer_is_worth_accepting": (1, 1),  # no digest on the taker's verdicts; its cached reuse is no call
    }


def test_one_unreadable_duel_never_stops_the_pass(seeded: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    from bazaar_agent.evals import run as run_module

    real = run_module.score_duel

    def flaky(duel: Any, closure: Any = None) -> Any:
        if duel.get("duel") == 85:
            raise TypeError("'int' object is not iterable")
        return real(duel, closure)

    monkeypatch.setattr(run_module, "score_duel", flaky)
    warnings: list[str] = []
    summary = run_once(seeded, OURS, warn=warnings.append)
    assert summary.scored["duel"] == 19 and warnings == ["evals: duel 85 skipped, unreadable payload (TypeError)"]


def test_an_unreadable_settlement_is_skipped(seeded: psycopg.Connection) -> None:
    from bazaar_agent.evals.inputs import our_settlements

    bad = {**TRADE_SETTLEMENT, "id": 900002, "payload": {**TRADE_SETTLEMENT["payload"], "price": "lots"}}
    worse = {**TRADE_SETTLEMENT, "id": 900003, "payload": {**TRADE_SETTLEMENT["payload"], "items": 3}}
    for e in (bad, worse):  # straight into the table: the monitor's own loader would refuse them
        seeded.execute(
            "insert into feed_events (id, tick, type, actor, payload) values (%s, %s, %s, %s, %s)",
            (e["id"], e["tick"], e["type"], e["actor"], json.dumps(e["payload"])),
        )
    seeded.commit()
    assert [s.settlement for s in our_settlements(seeded, OURS, None)] == [500]


def test_an_agent_scores_and_annotates_only_its_own_targets(seeded: psycopg.Connection) -> None:
    from bazaar_agent.evals import store

    duels_only = run_once(seeded, OURS, targets={"duel"})
    assert duels_only.scored == {"duel": 20} and duels_only.notes == ()
    taker = run_once(seeded, OURS, targets={"dealer", "trade"})
    assert taker.scored == {"dealer": 6, "trade": 1}
    assert {p.target for p in store.pending_annotations(seeded, {"dealer"})} == {"dealer"}
    assert len(store.pending_annotations(seeded)) == 27


def test_an_agents_pass_makes_no_network_call_but_postgres(
    database_url: str, schema: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    from bazaar_agent.evals.inline import TickEvals

    setup = open_in(database_url, schema)
    db.init_schema(setup)
    seed(setup)
    setup.close()
    calls: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        calls.append("network")
        raise AssertionError("a pass called the network")

    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    logs: list[str] = []
    for agent in ("duels", "taker", "maker"):
        evals = TickEvals(
            agent, 1, lambda: open_in(database_url, schema), lambda c: OURS, lambda: None, logs.append, lambda w: w()
        )
        evals.after_tick(1)
        assert evals.after_tick(2) is True
    assert calls == [] and not [m for m in logs if "failed" in m], logs
    assert [m.split(":")[1].split("·")[0].strip() for m in logs if "new/changed" in m] == [
        "duel 20",
        "dealer 6, trade 1",
        "nothing settled yet",
    ]


def test_a_second_process_of_the_same_kind_skips_while_the_first_scores(
    database_url: str, schema: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bazaar_agent.evals.inline import TickEvals

    setup = open_in(database_url, schema)
    db.init_schema(setup)
    holder = open_in(database_url, schema)
    assert lock(holder, "taker")
    logs: list[str] = []
    evals = TickEvals(
        "taker", 1, lambda: open_in(database_url, schema), lambda c: OURS, lambda: None, logs.append, lambda w: w()
    )
    evals.after_tick(1)
    evals.after_tick(2)
    assert logs == ["evals (taker): another taker process is scoring; skipped 1x"]
    holder.close()  # the lock goes with its session
    evals.after_tick(3)
    assert any("new/changed" in m for m in logs[1:]), logs
    setup.close()


def test_the_cli_pass_scores_only_the_kinds_no_agent_is_scoring(cli_db: None, database_url: str, schema: Any) -> None:
    holder = open_in(database_url, schema)
    assert lock(holder, "duels")
    ran = CliRunner().invoke(evals_cli.evals_app, ["run", "--no-phoenix"])
    holder.close()
    assert ran.exit_code == 0, ran.output
    assert "the duels agent is scoring duel right now" in ran.output
    assert "scored dealer 6, trade 1" in ran.output and "duel" not in ran.output.split("scored")[1].split("·")[0]


def test_the_cli_pass_releases_its_locks_and_timeouts_even_when_the_annotator_fails(
    cli_db: None, database_url: str, schema: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = open_in(database_url, schema)

    def broken(phoenix: bool) -> Any:
        raise RuntimeError("config error mid-run")

    monkeypatch.setattr(evals_cli, "_annotator", broken)
    with pytest.raises(RuntimeError):
        evals_cli._pass(conn, None, True, False)
    other = open_in(database_url, schema)
    got = [
        other.execute("select pg_try_advisory_lock(hashtext(%s))", (f"bazaar-evals:{a}",)).fetchone()[0]
        for a in ("duels", "taker", "maker")
    ]
    assert got == [True, True, True]  # nothing left held for the agents
    assert conn.execute("show idle_session_timeout").fetchone()[0] in ("0", "0ms")
    other.close()
    conn.close()


def test_evals_run_json_stays_pure_json_when_a_kind_is_busy(cli_db: None, database_url: str, schema: Any) -> None:
    holder = open_in(database_url, schema)
    assert lock(holder, "duels")
    ran = CliRunner().invoke(evals_cli.evals_app, ["run", "--no-phoenix", "--json"])
    holder.close()
    assert ran.exit_code == 0
    assert json.loads(ran.stdout)["scored"] == {"dealer": 6, "trade": 1}


def test_a_failing_annotator_close_still_unlocks_and_the_loop_drops_its_session(
    cli_db: None, database_url: str, schema: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Closing:
        def find(self, query: Any) -> list[Any]:
            return []

        def close(self) -> None:
            raise UnicodeEncodeError("ascii", "", 0, 1, "a non-ASCII key")

    monkeypatch.setattr(evals_cli, "_annotator", lambda phoenix: Closing())
    gate = evals_cli.TickGate(1, lambda conn: evals_cli._pass(conn, None, True, False), phoenix=True)
    from bazaar_agent.ticks import Clock

    with pytest.raises(UnicodeEncodeError):
        gate(Clock.model_validate({"tick": 1, "next_tick_in": 20, "tick_seconds": 30}))
    assert gate.conn is None  # the loop dropped its session
    other = open_in(database_url, schema)
    assert [lock(other, a) for a in ("duels", "taker", "maker")] == [True, True, True]
    other.close()
