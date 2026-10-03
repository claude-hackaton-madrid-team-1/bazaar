"""`bazaar supply` and `bazaar supply scan` through the real CLI, with fakes only (no network, no database)."""

import contextlib
import json

import pytest
from typer.testing import CliRunner

from bazaar_agent import cli, supply_cli
from bazaar_agent.config import Settings
from bazaar_agent.sdk import BazaarError
from bazaar_agent.supply_db import read_scan_file
from tests.test_strategy import CATALOG, ME
from tests.test_supply import FEED, SCAN

WIDE = {"COLUMNS": "200"}


class Public:
    def catalog(self):
        return CATALOG


class Team:
    """GET /api/cards/{id}: the three scanned assets exist up to id 271, then nothing."""

    def __init__(self):
        self.reads: list[int] = []

    def clock(self):
        return {"tick": 160}

    def card(self, aid):
        self.reads.append(aid)
        if aid > 271:
            raise BazaarError("unknown_asset", "no such asset", 404)
        return next((r for r in SCAN if r["id"] == aid), {"id": aid, "kind": "card", "ref": "LAV-01"})


@pytest.fixture
def run(monkeypatch, tmp_path):
    team, saved = Team(), []
    settings = Settings(data_dir=tmp_path, team_id="t01")
    for module in (cli, supply_cli):
        monkeypatch.setattr(module, "load_settings", lambda: settings)
        monkeypatch.setattr(module, "public_client", lambda s: Public())
    monkeypatch.setattr(cli, "_team_me", lambda: (team, ME))
    monkeypatch.setattr(cli, "_events", lambda live: list(FEED))
    monkeypatch.setattr(supply_cli, "team_client", lambda s: team)
    monkeypatch.setattr(supply_cli, "_connect", lambda: None)
    monkeypatch.setattr("bazaar_agent.db.connect_ready", lambda app: contextlib.nullcontext("conn"))
    monkeypatch.setattr(supply_cli, "save_scan", lambda conn, rows, tick: saved.append(("scan", tick)) or len(rows))
    monkeypatch.setattr(supply_cli, "save_map", lambda conn, sm, tick: saved.append(("map", tick)) or len(sm.cards))
    monkeypatch.setattr(supply_cli.time, "sleep", lambda s: None)

    def invoke(*args):
        result = CliRunner().invoke(cli.app, ["supply", *args], env=WIDE)
        return result, result.output

    invoke.team, invoke.saved, invoke.folder = team, saved, tmp_path / "supply"
    return invoke


def test_supply_prints_pages_and_scarce_cards_and_saves(run):
    result, out = run("--save")
    assert result.exit_code == 0, out
    assert "packs opened" in out and "LAV-10" in out and "LAT-09" in out and "t14" in out
    assert run.saved == [("map", 50)]


def test_supply_json(run):
    result, out = run("--json")
    data = json.loads(out[out.index("{") :])
    assert data["sets"]["LAV"]["bottleneck"] == ["LAV-10"] and data["cards"]["LAT-09"]["ours"] == 1


def test_scan_reads_until_the_gap_and_stores_the_scan(run):
    result, out = run("scan", "--rate", "4", "--gap", "2", "--save")
    assert result.exit_code == 0, out
    assert run.team.reads[-2:] == [272, 273] and "2 unknown ids in a row" in out
    assert [r["id"] for r in read_scan_file(run.folder / "scan.jsonl")][:3] == [1, 2, 3]
    assert run.saved == [("scan", 160)]


def test_scan_from_an_id_keeps_the_ids_below_it(run):
    run("scan", "--rate", "4", "--gap", "2")
    result, out = run("scan", "--rate", "4", "--gap", "2", "--from-id", "270")
    assert len(read_scan_file(run.folder / "scan.jsonl")) == 271 and "0 new ids" in out


def test_a_rate_above_four_is_refused(run):
    result, out = run("scan", "--rate", "5")
    assert result.exit_code == 2 and "--rate must be in (0, 4]" in out
