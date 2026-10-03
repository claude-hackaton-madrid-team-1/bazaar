"""`bazaar dealer personas` through the real CLI with a fake public client (no network)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from bazaar_agent import cli, persona_cli
from bazaar_agent.config import Settings
from bazaar_agent.sdk import BazaarError
from tests.test_persona_replay import PERSONAS

WIDE = {"COLUMNS": "250"}


class Public:
    def __init__(self, payload=None, fail=False):
        self.payload = payload if payload is not None else {"personas": PERSONAS}
        self.fail = fail
        self.calls = 0

    def dealers(self):
        self.calls += 1
        if self.fail:
            raise BazaarError("network", "down", 0)
        return self.payload


@pytest.fixture
def run(monkeypatch, tmp_path):
    def invoke(public, *args):
        monkeypatch.setattr(persona_cli, "load_settings", lambda: Settings(data_dir=tmp_path))
        monkeypatch.setattr(persona_cli, "public_client", lambda s: public)
        return CliRunner().invoke(cli.app, ["dealer", "personas", *args], env=WIDE)

    return invoke


def test_json_lists_one_row_per_persona_and_sell_line(run):
    public = Public()
    result = run(public, "--json")
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert public.calls == 1
    assert [(r["dealer"], r["item"]) for r in rows] == [
        ("abuela", "sobre_barrio"),
        ("abuela", "common"),
        ("abuela", "uncommon"),
        ("chato", "sobre_plata"),
        ("chato", "uncommon"),
        ("chato", "rare"),
    ]
    uncommon = rows[2]
    assert uncommon["source"] == "traits" and uncommon["tone"] == "kind" and uncommon["list"] == 25
    assert rows[5]["tone"] == "terse" and rows[5]["ladder"].endswith("step 1")


def test_table_prints_every_dealer(run):
    result = run(Public())
    assert result.exit_code == 0, result.output
    assert "abuela" in result.stdout and "chato" in result.stdout and "sobre_plata" in result.stdout


def test_bad_payload_prints_nothing_and_a_failed_read_exits_1(run):
    result = run(Public({"personas": [{"id": "x", "menu": "junk"}, "junk"]}), "--json")
    assert result.exit_code == 0 and json.loads(result.stdout) == []
    failed = run(Public(fail=True))
    assert failed.exit_code == 1 and "could not read" in failed.stderr
