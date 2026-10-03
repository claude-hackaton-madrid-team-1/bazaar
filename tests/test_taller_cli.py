"""`bazaar taller`: the Workshop from the command line, through `agents.taller.craft_one` (the taker's path). No ids:
the ranked triples, nothing sent; three ids: checked by the guardrails, dry run unless --live."""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from bazaar_agent.agents.taller import TALLER_ITEM
from bazaar_agent.guardrails import Guardrails, Ledger
from tests.agent_fakes import FakePublic, clock, rows
from tests.test_taller import CATALOG, DEALERS, SPARES, Team, crafts, me

FREE = [a for a in SPARES["assets"] if a["ref"] != "LAV-07"]  # LAV-01 x3 (#1-3), SAL-01 x2 (#4-5)


@pytest.fixture
def taller_cli(monkeypatch, tmp_path):
    import psycopg

    from bazaar_agent import cli, db
    from bazaar_agent.config import Settings

    def down(*args, **kwargs):
        raise psycopg.OperationalError("no database in unit tests")

    team = Team(me=me(*FREE), now=clock())
    ledger = Ledger(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "team_client", lambda settings: team)
    monkeypatch.setattr(cli, "public_client", lambda settings: FakePublic(catalog=CATALOG, dealers=DEALERS))
    monkeypatch.setattr(cli, "_rules", lambda: SimpleNamespace(rules=Guardrails(taller_enabled=True)))
    monkeypatch.setattr(cli, "_ledger", lambda source, live=False: ledger)
    monkeypatch.setattr(db, "connect", down)
    monkeypatch.setattr(db, "connect_ready", down)
    return team, cli, ledger


def test_no_ids_lists_the_ranked_triples_and_sends_nothing(taller_cli):
    team, cli, ledger = taller_cli
    result = CliRunner().invoke(cli.app, ["taller", "--live"])
    assert result.exit_code == 0, result.output
    assert "5 2 3" in result.stdout and crafts(team) == [] and ledger.entries() == []


def test_three_ids_dry_run_records_the_check_and_sends_nothing(taller_cli, tmp_path):
    team, cli, ledger = taller_cli
    result = CliRunner().invoke(cli.app, ["taller", "2", "3", "5"])
    assert result.exit_code == 0, result.output
    assert "allowed" in result.stdout and "dry run" in result.stdout
    assert crafts(team) == [] and ledger.entries() == []
    assert [(r["kind"], r["status"]) for r in rows(tmp_path)] == [("taller", "approved")]


def test_live_sends_one_craft_books_it_and_prints_the_answer(taller_cli):
    team, cli, ledger = taller_cli
    result = CliRunner().invoke(cli.app, ["taller", "2", "3", "5", "--live"])
    assert result.exit_code == 0, result.output
    assert crafts(team) == [("call", "POST", "/api/taller", {"assets": [2, 3, 5]})]
    assert [(e["kind"], e["price"], e["item"]) for e in ledger.entries()] == [
        ("spend", 0, TALLER_ITEM + "LAV-01,LAV-01,SAL-01")
    ]
    assert "crafted: LAV-07 Samosas" in result.stdout


def test_the_last_free_copy_and_a_copy_still_settling_are_refused(taller_cli):
    team, cli, ledger = taller_cli
    result = CliRunner().invoke(cli.app, ["taller", "1", "2", "3", "--live"])  # every LAV-01 we hold
    assert result.exit_code == 1 and "LAV-01" in result.stdout and crafts(team) == []
    ledger.record("accept", clock().tick, 1.5, 0, "sell:5")  # SAL-01 #5 sold into a bid this tick
    result = CliRunner().invoke(cli.app, ["taller", "2", "3", "5", "--live"])
    assert result.exit_code == 1 and "accept still settling" in result.output and crafts(team) == []
