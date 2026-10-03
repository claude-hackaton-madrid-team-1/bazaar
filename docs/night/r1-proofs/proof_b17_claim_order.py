"""r1 probe (not committed): the once-only claim is taken BEFORE the spend is written; a ledger write failure
between the two loses the booking for good (the retry finds the claim taken)."""

from bazaar_agent.ledger_pg import LedgerUnavailable
from tests.agent_fakes import TICK, FakePublic, FakeTeam
from tests.bites.kit import at, dealer_took_our_bid, make_taker, thread_bid


def test_a_ledger_blip_after_the_claim_never_books_the_deal(tmp_path):
    team = FakeTeam()
    t, lines, ledger = make_taker(tmp_path, team, FakePublic())
    t.on_tick(at(team, TICK))  # opens 5000, bids 18
    team.threads = [{"id": 5000, "with": "abuela", "team": "t01", "status": "open"}]
    team.offers = [thread_bid(5001, 5000, "LAV-08", 18) | {"created_tick": TICK, "expires_tick": TICK + 2}]
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    real, calls = ledger.record, {"n": 0}

    def blip(kind, *a, **k):
        if kind == "spend" and calls["n"] == 0:
            calls["n"] += 1
            raise LedgerUnavailable("ledger write failed (OperationalError)")
        return real(kind, *a, **k)

    ledger.record = blip
    for k in range(1, 6):
        t.on_tick(at(team, TICK + k))
    print("\n".join(lines[-8:]))
    assert calls["n"] == 1
    assert ledger.spent_since(0) == 18  # FAILS on 9c5bd64: 0
