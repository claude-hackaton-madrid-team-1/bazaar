from tests.agent_fakes import TICK, FakePublic, FakeTeam
from tests.bites.kit import at, dealer_took_our_bid, make_taker, thread_bid
from tests.test_taker import dealer_ask, her
from tests.test_taker_restart import _bid_row
from bazaar_agent.decisions import DecisionLog as _DL


def _old_taker_opened(tmp_path, tick, price):
    _DL(tmp_path).decide(_bid_row(40, tick, price))


def test_overlap_new_process_does_not_drive_the_live_old_process_thread(tmp_path):
    team = FakeTeam()
    a, la, ledger = make_taker(tmp_path, team, FakePublic())
    a.on_tick(at(team, TICK))  # A opens 5000, bids 18
    team.threads = [{"id": 5000, "with": "abuela", "team": "t01", "status": "open"}]
    team.offers = [thread_bid(5001, 5000, "LAV-08", 18) | {"created_tick": TICK, "expires_tick": TICK + 2}]
    b, lb, _ = make_taker(tmp_path, team, FakePublic())
    her(team, 5000, dealer_ask(800, 24))
    for k in range(1, 5):
        a.on_tick(at(team, TICK + k))
        b.on_tick(at(team, TICK + k))
    assert ("close_thread", 5000) not in team.sent  # FAILS: B walks A's live thread at tick 101


def test_adopted_thread_sees_her_counter(tmp_path):
    _old_taker_opened(tmp_path, tick=TICK, price=15)
    team = FakeTeam(threads=[{"id": 40, "with": "abuela", "team": "t01", "status": "open"}])
    team.offers = [thread_bid(77, 40, "LAV-08", 15) | {"created_tick": TICK, "expires_tick": TICK + 2}]
    her(team, 40, dealer_ask(800, 24, status="countered"), dealer_ask(801, 20))
    t, lines, _ = make_taker(tmp_path, team, FakePublic())
    for k in range(1, 4):
        t.on_tick(at(team, TICK + k))
    assert team.sent == [("close_thread", 40)] and t.reopen_at == {} and t.cooling == {}  # observed


def test_one_failed_store_read_at_start_latches_the_wrapup_off(tmp_path, monkeypatch):
    from bazaar_agent.decisions import DecisionLog
    team = FakeTeam()
    old, _, ledger = make_taker(tmp_path, team, FakePublic())
    old.on_tick(at(team, TICK))
    team.threads = [{"id": 5000, "with": "abuela", "team": "t01", "status": "open"}]
    team.offers = [thread_bid(5001, 5000, "LAV-08", 18) | {"created_tick": TICK, "expires_tick": TICK + 2}]
    real, calls = DecisionLog.thread_trails, {"n": 0}
    def flaky(self, agent, since):
        calls["n"] += 1
        return {} if calls["n"] == 1 else real(self, agent, since)
    monkeypatch.setattr(DecisionLog, "thread_trails", flaky)
    new, _, _ = make_taker(tmp_path, team, FakePublic())
    new.on_tick(at(team, TICK + 1))
    dealer_took_our_bid(team, 5000, 5001, "LAV-08", 18)
    for k in range(2, 6):
        new.on_tick(at(team, TICK + k))
    assert ledger.spent_since(0) == 18  # FAILS: 0 (trails read once, never again)


def _mirror_bids(team, tick, tid=5000):
    """What the real game lists: A's newest say(price) is an open thread bid made this tick, standing 2 ticks."""
    says = [s for s in team.sent if s[0] == "say" and s[1] == tid]
    if says:
        team.offers = [thread_bid(9000 + len(says), tid, "LAV-08", says[-1][2]) | {"created_tick": tick, "expires_tick": tick + 2}]


def test_overlap_realistic_bids_listed(tmp_path):
    """Same overlap, but A's bids are listed as the real game lists them (made this tick, stand 2 ticks)."""
    team = FakeTeam()
    a, la, ledger = make_taker(tmp_path, team, FakePublic())
    a.on_tick(at(team, TICK))
    team.threads = [{"id": 5000, "with": "abuela", "team": "t01", "status": "open"}]
    _mirror_bids(team, TICK)
    b, lb, _ = make_taker(tmp_path, team, FakePublic())
    her(team, 5000, dealer_ask(800, 24))
    by_b = []
    for k in range(1, 8):
        a.on_tick(at(team, TICK + k))
        _mirror_bids(team, TICK + k)
        n = len(team.sent)
        b.on_tick(at(team, TICK + k))
        by_b += [(TICK + k, s) for s in team.sent[n:]]
    print("B sent:", by_b, "A sent:", [s for s in team.sent if s not in [x for _, x in by_b]])
    assert not [s for _, s in by_b if s[:2] == ("close_thread", 5000)]


def test_overlap_she_is_slow_to_answer(tmp_path):
    """A waits for her first ask (no new bid); B (same store, started at 101) must not close A's live thread."""
    team = FakeTeam()
    a, la, ledger = make_taker(tmp_path, team, FakePublic())
    a.on_tick(at(team, TICK))
    team.threads = [{"id": 5000, "with": "abuela", "team": "t01", "status": "open"}]
    _mirror_bids(team, TICK)
    b, lb, _ = make_taker(tmp_path, team, FakePublic())
    by_b = []
    for k in range(1, 8):
        if TICK + k > team.offers[0]["expires_tick"] if team.offers else False:
            team.offers = []  # her silence: our bid lapses after 2 ticks
        a.on_tick(at(team, TICK + k))
        _mirror_bids(team, TICK + k) if any(s[0] == "say" and len(s) > 2 for s in team.sent[-1:]) else None
        n = len(team.sent)
        b.on_tick(at(team, TICK + k))
        by_b += [(TICK + k, s) for s in team.sent[n:]]
    print("B sent:", by_b, "A conv:", a.convs.get("abuela") is not None, "A log:", [l for l in la if "abuela" in l][-3:])
    assert not [s for _, s in by_b if s[:2] == ("close_thread", 5000)]
