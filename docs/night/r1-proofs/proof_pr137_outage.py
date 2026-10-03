# r1 proof (#137): an accept-slot ledger outage longer than the ticks left counts toward max_ticks and the
# thread is closed as a timeout, instead of holding like a kill-switch tick (86b3937: "a ledger outage should
# hold, not walk"). Fix: state["held"] += 1 on the `slot is None` branch in agents/dealer.py negotiate().
from bazaar_agent.agents.dealer import BidPlan, negotiate
from tests.test_dealer import FakeDealerClient


def test_a_ledger_outage_at_the_accept_slot_holds_and_then_takes_the_deal():
    client = FakeDealerClient(asks=[12, 10, 9])
    calls = []

    def reserve(move, clock):
        calls.append(clock.tick)
        return None if len(calls) <= 10 else True  # the shared ledger is down for 10 ticks, then answers

    out = negotiate(client, "abuela", {"buy": {"card": "LAV-03"}}, BidPlan(6, 1, 10),
                    log=lambda _: None, sleep=lambda _: None, reserve=reserve, max_ticks=8)
    assert client.sent == [6, 7, 8]  # never her 9 while the ledger is down (the #120 HIGH: fixed)
    assert not client.closed and out.status == "deal", f"{out.status} after {len(calls)} held ticks"
