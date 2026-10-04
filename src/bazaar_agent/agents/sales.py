"""SALES owns team conversations; Taker's proven executors keep every trade guard.

No dealer openings, venue-board scan, crafting, pack opening or second rival scanner.
The existing feed archive/supply scan and latest team matrix provide prospect hints.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from bazaar_agent import telemetry as tm
from bazaar_agent.agents.market import our_open_offers
from bazaar_agent.agents.runtime import Snapshot, TickWindow
from bazaar_agent.agents.sales_outreach import SalesOutreach
from bazaar_agent.agents.sales_promotion import SalesPromotion
from bazaar_agent.agents.seller import offers_in, unsettled_accepts
from bazaar_agent.agents.taker import Taker, _TickRun, board_proposal, swap_proposal
from bazaar_agent.guardrails import kill_switch
from bazaar_agent.strategy import build_market
from bazaar_agent.ticks import Clock, action_budget_s


class Sales(Taker):
    """Reuse the acceptance engine, replacing the entire orchestration with team-only work."""

    snapshot_dealers = False

    def __init__(self, *args: Any, latest_matrix: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rec.agent = "sales"
        self.team_desk.words = self.words_fn
        self.latest_matrix = latest_matrix
        self.outreach = SalesOutreach(
            self.team, self.rules, self.ledger, self.rec, self.log, self.live, words=self.words_fn
        )
        self.promotion = SalesPromotion(self.team, self.public, self.rules, self.ledger, self.rec, self.live)
        if self.thread_store is not None:
            self.promotion.sent_words = self.thread_store.sent
            self.team_desk.sent_words = self.thread_store.sent
            self.outreach.sent_words = self.thread_store.sent

    def on_tick(self, clock: Clock) -> None:
        with tm.span("sales.tick", values={"game.tick": clock.tick, "agent": "sales"}):
            super().on_tick(clock)

    def _tick(self, snap: Snapshot, threads: list[dict[str, Any]], window: TickWindow) -> None:
        clock = snap.clock
        if self.hub is not None:
            self.hub.tick(clock.tick, clock.t_hours, snap.us)
        if kill_switch(self.rules) or not window.open() or not self._claim_team_desk(clock):
            self.log(f"tick {clock.tick} sales: holding (paused, deadline or team desk owned elsewhere)")
            return
        offers = offers_in(snap.offers)
        mine, _ = our_open_offers(snap.offers, snap.us)
        run = _TickRun(snap, window, self.params(clock.tick), offers, mine, window.deadline - action_budget_s(clock))
        self._unsettled = unsettled_accepts(snap.me, self.ledger, clock.tick)
        run.listed = frozenset(int(t["id"]) for t in threads if isinstance(t.get("id"), int))
        view = run.team_view = self._team_view(run, threads)
        if self.latest_matrix is not None:
            self.latest_matrix.refresh(clock.tick)
            self.team_desk.matrix = self.latest_matrix.current(clock.tick)
        market = build_market(snap.me, snap.catalog, snap.events, [], snap.scan)
        venues = {v.id: v for v in snap.venues if v.status == "open" and v.owner != snap.us}
        proposals = [swap_proposal(a) for a in self._team_desk("proposals", lambda: self.team_desk.proposals(view))]
        for tid, offer in self.team_desk.cash_offers(view):
            candidates = [board_proposal(c) for c in self._board(run, market, [offer], venues)]
            candidates += self._bids(run, market, [offer], venues)
            proposals += [replace(p, cash_thread=tid, inputs={**p.inputs, "thread_id": tid}) for p in candidates]
        self._accept(run, proposals)
        taken = {p.swap.thread_id for p in run.accepted if p.swap is not None}
        taken |= {p.cash_thread for p in run.accepted if p.cash_thread is not None}
        self._team_desk("converse", lambda: self.team_desk.converse(run.team_view or view, taken))
        if window.open():
            offered = self.outreach.on_tick(run.team_view or view, self.team_desk.matrix)
            if not offered:
                self.promotion.on_tick(run.team_view or view, self.team_desk.matrix)
        for payload in self.team_desk.payloads():
            self._keep(payload, snap)
        self.log(f"tick {clock.tick} sales: {len(proposals)} candidates, {len(run.accepted)} accepted")

    def _after_sends(self, tick: int) -> None:
        # ponytail: existing thread persistence; no second scanner, watchdog or outcome learner.
        if self.thread_store is not None:
            self.thread_store.flush(tick)
