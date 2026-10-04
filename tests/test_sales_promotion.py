"""Public-offer introductions: text only, fresh evidence, bounded shared quotas."""

from dataclasses import replace

import pytest

from bazaar_agent.agents.market import venues_from
from bazaar_agent.agents.sales_promotion import SalesPromotion, public_ask
from bazaar_agent.agents.seller import committed_context, open_commitments
from bazaar_agent.sdk import BazaarError
from tests.agent_fakes import RASTRO, FakePublic, ask, our_ask
from tests.test_sales_outreach import setup


def promotion(tmp_path):
    outreach, team, ledger, v, matrix = setup(tmp_path)
    own = {"venue": "v19", "owner": "t01", "status": "open", "fee_bps": 0, "fee_per_card": 0, "rules": {}}
    partner = {**own, "venue": "v15", "owner": "t15"}
    v = replace(
        v,
        venues=(*v.venues, *venues_from({"venues": [own, partner]})),
        params=v.params.model_copy(update={"preferred_sell_venue_owners": "t15"}),
    )
    # Every copy is already promised: this must still make a useful introduction.
    team.offers = [our_ask(50, 3, "LAT-03", 8), our_ask(51, 4, "LAT-03", 8)]
    v = replace(v, offers=team.offers)
    public = FakePublic(
        venues=[RASTRO, own, partner], boards={"v19": [ask(25018, "LAT-03", 7, venue="v19", maker="t04")]}
    )
    actor = SalesPromotion(team, public, outreach.rules, ledger, outreach.rec, True)
    words = []
    original = team.say

    def say(tid, text="", **kwargs):
        words.append((tid, text, kwargs))
        return original(tid, text, **kwargs)

    team.say = say
    return actor, team, public, ledger, v, matrix, words


def test_promotes_other_teams_public_ask_while_all_inventory_is_listed(tmp_path):
    actor, team, _, ledger, v, matrix, words = promotion(tmp_path)
    saved = []
    actor.sent_words = lambda *args: saved.append(args)
    assert actor.on_tick(v, matrix)
    assert team.sent[0] == ("open_thread", "t05", {"trade": "cards"}, "rastro")
    assert words[0][2] == {}  # no structured price, offer, assets or acceptance
    assert "#25018" in words[0][1] and "LAT-03" in words[0][1] and "7 P en v19" in words[0][1]
    assert "¿Encaja con lo que buscas?" in words[0][1]
    assert "vigencia y las comisiones" in words[0][1] and "coste total" in words[0][1]
    assert "no reserva cartas" in words[0][1]
    assert saved[0][-1] == {} and saved[0][4] == 1
    assert not ledger.publication_rows() and ledger.spent_since(0) == 0
    assert ledger.count_in_tick("listing", v.tick) == 0
    assert not ledger.accept_items(v.tick)


@pytest.mark.parametrize(
    "change", ["expired", "private", "thread", "closed", "swap", "own", "stale_matrix", "slots", "pause"]
)
def test_unavailable_or_private_quotes_never_get_promoted(tmp_path, change):
    actor, team, public, _, v, matrix, words = promotion(tmp_path)
    raw = public.boards["v19"][0]
    if change == "expired":
        raw["expires_tick"] = v.tick - 1
    if change == "private":
        raw["to"] = "t05"
    if change == "thread":
        raw["thread"] = 42
    if change == "closed":
        raw["status"] = "cancelled"
    if change == "swap":
        raw["want"]["types"] = ["card:LAV-02"]
    if change == "own":
        raw["maker"] = v.us
    if change == "stale_matrix":
        matrix = replace(matrix, tick=v.tick - 31)
    if change == "slots":
        team.threads = [{"kind": "team", "with": "t02"}, {"kind": "team", "with": "t03"}]
    if change == "pause":
        (tmp_path / "PAUSE").touch()
    assert not actor.on_tick(v, matrix)
    assert not team.sent and not words


def test_partner_ask_requires_exact_fresh_public_quote(tmp_path):
    actor, team, public, _, v, matrix, words = promotion(tmp_path)
    public.boards["v19"] = []
    ours = our_ask(55, 4, "LAT-03", 8, venue="v15")
    ctx = committed_context(v.ctx(None), open_commitments([ours], v.us))
    v = replace(v, offers=[ours], ctx=lambda _: ctx)
    public.boards["v15"] = [{**ours, "maker": "anonymous"}]
    assert actor.on_tick(v, matrix)
    assert "#55" in words[0][1] and "v15" in words[0][1]


@pytest.mark.parametrize("change", ["price", "copy", "private", "missing"])
def test_partner_quote_changed_before_message_is_not_promoted(tmp_path, change):
    actor, team, public, _, v, matrix, _ = promotion(tmp_path)
    public.boards["v19"] = []
    ours = our_ask(55, 4, "LAT-03", 8, venue="v15")
    v = replace(v, offers=[ours])
    changed = our_ask(55, 4, "LAT-03", 9 if change == "price" else 8, venue="v15")
    if change == "copy":
        changed["give"]["assets"][0]["id"] = 3
    if change == "private":
        changed["to"] = "t05"
    public.boards["v15"] = [] if change == "missing" else [changed]
    assert not actor.on_tick(v, matrix)
    assert not team.sent


def test_hour_cooldown_and_unknown_send_survive_restart(tmp_path):
    actor, team, public, ledger, v, matrix, words = promotion(tmp_path)

    def lost(*args, **kwargs):
        raise BazaarError("network", "lost", 0)

    team.say = lost
    actor.on_tick(v, matrix)
    second = SalesPromotion(team, public, actor.rules, ledger, actor.rec, True)
    assert not second.on_tick(replace(v, tick=v.tick + 1), matrix)
    assert len([s for s in team.sent if s[0] == "open_thread"]) == 1
    assert not words


def test_unknown_open_does_not_retry_for_a_different_offer(tmp_path):
    actor, team, public, ledger, v, matrix, _ = promotion(tmp_path)
    attempts = []

    def lost(*args, **kwargs):
        attempts.append(args)
        raise BazaarError("network", "lost", 0)

    team.open_thread = lost
    actor.on_tick(v, matrix)
    public.boards["v19"][0]["id"] += 1
    second = SalesPromotion(team, public, actor.rules, ledger, actor.rec, True)
    assert not second.on_tick(replace(v, tick=v.tick + 1), matrix)
    assert len(attempts) == 1


def test_pause_after_open_blocks_text(tmp_path):
    actor, team, _, _, v, matrix, words = promotion(tmp_path)
    original = team.open_thread

    def paused(*args, **kwargs):
        body = original(*args, **kwargs)
        (tmp_path / "PAUSE").touch()
        return body

    team.open_thread = paused
    actor.on_tick(v, matrix)
    assert not words


def test_parser_rejects_boolean_cash():
    raw = ask(1, "LAT-03", True, venue="v19")
    assert public_ask(raw, "v19", 100) is None


def test_sales_tick_promotes_even_when_cash_sale_has_no_free_copy(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bazaar_agent.agents.sales import Sales
    from tests.agent_fakes import clock, parts

    actor, team, public, _, v, matrix, words = promotion(tmp_path)
    kw = parts(tmp_path, team_threads_enabled=True, team_threads_max_open=2)
    latest = SimpleNamespace(refresh=lambda _: None, current=lambda _: matrix)
    sales = Sales(
        team,
        public,
        live=True,
        log=lambda _: None,
        now=lambda: 1000.0,
        sleep=lambda _: None,
        latest_matrix=latest,
        **kw,
    )
    monkeypatch.setattr(sales.team_desk, "_trades", lambda _: ())
    sales.on_tick(clock())
    assert len(words) == 1 and "#25018" in words[0][1]
    assert all(s[0] in {"open_thread", "say"} for s in team.sent)
    assert not kw["ledger"].publication_rows()


def test_no_acknowledgement_never_persists_fabricated_words(tmp_path):
    actor, team, _, _, v, matrix, _ = promotion(tmp_path)
    team.say = lambda *args, **kwargs: {"id": 777}  # not the SDK's acknowledged message field
    saved = []
    actor.sent_words = lambda *args: saved.append(args)
    actor.on_tick(v, matrix)
    assert not saved


def test_quote_expiring_on_current_tick_is_not_promoted():
    raw = ask(1, "LAT-03", 7, venue="v19")
    raw["expires_tick"] = 100
    assert public_ask(raw, "v19", 100) is None


@pytest.mark.parametrize("blocked", ["other_promise", "floor", "missing_copy"])
def test_promoting_existing_ask_preserves_other_promises_and_sale_guards(tmp_path, blocked):
    actor, team, public, ledger, v, matrix, words = promotion(tmp_path)
    public.boards["v19"] = []
    ours = our_ask(55, 4, "LAT-03", 8, venue="v15")
    offers = [ours]
    if blocked == "other_promise":
        offers.append(our_ask(56, 3, "LAT-03", 8, venue="v15"))
    if blocked == "floor":
        actor.rules = actor.rules.model_copy(update={"sell_min_value_ratio": 100})
    if blocked == "missing_copy":
        v = replace(v, me={**v.me, "assets": [a for a in v.me["assets"] if a["id"] != 4]})
    ctx = committed_context(v.ctx(None), open_commitments(offers, v.us))
    v = replace(v, offers=offers, ctx=lambda _: ctx)
    public.boards["v15"] = [ours]
    assert not actor.on_tick(v, matrix)
    assert not team.sent and not words and not ledger.publication_rows()


def test_sales_prioritizes_real_offer_over_public_introduction(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bazaar_agent.agents.sales import Sales
    from tests.agent_fakes import clock, parts

    _, team, public, _, _, matrix, words = promotion(tmp_path)
    team.offers = []
    kw = parts(tmp_path, team_threads_enabled=True, team_threads_max_open=2)
    sales = Sales(
        team,
        public,
        live=True,
        log=lambda _: None,
        now=lambda: 1000.0,
        sleep=lambda _: None,
        latest_matrix=SimpleNamespace(refresh=lambda _: None, current=lambda _: matrix),
        **kw,
    )
    monkeypatch.setattr(sales.team_desk, "_trades", lambda _: ())
    sales.on_tick(clock())
    assert len(words) == 1
    assert words[0][2]["offer"]["give"]["assets"]
    assert "#25018" not in words[0][1]
    assert kw["ledger"].publication_rows()


def test_unknown_structured_open_also_blocks_promotion_after_restart(tmp_path):
    actor, team, _, ledger, v, matrix, words = promotion(tmp_path)
    ledger.record(f"operator_say:sales_open:{ledger.world}:{v.us}:t05", v.tick - 1, v.t_hours)
    assert not actor.on_tick(v, matrix)
    assert not team.sent and not words


def test_known_feed_maker_is_not_invited_to_own_pseudonymous_ask(tmp_path):
    from tests.test_rivals import ask as feed_ask

    actor, team, public, _, v, matrix, words = promotion(tmp_path)
    public.boards["v19"][0]["maker"] = "merchant_a"
    event = feed_ask(25018, 99, "t05", "LAT-03", 7, 99)
    v = replace(v, events=[event])
    assert not actor.on_tick(v, matrix)
    assert not team.sent and not words
