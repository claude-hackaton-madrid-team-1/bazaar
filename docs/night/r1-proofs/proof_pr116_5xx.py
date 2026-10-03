"""Reviewer proof tests for PR #116 (B18). Not committed."""

import pytest

from tests.agent_fakes import TICK, clock
from tests.bites.kit import make_taker
from tests.test_accept_release import BOARD, RefusingAccept


@pytest.mark.parametrize("code,status", [("http_502", 502), ("http_504", 504), ("http_500", 500)])
def test_a_gateway_error_on_accept_keeps_the_slot_and_books_the_spend(tmp_path, code, status):
    """A 5xx from a proxy/gateway (the SDK maps it to http_<status>) is as ambiguous as a timeout: the
    accept may have landed. It must keep the slot (and, fail safe, book the spend)."""
    team = RefusingAccept(code, status)
    t, _, ledger = make_taker(tmp_path, team, BOARD, threads=0)
    t.on_tick(clock())
    assert len(team.attempts) == 1, f"next candidate tried after an ambiguous {code}: {team.attempts}"
    assert ledger.accepts_in_tick(TICK) == 1
    assert ledger.spent_since(0) == 20  # offer 2 (LAV-08 at 20) ranks first
