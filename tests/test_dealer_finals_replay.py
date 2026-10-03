"""N14a evidence: the final-aware replay (`bazaar dealer finals`) on Chato-shaped threads."""

from bazaar_agent.agents.dealer_finals import finals_rows
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.intel import DealerThread
from bazaar_agent.learn.evolve import Ladder
from bazaar_agent.learn.replay import replay_thread


def thread(tid, team, seq, fill=None, final=None):
    t = DealerThread(tid, team, "chato", "buy", "LAV-08", 100, fill_price=fill, final_price=final)
    for who, price in seq:
        (t.team_prices if who == "team" else t.dealer_prices).append(price)
        t.sequence.append((who, price))
    return t


# Friday: t03 stepped by 1 from 13 and took his final 28 (thread 253); t05 stepped by 3 and paid 31 (228).
T253 = thread(253, "t03", [("team", 13), ("dealer", 33), ("team", 14), ("dealer", 32), ("dealer", 28)], 28, 28)
T228 = thread(228, "t05", [("team", 23), ("dealer", 33), ("team", 26), ("dealer", 32), ("team", 31)], 31)


def test_replay_takes_a_final_up_to_final_max_and_none_keeps_the_walk_point():
    ladder = Ladder(19, 1, 26)
    assert replay_thread(T253, ladder, 28, 6.0).price is None  # today: walk at 26
    assert replay_thread(T253, ladder, 28, 6.0, final_max=29).price == 28
    assert replay_thread(T228, ladder, 28, 6.0, final_max=29).price is None
    assert replay_thread(T228, ladder, 28, 6.0, final_max=32).price == 31


def test_finals_rows_per_lift_show_which_conversations_close_and_their_cash():
    rows = finals_rows([T253, T228], Guardrails(), [0.0, 0.15, 0.25], dealer="chato")
    by_lift = {r.lift: r for r in rows}
    assert by_lift[0.0].deals == () and by_lift[0.0].final_cap == 26
    assert by_lift[0.15].deals == ((253, 28),) and by_lift[0.15].final_cap == 29
    assert by_lift[0.25].deals == ((253, 28), (228, 31)) and by_lift[0.25].per_hour(150) == 5
    # the patience play the taker would run: at least 9 distinct bids up to 26
    assert str(by_lift[0.15].ladder) == "18→26 step 1"


def test_the_cli_prints_each_lift_and_the_deals_it_would_take(monkeypatch):
    from typer.testing import CliRunner

    from bazaar_agent import cli

    monkeypatch.setattr(cli, "_events", lambda live: [{"id": 1}])
    monkeypatch.setattr("bazaar_agent.intel.dealer_threads", lambda events, us=None: [T253, T228])
    result = CliRunner().invoke(cli.app, ["dealer", "finals", "--lift", "0", "--lift", "0.25", "--threads"])
    assert result.exit_code == 0, result.output
    assert "lift 0.25 chato card:uncommon: thread 253 at 28, thread 228 at 31" in result.output
    assert "lift 0 chato" not in result.output  # no deal at lift 0: nothing listed
