"""The evals end to end on Postgres (a throwaway schema, like test_db.py), seeded with our real practice data.

Skipped when Postgres is unreachable. No game API and no real Phoenix: annotations go to a fake one.
"""

from __future__ import annotations

import json
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


def test_the_loop_backs_off_while_postgres_fails() -> None:
    waits: list[float] = []
    calls = {"n": 0}

    def step() -> None:
        calls["n"] += 1
        if calls["n"] in (2, 3):
            raise psycopg.OperationalError("down")
        if calls["n"] == 5:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        evals_cli.run_forever(60, step, waits.append)
    assert waits == [60, 60, 120, 60]


def test_the_loop_scores_when_an_input_moved_or_a_span_is_still_pending(
    database_url: str, schema: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = open_in(database_url, schema)
    db.init_schema(setup)
    setup.close()
    monkeypatch.setattr(evals_cli, "_connect", lambda: open_in(database_url, schema))
    passes: list[int | None] = []
    monkeypatch.setattr(evals_cli, "_pass", lambda conn, since, phoenix, as_json: passes.append(since))
    # (inputs, pending spans) per poll: new tick · same · same with a span pending · a duel from `duel done`
    polls = iter([((10, None), 0), ((10, None), 0), ((10, None), 2), ((10, "duel 85"), 0)])
    current: dict[str, Any] = {}

    def fake_forever(every: float, step: Any) -> None:
        for _ in range(4):
            current["inputs"], current["pending"] = next(polls)
            step()

    monkeypatch.setattr(evals_cli, "_inputs_state", lambda conn: current["inputs"])
    monkeypatch.setattr(evals_cli, "_pending", lambda conn: current["pending"])
    monkeypatch.setattr(evals_cli, "run_forever", fake_forever)
    result = CliRunner().invoke(evals_cli.evals_app, ["run", "--every", "60"])
    assert result.exit_code == 0, result.output
    assert len(passes) == 3  # the second poll saw nothing new


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
        "(150, 'duels', 'duel_accept', 'done', '{\"verdict\": \"accept\"}'), "
        "(151, 'duels', 'duel_offer', 'done', '{\"verdict\": \"undecided\"}'), "
        "(152, 'maker', 'post_ask', 'approved', '{\"verdict\": \"aggressive\"}'), "
        "(153, 'maker', 'hold_ask', 'approved', '{\"verdict\": \"yes\"}')"
    )
    seeded.commit()
    assert jev_calls(seeded) == {
        "duel_move": (2, 1),
        "list_price_choice": (1, 1),
        "reprice_or_hold": (1, 1),
        "offer_is_worth_accepting": (1, 1),
    }
