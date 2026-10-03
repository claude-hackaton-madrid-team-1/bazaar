"""No dealer ever gets `reciprocity` (a promise to come back): RULES.md, Dealers, "some stop dealing with you for a
while if you try to trick them". Duel rivals and teams still may."""

from __future__ import annotations

import pytest

from bazaar_agent.agents.bluff import PLAIN, Counterparty, TacticBook
from bazaar_agent.agents.tactics import BY_ID, eligible, render

DEALERS = ("abuela", "chato", "pilar", "Pilar", " CHATO ", "mercader", "vault_keeper_9")


@pytest.mark.parametrize("dealer", DEALERS)
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_no_dealer_is_offered_reciprocity(dealer, side):
    assert "reciprocity" not in eligible("dealer", dealer, side)
    for language in ("es", "en"):
        assert render("reciprocity", 30, side=side, language=language, kind="dealer", counterparty=dealer) is None


@pytest.mark.parametrize("kind, who", [("rival", "rival_plata"), ("team", "t07")])
def test_rivals_and_teams_keep_reciprocity(kind, who):
    assert BY_ID["reciprocity"].dealers is False and BY_ID["reciprocity"].others is True
    for side in ("buy", "sell"):
        assert "reciprocity" in eligible(kind, who, side)
        text = render("reciprocity", 30, side=side, language="es", kind=kind, counterparty=who)
        assert text is not None and "30" in text


def test_every_other_tactic_still_reaches_the_dealers_it_reached_before():
    """Only reciprocity left the dealer sets: Chato still gets the rest of the psychology and bluffs."""
    for side in ("buy", "sell"):
        rival = set(eligible("rival", "rival_plata", side))
        chato = set(eligible("dealer", "chato", side))
        assert rival - chato == {"reciprocity"}


def _picks(cp: Counterparty, seed: int, messages: int) -> list[str | None]:
    """The tactic chosen for each message of one conversation, the counterparty answering every message."""
    book = TacticBook(env={}, us="t01", seed=seed)
    out: list[str | None] = []
    for step in range(messages):
        tick = 100 + 2 * step
        c = book.choose(cp, "buy", "thread:1", step, 30, their_price=40)
        book.sent(c, their_price=40, their_offer=tick * 10, tick=tick)
        book.observe("thread:1", their_price=39, their_offer=tick * 10 + 1, tick=tick + 1)
        out.append(c.tactic)
    return out


@pytest.mark.parametrize("dealer", ["abuela", "chato", "pilar", "mercader"])
def test_the_chooser_never_picks_reciprocity_for_a_dealer_over_every_seed(dealer):
    n = len(eligible("dealer", dealer, "buy")) + 3  # every untried tactic gets its turn, then the learned ones
    for seed in range(12):
        picks = _picks(Counterparty.dealer(dealer), seed, n)
        assert "reciprocity" not in picks, (dealer, seed, picks)
        assert all(p is None or p == PLAIN or p in BY_ID for p in picks)


def test_the_same_rotation_does_reach_reciprocity_with_a_rival():
    """The dealer test above can see a reciprocity pick: the same rotation with a rival makes one."""
    n = len(eligible("rival", "rival_plata", "buy")) + 3
    assert any("reciprocity" in _picks(Counterparty.rival("Rival Plata"), seed, n) for seed in range(12))
