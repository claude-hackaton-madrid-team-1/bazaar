"""r1 probes for B17 decide_once (not committed)."""

import multiprocessing as mp
import threading

import psycopg
import pytest

from bazaar_agent.decisions import THREAD_CLOSED, DecisionLog
from tests.test_db import database_url, open_in, schema  # noqa: F401
from tests.test_taker_restart import _bid_row

N = 300


def _closing(tid):
    return _bid_row(tid, 100, 0, kind=THREAD_CLOSED, chosen=False, status="done", move={})


@pytest.mark.integration
def test_two_concurrent_pg_claimers_get_each_thread_once(database_url, schema, tmp_path):  # noqa: F811
    from bazaar_agent import db

    conn = open_in(database_url, schema)
    db.init_schema(conn)
    logs = [DecisionLog(tmp_path / f"m{i}", lambda: open_in(database_url, schema)) for i in range(2)]
    won = [[], []]
    gate = threading.Barrier(2)

    def run(i):
        gate.wait()
        for tid in range(1, N + 1):
            if logs[i].decide_once(_closing(tid)) is not None:
                won[i].append(tid)

    ts = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(won[0] + won[1]) == list(range(1, N + 1))
    rows = conn.execute("select count(*) from decisions where kind = 'dealer_closed'").fetchone()
    assert rows[0] == N
    print("pg split", len(won[0]), len(won[1]))


@pytest.mark.integration
def test_index_creation_fails_when_duplicates_already_exist(database_url, schema, tmp_path):  # noqa: F811
    """A store that already holds two dealer_closed rows for one thread (e.g. written by #140's plain decide()
    from two overlapping takers) cannot take the new index: init_schema raises."""
    from bazaar_agent import db

    conn = open_in(database_url, schema)
    db.init_schema(conn)
    conn.execute("drop index decisions_thread_closed")
    conn.commit()
    log = DecisionLog(tmp_path, lambda: open_in(database_url, schema))
    log.decide(_closing(5))
    log.decide(_closing(5))
    with pytest.raises(psycopg.Error):
        db.init_schema(conn)


def _proc_claim(path, q, start):
    from pathlib import Path

    log = DecisionLog(Path(path))
    start.wait()
    q.put([tid for tid in range(1, 151) if log.decide_once(_closing(tid)) is not None])


def test_two_processes_jsonl_claim_each_thread_once(tmp_path):
    ctx = mp.get_context("spawn")
    q, start = ctx.Queue(), ctx.Barrier(2)
    ps = [ctx.Process(target=_proc_claim, args=(str(tmp_path), q, start)) for _ in range(2)]
    for p in ps:
        p.start()
    a, b = q.get(timeout=60), q.get(timeout=60)
    for p in ps:
        p.join()
    assert sorted(a + b) == list(range(1, 151))
    print("jsonl split", len(a), len(b))


def test_a_dry_run_or_sim_closing_row_blocks_a_live_claim(tmp_path):
    """The JSONL check ignores dry_run (and agent/game): any earlier dealer_closed row with that thread id wins."""
    log = DecisionLog(tmp_path)
    assert log.decide_once(_bid_row(42, 5, 0, kind=THREAD_CLOSED, chosen=False, status="done", move={}, dry_run=True))
    assert log.decide_once(_closing(42)) is not None  # FAILS if a dry-run row claims the thread
