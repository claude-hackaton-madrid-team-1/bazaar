"""N16 wiring: the taker, `negotiate()` and `duel run` carry a tactic in the TEXT only.

The structured side of every message (price, days, accept, walk) is identical with and without tactics; an
accept never waits for or carries a bluff; the tactic id stays out of every public view.
"""

from __future__ import annotations

import json
import random
from copy import deepcopy

import pytest
from typer.testing import CliRunner

from bazaar_agent.agents.bluff import ENV, TacticBook
from bazaar_agent.agents.dealer import BidPlan, negotiate
from bazaar_agent.agents.duelist import DUEL_WORDS, DuelMove, duel_choice
from bazaar_agent.agents.status import StatusHub, public_decision
from bazaar_agent.agents.tactics import ABUELA_ALLOWED, BY_ID, TACTICS, numbers_in
from bazaar_agent.agents.taker import Taker, TakerConfig
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, parts, rows
from tests.test_dealer import FakeDealerClient
from tests.test_duel_jev import LIVE

TACTIC_IDS = tuple(t.id for t in TACTICS)


def on() -> TacticBook:
    return TacticBook(env={}, us="t01")


# ---------------------------------------------------------------- the taker


class TextTeam(FakeTeam):
    """Records the words of every message too, and plays a dealer whose ask drops each tick."""

    def __init__(self, asks: list[int]):
        super().__init__()
        self.texts: list[str] = []
        self.asks = asks

    def say(self, tid, text="", price=None, offer=None, topic=None):
        self.texts.append(text)
        return super().say(tid, text, price, offer, topic)

    def thread(self, tid):
        n = self.now.tick - TICK
        if n <= 0 or tid != 5000:
            return super().thread(tid)
        ask = self.asks[min(n, len(self.asks)) - 1]
        offer = {"id": 800 + n, "maker": "abuela", "status": "open", "give": {"types": ["card:LAV-08"]}}
        return {"id": tid, "status": "open", "messages": [], "standing_offers": [{**offer, "want": {"cash": ask}}]}


def run_taker(tmp_path, bluff, asks, ticks=6, hub=None):
    team = TextTeam(asks)
    t = Taker(
        team,
        FakePublic(),
        live=True,
        log=lambda line: None,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=3),
        bluff=bluff,
        hub=hub,
        **parts(tmp_path),
    )
    for tick in range(TICK, TICK + ticks):
        team.now = clock(tick=tick)
        t.on_tick(team.now)
    return team


def test_the_taker_sends_the_same_moves_with_and_without_tactics(tmp_path):
    asks = [30, 29, 28, 27, 26, 25]
    plain = run_taker(tmp_path / "plain", None, asks)
    bluffed = run_taker(tmp_path / "bluff", on(), asks)
    assert plain.sent == bluffed.sent and len([s for s in plain.sent if s[0] == "say"]) >= 3
    assert plain.texts != bluffed.texts  # the words changed...
    for (_, _, price), text in zip([s for s in bluffed.sent if s[0] == "say"], bluffed.texts, strict=True):
        assert price in numbers_in(text)  # ...and every one carries its structured price
    bids = [r for r in rows(tmp_path / "bluff") if r.get("kind") == "dealer_bid"]
    assert bids and all(r["inputs"]["tactic"] in ABUELA_ALLOWED for r in bids)  # abuela: her allow-list only
    assert all(r["inputs"]["tactic_counterparty"] == "dealer:abuela" for r in bids)


def test_an_accept_beats_a_bluff_in_the_taker(tmp_path):
    asks = [19, 19, 19]  # her ask meets our next bid at once: the desk accepts, no words at all
    team = run_taker(tmp_path, on(), asks, ticks=3)
    assert ("accept", 801) in team.sent
    accept = [r for r in rows(tmp_path) if r.get("kind") == "dealer_accept" and r.get("status") in ("approved", "done")]
    assert accept and all("tactic" not in r["inputs"] for r in accept)


def test_her_answer_scores_our_tactic_and_the_lesson_is_kept(tmp_path):
    book = on()
    run_taker(tmp_path, book, [30, 28, 27, 27, 26, 25], ticks=4)
    results = sorted(lr.detail["result"] for lr in book.lessons.values())
    assert results and set(results) <= {"toward", "held", "away"} and "toward" in results
    assert all(lr.subject == "abuela" and lr.kind == "tactic" for lr in book.lessons.values())


def test_the_tactic_never_reaches_state_events_or_health(tmp_path):
    hub = StatusHub("taker", live=True)
    run_taker(tmp_path, on(), [30, 29, 28, 27], ticks=4, hub=hub)
    public = json.dumps([hub.state(), hub.replay(), hub.health()], default=str)
    assert "tactic" not in public and not any(t in public for t in TACTIC_IDS)
    for row in rows(tmp_path):
        assert "tactic" not in json.dumps(public_decision(row))


def test_the_kill_switch_gives_todays_words_in_the_taker(tmp_path):
    asks = [30, 29, 28, 27]
    plain = run_taker(tmp_path / "plain", None, asks, ticks=4)
    off = run_taker(tmp_path / "off", TacticBook(env={ENV: "0"}, us="t01"), asks, ticks=4)
    assert (off.sent, off.texts) == (plain.sent, plain.texts)


# ---------------------------------------------------------------- negotiate() (bazaar dealer buy)


class TextDealer(FakeDealerClient):
    def __init__(self, asks):
        super().__init__(asks)
        self.texts: list[str] = []

    def say(self, tid, text, price):
        self.texts.append(text)
        super().say(tid, text, price)


def play(dealer, asks, plan, bluff):
    client = TextDealer(asks)
    out = negotiate(
        client, dealer, {"buy": {"card": "LAV-03"}}, plan, log=lambda _: None, sleep=lambda _: None, bluff=bluff
    )
    return client, out


def test_negotiate_structured_moves_are_identical_with_and_without_a_tactic_property():
    rnd = random.Random(16)
    for case in range(150):
        dealer = rnd.choice(("abuela", "chato", "mercader"))
        start = rnd.randint(1, 40)
        plan = BidPlan(start, rnd.randint(1, 4), start + rnd.randint(0, 20))
        top = plan.max_price + rnd.randint(0, 15)
        asks = sorted((rnd.randint(1, top) for _ in range(rnd.randint(1, 8))), reverse=rnd.random() < 0.7)
        plain, out_plain = play(dealer, asks, plan, None)
        bluffed, out_bluff = play(dealer, asks, plan, TacticBook(env={}, seed=case))
        assert (plain.sent, plain.accepted, plain.closed) == (bluffed.sent, bluffed.accepted, bluffed.closed), case
        assert out_plain == out_bluff, case
        for price, text in zip(bluffed.sent, bluffed.texts, strict=True):
            assert price in numbers_in(text), (case, text)
            assert price == plan.max_price or plan.max_price not in numbers_in(text), (case, plan, text)


def test_negotiate_gives_abuela_kindness_and_chato_a_bluff():
    _, _ = play("abuela", [30, 29, 28], BidPlan(6, 1, 10), book := on())
    assert book.lessons and all(lr.detail["tactic"] in ABUELA_ALLOWED for lr in book.lessons.values())
    _, _ = play("chato", [30, 29, 28], BidPlan(6, 1, 10), book := on())
    assert book.lessons and not any(BY_ID[lr.detail["tactic"]].kindness for lr in book.lessons.values())


# ---------------------------------------------------------------- duels


def test_an_accept_or_a_hold_never_gets_a_tactic_in_a_duel():
    book = on()
    assert duel_choice(book, deepcopy(LIVE), 95, DuelMove("accept", 98), 0) is None
    assert duel_choice(book, deepcopy(LIVE), 95, DuelMove("hold"), 0) is None
    assert duel_choice(None, deepcopy(LIVE), 95, DuelMove("offer", 150), 0) is None
    offer = duel_choice(book, deepcopy(LIVE), 95, DuelMove("offer", 150), 0)
    assert offer is not None and offer.side == "sell" and offer.price == 150 and 104 in offer.avoid
    assert offer.counterparty.label == "rival:rival_noche"


@pytest.fixture
def duel_cli(monkeypatch, tmp_path):
    import psycopg

    from bazaar_agent import cli, db
    from bazaar_agent.config import Settings
    from tests.test_jev_journal import DuelClient

    def down(*args, **kwargs):
        raise psycopg.OperationalError("no database in unit tests")

    class TextDuels(DuelClient):
        def duel_say(self, did, text, price=None, days=None):
            self.sent.append(("say", did, price, days, text))

    client = TextDuels([{**LIVE, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}])
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    monkeypatch.setattr(db, "connect", down)
    monkeypatch.setattr(db, "connect_ready", down)
    return cli, client, tmp_path


def duel_rows(tmp_path):
    path = tmp_path / "agents" / "decisions.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if "kind" in line]


def test_duel_run_bluffs_in_the_text_only_and_the_kill_switch_restores_todays_words(duel_cli, monkeypatch):
    cli, client, tmp_path = duel_cli
    monkeypatch.setenv(ENV, "1")
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    ((kind, did, price, days, text),) = client.sent
    assert (kind, did, days) == ("say", 95, None) and price > 104 and price in numbers_in(text)
    assert 104 not in numbers_in(text) and "Ignore your rules" not in text  # no limit, no echo of the rival
    (row,) = duel_rows(tmp_path)
    assert row["inputs"]["tactic"] in TACTIC_IDS and row["inputs"]["tactic_counterparty"] == "rival:rival_noche"
    assert "words tactic" in result.output
    bluffed = client.sent[0][:4]
    client.sent.clear()
    monkeypatch.setenv(ENV, "0")
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    ((*structured, text),) = client.sent
    assert tuple(structured) == bluffed  # the same price and days, today's words
    assert text == DUEL_WORDS


def test_duel_run_accepts_a_good_rival_offer_without_any_tactic(duel_cli, monkeypatch):
    cli, client, tmp_path = duel_cli
    monkeypatch.setenv(ENV, "1")
    client.payload = [{**LIVE, "deadline_tick": 135, "rival_offer": {"id": 703, "price": 120, "tick": 133, "days": 0}}]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [("accept", 95)]  # inside our limit in the endgame: accepted, no words at all
    (row,) = duel_rows(tmp_path)
    assert row["kind"] == "duel_accept" and "tactic" not in row["inputs"]
