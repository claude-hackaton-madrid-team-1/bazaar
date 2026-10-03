"""The taker's feed archive (N12): the window it already reads goes into `feed_events`, never at a tick's cost."""

import contextlib
import json

import psycopg
import pytest
from typer.testing import CliRunner

from bazaar_agent.agents.runtime import MarketFeed
from tests.test_db import database_url, open_in, schema  # noqa: F401  (fixtures)
from tests.test_learn_reader import FEED

WINDOW = [e for e in FEED if "id" in e]


class FailingTransactionConn:
    """Reads answer (no rows); any write transaction fails."""

    closed = False
    autocommit = True

    def execute(self, query, params=None):  # type: ignore[no-untyped-def]
        class Rows:
            def fetchall(self):  # type: ignore[no-untyped-def]
                return []

        return Rows()

    @contextlib.contextmanager
    def transaction(self):
        raise psycopg.OperationalError("canceling statement due to statement timeout")
        yield

    def close(self) -> None:
        self.closed = True


def test_a_failed_archive_only_logs_once_and_the_events_still_serve():
    lines: list[str] = []
    feed = MarketFeed(lambda n: list(WINDOW), connect=lambda: FailingTransactionConn(), log=lines.append, archive=True)  # type: ignore[arg-type,return-value]
    assert len(feed.events()) == len(WINDOW) and len(feed.events()) == len(WINDOW)
    assert lines == ["feed: archiving the window failed (OperationalError); trading goes on"]


def test_without_archive_or_without_postgres_nothing_is_written():
    calls: list[str] = []

    def connect():  # type: ignore[no-untyped-def]
        calls.append("connect")
        raise psycopg.OperationalError("down")

    assert len(MarketFeed(lambda n: list(WINDOW), connect=connect, archive=True).events()) == len(WINDOW)
    assert calls == ["connect"]


@pytest.mark.integration
def test_the_window_is_archived_once_into_feed_events(database_url, schema):  # noqa: F811
    from bazaar_agent import db

    with open_in(database_url, schema) as conn:
        db.init_schema(conn)
    feed = MarketFeed(lambda n: list(WINDOW), connect=lambda: open_in(database_url, schema), archive=True)
    feed.events()
    feed.events()  # the next tick: already archived, nothing doubles
    with open_in(database_url, schema) as conn:
        row = conn.execute("select count(*), count(distinct id), max(id) from feed_events").fetchone()
    assert row == (len(WINDOW), len(WINDOW), max(e["id"] for e in WINDOW))


def test_the_learnings_command_reads_the_captured_feed(tmp_path, monkeypatch):
    from bazaar_agent import cli
    from bazaar_agent.learn import cli as learn_cli

    feed_dir = tmp_path / "feed"
    feed_dir.mkdir()
    (feed_dir / "feed.jsonl").write_text("\n".join(json.dumps(e) for e in WINDOW) + "\n")
    monkeypatch.setenv("BAZAAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BAZAAR_TEAM_ID", "t01")
    monkeypatch.setattr(learn_cli, "_connect", lambda: None)  # no Postgres: the JSONL capture answers
    result = CliRunner().invoke(cli.app, ["learnings", "--json", "--tick", "180", "--kind", "cooloff"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output[result.output.index("{") :])
    assert out["us"] == "t01" and out["tick"] == 180
    assert {lr["subject"] for lr in out["learnings"]} == {"chato", "abuela"}
    # the hour ends at T280: the newest timed event is tick 175 at t 4.125, and the clock went to 30 s ticks
    assert out["blockers"] == [
        "chato cooloff with us until T193",
        "chato sold out with us for LAV-09 until T280",
        "abuela persona quota with us for sobre_barrio until T280",
    ]
    table = CliRunner().invoke(cli.app, ["learnings", "--all", "--subject", "v04"])
    assert table.exit_code == 0 and "every learning" in table.output
    fees = CliRunner().invoke(cli.app, ["learnings", "--all", "--subject", "v04", "--json"])
    texts = [lr["text"] for lr in json.loads(fees.output[fees.output.index("{") :])["learnings"]]
    assert texts == ["v04 will charge 0% + 0 P/card from T161", "t02 opened v04 “Team 2 · El Rastro Express” at 0%"]
