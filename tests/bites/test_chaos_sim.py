"""End to end: the real taker and maker, LIVE, against the in-process simulator, with redeploys (chaos.py).

The only invariant check in the repo that reads ground truth from the game side: what the simulator says we
paid and how much cash we had, not what our own ledger believes. 130 ticks of 30 s (past one game hour).
"""

import pytest

from bazaar_agent.guardrails import load_guardrails
from tests.bites.strictness import STRICT

chaos = pytest.importorskip("tests.bites.chaos")  # needs the simulator (main has it)

RULES = load_guardrails().rules


@pytest.fixture(
    scope="module",
    params=[(0, 900), (10, 900), (0, 330)],
    ids=["steady", "redeploy-every-10-ticks", "steady-near-the-floor"],
)
def report(request, tmp_path_factory):
    restart, cash = request.param  # 900: the hourly cap binds; 330: the cash floor (270) binds
    return chaos.run_chaos(tmp_path_factory.mktemp("chaos"), ticks=130, restart_every=restart, start_cash=cash)


def test_cash_never_goes_below_the_floor(report):
    assert report.min_cash >= RULES.cash_floor


def test_what_we_really_paid_in_any_game_hour_stays_under_the_cap(report):
    start, total = report.worst_hour()
    assert total <= RULES.max_spend_per_game_hour, f"paid {total} P in the game hour ending h{start}"


@pytest.mark.xfail(strict=STRICT, reason="BITE X15: expired maker bids stay booked as spend (phantom spend)")
def test_the_ledger_books_what_we_really_paid(report):
    if (report.start_cash or 0) < 500:
        pytest.skip("near the cash floor the maker posts no bid, so X15 cannot show")
    paid = sum(p.amount for p in report.paid)
    assert report.ledger_spend == paid, f"ledger {report.ledger_spend} P vs really paid {paid} P"
