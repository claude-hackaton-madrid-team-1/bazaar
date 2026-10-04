"""Proactive listings obey the same incomplete-page policy as guarded sales."""

from copy import deepcopy

from bazaar_agent.agents.maker import _leave_desk_copy, targets_from
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.strategy import build_playbook
from tests.agent_fakes import FakePublic, clock, our_ask
from tests.test_maker import NoAccept, maker, posted
from tests.test_strategy import CATALOG, SPARES, card


def inventory():
    """Tick1943 shape: four complete pages, eight incomplete singles, one reserved spare."""
    complete = ("LAV", "MAL", "SAL", "CHA")
    incomplete = {"LAT": (1, 2, 3, 9), "RET": (1, 4, 7, 8)}
    catalog = {**deepcopy(CATALOG), "sets": []}
    assets = []
    pages = []
    for code in (*complete, *incomplete):
        cards = [
            card(f"{code}-{i:02}", "common" if i <= 5 else "uncommon" if i <= 8 else "rare", 20) for i in range(1, 11)
        ]
        catalog["sets"].append({"id": code, "cards": cards})
        chosen = cards if code in complete else [c for c in cards if int(c["id"][-2:]) in incomplete[code]]
        pages.append({"set": code, "complete": code in complete, "have": len(chosen), "of": 10})
        for c in chosen:
            value = c["book"] * (0.5 if code == "LAT" else 0.7 if code == "RET" else 1.0)
            assets.append(
                {"id": len(assets) + 1, "kind": "card", "ref": c["id"], "rarity": c["rarity"], "your_value": value}
            )
    assets[3]["your_value"] = 4
    assets.append({**assets[3], "id": 674})
    me = {
        "id": "t01",
        "cash": 466,
        "assets": assets,
        "affinity": {s: 0.5 if s == "LAT" else 0.7 if s == "RET" else 1 for s in (*complete, *incomplete)},
        "album": {"pages": pages},
    }
    return me, catalog


RULES = Guardrails(protect_page_sets="LAV,MAL,SAL,CHA,LAT,RET", protect_complete_pages_only=True)
# Inspect the whole eligible inventory, independently of the per-tick/spare listing budget.
PARAMS = SPARES.model_copy(update={"sell_spare_slots": 12})


def test_complete_pages_keep_forty_copies_and_eight_incomplete_singles_can_be_offered():
    me, catalog = inventory()
    assert len(me["assets"]) == 49
    book = build_playbook(me, catalog, [], [], PARAMS, RULES)
    targets = _leave_desk_copy(targets_from(book), me, RULES)
    asks = [t for t in targets if t.side == "ask"]
    assert {t.ref for t in asks} == {
        "LAV-04",
        "LAT-01",
        "LAT-02",
        "LAT-03",
        "LAT-09",
        "RET-01",
        "RET-04",
        "RET-07",
        "RET-08",
    }
    assert len(asks) == 9 and len(me["assets"]) - len(asks) == 40
    assert all(t.price >= t.value for t in asks)
    assert {t.asset_id for t in asks if t.ref == "LAV-04"} == {674}


def test_legacy_policy_or_missing_album_still_keeps_only_copies():
    me, catalog = inventory()
    for state, rules in (
        (me, RULES.model_copy(update={"protect_complete_pages_only": False})),
        ({**me, "album": None}, RULES),
    ):
        book = build_playbook(state, catalog, [], [], PARAMS, rules)
        assert {m.ref for m in book.sells} == {"LAV-04"}
        # The maker's second filter independently preserves the same policy.
        liberal = build_playbook(me, catalog, [], [], PARAMS, RULES)
        asks = _leave_desk_copy(targets_from(liberal), state, rules)
        assert {t.ref for t in asks if t.side == "ask"} == {"LAV-04"}


def test_maker_actually_posts_incomplete_singles_without_reusing_the_reserved_duplicate(tmp_path):
    me, catalog = inventory()
    team = NoAccept(me=me, offers=[our_ask(23324, 674, "LAV-04", 6)])
    m, _ = maker(tmp_path, team, FakePublic(catalog=catalog, dealers=[], events=[]), live=True, **RULES.model_dump())
    m.params = lambda tick: PARAMS
    m.ledger.record("listing", 90, 1.4, 6, "hands-off:23324")
    m.on_tick(clock(tick_seconds=15, next_tick_in=14))
    sales = [s for s in posted(team) if s[1].get("assets")]
    assert sales
    by_id = {a["id"]: a for a in me["assets"]}
    for sale in sales:
        asset = by_id[sale[1]["assets"][0]]
        assert asset["ref"].startswith(("LAT-", "RET-"))
        assert sale[2]["cash"] >= asset["your_value"]
    assert not any(s[0] == "cancel" for s in team.sent)


def test_existing_singleton_offers_are_not_promised_again(tmp_path):
    me, catalog = inventory()
    selected = [a for a in me["assets"] if a["ref"] in {"LAT-01", "LAT-02"}]
    offers = [
        our_ask(24045 + i, a["id"], a["ref"], 10, venue="v15" if i == 0 else "v28") for i, a in enumerate(selected)
    ]
    team = NoAccept(me=me, offers=offers)
    m, _ = maker(tmp_path, team, FakePublic(catalog=catalog, dealers=[], events=[]), live=True, **RULES.model_dump())
    m.params = lambda tick: PARAMS
    for offer in offers:
        m.ledger.record("listing", 90, 1.4, 10, f"hands-off:{offer['id']}")
    m.on_tick(clock(tick_seconds=15, next_tick_in=14))
    promised = {a["id"] for a in selected}
    assert posted(team)
    assert all(not promised.intersection(s[1].get("assets", [])) for s in posted(team))
    assert not any(s[0] == "cancel" for s in team.sent)


def test_fresh_complete_page_guard_rejects_a_previously_eligible_singleton(tmp_path):
    me, catalog = inventory()
    # No spare: every remaining proposed sale is an incomplete-page singleton.
    me["assets"] = [a for a in me["assets"] if a["id"] != 674]

    class Completed(NoAccept):
        def me(self):
            state = super().me()
            if self.reads.count("me") > 1:
                for page in state["album"]["pages"]:
                    page["complete"] = True
            return state

    team = Completed(me=me)
    m, lines = maker(
        tmp_path, team, FakePublic(catalog=catalog, dealers=[], events=[]), live=True, **RULES.model_dump()
    )
    m.params = lambda tick: PARAMS
    m.on_tick(clock(tick_seconds=15, next_tick_in=14))
    assert posted(team) == []
    assert any("protect_page_sets" in line for line in lines)
