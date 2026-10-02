"""Fakes for the taker/maker tests: a team client that records every write, a public client, a fixed clock.

No network anywhere: every read returns a canned payload, every write is appended to `sent`.
"""

from copy import deepcopy

from bazaar_agent.agents.runtime import MarketFeed
from bazaar_agent.decisions import DecisionLog
from bazaar_agent.guardrails import Guardrails, Ledger
from bazaar_agent.ticks import Clock
from tests.test_strategy import CATALOG, DEALERS, EVENTS, ME, PARAMS

RASTRO = {
    "venue": "rastro",
    "owner": "world",
    "fee_bps": 500,
    "fee_per_card": 1,
    "status": "open",
    "house": True,
    "trades": 40,
    "rules": {},
}
CHEAP = {
    "venue": "v02",
    "owner": "t12",
    "fee_bps": 0,
    "fee_per_card": 0,
    "status": "open",
    "trades": 0,
    "rules": {"mechanism": "board"},
}
OURS = {
    "venue": "v09",
    "owner": "t01",
    "fee_bps": 0,
    "fee_per_card": 0,
    "status": "open",
    "trades": 500,
    "rules": {"mechanism": "board"},
}
TICK = 100


def clock(tick=TICK, next_tick_in=40.0, tick_seconds=60.0, **limits):
    return Clock(tick=tick, tick_seconds=tick_seconds, next_tick_in=next_tick_in, t_hours=1.5, limits=limits or {})


def ask(oid, ref, price, venue="rastro", asset=900, maker="m1", to=None):
    return {
        "id": oid,
        "maker": maker,
        "to": to,
        "venue": venue,
        "thread": None,
        "status": "open",
        "give": {"cash": 0, "assets": [{"id": asset, "kind": "card", "ref": ref, "rarity": "common"}], "types": []},
        "want": {"cash": price, "assets": [], "types": []},
        "expires_tick": 140,
        "created_tick": 90,
    }


def bid(oid, ref, price, venue="rastro", maker="t01", thread=None, expires=140, created=90):
    return {
        "id": oid,
        "maker": maker,
        "to": None,
        "venue": venue,
        "thread": thread,
        "status": "open",
        "give": {"cash": price, "assets": [], "types": []},
        "want": {"cash": 0, "assets": [], "types": [f"card:{ref}"]},
        "expires_tick": expires,
        "created_tick": created,
    }


def our_ask(oid, asset_id, ref, price, venue="rastro", expires=140, created=90):
    o = ask(oid, ref, price, venue, asset_id, maker="t01")
    return {**o, "expires_tick": expires, "created_tick": created}


class FakeTeam:
    """The team client. Reads are canned; writes are recorded in `sent` and answer like the server."""

    def __init__(self, me=None, offers=(), threads=(), thread_payloads=None, now=None):
        self._me = deepcopy(me or ME)
        self.offers = list(offers)
        self.threads = list(threads)
        self.thread_payloads = dict(thread_payloads or {})
        self.now = now or clock()
        self.sent: list[tuple] = []
        self.reads: list[str] = []
        self._ids = iter(range(5000, 6000))

    def me(self):
        self.reads.append("me")
        return deepcopy(self._me)

    def my_offers(self):
        self.reads.append("my_offers")
        return {"offers": deepcopy(self.offers)}

    def my_threads(self, status=None):
        self.reads.append("my_threads")
        return {"threads": deepcopy(self.threads)}

    def thread(self, tid):
        self.reads.append(f"thread {tid}")
        return deepcopy(
            self.thread_payloads.get(tid, {"id": tid, "status": "open", "messages": [], "standing_offers": []})
        )

    def clock(self):
        return self.now.model_dump()

    def open_thread(self, with_, topic=None, venue=None):
        tid = next(self._ids)
        self.sent.append(("open_thread", with_, topic))
        return {"id": tid, "with": with_, "topic": topic, "status": "open"}

    def say(self, tid, text="", price=None, offer=None, topic=None):
        self.sent.append(("say", tid, price))
        return {"id": next(self._ids)}

    def close_thread(self, tid):
        self.sent.append(("close_thread", tid))
        return {"id": tid, "status": "closed"}

    def accept(self, offer_id, assets=None):
        self.sent.append(("accept", offer_id))
        return {"ok": True, "settles_tick": self.now.tick + 1}

    def cancel(self, offer_id):
        self.sent.append(("cancel", offer_id))
        return {"id": offer_id, "status": "cancelled"}

    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.sent.append(("list_offer", give, want, venue))
        return {"id": next(self._ids), "status": "open"}


class FakePublic:
    def __init__(self, boards=None, venues=(RASTRO, CHEAP), catalog=None, dealers=None, events=None):
        self.boards = boards or {}
        self._venues = list(venues)
        self._catalog = catalog or CATALOG
        self._dealers = dealers if dealers is not None else DEALERS
        self._events = events if events is not None else EVENTS

    def catalog(self):
        return deepcopy(self._catalog)

    def dealers(self):
        return {"dealers": deepcopy(self._dealers)}

    def venues(self):
        return {"venues": deepcopy(self._venues)}

    def board(self, venue="rastro"):
        return {"offers": deepcopy(self.boards.get(venue, []))}

    def feed_window(self, limit):
        return deepcopy(self._events)


def parts(tmp_path, **rules):
    """Rules, params, a file ledger, a JSONL decision log and a feed: what both agents need."""
    return {
        "rules": Guardrails(**rules),
        "params": lambda tick: PARAMS,
        "ledger": Ledger(tmp_path / "ledger.jsonl"),
        "decisions": DecisionLog(tmp_path),
        "feed": MarketFeed(lambda n: deepcopy(EVENTS)),
    }


def rows(tmp_path, name="decisions.jsonl"):
    import json

    path = tmp_path / "agents" / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def writes(team):
    return [s for s in team.sent]
