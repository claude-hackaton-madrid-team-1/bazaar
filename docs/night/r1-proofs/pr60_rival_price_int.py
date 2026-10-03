"""PR #60: a non-integer rival price is truncated by _rival_price, so a buyer accepts outside its limit
and duel_action hands the guardrail the truncated price (it allows it)."""
from bazaar_agent import guardrails as gr
from bazaar_agent.agents.duelist import duel_action, duel_move


def _duel() -> dict:
    return {
        "duel": 7, "role": "buyer", "your_limit": 100, "issues": ["price", "days"],
        "your_days_weight": 0.4, "rival_offer": {"price": 99.9, "days": 1},
        "deadline_tick": 112, "status": "live",
    }


def test_buyer_never_accepts_outside_the_limit_after_days() -> None:
    d = _duel()
    move = duel_move(d, tick=111, started_tick=100)  # endgame: accept anything "inside"
    true_cost = 99.9 + 0.4 * 1  # 100.3 > value 100: outside
    assert move.kind != "accept", f"accepted at true cost {true_cost} vs limit 100: {move}"


def test_guardrail_sees_the_real_rival_price() -> None:
    d = _duel()
    move = duel_move(d, tick=111, started_tick=100)
    action = duel_action(d, move)
    v = gr.check(action, gr.Context(cash=0, held={}, tick=111, t_hours=1.0), gr.Guardrails())
    assert not v.allowed, f"guardrail allowed {action}"
