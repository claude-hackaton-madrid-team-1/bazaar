"""Flag precision over the feed (S1 part B): the evidence read before `allow_flags` goes on."""

import json
from pathlib import Path

from typer.testing import CliRunner

from bazaar_agent.agents.flag_evidence import dealer_offers, precision
from bazaar_agent.agents.inspector import CardIndex

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG = json.loads((FIXTURES / "api" / "get_api_catalog.anon.json").read_text())["body"]
CARDS = CardIndex.from_catalog(CATALOG)
TRUSTED = frozenset({"abuela", "chato"})


def opened(eid, thread, dealer, topic):
    payload = {"thread": thread, "kind": "persona", "team": "t07", "with": dealer, "topic": topic}
    return {"id": eid, "tick": 1, "type": "thread.opened", "payload": payload}


def said(eid, thread, dealer, mid, give, cash, text, sender=None):
    offer = {"id": 100 + mid, "maker": sender or dealer, "give": give, "want": {"cash": cash}, "status": "open"}
    payload = {"thread": thread, "kind": "persona", "message": mid, "sender": sender or dealer, "with": dealer}
    payload |= {"text": text, "offer": offer}
    return {"id": eid, "tick": 2, "type": "thread.message", "payload": payload}


EVENTS = [
    opened(1, 10, "abuela", {"buy": {"card": "LAV-08"}}),
    said(2, 10, "abuela", 1, {"types": ["card:LAV-08"]}, 21, "Teatro Valle-Inclán, 21 P. Toma un regalo: La Corrala"),
    said(3, 10, "abuela", 2, {"types": ["card:LAV-08"]}, 18, None, sender="t07"),  # the team's bid: not a dealer's
    opened(4, 11, "trile", {"buy": {"card": "SAL-12"}}),
    said(5, 11, "trile", 3, {"types": ["card:SAL-02"]}, 120, "La Dama de Serrano, the legendary. Only 120."),
    said(6, 11, "trile", 3, {"types": ["card:SAL-02"]}, 120, "La Dama de Serrano, the legendary. Only 120."),  # dup
    said(7, 12, "abuela", 4, {"types": ["pack:sobre_barrio"]}, 25, None),  # opened before the capture
    opened(8, 13, "abuela", {}),  # an empty topic is no topic
    said(9, 13, "abuela", 5, {"types": ["pack:sobre_barrio"]}, 25, None),
]


def test_dealer_offers_keep_one_row_per_dealer_offer_with_its_topic():
    offers = dealer_offers(EVENTS)
    assert [(o.dealer, o.message_id, o.thread) for o in offers] == [
        ("abuela", 1, 10),
        ("trile", 3, 11),
        ("abuela", 4, 12),
        ("abuela", 5, 13),
    ]
    assert offers[1].topic == {"buy": {"card": "SAL-12"}} and offers[2].topic is None and offers[3].topic is None


def test_precision_counts_flags_per_dealer_and_jevs_state_has_no_text():
    e = precision(dealer_offers(EVENTS), CARDS, TRUSTED)
    assert (e.offers, e.known_topic, dict(e.counts)) == (4, 2, {"clean": 1, "block": 0, "flag": 1})
    assert [i.message_id for i in e.would_flag] == [3] and e.flags_from_untrusted == 1
    state = e.as_state()
    assert state["would_flag_untrusted_dealers"] == 1 and state["would_flag_trusted_dealers"] == 0
    assert state["untrusted_dealers_seen"] == ["trile"] and state["untrusted_dealer_offers"] == 1
    assert "Dama" not in json.dumps(state)  # counts only: no counterparty words reach Jev


def test_fridays_feed_offers_never_flag_a_trusted_dealer():
    """The fixture's rows are Friday's feed offers: the trusted dealers' would-flag count is 0."""
    rows = json.loads((FIXTURES / "evals" / "dealer_offers.json").read_text())["rows"]
    events = []
    for n, (dealer, mid, topic, offer, text) in enumerate(rows):
        events.append(opened(3 * n, 1000 + n, dealer, topic))
        payload = {"thread": 1000 + n, "kind": "persona", "message": mid, "sender": dealer, "with": dealer}
        events.append(
            {"id": 3 * n + 1, "tick": 1, "type": "thread.message", "payload": payload | {"text": text, "offer": offer}}
        )
    e = precision(dealer_offers(events), CARDS, TRUSTED)
    assert e.as_state()["would_flag_trusted_dealers"] == 0 and e.counts["clean"] >= 540


def test_the_flags_precision_command_prints_jevs_state(monkeypatch, tmp_path):
    from bazaar_agent import cli, flags_cli
    from bazaar_agent.config import Settings

    feed = tmp_path / "feed"
    feed.mkdir()
    (feed / "feed.jsonl").write_text("".join(json.dumps(e) + "\n" for e in EVENTS))

    class Public:
        def catalog(self):
            return CATALOG

        def feed_window(self, n):
            return []

    monkeypatch.setattr(flags_cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(flags_cli, "public_client", lambda settings: Public())
    result = CliRunner().invoke(cli.app, ["flags", "precision", "--json", "--feed-dir", str(feed)])
    assert result.exit_code == 0, result.output
    state = json.loads(result.output.strip().splitlines()[-1])
    assert state["would_flag_untrusted_dealers"] == 1 and state["dealer_offers_inspected"] == 4
    table = CliRunner().invoke(cli.app, ["flags", "precision", "--feed-dir", str(feed)])
    assert table.exit_code == 0 and "would flag message 3 from trile" in " ".join(table.output.split())
