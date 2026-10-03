"""`bazaar taller`: El Taller from the command line, through `taller.convert` (the one code path). Dry run by default;
three hand-picked ids are still checked by the guardrails; --live sends exactly one POST /api/taller."""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from bazaar_agent.guardrails import Guardrails, Ledger
from tests.agent_fakes import FakePublic, FakeTeam, clock, rows

ME = {
    "id": "t01",
    "cash": 400,
    "affinity": {"LAV": 1.6, "LAT": 0.5},
    "assets": [
        *({"id": i, "kind": "card", "ref": "LAT-03", "rarity": "common", "your_value": 1.2} for i in (10, 11, 12, 13)),
        {"id": 20, "kind": "card", "ref": "LAV-06", "rarity": "uncommon", "your_value": 40.0},
        {"id": 21, "kind": "card", "ref": "LAV-06", "rarity": "uncommon", "your_value": 40.0},
        {"id": 30, "kind": "card", "ref": "LAV-01", "rarity": "common", "your_value": 16.0},
    ],
}


@pytest.fixture
def taller_cli(monkeypatch, tmp_path):
    import psycopg

    from bazaar_agent import cli, db
    from bazaar_agent.config import Settings

    def down(*args, **kwargs):
        raise psycopg.OperationalError("no database in unit tests")

    team = FakeTeam(me=ME, now=clock())
    ledger = Ledger(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "team_client", lambda settings: team)
    monkeypatch.setattr(cli, "public_client", lambda settings: FakePublic())
    monkeypatch.setattr(cli, "_rules", lambda: SimpleNamespace(rules=Guardrails(taller_enabled=True)))
    monkeypatch.setattr(cli, "_ledger", lambda source, live=False: ledger)
    monkeypatch.setattr(db, "connect", down)
    monkeypatch.setattr(db, "connect_ready", down)
    return team, cli, ledger


def test_a_dry_run_with_no_ids_prints_the_plan_and_sends_nothing(taller_cli, tmp_path):
    team, cli, ledger = taller_cli
    result = CliRunner().invoke(cli.app, ["taller"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "plan: common x3 -> one uncommon" in out and "LAT-03" in out and "dry run" in out
    assert "asset 30" not in out  # LAV-01 is our only copy: never fed in
    assert team.sent == [] and ledger.entries() == []
    assert [(r["kind"], r["dry_run"], r["status"]) for r in rows(tmp_path)] == [("taller", True, "approved")]


def test_three_ids_of_different_rarities_are_refused_by_the_guardrails(taller_cli):
    team, cli, _ = taller_cli
    result = CliRunner().invoke(cli.app, ["taller", "10", "20", "30", "--live"])
    assert result.exit_code == 1
    assert "refused by the guardrails" in result.stdout and "only commons or uncommons" in result.stdout
    assert team.sent == []


def test_an_id_we_do_not_hold_is_refused_before_the_guardrails(taller_cli):
    team, cli, _ = taller_cli
    result = CliRunner().invoke(cli.app, ["taller", "10", "11", "999"])
    assert result.exit_code == 1 and "asset 999 is not a card we hold" in result.stdout
    assert team.sent == []


def test_live_sends_exactly_one_conversion_and_books_it(taller_cli, tmp_path):
    team, cli, ledger = taller_cli
    result = CliRunner().invoke(cli.app, ["taller", "--live"])
    assert result.exit_code == 0, result.output
    assert team.sent == [("taller", [11, 12, 13])]  # id 10, the oldest copy, is the one we keep
    assert [(e["kind"], e["price"], e["item"]) for e in ledger.entries()] == [
        ("spend", 0, "taller:LAT-03,LAT-03,LAT-03")
    ]
    assert "pulled: RET-06" in result.stdout
    assert {r["sdk_method"] for r in rows(tmp_path, "executions.jsonl")} == {"taller"}


def test_no_spare_triple_says_so(taller_cli, monkeypatch):
    team, cli, _ = taller_cli
    monkeypatch.setattr(cli, "_rules", lambda: SimpleNamespace(rules=Guardrails(taller_enabled=False)))
    result = CliRunner().invoke(cli.app, ["taller", "--live"])
    assert result.exit_code == 0 and "taller_enabled = false" in result.stdout
    assert team.sent == []
