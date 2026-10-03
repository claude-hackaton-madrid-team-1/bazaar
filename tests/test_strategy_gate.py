"""SG1: Jev decides which strategy runs. The gate asks once per refresh window, fails closed and logs every
answer; the maker's dealer sells open a thread only on its yes, never with a dealer the taker wanted, and take
a final only at or above max(our floor, a share of our first ask)."""

import json

from bazaar_agent.agents.dealer_sell import AskPlan, SellNegotiation, decide_sell
from bazaar_agent.agents.runtime import JevAdvice, Recorder
from bazaar_agent.agents.strategy_gate import DEALER_SELL, LADDER_PROBE, StrategyGate, closed_gate
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.jev import load_questions
from tests.agent_fakes import FakeTeam, clock, rows
from tests.test_dealer_sell_desk import ME_DUP, maker, opened

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def gate(tmp_path, answers, refresh=10):
    asked: list[tuple[str, dict]] = []

    def ask(name, state):
        asked.append((name, state))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    rec = Recorder("taker", DecisionLog(tmp_path), True, lambda line: None)
    return StrategyGate(ask, rec, refresh), asked


def test_the_questions_file_has_a_noul_per_strategy():
    questions = load_questions(ROOT / "questions" / "strategies.json")
    assert {LADDER_PROBE, DEALER_SELL} <= set(questions)
    assert all(questions[q]["type"] == "noul" for q in (LADDER_PROBE, DEALER_SELL))


def test_only_a_decided_yes_turns_a_strategy_on_and_every_answer_is_a_row(tmp_path):
    g, _ = gate(tmp_path, [JevAdvice("yes", 0.81), JevAdvice("no", 0.1), JevAdvice("undecided", 0.6)], refresh=1)
    assert g.allows(LADDER_PROBE, 100, lambda: {"cash": 81}) is True
    assert g.allows(LADDER_PROBE, 101, lambda: {"cash": 81}) is False
    assert g.allows(LADDER_PROBE, 102, lambda: {"cash": 81}) is False
    made = [r for r in rows(tmp_path) if r.get("kind") == "strategy_gate"]
    assert [r["status"] for r in made] == ["approved", "rejected", "rejected"]
    assert made[0]["inputs"]["state"] == {"cash": 81} and made[0]["jev"]["verdict"] == "yes"


def test_the_gate_asks_once_per_refresh_window_and_builds_the_state_only_then(tmp_path):
    g, asked = gate(tmp_path, [JevAdvice("yes", 0.9), JevAdvice("no", 0.05)], refresh=10)
    built: list[int] = []

    def state():
        built.append(1)
        return {}

    assert all(g.allows(DEALER_SELL, t, state) for t in range(100, 110))
    assert len(asked) == 1 and len(built) == 1
    assert g.allows(DEALER_SELL, 110, state) is False and len(asked) == 2


def test_an_error_or_no_jev_keeps_the_strategy_off(tmp_path):
    g, _ = gate(tmp_path, [RuntimeError("timeout")])
    assert g.allows(LADDER_PROBE, 100, lambda: {}) is False
    g2 = StrategyGate(closed_gate, Recorder("maker", DecisionLog(tmp_path), True, lambda line: None), 10)
    assert g2.allows(DEALER_SELL, 100, lambda: {}) is False


def test_a_broken_state_keeps_the_strategy_off_without_asking(tmp_path):
    g, asked = gate(tmp_path, [JevAdvice("yes", 0.9)])

    def state():
        raise KeyError("cash")

    assert g.allows(LADDER_PROBE, 100, state) is False and asked == []


# ---------------------------------------------------------------- the maker's dealer sells


def test_without_a_jev_yes_the_maker_opens_no_sell_thread(tmp_path):
    for jev in (None, lambda name, state: JevAdvice("undecided", 0.6)):
        team = FakeTeam(me=ME_DUP)
        maker(tmp_path, team, live=True, strategy_jev=jev, dealer_sell_enabled=True)[0].on_tick(clock())
        assert opened(team) == []


def test_jev_reads_our_duplicates_and_the_backoff_rules(tmp_path):
    seen: list[dict] = []

    def jev(name, state):
        seen.append(state)
        return JevAdvice("yes", 0.9)

    team = FakeTeam(me=ME_DUP)
    maker(tmp_path, team, live=True, strategy_jev=jev, dealer_sell_enabled=True)[0].on_tick(clock())
    [state] = seen
    assert any(d["card"] == "LAV-08" and d["copies"] == 3 for d in state["duplicates"])
    assert state["rules"]["taker_window_ticks"] == Guardrails().dealer_sell_taker_window_ticks
    json.dumps(state)  # JSON-safe for Jev


def taker_row(tmp_path, tick, kind, **inputs):
    path = tmp_path / "agents" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"id": -tick, "agent": "taker", "tick": tick, "kind": kind, "status": "approved", "inputs": inputs}
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def test_no_sell_thread_with_a_dealer_the_taker_wanted_in_the_window(tmp_path):
    taker_row(tmp_path, 95, "dealer_skip", blocked_dealer="abuela", busy_thread=7)
    taker_row(tmp_path, 96, "ladder_probe", dealer="chato")
    team = FakeTeam(me=ME_DUP)
    maker(tmp_path, team, live=True, dealer_sell_enabled=True, dealer_sell_taker_window_ticks=20)[0].on_tick(
        clock(tick=100)
    )
    assert opened(team) == []


def test_an_old_or_unrelated_taker_row_leaves_the_dealer_free(tmp_path):
    taker_row(tmp_path, 50, "dealer_skip", blocked_dealer="abuela", busy_thread=7)  # outside the window
    taker_row(tmp_path, 99, "dealer_skip", blocked_dealer="chato", why="cooloff")  # not a busy-thread skip
    log = DecisionLog(tmp_path)
    assert log.wanted_dealers("taker", 80) == set()
    team = FakeTeam(me=ME_DUP)
    maker(tmp_path, team, live=True, dealer_sell_enabled=True, dealer_sell_taker_window_ticks=20)[0].on_tick(
        clock(tick=100)
    )
    assert len(opened(team)) == 1


def test_a_final_below_half_our_first_ask_walks_even_above_our_floor():
    neg = SellNegotiation(AskPlan(20, 2, 6))
    neg.asks.append(20)
    neg.see_bid(5)  # her opening bid
    move = decide_sell(neg, 8, 300, True, final_min=10)
    assert move.kind == "walk" and "first ask" in move.reason
    neg2 = SellNegotiation(AskPlan(20, 2, 6))
    neg2.asks.append(20)
    neg2.see_bid(5)
    assert decide_sell(neg2, 10, 300, True, final_min=10).kind == "accept"


def test_every_state_jev_reads_carries_the_risk_posture(tmp_path):
    seen: list[dict] = []

    def ask(name, state):
        seen.append(state)
        return JevAdvice("no", 0.1)

    rec = Recorder("taker", DecisionLog(tmp_path), True, lambda line: None)
    StrategyGate(ask, rec, 10, "aggressive").allows(LADDER_PROBE, 100, lambda: {"cash": 81})
    assert seen == [{"cash": 81, "risk_posture": "aggressive"}]
