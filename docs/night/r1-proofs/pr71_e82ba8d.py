"""r1 review proofs for PR #71 @ e82ba8d: each test PASSES on that head, which confirms the bug."""

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
        team, store = Team(), {"v09": ("simbk-x", 300)}
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
    assert len(team.opened) == 1 and store == {}
    # next ticks: the venue shows in /me and the public list; no key anywhere -> no broker, ever
    for t in range(401, 450):
        s = snap(tick=t, cash=250, venue={"venue": "v09", "status": "open"}, venues=(RASTRO, ours()))
        k.on_tick(s.clock, s, window())
    assert len(team.opened) == 1 and k.made == []
    assert any("NO broker key" in line for line in lines)
