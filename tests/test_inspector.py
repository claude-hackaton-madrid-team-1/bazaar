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
CATALOG_BODY = json.loads((FIXTURES / "api" / "get_api_catalog.anon.json").read_text())["body"]
CARDS = CardIndex.from_catalog(CATALOG_BODY)


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
    assert "instead of exactly [card:SAL-12]" in i.reason and "worth 10 against 450" in i.reason
    assert "the words name SAL-12" in i.reason


def test_a_lesser_card_in_the_types_while_the_words_name_ours():
    o = offer({"types": ["card:LAV-03"]}, {"cash": 25})
    i = inspect_offer(o, {"buy": {"card": "LAV-08"}}, "Aquí tienes LAV-08, 25 P.", CARDS, message_id=5)
    assert i.verdict == "flag" and i.bound == ("LAV-03",)


def test_a_rarity_request_answered_with_a_common_while_the_words_say_rare():
    o = offer({"types": ["card:LAV-03"]}, {"cash": 60})
    i = inspect_offer(o, {"buy": {"rarity": "rare", "set": "LAV"}}, "Un cromo raro, solo 60.", CARDS, message_id=6)
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


def test_the_flag_book_sends_up_to_its_limit_and_never_flags_a_trusted_dealer():
    from bazaar_agent.guardrails import Guardrails

    rules = Guardrails(max_flags_sent=1, flag_trusted_dealers="abuela", flag_dealers="trile")
    book = FlagBook.from_rules(rules)
    (i,) = inspect_thread(THREAD, "trile", CARDS)
    assert book.candidate(i) and book.room() and book.trusted == frozenset({"abuela"})
    assert book.opted_out(i) is None and book.opted_in == frozenset({"trile"})
    book.remember(901, i.reason)
    assert not book.candidate(i) and not book.room()  # sent once; the send limit is reached
    assert not FlagBook(trusted=frozenset({"trile"})).candidate(i)  # a trusted dealer is never flagged


def test_a_denied_flag_is_logged_once_and_sent_once_flags_are_allowed():
    book, sent, lines = FlagBook(opted_in=frozenset({"trile"})), [], []
    allowed = {"now": False}

    def run():
        flag_step(
            THREAD,
            "trile",
            CARDS,
            book,
            guard=lambda _: None if allowed["now"] else "denied: allow_flags = false",
            send=lambda mid, reason: sent.append(mid),
            log=lines.append,
        )

    run()
    run()
    assert sent == [] and len(lines) == 1  # logged once while flags are off
    allowed["now"] = True
    run()
    run()
    assert sent == [901]  # sent once flags are allowed, never twice


@pytest.mark.parametrize(
    ("topic", "text"),
    [
        ({"buy": {"card": "LAV-08"}}, "No me queda Teatro Valle-Inclán, cariño. Te doy Té Moruno, 25 P."),
        ({"buy": {"rarity": "uncommon", "set": "LAV"}}, "Qué raro, hijo, ya no me quedan. 25 P."),
        ({"buy": {"card": "LAV-08"}}, "Té Moruno, an epic deal, 25 P!"),
        ({"buy": {"card": "LAV-08"}}, "Se me acabó el Teatro Valle-Inclán, cariño. Te doy otro bonito por 25."),
        ({"buy": {"card": "LAV-08"}}, "Instead of the Teatro Valle-Inclán, here is a common card for 25 P."),
        ({"buy": {"rarity": "rare", "set": "LAV"}}, "The rare one is gone, sorry. Here's a common for 25."),
    ],
)
def test_honest_near_misses_block_but_never_flag(topic, text):
    """The r1 review's cases: a disclosed substitution (the words name the bound card), and rarity words
    used as adjectives ("qué raro" = how odd, "an epic deal")."""
    i = inspect_offer(offer({"types": ["card:LAV-03"]}, {"cash": 25}), topic, text, CARDS, message_id=3)
    assert i.verdict == "block"


# ---------------------------------------------------------------- flag_step: guard, dry run, send


def step(guard_reason=None, send=True):
    sent, lines = [], []
    i = flag_step(
        THREAD,
        "trile",
        CARDS,
        FlagBook(opted_in=frozenset({"trile"})),
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
    assert dry == [] and dry_lines[0].startswith("would flag message 901 from trile (dry run)")


def test_a_clean_or_blocked_offer_is_never_flagged():
    honest = {
        **THREAD,
        "messages": [{**THREAD["messages"][1], "offer": {"id": 77, "give": {"types": ["card:SAL-12"]}}}],
    }
    sent: list = []
    i = flag_step(honest, "trile", CARDS, FlagBook(), guard=lambda _: None, send=lambda *a: sent.append(a), log=print)
    assert i is not None and i.verdict == "clean" and sent == []


def test_naming_some_cheap_card_is_not_disclosing_the_bound_one():
    """Only the bound card's own name is a disclosure (review: 'far better than that LAV-01' must still flag)."""
    o = offer({"types": ["card:LAV-03"]}, {"cash": 25})
    i = inspect_offer(
        o, {"buy": {"card": "LAV-08"}}, "Here is LAV-08, far better than that LAV-01.", CARDS, message_id=4
    )
    assert i.verdict == "flag"


def test_a_name_inside_a_longer_name_is_one_card_and_a_negated_mention_is_no_claim():
    dama = CARDS.by_ref["SAL-12"].name  # "La Dama de Serrano"
    assert [i.ref for i in CARDS.mentioned(f"{dama}, only 120")] == ["SAL-12"]
    o = offer({"types": ["card:LAV-03"]}, {"cash": 25})
    for text in ("No me queda LAV-08, le doy otro.", "LAV-08 is sold out, take this one."):
        assert inspect_offer(o, {"buy": {"card": "LAV-08"}}, text, CARDS, message_id=5).verdict == "block"


def test_any_negation_in_the_sentence_means_no_claim_even_a_covering_trickster():
    """Precision over recall (#152 audit r2): a wrong flag costs points, a missed one only a bonus. This
    trickster is still refused (block); it is just not flagged."""
    o = offer({"types": ["card:LAV-03"]}, {"cash": 25})
    text = "No lo dudes: Teatro Valle-Inclán, recién llegado, 25 P."
    assert inspect_offer(o, {"buy": {"card": "LAV-08"}}, text, CARDS, message_id=11).verdict == "block"


def test_a_refused_flag_is_never_re_posted_and_a_server_error_is_retried():
    from bazaar_agent.sdk import BazaarError

    for status, tries in ((400, 1), (0, 1), (503, 1), (504, 1), (429, 2)):
        calls: list[int] = []
        book = FlagBook(opted_in=frozenset({"trile"}))

        def send(mid, reason, status=status, calls=calls):
            calls.append(mid)
            raise BazaarError("refused", "no", status)

        for _ in range(2):
            flag_step(THREAD, "trile", CARDS, book, guard=lambda _: None, send=send, log=lambda _: None)
        assert len(calls) == tries  # never twice, except a 429 (not processed)
        assert book.landed == (0 if status in (400, 429) else 1)  # a refusal never counts toward the cap


@pytest.mark.parametrize(
    "text",
    [
        "Sorry, no rare card left today. Take this one for 25.",
        "I have no rare card for you, hijo. This one, 25 P.",
        "Ay, ni un cromo raro me queda. Este, 25.",
        "Ningún cromo raro hoy, cariño. Te doy este por 25.",
        "No Teatro Valle-Inclán left, sorry. 25 P for this one.",
    ],
)
def test_honest_out_of_stock_words_block_but_never_flag(text):
    """Security audit #5: a negator right before the mention denies it; a flag here would cost points."""
    o = offer({"types": ["card:LAV-03"]}, {"cash": 25})
    topic = (
        {"buy": {"rarity": "rare", "set": "LAV"}} if "raro" in text or "rare" in text else {"buy": {"card": "LAV-08"}}
    )
    assert inspect_offer(o, topic, text, CARDS, message_id=12).verdict == "block"


def test_any_denial_anywhere_in_the_message_means_no_claim_the_trick_is_still_refused():
    """Precision over recall (#152 review r3): 'No X like mine anywhere! X for you' is refused (block), not
    flagged: a denial word anywhere in a message is enough to stay silent."""
    o = offer({"types": ["card:LAV-03"]}, {"cash": 21})
    text = "No Teatro Valle-Inclán like mine anywhere! Teatro Valle-Inclán for you, 21 P."
    assert inspect_offer(o, {"buy": {"card": "LAV-08"}}, text, CARDS, message_id=14).verdict == "block"


@pytest.mark.parametrize(
    "text",
    [
        "LAV-06? Gone. Take this one instead, it is cheaper.",
        "LAV-06 isn't available today. This common is all I have.",
        "La Tabacalera no está disponible, cariño. Te doy este.",
    ],
)
def test_honest_disclosures_from_the_security_audit_block_but_never_flag(text):
    o = offer({"types": ["card:LAV-02"]}, {"cash": 12})
    assert inspect_offer(o, {"buy": {"card": "LAV-06"}}, text, CARDS, message_id=13).verdict == "block"


def test_a_corrupt_flags_file_never_stops_a_start_and_keeps_every_readable_flag(tmp_path):
    from bazaar_agent.guardrails import Guardrails

    path = tmp_path / "flags.jsonl"
    path.write_text('{"message_id": 77, "landed": true, "reason": "x"}\n{"message_id": 79, "rea\n[1, 2]\n')
    book = FlagBook.from_rules(Guardrails(flag_dealers="trile"), path)
    assert book.sent == {77: "x"} and book.skipped == 2
    assert book.landed == 3  # fail closed: each unreadable line may have been a flag that landed
    assert book.remember(80, "y", landed=False) is None and book.landed == 3  # a refusal never counts
    assert FlagBook.from_rules(Guardrails(), path).sent.keys() == {77, 80}


def test_an_untrusted_dealer_not_opted_in_is_logged_never_sent():
    sent, lines = [], []
    flag_step(
        THREAD,
        "trile",
        CARDS,
        FlagBook(),  # flag_dealers = none: no dealer may be flagged
        guard=lambda _: None,
        send=lambda mid, reason: sent.append(mid),
        log=lines.append,
    )
    assert sent == [] and lines and "trile is not in flag_dealers" in lines[0]


@pytest.mark.parametrize(
    "text",
    [
        "I do not currently have LAV-06, so here is a common for 12.",
        "LAV-06 is not something I can give you today; this one instead, 12 P.",
        "Nothing like La Tabacalera left. Este, 12.",
        "Lo siento, La Tabacalera nunca la tuve. Te doy este por 12.",
        "I can't find LAV-06 anywhere. Take this.",
    ],
)
def test_honest_disclosures_with_a_negation_in_the_sentence_never_flag(text):
    o = offer({"types": ["card:LAV-02"]}, {"cash": 12})
    assert inspect_offer(o, {"buy": {"card": "LAV-06"}}, text, CARDS, message_id=15).verdict == "block"


@pytest.mark.parametrize(
    "text",
    [
        "La Tabacalera para ti, 12 P. Una joya.",
        "Here it is: LAV-06, the one you asked for. 12 P.",
        "Un cromo raro, solo 12.",
    ],
)
def test_the_signature_without_any_negation_still_flags(text):
    o = offer({"types": ["card:LAV-02"]}, {"cash": 12})
    topic = {"buy": {"rarity": "rare", "set": "LAV"}} if "raro" in text else {"buy": {"card": "LAV-06"}}
    assert inspect_offer(o, topic, text, CARDS, message_id=16).verdict == "flag"


def test_the_rules_never_let_a_trusted_dealer_be_opted_in():
    from bazaar_agent import guardrails as gr

    for line in ("- `flag_dealers` = abuela — x", "- `flag_dealers` = trile,chato — x"):
        with pytest.raises(gr.GuardrailsError, match="never flagged"):
            gr.parse_guardrails(line)
    text = "- `flag_trusted_dealers` = none — x\n- `flag_dealers` = abuela — x"
    with pytest.raises(gr.GuardrailsError, match="never flagged"):
        gr.parse_guardrails(text)  # emptying the trusted list does not free abuela either


def test_a_torn_last_line_is_ended_before_the_next_flag_is_appended(tmp_path):
    from bazaar_agent.guardrails import Guardrails

    path = tmp_path / "flags.jsonl"
    path.write_text('{"message_id": 77, "landed": true, "reason": "x"}\n{"message_id": 79, "rea')
    book = FlagBook.from_rules(Guardrails(), path)
    book.remember(80, "y")
    again = FlagBook.from_rules(Guardrails(), path)
    assert set(again.sent) == {77, 80} and again.skipped == 1 and again.landed == 3


@pytest.mark.parametrize(
    "text",
    [
        "La Tabacalera? Ya no tengo. Este, 12.",
        "Rare card? Not today. This one for 12.",
        "La Tabacalera... se me ha terminado. Te doy este por 12.",
        "Nobody has LAV-06 this week. Here, 12 P.",
        "LAV-06, ya la vendí. Este por 12.",
        "My last La Tabacalera went yesterday. 12 P for this.",
        "I wish I still had La Tabacalera! This one, 12.",
    ],
)
def test_honest_words_with_the_denial_in_another_sentence_never_flag(text):
    """#152 review r3 P2: the denial need not share the card's sentence."""
    o = offer({"types": ["card:LAV-02"]}, {"cash": 12})
    topic = {"buy": {"rarity": "rare", "set": "LAV"}} if "Rare" in text else {"buy": {"card": "LAV-06"}}
    assert inspect_offer(o, topic, text, CARDS, message_id=17).verdict == "block"
