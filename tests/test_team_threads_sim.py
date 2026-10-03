"""N17 end to end: the taker's team desk against the simulator's rival bots (in process, no network). The
desk opens swap threads, concedes, judges the rivals' counters, and closes swaps that give only duplicates
for cards we miss, on the house venue."""

from dataclasses import replace

from bazaar_sim import market, threads
from bazaar_sim.errors import SimError
from bazaar_sim.world import World
from tests.simkit import QUIET, manual_world

US = "t01"
# ---------------------------------------------------------------- end to end: our team desk against the simulator


class _SimTeam:
    """Our team client, straight onto the simulator's rule-checked actions (no HTTP): a refusal is the SDK's
    BazaarError, as the real client raises it."""

    def __init__(self, w: World, us: str = US) -> None:
        self.w, self.us = w, us

    def _call(self, fn, *args):
        from bazaar_agent.sdk import BazaarError

        try:
            return fn(*args)
        except SimError as e:
            raise BazaarError(e.code, str(e)) from None

    def thread(self, tid):
        from bazaar_sim import views

        return self._call(lambda: views.thread_view(self.w, threads.participant_thread(self.w, self.us, tid)))

    def open_thread(self, with_, topic=None, venue=None):
        th = self._call(threads.open_thread, self.w, self.us, {"with": with_, "topic": topic, "venue": venue})
        return {"id": th.id}

    def say(self, tid, text="", price=None, offer=None, topic=None):
        return self._call(threads.say, self.w, self.us, tid, {"text": text, "offer": offer})

    def cancel(self, oid):
        return self._call(market.cancel, self.w, self.us, oid)

    def close_thread(self, tid):
        return self._call(threads.close_thread, self.w, self.us, tid)

    def accept(self, oid, assets=None):
        return self._call(market.accept, self.w, self.us, oid, {"assets": assets} if assets else {})


def _jev_yes(state):
    """A stub Jev that says a confident yes (the real one is a network call: never in a unit test)."""
    from bazaar_agent.agents.runtime import JevAdvice

    _jev_yes.states.append(state)
    return JevAdvice("yes", 0.95)


_jev_yes.states = []


def _run_desk(tmp_path, enabled: bool, ticks: int = 40, jev=_jev_yes):
    from bazaar_agent.agents.market import venues_from
    from bazaar_agent.agents.runtime import Recorder
    from bazaar_agent.agents.team_desk import DeskView, TeamDesk
    from bazaar_agent.decisions import DecisionLog
    from bazaar_agent.guardrails import Guardrails, Ledger, context_from
    from bazaar_agent.strategy import load_strategy
    from bazaar_sim import scoring, views

    m = manual_world(replace(QUIET, rivals=6))
    w = m.world
    client, lines = _SimTeam(w), []
    rules = Guardrails(team_threads_enabled=enabled)
    desk = TeamDesk(client, rules, Recorder("taker", DecisionLog(tmp_path), True, lines.append), lines.append, True)
    desk.env = {}
    ledger, params = Ledger(tmp_path / "ledger.jsonl"), load_strategy().params
    m.step(20)  # the rivals list their duplicates: the feed shows who holds what
    before = dict(w.held_counts(US))
    for _ in range(ticks):
        me = scoring.me_view(w, US)
        mine = [
            views.thread_view(w, t) for t in w.state.threads.values() if US in (t.team, t.with_) and t.status == "open"
        ]
        offers = [o for rows in market.my_offers(w, US).values() if isinstance(rows, list) for o in rows]
        view = DeskView(
            tick=w.tick,
            t_hours=1.0,
            us=US,
            me=me,
            catalog=views.catalog_view(w),
            events=views.feed_view(w, 500)["events"],
            venues=venues_from(views.venues_view(w)),
            threads=mine,
            offers=offers,
            params=params,
            max_threads=6,
            in_use=len(mine),
            ctx=lambda thread, me=me: context_from(me, w.tick, 1.0, ledger, rules),
            window_open=lambda: True,
            jev=jev,
        )
        taken = set()
        for a in desk.proposals(view):  # the taker would rank it; here it is the only candidate
            if not desk.jev_gate(view, a.trade, a.offer.net_cash, a.fee, a.thread_id, 0)[0]:
                continue  # as the taker's `_accept_swap`: only Jev's confident yes takes their offer
            client.accept(a.offer.offer_id, a.pick)
            desk.accepted(a, w.tick)
            taken.add(a.thread_id)
        desk.converse(view, taken)
        m.step()
    return w, before, lines


def test_our_desk_closes_swaps_with_the_rivals_and_only_ever_gives_duplicates(tmp_path):
    w, before, lines = _run_desk(tmp_path, enabled=True)
    deals = [t for t in w.state.threads.values() if t.kind == "team" and t.team == US and t.status == "deal"]
    assert deals, "\n".join(lines[-20:])
    swaps = [
        e.payload
        for e in w.state.events
        if e.type == "settlement" and US in (e.payload.get("parties") or []) and e.payload.get("venue")
    ]
    gave = [i["ref"] for p in swaps for i in p["items"] if i["frm"] == US]
    got = [i["ref"] for p in swaps for i in p["items"] if i["to"] == US]
    assert gave and all(before.get(ref, 0) >= 2 for ref in gave)  # a duplicate, never the last copy
    assert got and all(before.get(ref, 0) == 0 for ref in got)  # a card we were missing
    assert all(p["venue"] == "rastro" for p in swaps)  # the house venue
    assert any("refused" in line for line in lines)  # the rivals' greedy counters were judged and refused


def test_with_the_desk_off_we_open_no_team_thread(tmp_path):
    w, _, lines = _run_desk(tmp_path, enabled=False, ticks=10)
    assert not [t for t in w.state.threads.values() if t.kind == "team" and t.team == US] and lines == []


def test_every_swap_we_send_or_take_was_judged_by_jev(tmp_path):
    _jev_yes.states.clear()
    w, _, lines = _run_desk(tmp_path, enabled=True)
    asked = {s["swap"]["kind"] for s in _jev_yes.states}
    deals = [t for t in w.state.threads.values() if t.kind == "team" and t.team == US and t.status == "deal"]
    assert deals and "propose" in asked, "\n".join(lines[-20:])
    assert all(s["swap"]["give"]["copies_held"] >= 2 for s in _jev_yes.states)  # only duplicates are ever asked


def test_an_undecided_jev_sends_no_swap_in_the_simulator(tmp_path):
    from bazaar_agent.agents.runtime import JevAdvice

    asked = []

    def undecided(state):
        asked.append(state)
        return JevAdvice("undecided", 0.6, reason="below_threshold")

    w, before, lines = _run_desk(tmp_path, enabled=True, jev=undecided)
    ours = [t for t in w.state.threads.values() if t.kind == "team" and t.team == US]
    swaps = [e for e in w.state.events if e.type == "settlement" and US in (e.payload.get("parties") or [])]
    assert asked and ours == [] and swaps == [] and dict(w.held_counts(US)) == before
    assert any("jev undecided" in line for line in lines)
