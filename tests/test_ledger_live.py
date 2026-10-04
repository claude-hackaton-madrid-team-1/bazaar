"""#156: every real-game live writer counts on ONE shared ledger, offline. Two processes share one accept per
tick; a down Postgres means no game write at all (never a per-process file), /health says so, and the
writer recovers by itself once Postgres is back, without a restart and without repeating a write."""

import pytest

from bazaar_agent.agents.maker import Maker
from bazaar_agent.agents.status import StatusHub
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.config import DEFAULT_URL
from bazaar_agent.ledger_pg import LedgerUnavailable, PgLedger, ledger_health, open_ledger
from tests.agent_fakes import TICK, FakePublic, FakeTeam, ask, clock, parts
from tests.pg_fakes import FakePostgres

SHARED = "postgresql://bazaar:Never-Printed-pw@postgres.railway.internal:5432/railway"  # a name: shared


def refused():
    raise ConnectionError("Postgres unreachable (fake)")


def live_ledger(data_dir, connect, source, now=lambda: 0.0, lines=None):
    """What a live process on the real game opens: `open_ledger(live=True)`, in its own data dir."""
    ledger = open_ledger(
        data_dir, source=source, live=True, database_url=SHARED, game_url=DEFAULT_URL, connect=connect,
        log=(lines if lines is not None else []).append,
    )  # fmt: skip
    if isinstance(ledger, PgLedger):
        ledger._pg._now = now  # the reconnect throttle on a fake clock
    return ledger


def reserve(ledger):
    try:
        return ledger.reserve_accept(999999, 1.0, 0, "repro", 1)
    except LedgerUnavailable:
        return "refused"


def test_two_live_processes_on_the_shared_ledger_take_one_accept_per_tick(tmp_path):
    pg = FakePostgres(tmp_path / "shared.db")
    first = live_ledger(tmp_path / "proc-a", pg.connect, "taker")
    second = live_ledger(tmp_path / "proc-b", pg.connect, "maker")
    assert [reserve(first), reserve(second)] == [True, False]
    assert [r[0] for r in pg.rows()] == ["accept"]


def test_a_live_process_with_postgres_down_never_counts_on_its_own_file(tmp_path):
    lines: list[str] = []
    first = live_ledger(tmp_path / "proc-a", refused, "taker", lines=lines)
    second = live_ledger(tmp_path / "proc-b", refused, "maker")
    assert [reserve(first), reserve(second)] == ["refused", "refused"]  # was [True, True] on two files
    assert isinstance(first, PgLedger) and isinstance(second, PgLedger)
    assert not (tmp_path / "proc-a" / "ledger.jsonl").exists() and not (tmp_path / "proc-b" / "ledger.jsonl").exists()
    assert "no game write until it answers (fail closed)" in " ".join(lines)
    assert "Never-Printed-pw" not in " ".join(lines)


def test_the_ledger_reconnects_by_itself_and_reads_what_was_written_before(tmp_path):
    pg, now, lines = FakePostgres(tmp_path / "shared.db"), [0.0], []
    ledger = live_ledger(tmp_path, pg.connect, "taker", now=lambda: now[0], lines=lines)
    assert ledger.reserve_accept(10, 1.0, 5, "LAV-01", 1)
    pg.up = False
    with pytest.raises(LedgerUnavailable):  # the open connection breaks
        ledger.accepts_in_tick(10)
    with pytest.raises(LedgerUnavailable):  # one reopen, refused
        ledger.accepts_in_tick(10)
    opens = pg.opens
    pg.up = True
    with pytest.raises(LedgerUnavailable):  # inside the retry window: no network at all
        ledger.accepts_in_tick(10)
    assert pg.opens == opens and ledger_health(ledger) == "down"
    now[0] += 16.0
    assert ledger.accept_items(10) == ["LAV-01"] and ledger_health(ledger) == "shared"
    assert not ledger.reserve_accept(10, 1.0, 6, "LAV-02", 1)  # the slot taken before the outage holds
    assert any("reconnected" in line for line in lines)


def taker_on(tmp_path, team, public, ledger, hub=None):
    kw = {**parts(tmp_path), "ledger": ledger}
    taker = Taker(
        team, public, live=True, log=[].append, now=lambda: 1000.0, sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0), hub=hub, **kw,
    )  # fmt: skip
    return taker


def test_a_live_taker_sends_nothing_while_postgres_is_down_then_resumes_without_repeating(tmp_path):
    pg, now = FakePostgres(tmp_path / "shared.db"), [0.0]
    ledger = live_ledger(tmp_path / "taker", pg.connect, "taker", now=lambda: now[0])
    team, public = FakeTeam(), FakePublic(boards={"rastro": [ask(1, "LAV-02", 10)]})
    hub = StatusHub("taker", True, target={"mode": "real"}, ledger=lambda: ledger_health(ledger))
    taker = taker_on(tmp_path / "taker", team, public, ledger, hub)
    pg.up = False
    team.now = clock(tick=TICK)
    taker.on_tick(team.now)
    assert team.sent == [] and pg.rows() == []  # an ask worth taking, and nothing went out
    assert hub.health()["ledger"] == "down"
    pg.up = True
    now[0] += 16.0  # past the retry window: the next tick reconnects by itself
    team.now = clock(tick=TICK + 1)
    taker.on_tick(team.now)
    assert team.sent == [("accept", 1)] and hub.health()["ledger"] == "shared"
    taker.on_tick(team.now)  # the same tick again (a re-read clock): the accept is never repeated
    assert team.sent == [("accept", 1)]
    assert [(kind, tick) for kind, tick, *_ in pg.rows()] == [
        (f"operator_say:team_desk:{ledger.world}", TICK + 1),
        ("accept", TICK + 1),
        ("publication_pending", TICK + 1),
        ("spend", TICK + 1),
    ]


def test_a_live_maker_sends_nothing_while_postgres_is_down_then_posts_once_it_is_back(tmp_path):
    pg, now = FakePostgres(tmp_path / "shared.db"), [0.0]
    ledger = live_ledger(tmp_path / "maker", pg.connect, "maker", now=lambda: now[0])
    team = FakeTeam()
    kw = {**parts(tmp_path / "maker"), "ledger": ledger}
    maker = Maker(team, FakePublic(), live=True, log=[].append, now=lambda: 1000.0, **kw)
    pg.up = False
    maker.on_tick(clock())
    assert team.sent == [] and pg.rows() == []
    pg.up, now[0] = True, now[0] + 16.0
    maker.on_tick(clock(tick=TICK + 1))
    assert ("list_offer", {"cash": 65}, {"cards": ["LAV-09"]}, "rastro") in team.sent  # the bid it held back
    assert ledger.spent_since(0) == 65


class SpyMarket:
    """Our venue keeper as the maker sees it: records every tick it is handed."""

    def __init__(self):
        self.ticks: list[int] = []

    def on_tick(self, clock, snap, window):
        self.ticks.append(clock.tick)


def test_a_live_maker_with_our_venue_sends_nothing_at_all_while_postgres_is_down(tmp_path):
    """#71 + #162: the venue keeper runs inside the maker tick, so a down ledger stops it too (no list, cancel,
    reprice or venue call); once Postgres answers, the venue runs first and the held bid goes out."""
    pg, now = FakePostgres(tmp_path / "shared.db"), [0.0]
    ledger = live_ledger(tmp_path / "maker", pg.connect, "maker", now=lambda: now[0])
    team, market = FakeTeam(), SpyMarket()
    kw = {**parts(tmp_path / "maker"), "ledger": ledger}
    maker = Maker(team, FakePublic(), live=True, log=[].append, now=lambda: 1000.0, market=market, **kw)
    pg.up = False
    maker.on_tick(clock())
    assert team.sent == [] and market.ticks == [] and pg.rows() == []
    pg.up, now[0] = True, now[0] + 16.0
    maker.on_tick(clock(tick=TICK + 1))
    assert market.ticks == [TICK + 1]
    assert ("list_offer", {"cash": 65}, {"cards": ["LAV-09"]}, "rastro") in team.sent


def test_a_password_with_a_raw_at_sign_is_never_logged_and_never_counts_as_shared(tmp_path):
    from bazaar_agent.ledger_pg import LedgerNotShared

    lines: list[str] = []
    url = "postgresql://bazaar:se@cretpw@db.example.com:5432/railway"  # libpq reads "cretpw@db.example.com"
    with pytest.raises(LedgerNotShared) as refused_live:
        open_ledger(tmp_path, source="taker", live=True, database_url=url, game_url=DEFAULT_URL, connect=refused)
    open_ledger(tmp_path, source="taker", database_url=url, game_url=DEFAULT_URL, connect=refused, log=lines.append)
    assert "cretpw" not in str(refused_live.value) and "cretpw" not in " ".join(lines)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:SEC/RETPW@x.proxy.rlwy.net:12345/railway",  # libpq reads user + half the password as host
        "postgresql://u:pw@localhost,shared.example.com:5432/railway",  # a host list that tries this machine first
        "postgresql://u:pw@shared.example.com:5432/railway?hostaddr=127.0.0.1",
        "postgresql://u:pw@127.1:5432/railway",
        "postgresql://u:pw@2130706433:5432/railway",
        "postgresql://u:pw@0x7f000001:5432/railway",
        "postgresql://u:pw@my-laptop.local:5432/railway",
        "postgresql://u:pw@postgres:5432/railway",  # a compose service name
        "postgresql://u:pw@0x7f.1:5432/railway",
        "postgresql://u:pw@0177.1:5432/railway",
        "postgresql://u:ab@cd.FRAG/ef@x.proxy.rlwy.net/railway",  # "cd.FRAG" read as the host
        "postgresql://u:ab@CDFRAG:9999/x@x.proxy.rlwy.net/railway",
    ],
)
def test_a_url_that_may_reach_this_machine_or_leak_its_password_is_never_shared(tmp_path, url):
    from bazaar_agent.ledger_pg import LedgerNotShared

    with pytest.raises(LedgerNotShared) as refused_live:
        open_ledger(tmp_path, source="taker", live=True, database_url=url, game_url=DEFAULT_URL, connect=refused)
    assert not any(part in str(refused_live.value) for part in ("RETPW", "SEC", "FRAG"))


def test_railway_hosts_are_shared(tmp_path):
    for url in (SHARED, "postgresql://u:pw@shuttle.proxy.rlwy.net:41234/railway"):
        ledger = open_ledger(
            tmp_path, source="taker", live=True, database_url=url, game_url=DEFAULT_URL, connect=refused
        )
        assert isinstance(ledger, PgLedger) and "(shared, counted across every machine)" in ledger.where
