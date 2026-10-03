"""Shared helpers for the bite tests: a live Taker on the fakes, and the server-side effects of a deal.

Only `tests.agent_fakes` (identical on main and on fix/cash-spend-accounting) and the public Taker API
are used, so every bite test runs unchanged on both versions. No network, no DATABASE_URL.
"""

from bazaar_agent.agents.taker import Taker, TakerConfig
from tests.agent_fakes import clock, parts

US = "t01"


def make_taker(tmp_path, team, public, *, live=True, threads=3, **rules):
    lines: list[str] = []
    kw = parts(tmp_path, **rules)
    t = Taker(
        team,
        public,
        live=live,
        log=lines.append,
        now=lambda: 1000.0,
        sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=threads),
        **kw,
    )
    return t, lines, kw["ledger"]


def at(team, tick):
    team.now = clock(tick=tick)
    return team.now


def thread_bid(oid, tid, ref, price, status="open"):
    """Our structured bid inside a dealer thread, as /api/me/offers and the thread view show it."""
    return {
        "id": oid,
        "maker": US,
        "to": "abuela",
        "venue": None,
        "thread": tid,
        "status": status,
        "give": {"cash": price, "assets": [], "types": []},
        "want": {"cash": 0, "assets": [], "types": [f"card:{ref}"]},
        "expires_tick": 999,
        "created_tick": 0,
        "final": False,
    }


def dealer_took_our_bid(team, tid, oid, ref, price, dealer="abuela", offer_in_messages=True):
    """The dealer accepts our standing bid at the tick boundary and it settles at once (sim
    `threads._dealer_accepts` -> `settle_now`): the thread is a deal, our offer is settled (so it
    leaves /api/me/offers and /api/me/threads?status=open), the cash is gone and the card is ours.
    `offer_in_messages=False`: a thread view that does not carry the settled offer in its messages
    (the sim's does; the real server's shape is unverified)."""
    settled = thread_bid(oid, tid, ref, price, status="settled")
    team.thread_payloads[tid] = {
        "id": tid,
        "status": "deal",
        "closed_reason": "deal",
        "with": dealer,
        "team": US,
        "messages": [
            {"id": 1, "sender": US, "text": "...", "offer": settled if offer_in_messages else None},
            {"id": 2, "sender": dealer, "text": "Deal!", "offer": None},
        ],
        "standing_offers": [],
    }
    team.threads = [th for th in team.threads if th.get("id") != tid]
    team.offers = [o for o in team.offers if o.get("thread") != tid]
    team._me["cash"] -= price
    team._me["assets"].append({"id": 7000 + oid, "kind": "card", "ref": ref, "rarity": "uncommon", "your_value": 1.0})


def standing_thread_bids(team):
    """Each thread's standing bid after the tick: the last `say` price per thread, else the bid that was
    already open in /api/me/offers. A thread we closed has no standing bid."""
    standing = {o["thread"]: int(o["give"]["cash"]) for o in team.offers if o.get("thread") is not None}
    for s in team.sent:
        if s[0] == "say" and s[2] is not None:
            standing[s[1]] = s[2]
        if s[0] == "close_thread":
            standing.pop(s[1], None)
    return standing
