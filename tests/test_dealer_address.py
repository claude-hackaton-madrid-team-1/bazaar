"""We never call a dealer "amigo": Doña Pilar objected three times on Sat 3 Oct (threads 880-914)."""

from __future__ import annotations

import pytest

from bazaar_agent.agents import dealer, dealer_sell
from tests.agent_fakes import TICK, FakeTeam
from tests.test_taker_hard_dealers import CHATO_ME


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


# ---------------------------------------------------------------- #212 review r2: the address is enforced where we send

CHATO_SAYS = "No me llames 'Chato', para ti soy Don Ramón."  # to another team: the lesson binds everyone


def test_template_words_leave_the_address_out_for_an_empty_address_and_name_the_dealer_for_none() -> None:
    from bazaar_agent.agents.words import WordsRequest

    assert "Chato" in dealer.template_words(WordsRequest("chato", 20, 0))  # None: not computed → DEALER_NAMES
    assert "Chato" not in dealer.template_words(WordsRequest("chato", 20, 0, address=""))  # "": none is safe
    assert "Don Ramón" in dealer.template_words(WordsRequest("chato", 20, 0, address="Don Ramón"))


def test_bid_words_send_no_address_when_the_words_use_one_the_dealer_forbade() -> None:
    from bazaar_agent.agents.words import WordsRequest
    from tests.agent_fakes import clock

    def say(text: str):  # a words function (template, tactic or LLM) that ignores the address
        return lambda request: text

    forbade = WordsRequest("chato", 30, 2, never_address=("chato",))
    out = dealer.bid_words(say("Gracias por su paciencia, Chato. Subo a 30."), forbade, {}, clock(), 0.0)
    assert out == dealer.words(2, 30, "chato", name="") and "chato" not in out.lower()
    amigo = dealer.bid_words(say("Venga, amigo: 30 y cerramos."), WordsRequest("abuela", 30, 1), {}, clock(), 0.0)
    assert "amigo" not in amigo.lower() and "Carmen" not in amigo  # "amigo" is never sent, no request needed
    fine = dealer.bid_words(say("Buenas, Don Ramón. Subo a 30."), forbade, {}, clock(), 0.0)
    assert fine == "Buenas, Don Ramón. Subo a 30."  # nothing forbidden: the words go out as written


def test_a_tactic_line_takes_the_request_address_and_never_a_forbidden_one() -> None:
    from bazaar_agent.agents.bluff import Choice, Counterparty
    from bazaar_agent.agents.words import WordsRequest

    choice = Choice(Counterparty.dealer("chato"), "buy", "thread:1", 1, 30, "empathy_label", "test")
    fn = choice.words(dealer.template_words)
    assert "Chato" in fn(WordsRequest("chato", 30, 1))  # not computed: tactics.NAMES, as today
    nameless = fn(WordsRequest("chato", 30, 1, address="", never_address=("chato",)))
    assert "chato" not in nameless.lower() and "30" in nameless and ", ." not in nameless
    assert "Don Ramón" in fn(WordsRequest("chato", 30, 1, address="Don Ramón", never_address=("chato",)))


class ChatoTeam(FakeTeam):
    """Records the words of every message, and plays Chato asking one prima less each tick."""

    def __init__(self) -> None:
        super().__init__(me=CHATO_ME)
        self.texts: list[str] = []

    def say(self, tid, text="", price=None, offer=None, topic=None):  # type: ignore[no-untyped-def]
        self.texts.append(text)
        return super().say(tid, text, price, offer, topic)

    def thread(self, tid):  # type: ignore[no-untyped-def]
        n = self.now.tick - TICK
        if n <= 0 or tid != 5000:
            return super().thread(tid)
        offer = {"id": 800 + n, "maker": "chato", "status": "open", "give": {"types": ["card:LAV-08"]}}
        return {"id": tid, "status": "open", "messages": [], "standing_offers": [{**offer, "want": {"cash": 31 - n}}]}


@pytest.mark.parametrize("seed", [None, 0, 1])  # None: templates only; seeds 0 and 1 pick a named tactic or plain
def test_a_name_the_dealer_forbade_never_reaches_it_through_the_taker(tmp_path, seed: int | None) -> None:
    """Security r2 #1: Chato tells t05 "No me llames 'Chato'"; our template and tactic words never say it."""
    from copy import deepcopy

    from bazaar_agent.agents.bluff import TacticBook
    from bazaar_agent.agents.runtime import MarketFeed
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from bazaar_agent.learn.live import LiveLearner
    from bazaar_agent.learn.store import LearningStore
    from tests.agent_fakes import FakePublic, clock, parts, rows
    from tests.test_dealer_memory import said
    from tests.test_taker_hard_dealers import CHATO, EVENTS

    events = [*EVENTS, said(9, "chato", CHATO_SAYS, team="t05")]
    team = ChatoTeam()
    kw = {**parts(tmp_path, dealer_final_lift=0.15), "feed": MarketFeed(lambda n: deepcopy(events))}
    t = Taker(
        team,
        FakePublic(dealers=[{**CHATO, "name": "El Chato"}], events=events),
        live=True,
        log=lambda line: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        learner=LiveLearner(LearningStore(None)),
        bluff=TacticBook(env={}, us="t01", seed=seed) if seed is not None else None,
        **kw,
    )
    for tick in range(TICK, TICK + 6):
        team.now = clock(tick=tick)
        t.on_tick(team.now)
    (opened,) = [r for r in rows(tmp_path) if r.get("kind") == "dealer_open"]
    assert opened["inputs"]["dealer_address"] == "" and "chato" in opened["inputs"]["dealer_memory"]["never_address"]
    assert len(team.texts) == 6
    assert not [text for text in team.texts if "chato" in text.lower()], team.texts


# ---------------------------------------------------------------- #212 review r2: #211's short names stay


def test_the_three_known_dealers_keep_211s_short_names_over_their_published_ones() -> None:
    from bazaar_agent.agents.dealer_memory import DealerMemory, address_for
    from bazaar_agent.persona_model import parse_personas

    published = parse_personas(
        [
            {"id": "abuela", "name": "Abuela Carmen", "kind": "persona", "level": 1},
            {"id": "chato", "name": "El Chato", "kind": "persona", "level": 2},
            {"id": "pilar", "name": "Doña Pilar", "kind": "persona", "level": 3},
            {"id": "ramon", "name": "Don Ramón", "kind": "persona", "level": 4},
        ]
    )
    names = {d: address_for(d, DealerMemory(d), published) for d in ("abuela", "chato", "pilar", "ramon")}
    assert names == {"abuela": "Carmen", "chato": "Chato", "pilar": "Doña Pilar", "ramon": "Don Ramón"}
