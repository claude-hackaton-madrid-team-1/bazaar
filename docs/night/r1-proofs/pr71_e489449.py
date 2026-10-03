"""r1 review proofs for PR #71 @ e489449 (delta): proof B now FAILS (bug fixed); C, D1, D1b, D2 PASS (bugs confirmed)."""

from bazaar_agent.guardrails import Action, Context, Guardrails, check, effective_cash_floor, runs_venue
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import RASTRO
from tests.test_venue_keeper import Team, keeper, ours, snap, window

RULES = Guardrails(allow_venue_open=True, cash_floor=100, venue_bond_reserve=270)


def ctx(me):
    return Context(cash=int(me["cash"]), held={}, tick=1, t_hours=4.0, has_venue=runs_venue(me))


def test_a_starter_stall_shown_by_me_without_a_starter_flag_drops_the_floor_to_100_before_we_open():
    # /api/me `venue` for a starter stall, in the shape our own simulator uses for /me ({venue, name, status}),
    # or as a bare id: neither carries `starter: true`, so every writer's floor is 100, not 370.
    for venue in ({"venue": "s01", "name": "Team 1 stall", "status": "open"}, "s01"):
        me = {"cash": 350, "venue": venue}
        assert effective_cash_floor(RULES, ctx(me)) == 100
        assert check(Action("buy", "LAV-03", "uncommon", 20), ctx(me), RULES).allowed  # 350 - 20 = 330 < 370


def test_after_a_restart_a_closed_or_suspended_venue_is_opened_again(tmp_path):
    # Process 1 opened v09 earlier; it was suspended (bond cut) or closed. A redeploy starts a fresh keeper:
    # nothing in the vault/public list stops a second 270 P opening.
    for status in ("closed", "suspended"):
        team, store = Team(), {("", "v09"): ("simbk-x", 300)}
        k = keeper(tmp_path / status, team, store=store)
        s = snap(cash=520, venues=(RASTRO, ours(status=status)), venue={"venue": "v09", "status": status})
        k.on_tick(s.clock, s, window())
        assert len(team.opened) == 1


class LandedThenTimedOut(Team):
    """The POST lands on the server, the answer (with the one-time broker key) is lost to a timeout."""

    def open_venue(self, name, fee_bps=300, fee_per_card=0, rules=None, description=""):
        self.opened.append((name, fee_bps, fee_per_card, rules))
        raise BazaarError("network", "POST /api/venues: timed out", 0)


def test_a_timeout_after_the_open_landed_loses_the_key_and_the_broker_never_runs(tmp_path):
    team, store, lines = LandedThenTimedOut(), {}, []
    k = keeper(tmp_path, team, store=store, lines=lines)
    first = snap()
    k.on_tick(first.clock, first, window())
    assert len(team.opened) == 1 and all(v == "_claim" for _, v in store)
    # next ticks: the venue shows in /me and the public list; no key anywhere -> no broker, ever
    for t in range(401, 450):
        s = snap(tick=t, cash=250, venue={"venue": "v09", "status": "open"}, venues=(RASTRO, ours()))
        k.on_tick(s.clock, s, window())
    assert len(team.opened) == 1 and k.made == []
    assert any("NO broker key" in line for line in lines)


# ---------------------------------------------------------------- delta review @ e489449


def test_d1_a_landed_but_timed_out_opening_is_reopened_automatically_once_the_venue_is_suspended(tmp_path):
    # The open POST lands, the answer is lost (network): no venue_keys row, only the claim, held_claim = True.
    # The venue shows in the lists (no broker: no key). Later it is suspended (bond cut) or closed by hand:
    # the same process releases the claim, opened_before() is False (no venue row) and it opens a SECOND 270 P venue.
    team, store, lines = LandedThenTimedOut(), {}, []
    k = keeper(tmp_path, team, store=store, lines=lines)
    first = snap()
    k.on_tick(first.clock, first, window())
    for t in range(401, 430):
        s = snap(tick=t, cash=250, venue={"venue": "v09", "status": "open"}, venues=(RASTRO, ours()))
        k.on_tick(s.clock, s, window())
    assert len(team.opened) == 1
    gone = snap(tick=500, cash=520, venue={"venue": "v09", "status": "suspended"}, venues=(RASTRO, ours(status="suspended")))
    k.on_tick(gone.clock, gone, window())
    assert len(team.opened) == 2  # the docs promise "a venue closed or suspended since is reopened only by hand"


def test_d1b_same_after_a_restart_once_the_claim_is_stale(tmp_path):
    team, store = LandedThenTimedOut(), {}
    k = keeper(tmp_path, team, store=store)
    k.on_tick(snap().clock, snap(), window())
    assert set(store) == {("", "_claim")}
    team2 = Team()
    k2 = keeper(tmp_path / "p2", team2, store=store)  # redeploy, same Postgres
    gone = snap(tick=430, cash=520, venue=None, venues=(RASTRO, ours(status="closed")))
    k2.on_tick(gone.clock, gone, window())
    assert len(team2.opened) == 1


def test_d2_one_postgres_blip_locks_the_vault_out_of_postgres_for_the_life_of_the_process(tmp_path):
    # _db() raises ConfigError while _skip > 0; every caller catches Exception and calls _drop(), which sets
    # _skip back to DB_RETRY_CALLS: the countdown never reaches 0, so the vault never reconnects.
    from bazaar_agent import venue as vn
    from tests.test_venue import FakeConn

    down, store = [True], {}
    vault = vn.KeyVault(tmp_path, lambda: FakeConn(store, fail=down[0]))
    team = Team()
    k = keeper(tmp_path, team, store=store)
    k.vault = vault
    s = snap()
    k.on_tick(s.clock, s, window())  # h6.5, Postgres blips: nothing sent (fail closed, fine)
    down[0] = False  # Postgres is back for good
    for t in range(410, 1400, 10):  # ~100 retries, every RETRY_TICKS
        s = snap(tick=t)
        k.on_tick(s.clock, s, window())
    assert team.opened == []  # never opens again until a restart
    assert vault.ready(durable=True) is not None
