import pytest
from typer.testing import CliRunner

from bazaar_agent import render
from bazaar_agent.agents import seller
from bazaar_agent.cli import app
from bazaar_agent.guardrails import Context, Guardrails, Ledger
from tests.test_render import text

ME = {
    "id": "t01",
    "cash": 353,
    "assets": [
        {"id": 15, "kind": "card", "ref": "LAT-09", "rarity": "rare", "your_value": 35.0},
        {"id": 1, "kind": "card", "ref": "SAL-01", "rarity": "common", "your_value": 3.2},
        {"id": 11, "kind": "card", "ref": "SAL-01", "rarity": "common", "your_value": 3.2},
        {"id": 7, "kind": "pack", "ref": "sobre_barrio"},
    ],
}
CTX = Context(cash=353, held={"LAT-09": 1, "SAL-01": 2}, tick=90, t_hours=1.5)
RULES = Guardrails()


class FakeOfferClient:
    def __init__(self):
        self.posted = []

    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.posted.append({"give": give, "want": want, "venue": venue, "expires_in_ticks": expires_in_ticks})
        return {"id": 777, "status": "open"}


def test_find_copy_by_asset_id_or_by_the_copy_we_lose_least():
    assert seller.find_copy(ME, "15")["ref"] == "LAT-09"
    assert seller.find_copy(ME, "SAL-01")["id"] == 11
    with pytest.raises(seller.OfferError, match="no card"):
        seller.find_copy(ME, "LAV-09")
    with pytest.raises(seller.OfferError):
        seller.find_copy(ME, "7")  # a pack is not a card


def test_a_sell_listing_is_the_structured_offer_with_our_value():
    listing = seller.sell_listing(ME, "LAT-09", 68)
    assert (listing.give, listing.want, listing.venue) == ({"assets": [15]}, {"cash": 68}, "rastro")
    assert listing.action().your_value == 35.0 and listing.action().kind == "sell"
    with pytest.raises(seller.OfferError, match="outside"):
        seller.sell_listing(ME, "LAT-09", 0)


def test_sell_list_is_a_dry_run_by_default():
    client = FakeOfferClient()
    out = seller.post(client, seller.sell_listing(ME, "15", 68), CTX, RULES, live=False)
    assert not out.sent and out.verdict.allowed and client.posted == []
    assert out.message.startswith("dry run: would sell asset 15 (LAT-09) for 68 P")


def test_sell_list_live_posts_the_offer():
    client = FakeOfferClient()
    out = seller.post(client, seller.sell_listing(ME, "15", 68, "t03-venue"), CTX, RULES, live=True, expires_in_ticks=9)
    assert out.sent and out.offer == {"id": 777, "status": "open"}
    assert client.posted == [
        {"give": {"assets": [15]}, "want": {"cash": 68}, "venue": "t03-venue", "expires_in_ticks": 9}
    ]


def test_guardrails_deny_a_sell_below_our_value_even_when_live():
    client = FakeOfferClient()
    out = seller.post(client, seller.sell_listing(ME, "15", 30), CTX, RULES, live=True)
    assert not out.sent and not out.verdict.allowed and client.posted == []
    assert "your_value 35.0" in out.message


def test_the_kill_switch_stops_listings():
    client = FakeOfferClient()
    paused = Context(cash=353, held={}, tick=90, t_hours=1.5, paused=True)
    out = seller.post(client, seller.sell_listing(ME, "15", 90), paused, RULES, live=True)
    assert not out.sent and client.posted == []


def test_a_live_bid_posts_cash_for_any_copy_and_books_the_commitment(tmp_path):
    client, ledger = FakeOfferClient(), Ledger(tmp_path / "ledger.jsonl")
    out = seller.post(client, seller.bid_listing("LAV-09", "rare", 70), CTX, RULES, live=True, ledger=ledger)
    assert out.sent and client.posted[0]["give"] == {"cash": 70} and client.posted[0]["want"] == {"cards": ["LAV-09"]}
    assert ledger.spent_since(0) == 70


def test_bids_are_guarded_like_buys():
    client = FakeOfferClient()
    held = seller.post(client, seller.bid_listing("LAT-09", "rare", 60), CTX, RULES, live=True)
    too_high = seller.post(client, seller.bid_listing("LAV-09", "rare", 81), CTX, RULES, live=True)
    assert "block_buying_held_cards" in held.message and "max_price_rare" in too_high.message
    assert client.posted == []


def test_offer_sides_read_in_a_few_words():
    assert seller.offer_side({"assets": [{"ref": "LAT-09", "id": 15}], "cash": 0}) == "LAT-09#15"
    assert seller.offer_side({"cash": 70, "cards": ["LAV-09"]}) == "70 P + any LAV-09"
    assert seller.offer_side({"types": ["card:SAL-08"]}) == "any SAL-08"
    assert seller.offer_side(None) == "-"
    offers = [{"id": 5, "status": "open", "venue": "rastro", "give": {"assets": [{"ref": "LAT-09", "id": 15}]}}]
    assert "LAT-09#15" in text(render.offers_table(offers))


def test_cli_cancel_is_a_dry_run_without_live():
    result = CliRunner().invoke(app, ["sell", "cancel", "12"])
    assert result.exit_code == 0 and "would cancel offer 12" in result.stdout
