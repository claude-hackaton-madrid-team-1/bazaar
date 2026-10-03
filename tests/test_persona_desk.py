"""The taker's persona shaping (`agents/persona_desk.py`) and the tone of the template words."""

from __future__ import annotations

from typing import Any

from bazaar_agent.agents.dealer import KIND_WORDS, TERSE_WORDS, template_words
from bazaar_agent.agents.persona_desk import deal_ticks, persona_item, shape, ticks_per_hour
from bazaar_agent.agents.words import WordsRequest
from bazaar_agent.persona_model import parse_personas
from bazaar_agent.strategy import Move

US = "t01"


def persona(pid: str, traits: dict[str, float], sells: list[dict[str, Any]], per_hour: int = 6, **unlock: Any) -> dict:
    return {
        "id": pid,
        "status": "active",
        "level": 4,
        "traits": traits,
        "menu": {"sells": sells, "buys": [{"rarity": "rare", "sets": "released"}], "deals_per_team_per_hour": per_hour},
        "unlock": {"always": False, "early_min_deals": 3, **unlock},
        "open_to_all": False,
    }


SHREWD = {"patience": 0.3, "generosity": 0.2, "shrewdness": 0.9, "memory": 0.9, "strictness": 0.9, "chattiness": 0.2}
SOFT = {"patience": 0.85, "generosity": 0.8, "shrewdness": 0.2, "memory": 0.15, "strictness": 0.1, "chattiness": 0.75}


def move(dealer: str, ladder: tuple[int, int, int] = (60, 80, 3), score: float = 1.0, ref: str = "LAV-09") -> Move:
    start, top, step = ladder
    return Move(
        side="buy",
        strategy="dealer",
        ref=ref,
        rarity="rare",
        value=120.0,
        price=float(top),
        surplus=10.0,
        urgency=1.0,
        score=score,
        source=dealer,
        counterparties=(dealer,),
        action="buy",
        limit=top,
        reason="strategy",
        command=f"dealer buy {ref}",
        ladder=ladder,
    )


def settlement(tick: int, *parties: str) -> dict[str, Any]:
    return {"type": "settlement", "tick": tick, "payload": {"parties": list(parties), "tick": tick, "price": 10}}


def run(moves: list[Move], personas: list[dict], events: list[dict] | None = None, **kw: Any) -> Any:
    return shape(
        moves,
        parse_personas(personas),
        kw.get("curves", {}),
        kw.get("learned", ()),
        events or [],
        US,
        kw.get("unlocked", ["abuela", "chato", "trick"]),
        kw.get("tick", 500),
        kw.get("tick_seconds", 30.0),
    )


def test_a_new_dealer_gets_its_trait_prior_and_never_a_higher_ladder() -> None:
    p = persona("trick", SHREWD, [{"rarity": "rare", "list_price": 70}])
    out = run([move("trick", (60, 80, 3))], [p])
    (shaped,) = out.moves
    assert shaped.ladder is not None
    start, top, step = shaped.ladder
    assert start <= 60 and top <= 80 and step == 1  # only lowered: a strict dealer matches our step one for one
    assert shaped.limit == top and "persona trick (traits)" in shaped.reason
    assert out.params[("trick", "LAV-09")].tone == "terse"


def test_a_ladder_already_under_the_prior_is_left_alone() -> None:
    p = persona("trick", SOFT, [{"rarity": "rare", "list_price": 200}])
    mv = move("trick", (10, 20, 1))
    out = run([mv], [p])
    assert out.moves == [mv] and out.notes == {}


def test_a_learned_policy_keeps_its_class_untouched() -> None:
    p = persona("trick", SHREWD, [{"rarity": "rare", "list_price": 70}])
    mv = move("trick", (60, 80, 3))
    assert run([mv], [p], learned=[("trick", "card:rare")]).moves == [mv]


def test_the_hourly_deal_budget_drops_the_dealer() -> None:
    p = persona("trick", SHREWD, [{"rarity": "rare", "list_price": 70}], per_hour=2)
    events = [settlement(480, US, "trick"), settlement(490, "trick", US), settlement(495, "t02", "trick")]
    out = run([move("trick")], [p], events)
    assert out.moves == [] and "2 deals per hour" in out.skipped[0][1]
    old = [settlement(300, US, "trick"), settlement(490, US, "trick")]  # 120 ticks per hour at 30 s: 300 is out
    assert len(run([move("trick")], [p], old).moves) == 1


def test_the_dealers_that_unlock_the_next_one_go_first() -> None:
    abuela = persona("abuela", SOFT, [{"rarity": "rare", "list_price": 80}], per_hour=8)
    chato = persona("chato", SHREWD, [{"rarity": "rare", "list_price": 80}], early_deals_with="abuela")
    moves = [move("trick", (10, 20, 1), score=9), move("abuela", (10, 20, 1), score=1)]
    out = run(moves, [abuela, chato], unlocked=["abuela", "trick"])
    assert [m.source for m in out.moves] == ["abuela", "trick"]
    unlocked = run(moves, [abuela, chato], unlocked=["abuela", "chato", "trick"])
    assert [m.source for m in unlocked.moves] == ["trick", "abuela"]


def test_a_dealer_without_a_persona_passes_unchanged() -> None:
    mv = move("ghost")
    assert run([mv], []).moves == [mv]


def test_helpers() -> None:
    assert deal_ticks([settlement(5, US, "abuela"), settlement(6, "t02", "abuela")], US) == {"abuela": [5]}
    assert deal_ticks([settlement(5, US, "abuela")], "") == {}
    assert ticks_per_hour(30) == 120 and ticks_per_hour(0) == 3600
    assert persona_item(move("x")) == "rare"


def test_terse_words_for_a_strict_dealer_kind_by_default() -> None:
    terse = template_words(WordsRequest("chato", 25, step=1, tone="terse"))
    kind = template_words(WordsRequest("abuela", 25, step=1))
    assert terse == TERSE_WORDS[1].format(p=25, n="Chato") and "25" in terse
    assert kind == KIND_WORDS[1].format(p=25, n="Carmen")


def test_a_persona_without_published_traits_gets_no_prior() -> None:
    p = persona("trick", SHREWD, [{"rarity": "rare", "list_price": 70}])
    p.pop("traits")
    mv = move("trick", (60, 80, 3))
    assert run([mv], [p]).moves == [mv]


def test_a_malformed_settlement_payload_is_skipped() -> None:
    bad = {"type": "settlement", "payload": ["t01", "abuela"]}
    assert deal_ticks([bad, settlement(5, US, "abuela")], US) == {"abuela": [5]}
