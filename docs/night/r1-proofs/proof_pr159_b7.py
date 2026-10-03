"""r1 review of #159 (B7 early pass): one accept per tick across duels and with the taker. Not committed."""

from dataclasses import replace

from typer.testing import CliRunner

from bazaar_agent import guardrails as gr
from bazaar_agent.guardrails import load_guardrails
from bazaar_agent.sdk import BazaarError
from tests.test_duel_jev import LIVE
from tests.test_jev_journal import duel_cli  # noqa: F401

RIVAL = {"id": 702, "price": 110, "tick": 133, "days": 0}


def v2_rules(cli, monkeypatch, **extra):
    loaded = load_guardrails()
    rules = loaded.rules.model_copy(update={"duel_policy": "v2", **extra})
    monkeypatch.setattr(cli, "_rules", lambda: replace(loaded, rules=rules))


def accepts(client):
    return [s for s in client.sent if s[0] == "accept"]


def test_v2_two_acceptable_duels_one_slot_jev_says_accept_only_one_accept(duel_cli, monkeypatch):  # noqa: F811
    cli, client, asked, tmp_path = duel_cli
    v2_rules(cli, monkeypatch)
    client.payload = [
        {**LIVE, "deadline_tick": 136, "rival_offer": RIVAL},
        {**LIVE, "duel": 96, "deadline_tick": 136, "rival_offer": RIVAL},
    ]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])  # fake Jev: "accept"
    assert result.exit_code == 0, result.output
    assert len(accepts(client)) == 1, client.sent
    assert len(gr.Ledger(tmp_path / "ledger.jsonl").accept_items(134)) == 1


def test_v2_taker_books_between_the_slot_read_and_the_early_send(duel_cli, monkeypatch):  # noqa: F811
    cli, client, asked, tmp_path = duel_cli
    v2_rules(cli, monkeypatch)
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": RIVAL}]
    real = gr.Ledger.accepts_in_tick
    calls = []

    def racing(self, tick):
        calls.append(tick)
        n = real(self, tick)
        if len(calls) == 1:  # the planner saw a free slot; the taker books right after
            gr.Ledger(tmp_path / "ledger.jsonl").reserve_accept(134, 2.2, 12, "LAV-02", 1)
        return n

    monkeypatch.setattr(gr.Ledger, "accepts_in_tick", racing)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert accepts(client) == []
    assert gr.Ledger(tmp_path / "ledger.jsonl").accept_items(134) == ["LAV-02"]


def test_v2_refused_accept_keeps_the_teams_slot_booked(duel_cli, monkeypatch):  # noqa: F811
    """Known carry-over (r1 #115 low 2 / #86 low): no release on refusal -> the taker loses this tick's accept."""
    cli, client, asked, tmp_path = duel_cli
    v2_rules(cli, monkeypatch)
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": RIVAL}]

    def refuse(did):
        raise BazaarError("offer_changed", "the rival moved", 409)

    monkeypatch.setattr(client, "duel_accept", refuse)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "refused offer_changed" in result.output
    taker_ok = gr.Ledger(tmp_path / "ledger.jsonl").reserve_accept(134, 2.2, 12, "LAV-02", 1)
    assert taker_ok, "the refused duel accept stranded the team's one accept for this tick"


def test_v1_forced_accept_and_a_jev_early_accept_share_one_slot(duel_cli, monkeypatch):  # noqa: F811
    cli, client, asked, tmp_path = duel_cli
    loaded = load_guardrails()
    rules = loaded.rules.model_copy(update={"jev_can_accept_early": True})
    monkeypatch.setattr(cli, "_rules", lambda: replace(loaded, rules=rules))
    client.payload = [
        {**LIVE, "duel": 96, "deadline_tick": 134, "rival_offer": RIVAL},  # forced: ends this tick
        {**LIVE, "rival_offer": {**RIVAL, "price": 170}},  # meets the target: Jev's "accept" is legal
    ]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert accepts(client) == [("accept", 96)], client.sent
    assert gr.Ledger(tmp_path / "ledger.jsonl").accept_items(134) == ["duel:96"]


def test_v1_default_reads_the_ledger_once_more_before_the_forced_booking(duel_cli, monkeypatch):  # noqa: F811
    """2fe2a40 (b15, after #159's merge): v1 skips v2's slot read. Here v1 still pays it."""
    cli, client, asked, tmp_path = duel_cli
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": RIVAL}]
    order: list[str] = []
    real_read, real_reserve = gr.Ledger.accepts_in_tick, gr.Ledger.reserve_accept
    monkeypatch.setattr(gr.Ledger, "accepts_in_tick", lambda self, t: order.append("read") or real_read(self, t))
    monkeypatch.setattr(gr.Ledger, "reserve_accept", lambda self, *a: order.append("reserve") or real_reserve(self, *a))
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert order.index("reserve") == 1, order  # one read (the guard's ctx) then the booking


def test_v2_early_accept_row_keeps_its_jev_context_with_jev_on(duel_cli, monkeypatch):  # noqa: F811
    """B15 keeps forced_pick's context for v1 (legal_moves ["accept"], "jev not asked"); B7's v2 pass plays pick=None."""
    from tests.test_jev_journal import decision_rows

    cli, client, asked, tmp_path = duel_cli
    v2_rules(cli, monkeypatch)
    client.payload = [{**LIVE, "deadline_tick": 136, "rival_offer": RIVAL}]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    (row,) = decision_rows(tmp_path)
    assert row["kind"] == "duel_accept" and row["status"] == "done"
    assert row["inputs"].get("legal_moves") == ["accept"], row["inputs"]
