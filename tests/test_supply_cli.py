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
        self.retries: list[int] = []
        self.refuse_from = 10_000

    def clock(self):
        if self.refuse_from == 0:
            raise BazaarError("bad_key", "no", 401)
        return {"tick": 160}

    def card(self, aid):
        self.reads.append(aid)
        if aid >= self.refuse_from:
            raise BazaarError("rate_limited", "slow down", 429)
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
    monkeypatch.setattr(supply_cli, "team_client", lambda s, retries=2: team.retries.append(retries) or team)
    monkeypatch.setattr(supply_cli, "_connect", lambda: None)
    monkeypatch.setattr("bazaar_agent.db.connect_ready", lambda app: contextlib.nullcontext("conn"))
    monkeypatch.setattr(
        supply_cli, "save_scan", lambda conn, rows, tick: saved.append(("scan", tick, len(rows))) or len(rows)
    )
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
    result, out = run("scan", "--rate", "2", "--gap", "2", "--save")
    assert result.exit_code == 0, out
    assert run.team.reads[-2:] == [272, 273] and "2 unknown ids in a row" in out
    assert [r["id"] for r in read_scan_file(run.folder / "scan.jsonl")][:3] == [1, 2, 3]
    assert run.saved == [("scan", 160, 271)] and run.team.retries == [0]  # no SDK retry of a 429


def test_scan_from_an_id_keeps_the_ids_below_it_but_stores_only_what_it_read(run):
    run("scan", "--rate", "2", "--gap", "2")
    result, out = run("scan", "--rate", "2", "--gap", "2", "--from-id", "270", "--save")
    assert len(read_scan_file(run.folder / "scan.jsonl")) == 271 and "0 new ids" in out
    assert run.saved == [("scan", 160, 2)]  # ids 270-271: the older rows of this laptop are not re-stored


def test_a_scan_refused_at_once_leaves_the_files_and_the_database_alone(run):
    run("scan", "--rate", "2", "--gap", "2")
    run.team.refuse_from = 1
    result, out = run("scan", "--rate", "2", "--save")
    assert result.exit_code == 1 and "left as they were" in out
    assert len(read_scan_file(run.folder / "scan.jsonl")) == 271 and run.saved == []


def test_a_rate_above_two_is_refused(run):
    result, out = run("scan", "--rate", "3")
    assert result.exit_code == 2 and "--rate must be in (0, 2]" in out


def test_a_refused_clock_ends_the_scan_cleanly(run):
    run.team.refuse_from = 0
    result, out = run("scan")
    assert result.exit_code == 1 and "/api/clock refused: bad_key (401); nothing scanned" in out
    assert "Traceback" not in out


def test_a_scan_refused_partway_keeps_the_ids_it_did_not_reach(run):
    # #155 re-review P3: a scan refused at id 200 replaced 271 rows with 199.
    run("scan", "--rate", "2", "--gap", "2")
    run.team.refuse_from = 200
    result, out = run("scan", "--rate", "2")
    assert "refused at id 200" in out and len(read_scan_file(run.folder / "scan.jsonl")) == 271
