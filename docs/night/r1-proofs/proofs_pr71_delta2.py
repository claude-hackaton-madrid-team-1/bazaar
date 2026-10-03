"""r1 delta-2 proofs for PR #71 @ 1696789 (scratch, not committed). Ticks are 30 s; the vault's clock advances with them."""

from bazaar_agent import venue as vn
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import RASTRO
from tests.test_venue import FakeConn
from tests.test_venue_keeper import Team, keeper, ours, snap, window


class LandedThenTimedOut(Team):
    def open_venue(self, name, fee_bps=300, fee_per_card=0, rules=None, description=""):
        self.opened.append((name, fee_bps, fee_per_card, rules))
        raise BazaarError("network", "POST /api/venues: timed out", 0)


def _vault(tmp_path, store, down, fail_at=None):
    calls = {"n": 0}

    class Conn(FakeConn):
        def execute(self, sql, params=()):
            if sql.startswith("select count(*) from venue_broker_keys where target = %s and venue <>"):
                calls["n"] += 1
                if fail_at is not None and calls["n"] == fail_at:
                    raise RuntimeError("blip")
            return super().execute(sql, params)

    t = [0.0]
    v = vn.KeyVault(tmp_path, lambda: Conn(store, fail=down[0]))
    v.now = lambda: t[0]
    return v, t


def test_d2_adapted_one_blip_no_longer_locks_the_vault_out(tmp_path):
    store, down = {}, [True]
    vault, t = _vault(tmp_path, store, down)
    team = Team()
    k = keeper(tmp_path, team, store=store)
    k.vault = vault
    s = snap()
    k.on_tick(s.clock, s, window())
    assert team.opened == []
    down[0] = False
    for tick in range(401, 460):
        t[0] += 30.0
        s = snap(tick=tick)
        k.on_tick(s.clock, s, window())
    assert len(team.opened) == 1  # FIXED: reopens on the next retry (tick 410)


def test_d3_a_mark_lost_to_a_postgres_blip_is_never_retried_and_the_venue_is_reopened_after_suspension(tmp_path):
    store, down = {}, [False]
    vault, t = _vault(tmp_path, store, down)
    team, lines = LandedThenTimedOut(), []
    k = keeper(tmp_path, team, store=store, lines=lines)
    k.vault = vault
    s = snap()
    k.on_tick(s.clock, s, window())  # the open lands, the answer is lost: claim kept
    assert len(team.opened) == 1 and set(store) == {("", "_claim")}
    down[0] = True  # one blip on the tick the venue first shows in the list
    k.vault._conn = None
    t[0] += 30.0
    s = snap(tick=401, cash=250, venue={"venue": "v09", "status": "open"}, venues=(RASTRO, ours()))
    k.on_tick(s.clock, s, window())
    down[0] = False  # Postgres is back for good
    for tick in range(402, 430):
        t[0] += 30.0
        s = snap(tick=tick, cash=250, venue={"venue": "v09", "status": "open"}, venues=(RASTRO, ours()))
        k.on_tick(s.clock, s, window())
    assert ("", "v09") not in store  # the mark was never retried (_marked was set although mark() failed)
    t[0] += 30.0
    gone = snap(tick=500, cash=520, venue={"venue": "v09", "status": "suspended"}, venues=(RASTRO, ours(status="suspended")))
    k.on_tick(gone.clock, gone, window())
    assert len(team.opened) == 2  # second 270 P opening, automatically


def test_d4_no_answer_on_the_recheck_leaves_our_own_claim_and_delays_the_opening_20_ticks(tmp_path):
    store = {}
    vault, t = _vault(tmp_path, store, [False], fail_at=2)  # Postgres fails exactly on the re-check after the claim
    team = Team()
    k = keeper(tmp_path, team, store=store)
    k.vault = vault
    opened_at = None
    for tick in range(400, 500):
        s = snap(tick=tick)
        k.on_tick(s.clock, s, window())
        t[0] += 30.0
        if team.opened and opened_at is None:
            opened_at = tick
    assert opened_at is not None and opened_at - 400 >= 20, opened_at  # vs 10 if release() had worked


def test_d5_a_failed_postgres_save_after_a_good_open_is_never_retried_and_a_redeploy_loses_the_key(tmp_path):
    store, down = {}, [False]
    fail_save = [True]

    class Conn(FakeConn):
        def execute(self, sql, params=()):
            if fail_save[0] and sql.startswith("insert into venue_broker_keys") and len(params) == 4 and "''" not in sql:
                fail_save[0] = False
                raise RuntimeError("blip on the save")
            return super().execute(sql, params)

    t = [0.0]
    vault = vn.KeyVault(tmp_path / "p1", lambda: Conn(store, fail=down[0]))
    vault.now = lambda: t[0]
    team, lines = Team(), []
    k = keeper(tmp_path / "p1", team, store=store, lines=lines)
    k.vault = vault
    s = snap()
    k.on_tick(s.clock, s, window())
    assert len(team.opened) == 1 and k.opened is not None and "postgres" not in k.opened.saved
    for tick in range(401, 460):
        t[0] += 30.0
        s = snap(tick=tick, cash=250, venue={"venue": "v09", "status": "open"}, venues=(RASTRO, ours()))
        k.on_tick(s.clock, s, window())
    assert set(store) == {("", "_claim")}  # never re-saved to Postgres (only the claim row)
    # Railway redeploy: fresh container (empty data dir), same Postgres -> no key, no broker, no mark either? 
    team2, lines2 = Team(), []
    k2 = keeper(tmp_path / "p2", team2, store=store, lines=lines2)
    s = snap(tick=470, cash=250, venue={"venue": "v09", "status": "open"}, venues=(RASTRO, ours()))
    k2.on_tick(s.clock, s, window())
    assert k2._key("v09") is None and any("NO broker key" in line for line in lines2)
    assert team2.opened == []
