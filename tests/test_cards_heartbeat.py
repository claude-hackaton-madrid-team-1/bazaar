"""The cards heartbeat: new cards, released sets and minted jumps in the catalog become stored, logged hints that
rank those cards up for a while (ranking only)."""

import json
from copy import deepcopy

from bazaar_agent import cards_heartbeat as hb
from bazaar_agent import strategy
from bazaar_agent.agents.taker import Taker, TakerConfig
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.learn.model import Learning
from tests.agent_fakes import FakePublic, FakeTeam, clock, parts
from tests.test_strategy import CATALOG, DEALERS, EVENTS, ME, PARAMS, RULES

ON = Guardrails(card_release_boost_enabled=True, card_release_boost_ticks=10)
MENUS = [
    {
        "id": "abuela",
        "menu": {
            "sells": [{"pack": "sobre_barrio", "list_price": 26}, {"rarity": "common", "sets": "released"}],
            "buys": [{"rarity": "common", "sets": "released"}],
        },
    },
    {"id": "chato", "menu": {"sells": [{"rarity": "rare", "sets": ["RET"]}], "buys": []}},
]


def cat(*sets):
    return {"sets": list(sets)}


def card(ref, rarity="common", minted=0, hidden=False):
    return {"id": ref, "rarity": rarity, "minted": minted, "print_run": 30, "hidden": hidden}


def lav(*cards, released=True):
    return {"id": "LAV", "released": released, "cards": list(cards)}


def ret(*cards, released=True):
    return {"id": "RET", "released": released, "cards": list(cards)}


def beat(tmp_path, rules=ON):
    lines: list[str] = []
    stored: list[Learning] = []
    return hb.CardsHeartbeat(rules, stored.extend, lines.append, tmp_path / "agents"), lines, stored


def test_the_first_look_is_a_baseline_and_reports_nothing(tmp_path):
    h, lines, stored = beat(tmp_path)
    assert h.observe(1, cat(lav(card("LAV-01"))), MENUS) == []
    h.flush(1)
    assert stored == [] and lines == []
    assert json.loads((tmp_path / "agents" / hb.EVENTS_FILE).read_text())["baseline"]["LAV-01"]["visible"] is True


def test_a_released_set_names_every_card_with_the_dealers_that_sell_and_buy_it(tmp_path):
    h, lines, stored = beat(tmp_path)
    h.observe(1, cat(lav(card("LAV-01")), ret(card("RET-01"), card("RET-09", "rare"), released=False)), MENUS)
    fresh = h.observe(2, cat(lav(card("LAV-01")), ret(card("RET-01"), card("RET-09", "rare"))), MENUS)
    assert [(e.kind, e.card) for e in fresh] == [("set_released", "RET-01"), ("set_released", "RET-09")]
    common, rare = fresh
    assert (common.sold_by, common.bought_by, common.packs) == (("abuela",), ("abuela",), ("abuela:sobre_barrio",))
    assert (rare.sold_by, rare.bought_by) == (("chato",), ())
    h.flush(2)
    assert [lr.kind for lr in stored] == ["card_release", "card_release"]
    assert stored[1].detail["sold_by"] == ["chato"] and stored[1].subject == "RET"
    assert "tick 2 cards: set released: RET-09 (rare, RET) · sold by chato · bought by no dealer" in lines
    hint = json.loads((tmp_path / "agents" / hb.EVENTS_FILE).read_text())
    assert hint["boosted"] == ["RET-01", "RET-09"] and hint["events"][1]["sold_by"] == ["chato"]


def test_an_unhidden_card_is_new_and_a_minted_jump_adds_up_across_ticks(tmp_path):
    h, _, _ = beat(tmp_path)
    h.observe(1, cat(lav(card("LAV-01", minted=10), card("LAV-11", hidden=True))), MENUS)
    assert [(e.kind, e.card) for e in h.observe(2, cat(lav(card("LAV-01", minted=11), card("LAV-11"))), MENUS)] == [
        ("new_card", "LAV-11")
    ]
    assert h.observe(3, cat(lav(card("LAV-01", minted=12), card("LAV-11"))), MENUS) == []  # +2 so far
    (jump,) = h.observe(4, cat(lav(card("LAV-01", minted=13), card("LAV-11"))), MENUS)  # +3 since tick 1
    assert (jump.kind, jump.card, jump.minted) == ("minted_jump", "LAV-01", 13)
    assert h.observe(5, cat(lav(card("LAV-01", minted=14), card("LAV-11"))), MENUS) == []  # counted from 13 now


def test_an_empty_catalog_read_is_never_every_card_vanishing(tmp_path):
    h, _, _ = beat(tmp_path)
    h.observe(1, cat(lav(card("LAV-01"))), MENUS)
    assert h.observe(2, {}, MENUS) == []
    assert h.observe(3, cat(lav(card("LAV-01"))), MENUS) == []


def test_a_restart_sees_what_was_released_while_it_was_down(tmp_path):
    h, _, _ = beat(tmp_path)
    h.observe(1, cat(lav(card("LAV-01"))), MENUS)
    h.flush(1)
    again, lines, stored = beat(tmp_path)
    (ev,) = again.observe(9, cat(lav(card("LAV-01"), card("LAV-02"))), MENUS)
    assert (ev.kind, ev.card) == ("new_card", "LAV-02")


def test_an_unreadable_hint_file_starts_fresh(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / hb.EVENTS_FILE).write_text('{"baseline": {"LAV-01": {"set": "LAV"}}, "events": 5}')
    h, _, _ = beat(tmp_path)
    assert h.baseline == {} and h.events == []
    assert h.observe(1, cat(lav(card("LAV-01"))), MENUS) == []


def test_the_same_event_is_one_learning_row_and_jumps_at_two_ticks_are_two():
    ev = hb.CardEvent("new_card", "LAV-11", "LAV", "rare", 5, 1, 30, ("chato",), (), ())
    assert hb.learning_of(ev).key() == hb.learning_of(ev).key()
    later = hb.CardEvent("new_card", "LAV-11", "LAV", "rare", 7, 2, 30, ("chato",), (), ())
    assert hb.learning_of(later).key() == hb.learning_of(ev).key()  # one release, one row
    jump = hb.CardEvent("minted_jump", "LAV-11", "LAV", "rare", 5, 4, 30, (), (), ())
    assert hb.learning_of(jump).key() != hb.learning_of(hb.CardEvent(**{**jump.__dict__, "tick": 9})).key()


def test_the_boost_lasts_its_ticks_and_is_off_with_the_flag():
    ev = hb.CardEvent("new_card", "LAV-08", "LAV", "uncommon", 10, 1, 30, (), (), ())
    assert hb.boost(ON, [ev], 10) == {"LAV-08": hb.BOOST} and hb.boost(ON, [ev], 19) == {"LAV-08": hb.BOOST}
    assert hb.boost(ON, [ev], 20) == {}
    assert hb.boost(Guardrails(card_release_boost_enabled=False), [ev], 10) == {}


def test_a_store_or_disk_failure_never_raises(tmp_path):
    lines: list[str] = []

    def broken(_):
        raise RuntimeError("db down")

    blocked = tmp_path / "file"
    blocked.write_text("")
    h = hb.CardsHeartbeat(ON, broken, lines.append, blocked)  # out_dir is a file: the write fails
    h.observe(1, cat(lav(card("LAV-01"))), MENUS)
    h.observe(2, cat(lav(card("LAV-01"), card("LAV-02"))), MENUS)
    h.flush(2)
    assert any("learnings not stored (RuntimeError)" in x for x in lines)
    assert any("not written" in x for x in lines)
    assert h.observe(3, {"sets": [{"id": "LAV", "released": True, "cards": [{"id": "X", "minted": "many"}]}]}, []) == []
    assert any("cards: skipped (ValueError)" in x for x in lines)


def test_the_boost_reorders_buys_and_changes_no_move():
    plain = strategy.build_playbook(ME, CATALOG, EVENTS, DEALERS, PARAMS, RULES)
    assert len(plain.buys) >= 2
    last = plain.buys[-1]
    boosted = strategy.build_playbook(ME, CATALOG, EVENTS, DEALERS, PARAMS, RULES, boost={last.ref: 1000.0})
    assert boosted.buys[0] == last  # ranked first, the move itself unchanged (price, limit, score)
    assert set(boosted.buys) == set(plain.buys)


def test_the_taker_diffs_the_catalog_it_already_read_and_stores_after_the_sends(tmp_path):
    released = deepcopy(CATALOG)
    hidden = deepcopy(CATALOG)
    hidden["sets"][0]["cards"][-1]["hidden"] = True
    ref = released["sets"][0]["cards"][-1]["id"]
    public = FakePublic(catalog=hidden)
    lines: list[str] = []
    stored: list[Learning] = []
    cards = hb.CardsHeartbeat(ON, stored.extend, lines.append, tmp_path / "agents")
    kw = parts(tmp_path)
    t = Taker(
        FakeTeam(), public, live=False, log=lines.append, now=lambda: 1000.0, sleep=lambda s: None,
        config=TakerConfig(max_dealer_threads=0), cards=cards, **kw,
    )  # fmt: skip
    t.on_tick(clock(tick=1))
    public._catalog = released
    t.on_tick(clock(tick=2))
    assert [(lr.kind, lr.detail["item"]) for lr in stored] == [("card_release", ref)]
    assert any(line.startswith(f"tick 2 cards: new card: {ref}") for line in lines)
    assert cards.boost(2) == {ref: hb.BOOST}
