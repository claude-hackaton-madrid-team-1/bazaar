"""Holdings without a database: what a send does, the etag, the summary, the freshness verdict, live fallbacks.

No network and no Postgres: `SharedDb(None)` is "Postgres unavailable", so every read is live.
"""

from copy import deepcopy

import pytest

from bazaar_agent import holdings as hd
from bazaar_agent.catalog_db import CatalogSync, card_rows, released_sets
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.holdings import Holdings, MeRead, SharedDb, Stored
from bazaar_agent.sdk import BazaarError, TrackedBazaar
from tests.agent_fakes import clock
from tests.test_strategy import CATALOG, ME


@pytest.mark.parametrize(
    ("method", "path", "kind"),
    [
        ("GET", "/api/me", None),
        ("POST", "/api/duels/7/messages", None),
        ("POST", "/api/duels/7/accept", None),
        ("POST", "/api/flags", None),
        ("POST", "/api/threads", "thread"),
        ("POST", "/api/threads/12/messages", "thread"),
        ("POST", "/api/threads/12/close", "trade"),
        ("POST", "/api/offers", "trade"),
        ("POST", "/api/offers/9/accept", "trade"),
        ("DELETE", "/api/offers/9", "trade"),
        ("POST", "/api/packs/3/open", "trade"),
        ("POST", "/api/venues", "trade"),
        ("POST", "/api/some/new/level/route", "trade"),  # unknown: assume it moves holdings
    ],
)
def test_every_send_is_classified_and_unknown_routes_count_as_trades(method, path, kind):
    assert hd.write_kind(method, path) == kind


def test_the_digest_follows_what_we_hold_not_how_it_is_written():
    me = hd.parse_me(ME)
    shuffled = hd.parse_me({**ME, "assets": list(reversed(ME["assets"])), "score": {"score": 9.9}})
    assert me is not None and shuffled is not None
    assert hd.digest(me) == hd.digest(shuffled)  # order and score do not change the holdings
    sold = hd.parse_me({**ME, "assets": ME["assets"][1:]})
    richer = hd.parse_me({**ME, "cash": 401})
    assert sold is not None and richer is not None
    assert len({hd.digest(me), hd.digest(sold), hd.digest(richer)}) == 3


def test_the_summary_lists_cards_with_asset_ids_duplicates_and_sealed_packs():
    me = hd.parse_me(ME)
    assert me is not None
    held = hd.summary(me)
    assert [c["asset"] for c in held["cards"]] == [3, 4, 5, 1, 2]  # by ref, then asset id
    assert held["duplicates"] == {"LAT-03": [3, 4]}
    assert held["packs"] == [{"asset": 6, "pack": "sobre_barrio"}]
    assert [p["set"] for p in held["pages"]] == ["LAV", "LAT"]


def test_a_payload_that_does_not_validate_is_never_parsed():
    assert hd.parse_me({**ME, "id": "abuela"}) is None  # not a team id
    assert hd.parse_me({**ME, "cash": "lots"}) is None
    assert hd.parse_me({**ME, "assets": [{"id": "x"}]}) is None
    assert hd.parse_me(None) is None


def stored(**kw):
    base = {"tick": 100, "epoch": 4, "digest": "d", "read_by": "maker", "me": deepcopy(ME), "age_s": 1.0,
            "epoch_now": 4, "messaged": False}  # fmt: skip
    return Stored(**{**base, **kw})


def test_the_verdict_names_the_first_rule_a_snapshot_breaks():
    assert hd.verdict(None, 5.0) == "no snapshot this tick"
    assert hd.verdict(stored(), 5.0) == "fresh"
    assert hd.verdict(stored(epoch_now=5), 5.0) == "a write of ours since it was read"
    assert hd.verdict(stored(messaged=True), 5.0) == "a thread message of ours this tick"
    assert hd.verdict(stored(age_s=5.5), 5.0) == "older than 5 s"
    assert hd.verdict(stored(me={"id": "nobody"}), 5.0) == "stored payload unreadable"


def test_into_tick_counts_from_the_tick_start_with_slack():
    assert hd.into_tick_s(clock(tick_seconds=30.0, next_tick_in=20.0)) == 10.0 + hd.TICK_SLACK_S
    assert hd.into_tick_s(clock(tick_seconds=30.0, next_tick_in=20.0), 1.5) == 11.5 + hd.TICK_SLACK_S
    assert hd.into_tick_s(clock(tick_seconds=30.0, next_tick_in=99.0)) == hd.TICK_SLACK_S  # paused: no negative


class Reads:
    def __init__(self, me=None):
        self.me_payload = deepcopy(me or {**ME, "tick": 100})
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return deepcopy(self.me_payload)


def test_without_postgres_every_read_is_live_and_says_why():
    reads = Reads()
    h = Holdings(reads, SharedDb(None), reader="taker", rules=Guardrails(), team="t01")
    read = h.me(clock())
    assert (read.source, read.why, read.tick, read.epoch, reads.calls) == ("live", "postgres unavailable", 100, None, 1)
    assert read.line() == "/me live (postgres unavailable)"
    assert h.counts["live"] == 1 and h.counts["db"] == 0


def test_no_clock_unknown_team_or_the_kill_switch_read_live():
    reads = Reads()
    assert Holdings(reads, SharedDb(None), reader="t", rules=Guardrails(), team="t01").me(None).why == "no clock"
    unknown = Holdings(reads, SharedDb(None), reader="t", rules=Guardrails())
    assert unknown.me(clock()).why == "team id not known yet"
    assert unknown.team == "t01"  # learned from /me: the next read may use the database
    off = Holdings(reads, SharedDb(None), reader="t", rules=Guardrails(holdings_from_db=False), team="t01")
    assert off.me(clock()).why == "holdings_from_db = false"


def test_a_refused_me_read_is_raised_not_hidden():
    def refused():
        raise BazaarError("rate_limited", "slow down", 429)

    with pytest.raises(BazaarError):
        Holdings(refused, SharedDb(None), reader="t", rules=Guardrails(), team="t01").me(clock())


def test_a_live_payload_without_a_tick_takes_the_clock_tick():
    no_tick = {k: v for k, v in ME.items() if k != "tick"}
    read = Holdings(Reads(no_tick), SharedDb(None), reader="t", rules=Guardrails(), team="t01").me(clock(tick=77))
    assert read.tick == 77


def test_meta_is_what_a_tool_answers():
    read = MeRead(ME, "db", 100, 1.234, 7, "abc", "maker", "fresh")
    assert read.meta() == {"source": "db", "tick": 100, "age_s": 1.23, "epoch": 7, "digest": "abc",
                           "read_by": "maker", "why": "fresh"}  # fmt: skip
    assert read.line() == "/me from db (tick 100, 1.2 s old, epoch 7, read by maker)"


# ---------------------------------------------------------------- the tracked team client


class Recorded(TrackedBazaar):
    """The SDK's HTTP layer replaced by a list: no network."""

    def __init__(self, hook, fail=False):
        super().__init__("http://127.0.0.1:9", "tk-test-key-000000", on_write=hook)
        self.requests: list[tuple[str, str]] = []
        self.fail = fail


@pytest.fixture
def recorded(monkeypatch):
    from bazaar_agent.sdk import _Http

    def fake_call(self, method, path, body=None, query=None):
        self.requests.append((method, path))
        if getattr(self, "fail", False):
            raise BazaarError("insufficient_cash", "no", 400)
        return {"ok": True}

    monkeypatch.setattr(_Http, "_call", fake_call)
    return Recorded


def test_every_send_tells_the_hook_before_and_after_and_reads_do_not(recorded):
    seen = []
    client = recorded(lambda m, p, phase: seen.append((m, p, phase)))
    client.me()
    client.accept(9)
    client.say(12, "hola", price=10)
    assert seen == [
        ("POST", "/api/offers/9/accept", "before"),
        ("POST", "/api/offers/9/accept", "after"),
        ("POST", "/api/threads/12/messages", "before"),
        ("POST", "/api/threads/12/messages", "after"),
    ]
    assert client.requests[0] == ("GET", "/api/me")


def test_a_refused_send_still_tells_the_hook_after(recorded):
    seen = []
    client = recorded(lambda m, p, phase: seen.append(phase), fail=True)
    with pytest.raises(BazaarError):
        client.accept(9)
    assert seen == ["before", "after"]


def test_a_failing_hook_never_blocks_the_send(recorded):
    def broken(method, path, phase):
        raise RuntimeError("database gone")

    client = recorded(broken)
    assert client.accept(9) == {"ok": True}
    assert client.requests == [("POST", "/api/offers/9/accept")]


def test_the_write_tracker_without_postgres_counts_a_failure_and_returns():
    tracker = hd.WriteTracker(SharedDb(None), "taker")
    tracker("GET", "/api/me", "before")
    tracker("POST", "/api/duels/1/accept", "before")
    assert tracker.failures == 0  # nothing to bump
    tracker("POST", "/api/offers/9/accept", "before")
    assert (tracker.bumps, tracker.failures) == (0, 1)


def test_naming_the_process_renames_its_tracker():
    tracker = hd.process_tracker()
    hd.name_process("maker")
    assert tracker.writer == "maker" and hd.process_tracker() is tracker


# ---------------------------------------------------------------- the catalog


def test_catalog_rows_keep_valid_cards_and_skip_malformed_ones():
    bad = deepcopy(CATALOG)
    bad["sets"][0]["cards"].append({"id": "not a ref", "rarity": "common"})
    bad["sets"][0]["cards"].append({"id": "LAV-99", "rarity": "common", "minted": -1})
    rows = card_rows(bad)
    refs = [r.id for r in rows]
    assert refs == sorted(refs) and "LAV-99" not in refs and "not a ref" not in refs
    assert all(r.set_code == r.id[:3] for r in rows)
    assert released_sets(CATALOG) == frozenset(s["id"] for s in CATALOG["sets"] if s.get("released"))


def test_catalog_sync_writes_first_on_a_release_and_every_n_ticks():
    written = []
    sync = CatalogSync(lambda catalog, tick: written.append(tick) or 1, every=10)
    assert sync.observe(100, CATALOG) == 1
    assert sync.observe(105, CATALOG) is None  # nothing new, not due
    released = deepcopy(CATALOG)
    released["sets"].append({"id": "RET", "name": "El Retiro", "released": True, "cards": []})
    assert sync.observe(106, released) == 1  # a set was released: written at once
    assert sync.observe(116, released) == 1  # every 10 ticks for `minted`
    assert written == [100, 106, 116]


def test_a_failed_catalog_write_is_retried_at_the_next_read():
    calls = []

    def flaky(catalog, tick):
        calls.append(tick)
        if len(calls) == 1:
            raise ConnectionError("postgres unavailable")
        return 1

    h = Holdings(Reads(), SharedDb(None), reader="t", rules=Guardrails(), catalog=CatalogSync(flaky))
    h.observe_catalog(100, CATALOG)  # logged, not raised
    h.observe_catalog(101, CATALOG)
    assert calls == [100, 101]


# ---------------------------------------------------------------- the agents read through the holdings


class SpyHoldings:
    """Duck-typed `Holdings`: answers from the fake team, records every read, deal and catalog."""

    def __init__(self, team):
        self.team, self.reads, self.deals, self.catalogs = team, [], [], []

    def me(self, clock, clock_read_at=None, live_because=None):
        self.reads.append(clock.tick)
        return MeRead(self.team.me(), "db", clock.tick, 0.4, 3, "abc", "maker", "fresh")

    def after_deal(self, clock, what):
        self.deals.append(what)
        return MeRead(self.team.me(), "live", clock.tick, 0.0, 4, "def", "taker", f"after {what}")

    def observe_catalog(self, tick, catalog):
        self.catalogs.append(tick)


def spied_taker(tmp_path, team, public, live, config=None):
    from bazaar_agent.agents.taker import Taker, TakerConfig
    from tests.agent_fakes import parts

    lines: list[str] = []
    spy = SpyHoldings(team)
    kw = parts(tmp_path)
    t = Taker(team, public, live=live, log=lines.append, now=lambda: 1000.0, sleep=lambda s: None,
              config=config or TakerConfig(max_dealer_threads=0), holdings=spy, **kw)  # fmt: skip
    return t, spy, lines


def test_the_taker_decides_from_the_holdings_and_re_reads_after_a_live_accept(tmp_path):
    from tests.agent_fakes import FakePublic, FakeTeam, ask

    team = FakeTeam()
    t, spy, lines = spied_taker(tmp_path, team, FakePublic(boards={"rastro": [ask(2, "LAV-08", 20, asset=901)]}), True)
    t.on_tick(clock())
    assert team.sent == [("accept", 2)] and spy.reads == [100] and spy.catalogs == [100]
    assert spy.deals == ["accept of offer 2"]
    assert team.reads.count("me") == 2  # the spy's two reads (the tick's, the one after the deal), no other
    assert any("/me from db (tick 100, 0.4 s old, epoch 3, read by maker)" in line for line in lines)
    assert any("accept of offer 2: /me live (after accept of offer 2)" in line for line in lines)


def test_a_dry_run_never_re_reads_after_a_deal(tmp_path):
    from tests.agent_fakes import FakePublic, FakeTeam, ask

    team = FakeTeam()
    t, spy, _ = spied_taker(tmp_path, team, FakePublic(boards={"rastro": [ask(2, "LAV-08", 20, asset=901)]}), False)
    t.on_tick(clock())
    assert team.sent == [] and spy.deals == []


def test_a_dealer_thread_that_ended_in_a_deal_re_reads_the_holdings(tmp_path):
    from bazaar_agent.agents.taker import TakerConfig
    from tests.agent_fakes import FakePublic, FakeTeam

    team = FakeTeam()
    t, spy, _ = spied_taker(tmp_path, team, FakePublic(), True, TakerConfig(max_dealer_threads=3))
    t.on_tick(clock())  # opens thread 5000
    team.thread_payloads[5000] = {"id": 5000, "status": "deal", "messages": [], "standing_offers": []}
    team.now = clock(tick=101)
    t.on_tick(team.now)
    assert "deal in thread 5000" in spy.deals


def test_the_maker_reads_through_the_holdings_and_logs_where_from(tmp_path):
    from bazaar_agent.agents.maker import Maker
    from tests.agent_fakes import FakePublic, FakeTeam, parts

    team, lines = FakeTeam(), []
    spy = SpyHoldings(team)
    Maker(team, FakePublic(), live=False, log=lines.append, holdings=spy, **parts(tmp_path)).on_tick(clock())
    assert spy.reads == [100] and team.reads.count("me") == 1  # the spy's read only
    assert any("maker:" in line and "/me from db (tick 100" in line for line in lines)
