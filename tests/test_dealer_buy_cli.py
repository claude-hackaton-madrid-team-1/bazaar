"""`bazaar dealer buy --live` guards every move with our OTHER open offers (the maker's board bids, the
taker's dealer threads) and leaves out its own thread, whose bid the next move replaces (PR #72 review)."""

import re

import pytest
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_agent.agents.dealer import Move, Outcome
from bazaar_agent.config import Settings
from bazaar_agent.guardrails import GUARDRAILS_FILE, Ledger, parse_guardrails

OWN = 85  # the thread `dealer buy` opens
ACCEPT_20 = ((Move("accept", 20, 7), OWN),)


def offer(oid, cash, thread=None, ref="pack:sobre_barrio"):
    return {
        "id": oid,
        "maker": "t01",
        "status": "open",
        "thread": thread,
        "give": {"cash": cash},
        "want": {"types": [ref]},
    }


class Client:
    def __init__(self, cash, offers):
        self.cash, self.offers, self.opened = cash, offers, False

    def clock(self):
        return {"tick": 100, "t_hours": 1.5, "tick_seconds": 30.0, "next_tick_in": 20.0}

    def me(self):
        return {"id": "t01", "cash": self.cash, "assets": []}

    def my_offers(self):
        own = [offer(900, 18, thread=OWN)] if self.opened else []  # our bid in the thread we are playing
        return {"offers": self.offers + own}


@pytest.fixture
def dealer_buy(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def run(client, ledger_spent=0, moves=ACCEPT_20):
        ledger = Ledger(tmp_path / "ledger.jsonl")
        if ledger_spent:
            ledger.record("spend", 90, 1.4, ledger_spent, "LAV-02")

        def fake_negotiate(client_, dealer, topic, plan, *, guard, **kw):
            client_.opened = True
            seen["verdicts"] = [guard(move, tid) for move, tid in moves]
            return Outcome(OWN, "walked", None, (), 1)

        monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_dir=tmp_path))
        # the committed file as it was with our venue off (Omar's 270 floor, no bond reserve, the 150 hourly
        # cap): these cases are about the cash our open offers promise, not the venue or today's limits
        venue_off = re.sub(
            r"`max_spend_per_game_hour` = \d+",
            "`max_spend_per_game_hour` = 150",
            re.sub(
                r"`cash_floor` = \d+",
                "`cash_floor` = 270",
                GUARDRAILS_FILE.read_text(encoding="utf-8").replace(
                    "`allow_venue_open` = true", "`allow_venue_open` = false"
                ),
            ),
        )
        monkeypatch.setattr(cli, "_rules", lambda: parse_guardrails(venue_off, GUARDRAILS_FILE))
        monkeypatch.setattr(cli, "team_client", lambda settings: client)
        monkeypatch.setattr(cli, "_ledger", lambda source, live=False: ledger)
        monkeypatch.setattr(cli, "_dealer_personas", lambda settings: [])  # no /api/dealers read: today's plan
        monkeypatch.setattr("bazaar_agent.agents.dealer.negotiate", fake_negotiate)
        result = CliRunner().invoke(cli.app, ["dealer", "buy", "sobre_barrio", "--start", "6", "--max", "20", "--live"])
        return result, seen.get("verdicts")

    return run


def test_the_guard_counts_the_makers_bids_and_the_takers_threads(dealer_buy):
    # Cash 395: a maker bid of 40 and a taker thread bid of 70 at Chato are promised. Accepting 20 here
    # leaves 395 - 110 - 20 = 265 < cash_floor 270 if all three fill (no venue planned: no bond reserve).
    # Before the fix the guard saw 395 - 20 = 375.
    client = Client(395, [offer(1, 40, ref="card:LAV-09"), offer(2, 70, thread=90, ref="card:LAV-10")])
    result, verdicts = dealer_buy(client)
    assert result.exit_code == 0, result.output
    (denied,) = verdicts
    assert denied is not None and "cash_floor 270" in denied


def test_the_guard_leaves_out_its_own_thread_bid(dealer_buy):
    # 120 spent this game hour; our own thread bids 18, and the next move (accept 20) replaces it:
    # 120 + 20 = 140 <= 150 passes. Counting our own 18 as well would wrongly read 158 > 150.
    client = Client(400, [])
    result, verdicts = dealer_buy(
        client, ledger_spent=120, moves=((Move("accept", 20, 7), OWN), (Move("bid", 20), 999))
    )
    assert result.exit_code == 0, result.output
    own, other = verdicts
    assert own is None
    assert other is not None and "max_spend_per_game_hour 150" in other  # seen from any other thread


def test_it_refuses_to_open_when_our_open_offers_already_hold_the_cash(dealer_buy):
    client = Client(380, [offer(1, 40, ref="card:LAV-09"), offer(2, 70, thread=90, ref="card:LAV-10")])
    result, verdicts = dealer_buy(client)
    assert result.exit_code == 1 and verdicts is None  # 380 - 110 - 6 < cash_floor 270: no thread opened
    assert "guardrails refuse to open this thread" in result.output
