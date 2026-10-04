"""SALES invites the most active teams to our 0 % venue on its own, with words only.

No offer and no money move: one thread per team per game, opened on our venue, one plain true line (Omar,
Sun 4 Oct: the sales agent must bring other teams to our market and help sell our duplicates). Teams that bid
for a card we hold twice go first. A lost response is never retried blindly.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from bazaar_agent import rivals
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.agents.team_desk import DeskView, venue_invite
from bazaar_agent.guardrails import Guardrails, LedgerStore, kill_switch
from bazaar_agent.intel import TEAM_ID

SPARE_LINE = " Tenemos cromos repetidos para vender allí y compramos los que te sobren."


def own_free_venue(v: DeskView) -> Any | None:
    return next(
        (
            p
            for p in v.venues
            if p.owner == v.us
            and p.status == "open"
            and p.fee_bps == 0
            and p.fee_per_card == 0
            and p.pending_fee in (None, (0, 0))
        ),
        None,
    )


def duplicates(me: dict[str, Any]) -> set[str]:
    held = Counter(str(a.get("ref")) for a in me.get("assets") or [] if a.get("kind") == "card")
    return {ref for ref, n in held.items() if n >= 2}


def pick_team(v: DeskView, rules: Guardrails, invited: Callable[[str], bool]) -> str | None:
    """Most promising team not yet invited: bids for our duplicates first, then plain activity."""
    spare = duplicates(v.me)
    busy = {str(t.get("with")) for t in v.threads} | {str(t.get("team")) for t in v.threads}
    own_venue = {p.owner for p in v.venues if p.status == "open"}
    demand: Counter[str] = Counter()
    activity: Counter[str] = Counter()
    try:
        for row in rivals.listings(v.events):
            activity[row.maker] += 1
            if row.side == "bid" and row.ref in spare and row.open_at(v.tick):
                demand[row.maker] += 1
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        return None  # unreadable public hints cannot authorize a write
    teams = [
        t
        for t in activity
        if TEAM_ID.fullmatch(t) and t != v.us and t not in busy and not rules.never_trades_with(t) and not invited(t)
    ]
    return min(teams, key=lambda t: (-demand[t], t in own_venue, -activity[t], t), default=None)


class SalesInvite:
    def __init__(
        self, team: Any, rules: Guardrails, ledger: LedgerStore, rec: Recorder, log: Callable[[str], None], live: bool
    ) -> None:
        self.team, self.rules, self.ledger, self.rec, self.log, self.live = team, rules, ledger, rec, log, live
        self.sent_words: Callable[[int, str, str, int, int, str, dict[str, Any], str], None] | None = None

    def _claim(self, v: DeskView, team: str) -> str:
        return f"operator_say:sales_invite:{getattr(self.ledger, 'world', 'unknown')}:{v.us}:{team}"

    def on_tick(self, v: DeskView) -> bool:
        if not self.live or not self.rules.team_threads_enabled or not v.window_open() or kill_switch(self.rules):
            return False
        own = own_free_venue(v)
        if own is None:
            return False
        dealers = sum(t.get("kind") in ("dealer", "persona") for t in v.threads)
        if (
            len(v.threads) + max(0, self.rules.team_threads_dealer_reserve - dealers) >= v.max_threads
            or len(v.threads) - dealers >= self.rules.team_threads_max_open
        ):
            return False
        team = pick_team(v, self.rules, lambda t: self.ledger.count_since(self._claim(v, t), -1) > 0)
        if team is None:
            return False
        claim = self._claim(v, team)
        spare = bool(duplicates(v.me))
        text = "Hola: " + venue_invite(own.id).removeprefix("Por cierto: ") + (SPARE_LINE if spare else "")
        did = self.rec.decide(
            v.tick,
            "team_invite",
            f"invite {team} to {own.id}",
            inputs={"counterparty": team, "venue": own.id},
            reason="words-only invitation to our zero-fee venue; no offer, no cash",
            guardrail="no money or card moves",
            chosen=True,
            status="approved",
            move={"kind": "team_invite"},
        )
        self.ledger.record(claim, v.tick, v.t_hours)  # one invitation per team per game, even if the reply is lost
        if not v.window_open() or kill_switch(self.rules):
            self.rec.decisions.settle(did, "expired")
            return False
        opened = self.rec.send(
            did,
            v.tick,
            "open_thread",
            {"team": team, "venue": own.id},
            lambda: self.team.open_thread(team, topic={"trade": "cards"}, venue=own.id),
        )
        if opened is None or type(opened.get("id")) is not int:
            return True
        tid = opened["id"]
        if not v.window_open() or kill_switch(self.rules):
            return True
        body = self.rec.send(did, v.tick, "say", {"thread_id": tid}, lambda: self.team.say(tid, text))
        if body is not None and type(body.get("message")) is int and self.sent_words is not None:
            try:
                self.sent_words(tid, team, v.us, v.tick, body["message"], text, {}, own.id)
            except Exception as error:  # words buffer is best effort
                self.log(f"sales: invite words not buffered ({type(error).__name__})")
        return True
