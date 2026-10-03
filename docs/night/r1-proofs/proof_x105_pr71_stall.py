"""r1 proof (#105 x #71): on main+#71, every agent's /me goes through holdings.Holdings, which strips
`starter_broker_key` (without_secrets) even with holdings_from_db = false. #71's runs_venue() relies on that
key to tell the free starter stall from our own venue -> the 270 P bond reserve vanishes before we open."""

from bazaar_agent.guardrails import Context, context_from, effective_cash_floor, load_guardrails, runs_venue
from bazaar_agent.holdings import Holdings, SharedDb


class _Ledger:
    def spent_since(self, t):  # noqa: ANN001
        return 0

    def accepts_in_tick(self, tick):  # noqa: ANN001
        return 0

    def packs_since(self, t):  # noqa: ANN001
        return {}


def _raw_me(venue):
    return {"id": "t01", "name": "t01", "cash": 400, "assets": [], "venue": venue,
            "starter_broker_key": "bk_" + "x" * 20, "tick": 400}


def _floor(me):
    rules = load_guardrails().rules
    ctx: Context = context_from(me, 400, 6.0, _Ledger(), rules)
    return rules, effective_cash_floor(rules, ctx)


def test_shipped_rules_keep_a_reserve_before_the_opening():
    rules = load_guardrails().rules
    assert rules.allow_venue_open and rules.venue_bond_reserve == 270 and rules.cash_floor == 100
    assert not rules.holdings_from_db


def test_raw_me_keeps_the_reserve_but_the_holdings_me_drops_it():
    for venue in ("v-stall", {"venue": "v-stall", "name": "t01 stall", "status": "open"}):
        raw = _raw_me(venue)
        assert not runs_venue(raw)
        assert _floor(raw)[1] == 370  # without Holdings (#71 alone): stall + key -> reserve kept
        rules = load_guardrails().rules
        read = Holdings(lambda: dict(raw), SharedDb(None), reader="maker", rules=rules, team="t01").me(None)
        assert "starter_broker_key" not in read.me
        assert runs_venue(read.me)  # the stall now counts as our venue
        assert _floor(read.me)[1] == 100  # BUG: 270 P bond reserve gone before h6.5


def test_keeper_takes_an_unlisted_stall_for_our_venue_and_never_opens():
    from types import SimpleNamespace

    from bazaar_agent.agents.venue_keeper import our_venue

    raw = _raw_me("v-stall")
    read = Holdings(lambda: dict(raw), SharedDb(None), reader="maker", rules=load_guardrails().rules, team="t01").me(None)
    assert our_venue(SimpleNamespace(venues=[], us="t01", me=raw)) is None  # #71 alone: stall, we may open
    assert our_venue(SimpleNamespace(venues=[], us="t01", me=read.me)) is not None  # main+#71: "ours" -> no h6.5 open
