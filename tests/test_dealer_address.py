"""We never call a dealer "amigo": Doña Pilar objected three times on Sat 3 Oct (threads 880-914)."""

from __future__ import annotations

import pytest

from bazaar_agent.agents import dealer, dealer_sell


@pytest.mark.parametrize("step", range(6))
def test_pilar_is_addressed_as_dona_pilar_when_we_sell(step: int) -> None:
    text = dealer_sell.sell_words(step, 80, "pilar")
    assert "amigo" not in text.lower()


def test_pilar_opening_names_her() -> None:
    assert "Doña Pilar" in dealer_sell.sell_words(0, 80, "pilar")


@pytest.mark.parametrize("step", range(6))
@pytest.mark.parametrize("tone", ["", "terse"])
def test_an_unknown_dealer_is_never_called_amigo_and_the_text_stays_clean(step: int, tone: str) -> None:
    for text in (dealer.words(step, 30, "ramon", tone), dealer_sell.sell_words(step, 30, "ramon")):
        assert "amigo" not in text.lower()
        assert ", !" not in text and ", ?" not in text and ", ." not in text and "  " not in text


def test_known_dealers_keep_their_names() -> None:
    assert "Carmen" in dealer.words(0, 7, "abuela")
