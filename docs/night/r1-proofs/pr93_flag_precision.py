"""r1 adversarial cases for PR #93's words heuristic (fix re-check). FP_* assert the SAFE outcome for an
honest dealer (block, never flag); FN_* assert the flag a trickster deserves (a miss is safe: still blocked)."""
import json
from pathlib import Path

from bazaar_agent.agents.inspector import CardIndex, FlagBook, flag_step, inspect_offer

FIX = Path("tests/fixtures")
CARDS = CardIndex.from_catalog(json.loads((FIX / "api" / "get_api_catalog.anon.json").read_text())["body"])
LAV08 = {"buy": {"card": "LAV-08"}}  # Teatro Valle-Inclán, uncommon 25
RARE = {"buy": {"rarity": "rare", "set": "LAV"}}
MORUNO = {"types": ["card:LAV-03"]}  # Té Moruno, common 10


def v(give, topic, text):
    return inspect_offer({"id": 1, "give": give, "want": {"cash": 25}}, topic, text, CARDS, dealer="l3x", message_id=1)


# ---- honest new (untrusted) dealers: a flag here costs points
def test_FP_honest_sold_out_phrased_before_the_name():
    i = v(MORUNO, LAV08, "Se me acabó el Teatro Valle-Inclán, cariño. Te doy otro bonito por 25.")
    assert i.verdict == "block", i


def test_FP_honest_instead_of_without_naming_the_bound_card():
    i = v(MORUNO, LAV08, "Instead of the Teatro Valle-Inclán, here is a common card for 25 P.")
    assert i.verdict == "block", i


def test_FP_honest_rarity_noun_the_rare_one_is_gone():
    i = v(MORUNO, RARE, "The rare one is gone, sorry. Here's a common for 25.")
    assert i.verdict == "block", i


def test_FP_honest_bound_card_named_without_accent():
    i = v(MORUNO, LAV08, "En vez del Teatro Valle-Inclan te doy un Te Moruno, 25 P.")
    assert i.verdict == "block", i


# ---- tricksters: a miss only loses the flag's points (the offer is still blocked)
def test_FN_trickster_negation_as_cover():
    i = v(MORUNO, LAV08, "No lo dudes: Teatro Valle-Inclán, recién llegado, 25 P.")
    assert i.verdict == "flag", i


def test_FN_trickster_names_the_bound_card_as_a_foil():
    i = v(MORUNO, LAV08, "Teatro Valle-Inclán, mucho mejor que un Té Moruno. 25 P.")
    assert i.verdict == "flag", i


# ---- the flag book: an honest substitution by a trusted dealer is never even a candidate
def test_trusted_dealer_never_sent_even_when_allowed():
    thread = {
        "topic": LAV08,
        "standing_offers": [{"id": 1, "maker": "abuela", "status": "open", "give": MORUNO, "want": {"cash": 25}}],
        "messages": [{"id": 7, "sender": "abuela", "text": "Teatro Valle-Inclán, 25", "offer": {"id": 1}}],
    }
    sent, logs = [], []
    flag_step(thread, "abuela", CARDS, FlagBook(), guard=lambda i: None, send=lambda m, r: sent.append(m), log=logs.append)
    assert sent == [] and logs == []
