"""`bazaar buyers`: who to sell our duplicates to, read from files (no network), and the `team_buyer_rank` table."""

import json

import pytest
from typer.testing import CliRunner

from bazaar_agent import buyers_db
from bazaar_agent.cli import app
from tests import test_db
from tests.test_buyers import BOARD, CATALOG, settle

database_url, schema, conn = test_db.database_url, test_db.schema, test_db.conn  # the throwaway-schema fixtures

ME = {
    "id": "t01",
    "tick": 40,
    "cash": 300,
    "assets": [
        {"id": 1, "kind": "card", "ref": "SAL-01", "rarity": "common", "your_value": 6.0},
        {"id": 2, "kind": "card", "ref": "SAL-01", "rarity": "common", "your_value": 4.0},
        {"id": 3, "kind": "card", "ref": "LAV-04", "rarity": "common", "your_value": 9.0},
        {"id": 4, "kind": "pack", "ref": "sobre_barrio"},
    ],
}
EVENTS = [
    settle(1, "t16", "t03", "SAL-02", 9),
    settle(2, "t16", "t03", "SAL-01", 11),
    settle(3, "t13", "t04", "SAL-02", 7),
]


@pytest.fixture
def files(tmp_path):
    paths = {}
    for name, body in {"me": ME, "catalog": CATALOG, "leaderboard": BOARD}.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(body), encoding="utf-8")
    paths["events"] = tmp_path / "events.jsonl"
    paths["events"].write_text("".join(json.dumps(e) + "\n" for e in EVENTS), encoding="utf-8")
    return paths


def run(files, *extra):
    args = ["buyers", "--no-scan"]
    for name in ("events", "me", "catalog", "leaderboard"):
        args += [f"--{name}", str(files[name])]
    return CliRunner().invoke(app, [*args, *extra])


def test_cards_default_to_our_duplicates_and_our_value_is_the_cheapest_copy():
    assert buyers_db.duplicates(ME) == ["SAL-01"]
    assert buyers_db.our_value(ME, "SAL-01") == 4.0
    assert buyers_db.our_value(ME, "SAL-03") == 0.0


def test_json_ranks_every_duplicate_and_stdout_stays_pure_json(files):
    result = run(files, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert list(data) == ["SAL-01"]
    rows = data["SAL-01"]
    assert rows[0]["team"] == "t16" and rows[0]["willing"] == 10.0  # paid 9 and 11 for SAL commons
    assert "t01" not in {r["team"] for r in rows}
    assert {"card", "team", "rank", "willing", "interest", "missing", "expected", "rival", "blocked", "why"} <= set(
        rows[0]
    )


def test_given_cards_replace_the_duplicates(files):
    result = run(files, "--json", "--card", "SAL-03", "--card", "LAV-04")
    assert result.exit_code == 0, result.output
    assert list(json.loads(result.stdout)) == ["SAL-03", "LAV-04"]


def test_text_output_is_a_table_per_card(files):
    result = run(files)
    assert result.exit_code == 0, result.output
    assert "SAL-01" in result.stdout and "t16" in result.stdout and "top 5" in result.stdout


def test_rival_text_is_escaped_in_the_table():
    from bazaar_agent.buyers import BuyerRow

    row = BuyerRow("SAL-01", "t05", 3, 9.0, 0.5, True, 7.0, "[red]x[/red]", False, "why [bold]")
    table = buyers_db.table("SAL-01", [row])
    from rich.console import Console

    console = Console(record=True, width=200)
    console.print(table)
    assert "[red]x[/red]" in console.export_text()


def test_an_unreachable_scan_is_a_note_on_stderr_not_a_failure(monkeypatch):
    def boom():
        raise OSError("connection refused password=hunter2secret")

    rows, note = buyers_db.stored_scan(boom)
    assert rows == [] and "hunter2secret" not in note and "no scan" in note


@pytest.mark.integration
def test_save_replaces_the_rows_of_the_saved_cards_only(conn):
    from bazaar_agent.buyers import BuyerRow

    def row(card, team, expected):
        return BuyerRow(card, team, 4, 9.0, 0.5, True, expected, None, False, "why")

    assert buyers_db.save(conn, {"SAL-01": [row("SAL-01", "t16", 9.0), row("SAL-01", "t13", 3.0)]}, tick=40) == 2
    buyers_db.save(conn, {"LAV-04": [row("LAV-04", "t09", 5.0)]}, tick=40)
    buyers_db.save(conn, {"SAL-01": [row("SAL-01", "t13", 8.0)]}, tick=41)
    got = conn.execute(
        "select card, team, position, expected, updated_tick from team_buyer_rank order by card"
    ).fetchall()
    assert [(c, t, p, float(e), u) for c, t, p, e, u in got] == [
        ("LAV-04", "t09", 1, 5.0, 40),
        ("SAL-01", "t13", 1, 8.0, 41),
    ]
