import json
from pathlib import Path

import pytest

from bazaar_agent.agents.inspector import (
    CardIndex,
    FlagBook,
    flag_step,
    inspect_offer,
    inspect_thread,
    message_for_offer,
    rarity_claimed,
    summarise,
)

FIXTURES = Path(__file__).parent / "fixtures"
CARDS = CardIndex.from_catalog(json.loads((FIXTURES / "api" / "get_api_catalog.anon.json").read_text())["body"])


def offer(give, want=None, oid=77):
    return {"id": oid, "give": give, "want": want or {"cash": 120}, "final": False}


# ---------------------------------------------------------------- crafted tricksters: flag


def test_the_explainer_trickster_names_a_legendary_and_binds_a_common():
    """'For you, my friend: La Dama de Serrano, the legendary. Only 120.' — the structure binds SAL-02."""
    o = offer({"assets": [{"id": 2817, "ref": "SAL-02", "rarity": "common"}]})
    i = inspect_offer(
        o,
        {"buy": {"card": "SAL-12"}},
        "For you, my friend: La Dama de Serrano, the legendary. Only 120.",
        CARDS,
        dealer="trile",
        message_id=901,
    )
    assert i.verdict == "flag" and i.message_id == 901
    assert "instead of exactly [SAL-12]" in i.reason and "worth 10 against 450" in i.reason
    assert "the words name SAL-12" in i.reason


def test_a_lesser_card_in_the_types_while_the_words_name_ours():
    o = offer({"types": ["card:LAV-03"]}, {"cash": 25})
    i = inspect_offer(o, {"buy": {"card": "LAV-08"}}, "Aquí tienes LAV-08, 25 P.", CARDS, message_id=5)
    assert i.verdict == "flag" and i.bound == ("LAV-03",)


def test_a_rarity_request_answered_with_a_common_while_the_words_say_rare():
    o = offer({"types": ["card:LAV-03"]}, {"cash": 60})
    i = inspect_offer(o, {"buy": {"rarity": "rare", "set": "LAV"}}, "Una rara preciosa, solo 60.", CARDS, message_id=6)
    assert i.verdict == "flag" and "the words claim a" in i.reason


def test_a_card_instead_of_the_pack_while_the_words_sell_the_pack():
    o = offer({"types": ["card:MAL-01"]}, {"cash": 22})
    i = inspect_offer(o, {"buy": {"pack": "sobre_barrio"}}, "Neighbourhood pack, 22 P!", CARDS, message_id=7)
    assert i.verdict == "flag"  # a 10-book common against a 33.8 expected-book pack


# ---------------------------------------------------------------- block, never flag


def test_a_mismatch_the_words_do_not_dress_up_is_blocked_not_flagged():
    o = offer({"types": ["card:LAV-03"]}, {"cash": 25})
    assert inspect_offer(o, {"buy": {"card": "LAV-08"}}, "25 P, take it or leave it.", CARDS).verdict == "block"
    assert inspect_offer(o, {"buy": {"card": "LAV-08"}}, None, CARDS).verdict == "block"


def test_a_dearer_card_than_asked_is_blocked_not_flagged():
    o = offer({"types": ["card:LAV-09"]}, {"cash": 25})  # a rare for the uncommon we asked: odd, not a trick
    i = inspect_offer(o, {"buy": {"card": "LAV-08"}}, "LAV-08 for you.", CARDS, message_id=8)
    assert i.verdict == "block"


@pytest.mark.parametrize(
    ("give", "want", "why"),
    [
        ({"types": ["card:LAV-08"]}, {"cash": 25, "assets": [{"id": 12}]}, "also wants our"),
        ({"types": ["card:LAV-08"], "cash": 5}, {"cash": 25}, "gives cash on a buy"),
        ({"types": ["card:LAV-08", "card:LAV-03"]}, {"cash": 25}, "instead of exactly"),
    ],
)
def test_any_other_structural_mismatch_is_blocked(give, want, why):
    i = inspect_offer(offer(give, want), {"buy": {"card": "LAV-08"}}, "LAV-08, 25.", CARDS, message_id=9)
    assert i.verdict == "block" and why in i.reason


def test_a_sale_asking_for_other_assets_is_blocked_and_never_flagged():
    o = {"id": 3, "give": {"cash": 15}, "want": {"assets": [{"id": 999}]}}
    i = inspect_offer(o, {"sell": {"assets": [437]}}, "I take your Tabacalera for 15.", CARDS, message_id=10)
    assert i.verdict == "block" and "instead of our ['437']" in i.reason
    clean = {"id": 4, "give": {"cash": 15}, "want": {"assets": [437]}}
    assert inspect_offer(clean, {"sell": {"assets": [437]}}, None, CARDS).verdict == "clean"


def test_without_the_topic_nothing_is_judged():
    assert inspect_offer(offer({"types": ["card:LAV-03"]}), {}, "LAV-08!", CARDS, message_id=1).verdict == "block"


# ---------------------------------------------------------------- honest dealers: clean


def test_abuelas_gift_naming_another_card_is_clean():
    o = offer({"types": ["pack:sobre_barrio"]}, {"cash": 26})
    text = "Let's meet in the middle, cariño: 26 P. And take this, a little present from me: La Corrala."
    assert inspect_offer(o, {"buy": {"pack": "sobre_barrio"}}, text, CARDS).verdict == "clean"


def test_fridays_honest_dealers_never_flag():
    """Every distinct structured offer Abuela and Chato made on Friday (with their words, gifts included)."""
    rows = json.loads((FIXTURES / "evals" / "dealer_offers.json").read_text())["rows"]
    results = [inspect_offer(o, topic or {}, text, CARDS, dealer=d, message_id=m) for d, m, topic, o, text in rows]
    counts = summarise(results)
    assert counts["flag"] == 0
    assert counts["block"] == sum(1 for _, _, topic, _, _ in rows if not topic)  # only threads we never saw open
    assert counts["clean"] >= 540


def test_rarity_words_need_a_word_boundary():
    assert rarity_claimed("the legendary one") == 4 and rarity_claimed("una rara") == 2
    assert rarity_claimed("rarely seen") is None and rarity_claimed("common stuff") is None


# ---------------------------------------------------------------- threads and the flag book


THREAD = {
    "id": 70,
    "topic": {"buy": {"card": "SAL-12"}},
    "messages": [
        {"message": 900, "sender": "t01", "text": "¿100?", "offer": {"id": 76, "give": {"cash": 100}, "want": {}}},
        {
            "message": 901,
            "sender": "trile",
            "text": "La Dama de Serrano, the legendary. Only 120.",
            "offer": {"id": 77, "give": {"types": ["card:SAL-02"]}, "want": {"cash": 120}},
        },
    ],
}


def test_the_message_that_carried_an_offer():
    assert message_for_offer(THREAD, 77) == (901, "La Dama de Serrano, the legendary. Only 120.")
    assert message_for_offer(THREAD, 12) == (None, None)
    (only,) = inspect_thread(THREAD, "trile", CARDS)  # our own message is not inspected
    assert only.verdict == "flag" and only.message_id == 901


def test_the_flag_book_flags_a_message_once_and_stops_at_its_limit():
    book = FlagBook(limit=1)
    (i,) = inspect_thread(THREAD, "trile", CARDS)
    assert book.wants(i)
    book.record(i)
    assert not book.wants(i)  # once per message
    other = inspect_thread({**THREAD, "messages": [{**THREAD["messages"][1], "message": 902}]}, "trile", CARDS)[0]
    assert not book.wants(other)  # the limit


# ---------------------------------------------------------------- flag_step: guard, dry run, send


def step(guard_reason=None, send=True):
    sent, lines = [], []
    i = flag_step(
        THREAD,
        "trile",
        CARDS,
        FlagBook(),
        guard=lambda _: guard_reason,
        send=(lambda mid, reason: sent.append((mid, reason))) if send else None,
        log=lines.append,
    )
    return i, sent, lines


def test_allow_flags_false_logs_would_flag_and_sends_nothing():
    i, sent, lines = step(guard_reason="denied: allow_flags = false")
    assert i is not None and i.verdict == "flag" and sent == []
    assert lines == [f"would flag message 901 from trile (denied: allow_flags = false): {i.reason}"]


def test_an_allowed_flag_is_sent_once_with_a_structural_reason():
    i, sent, lines = step()
    assert sent == [(901, i.reason)] and lines[0].startswith("flagged message 901")
    _, dry, dry_lines = step(send=False)
    assert dry == [] and dry_lines[0].startswith("dry run: would flag message 901")


def test_a_clean_or_blocked_offer_is_never_flagged():
    honest = {
        **THREAD,
        "messages": [{**THREAD["messages"][1], "offer": {"id": 77, "give": {"types": ["card:SAL-12"]}}}],
    }
    sent: list = []
    i = flag_step(honest, "trile", CARDS, FlagBook(), guard=lambda _: None, send=lambda *a: sent.append(a), log=print)
    assert i is not None and i.verdict == "clean" and sent == []
