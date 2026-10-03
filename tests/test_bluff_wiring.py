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

from bazaar_agent.agents.bluff import ENV, PLAIN, Counterparty, TacticBook
from bazaar_agent.agents.dealer import BidPlan, negotiate
from bazaar_agent.agents.duelist import DUEL_WORDS, DuelMove, duel_choice
from bazaar_agent.agents.status import StatusHub, public_decision
from bazaar_agent.agents.tactics import ABUELA_ALLOWED, BY_ID, TACTICS, numbers_in
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.guardrails import Ledger
from tests.agent_fakes import TICK, FakePublic, FakeTeam, clock, parts, rows
from tests.test_dealer import FakeDealerClient
from tests.test_duel_jev import LIVE

TACTIC_IDS = tuple(t.id for t in TACTICS)
CHATO_CP = Counterparty.dealer("chato")


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
    assert bids and all(r["inputs"]["tactic"] in ABUELA_ALLOWED | {PLAIN} for r in bids)  # her allow-list only
    assert all(r["inputs"]["tactic_counterparty"] == "dealer:abuela" for r in bids)


def test_an_accept_beats_a_bluff_in_the_taker(tmp_path):
    # Her opening 30, then 19 meets our next bid: the desk accepts, no words on the accept. (An opening ask she
    # holds from the first offer is walked and reopened lower, so the accept needs her to move once.)
    asks = [30, 19, 19]
    team = run_taker(tmp_path, on(), asks, ticks=3)
    assert ("accept", 802) in team.sent
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


def picked(dealer: str, asks: list[int]) -> list[str]:
    """The tactic `negotiate()` picked for each bid (its `words tactic` log lines)."""
    lines: list[str] = []
    negotiate(
        TextDealer(asks),
        dealer,
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 12),
        log=lines.append,
        sleep=lambda _: None,
        bluff=on(),
    )
    return [line.split("words tactic ")[1].split(" ")[0] for line in lines if "words tactic " in line]


def test_negotiate_gives_abuela_her_allow_list_and_chato_bluffs():
    to_abuela = picked("abuela", [30, 29, 28, 27, 26, 25])
    assert to_abuela and set(to_abuela) <= ABUELA_ALLOWED | {PLAIN}
    to_chato = [t for t in picked("chato", [30, 29, 28, 27, 26, 25]) if t != PLAIN]
    assert to_chato and not any(BY_ID[t].kindness for t in to_chato)


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
            return {"ok": True, "message": 777}  # our message id, as the simulator answers

        def duels(self, done=False):  # the runner's first tick also reads `?done=true`
            return {"duels": []} if done else super().duels()

    class Feed:  # the keyless public feed, canned: no network in unit tests
        events: list[dict] = []

        def feed_window(self, limit):
            return deepcopy(Feed.events)

    client = TextDuels([{**LIVE, "rival_offer": {"id": 702, "price": 110, "tick": 133, "days": 0}}])
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
    monkeypatch.setattr(cli, "team_client", lambda settings: client)
    monkeypatch.setattr(cli, "_feed_reader", lambda settings: Feed().feed_window)
    # Stands in for the shared ledger a live run needs (#162), as tests/test_jev_journal.py does.
    monkeypatch.setattr(cli, "_ledger", lambda source, live=False: Ledger(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(db, "connect", down)
    monkeypatch.setattr(db, "connect_ready", down)
    client.feed = Feed
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


def test_duel_run_under_v2_keeps_its_template_words_and_no_tactic(duel_cli, monkeypatch):
    from dataclasses import replace

    cli, client, tmp_path = duel_cli
    monkeypatch.setenv(ENV, "1")
    loaded = cli._rules()
    v2 = replace(loaded, rules=loaded.rules.model_copy(update={"duel_policy": "v2"}))
    monkeypatch.setattr(cli, "_rules", lambda: v2)
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "bluff tactics OFF (duel_policy v2 sends template words only)" in " ".join(result.output.split())
    assert all(text == DUEL_WORDS for kind, *_, text in client.sent if kind == "say")
    assert all("tactic" not in r["inputs"] for r in duel_rows(tmp_path))


def test_duel_run_accepts_a_good_rival_offer_without_any_tactic(duel_cli, monkeypatch):
    cli, client, tmp_path = duel_cli
    monkeypatch.setenv(ENV, "1")
    client.payload = [{**LIVE, "deadline_tick": 135, "rival_offer": {"id": 703, "price": 120, "tick": 133, "days": 0}}]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert client.sent == [("accept", 95)]  # inside our limit in the endgame: accepted, no words at all
    (row,) = duel_rows(tmp_path)
    assert row["kind"] == "duel_accept" and "tactic" not in row["inputs"]


def test_duel_run_reads_a_flag_on_our_tactic_message_after_its_sends(duel_cli, monkeypatch):
    cli, client, tmp_path = duel_cli
    monkeypatch.setenv(ENV, "1")
    client.feed.events = [{"id": 31, "type": "flag.raised", "tick": 134, "payload": {"team": "t05", "message": 777}}]
    result = CliRunner().invoke(cli.app, ["duel", "run", "--play", "--no-jev", "--max-ticks", "1"])
    assert result.exit_code == 0, result.output
    assert "bluff: a team flagged our message 777" in " ".join(result.output.split())


class TimedDealer(TextDealer):
    """Records the tick of each bid, so a fake feed can show a strike only after the bid that drew it."""

    def __init__(self, asks):
        super().__init__(asks)
        self.bid_ticks: list[int] = []

    def tick(self) -> int:
        return 100 + self.reads // self.reads_per_tick

    def say(self, tid, text, price):
        self.bid_ticks.append(self.tick())
        super().say(tid, text, price)


def test_dealer_buy_blames_a_strike_on_the_message_that_drew_it():
    book, lines = on(), []
    client = TimedDealer([30, 29, 28])
    strike = {"id": 5, "type": "persona.strike", "payload": {"persona": "chato", "team": "t01"}}

    def feed(limit):  # Chato answers the first bid with a strike that shows from the next tick on
        first = client.bid_ticks[0] if client.bid_ticks else None
        return [{**strike, "tick": first}] if first is not None and client.tick() > first else []

    negotiate(
        client,
        "chato",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 12),
        log=lines.append,
        sleep=lambda _: None,
        bluff=book,
        events=feed,
    )
    first = [line.split("words tactic ")[1].split(" ")[0] for line in lines if "words tactic " in line][0]
    (penalty,) = [lr for lr in book.lessons.values() if lr.detail["result"] == "strike"]
    assert penalty.detail["tactic"] == first and penalty.detail["step"] == 0  # the guilty message, not the next one
    assert book.arms(CHATO_CP)[first].off_today() is not None


def test_dealer_buy_without_feed_or_with_tactics_off_never_reads_it():
    reads: list[int] = []
    book = TacticBook(env={ENV: "0"}, us="t01")
    negotiate(
        TextDealer([30, 29, 28]),
        "chato",
        {"buy": {"card": "LAV-03"}},
        BidPlan(6, 1, 12),
        log=lambda _: None,
        sleep=lambda _: None,
        bluff=book,
        events=lambda limit: reads.append(limit) or [],
    )
    assert reads == []
