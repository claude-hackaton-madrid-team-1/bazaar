"""N17-10: the team-thread spec's Q1-Q6 answered from stored data (pure, no database, no network)."""

from bazaar_agent import n17_checks as nc

US = "t01"


def opened(tid, team, other, topic=None, kind="team"):
    return {
        "type": "thread.opened",
        "payload": {"thread": tid, "kind": kind, "team": team, "with": other, "topic": topic},
    }


def message(tid, offer):
    return {"type": "thread.message", "payload": {"thread": tid, "kind": "team", "offer": offer}}


def test_an_empty_record_answers_unknown_and_still_goes_supervised():
    found = nc.answers([], US, [], [])
    assert [a.verdict for a in found] == ["unknown"] * 6
    verdict, why = nc.go_no_go(found)
    assert verdict == "GO (supervised)" and "Q1" in why and "BAZAAR_TEAM_THREADS" in why


def test_team_threads_in_the_feed_answer_publicity_expiry_and_mixed_sides():
    offer = {"id": 5, "give": {"assets": [{"id": 3}], "cash": 2}, "want": {"cards": ["LAV-02"]}}
    offer |= {"created_tick": 10, "expires_tick": 50}
    events = [
        opened(7, "t05", US, topic={"trade": "cards"}),
        opened(8, "t06", "t09"),
        opened(9, "t02", "abuela", kind="persona"),  # a dealer thread is not a team thread
        message(7, offer),
    ]
    by = {a.q: a for a in nc.answers(events, US, [], [])}
    assert (
        by["Q4"].verdict == "yes" and "2 team thread(s)" in by["Q4"].evidence and "1 with a topic" in by["Q4"].evidence
    )
    assert by["Q5"].verdict == "yes" and "{40: 1}" in by["Q5"].evidence
    assert by["Q6"].verdict == "yes" and by["Q1"].evidence.startswith("1 inbound")


def test_our_dealer_threads_show_whether_a_new_offer_retires_the_old_one():
    retired = [
        {"id": 1, "thread_id": 99, "maker": US, "status": "cancelled"},
        {"id": 2, "thread_id": 99, "maker": US, "status": "settled"},
    ]
    assert nc.q3_retire(retired, US).verdict == "yes"
    stacked = [{**o, "status": "open"} for o in retired]
    assert nc.q3_retire(stacked, US).verdict == "no"
    verdict, _ = nc.go_no_go(nc.answers([], US, [], stacked))
    assert verdict == "NO-GO"


def test_refusals_on_the_caps_and_the_conversation_limit_are_read():
    refusals = [{"sdk_method": "say", "error_code": "too_many_offers", "tick": 5}]
    assert nc.q2_listings(refusals).verdict == "yes"
    inbound = nc.team_threads([opened(7, "t05", US)])
    q1 = nc.q1_inbound(inbound, US, [{"sdk_method": "open_thread", "error_code": "too_many_threads", "tick": 6}])
    assert q1.verdict == "unknown" and "too_many_threads" in q1.evidence


def test_the_cli_answers_without_a_database(monkeypatch):
    import json

    from typer.testing import CliRunner

    from bazaar_agent import cli, db

    monkeypatch.setattr(cli, "_history", lambda events_file, live: [opened(7, "t05", US)])
    monkeypatch.setattr(cli, "_our_team", lambda settings=None: US)

    def down(app=None):
        raise RuntimeError("postgresql://user:secret@host/db unreachable")

    monkeypatch.setattr(db, "connect", down)
    out = CliRunner().invoke(cli.app, ["team-checks", "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.stdout)
    assert data["go_no_go"] == "GO (supervised)" and len(data["answers"]) == 6
    assert "secret" not in out.output  # a connect error is named by its type only
