"""The persona model on the sell side: a dealer's published menu filters our sell candidates (never a common to
Pilar), its favourite sets and an official fever rank them, and without personas nothing changes."""

from copy import deepcopy

from bazaar_agent.agents import dealer_sell_data as dd
from bazaar_agent.agents import dealer_sell_desk as desk
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.news import MarketEvent
from bazaar_agent.persona_model import parse_personas
from tests.test_strategy import CATALOG, ME, PARAMS, card

PILAR_TRAITS = {
    "patience": 0.6,
    "generosity": 0.5,
    "shrewdness": 0.75,
    "memory": 0.7,
    "strictness": 0.6,
    "chattiness": 0.55,
}
PILAR = {
    "id": "pilar",
    "kind": "dealer",
    "name": "Doña Pilar",
    "level": 3,
    "status": "active",
    "traits": PILAR_TRAITS,
    "menu": {
        "buys": [
            {"rarity": "uncommon", "sets": ["SAL", "RET"]},
            {"rarity": "rare", "sets": ["SAL", "RET"]},
            {"rarity": "epic", "sets": ["SAL", "RET"]},
            {"rarity": "uncommon", "sets": "released"},
            {"rarity": "rare", "sets": "released"},
            {"rarity": "epic", "sets": "released"},
        ],
        "deals_per_team_per_hour": 6,
    },
    "unlock": {"always": False},
}
# A data row that (wrongly, or from an older menu) says Pilar buys commons: the persona must still refuse them.
PILAR_ROW = {**PILAR, "menu": {**PILAR["menu"], "buys": [*PILAR["menu"]["buys"], {"rarity": "common"}]}}

CAT = deepcopy(CATALOG)
CAT["sets"].append({"id": "SAL", "cards": [card("SAL-01", "common", 12), card("SAL-07", "uncommon", 10)]})

# Three equal copies of LAV-08 and of SAL-07 (uncommons), and three LAT-03 (commons): spares for every dealer.
ME_SPARES = deepcopy(ME)
ME_SPARES["unlocked"] = ["abuela", "pilar"]
ME_SPARES["affinity"] = {**ME["affinity"], "SAL": 1.6}
ME_SPARES["album"]["pages"].append({"set": "SAL", "have": 1, "of": 6})
ME_SPARES["assets"] += [
    {"id": 10, "kind": "card", "ref": "LAV-08", "rarity": "uncommon", "your_value": 2.0},
    {"id": 11, "kind": "card", "ref": "LAV-08", "rarity": "uncommon", "your_value": 2.0},
    {"id": 12, "kind": "card", "ref": "LAV-08", "rarity": "uncommon", "your_value": 2.0},
    {"id": 20, "kind": "card", "ref": "SAL-07", "rarity": "uncommon", "your_value": 2.0},
    {"id": 21, "kind": "card", "ref": "SAL-07", "rarity": "uncommon", "your_value": 2.0},
    {"id": 22, "kind": "card", "ref": "SAL-07", "rarity": "uncommon", "your_value": 2.0},
    {"id": 30, "kind": "card", "ref": "LAT-03", "rarity": "common", "your_value": 1.2},
]

MARKET = dd.SellMarket(
    traders=tuple(t for t in (dd.trader_from(PILAR_ROW),) if t is not None),
    fills={("pilar", "uncommon"): dd.Fill(15, 20.0, 30, 3), ("pilar", "common"): dd.Fill(8, 9.0, 12, 2)},
    source="test",
)
PERSONAS = parse_personas([PILAR])


def found(**kw):
    return desk.candidates(ME_SPARES, CAT, MARKET, PARAMS, Guardrails(), **kw)


def fever_event(set_code, pct, persona="pilar", official=True):
    return MarketEvent("price_move", set_code, pct, 0.0, None, None, persona, official, "schedule:0:p", "fever")


def test_the_fixture_offers_pilar_a_common_without_personas():
    assert any(c.rarity == "common" and c.dealer == "pilar" for c in found())


def test_pilar_never_gets_a_common_candidate():
    with_personas = found(personas=PERSONAS)
    assert with_personas
    assert all(c.rarity != "common" for c in with_personas if c.dealer == "pilar")


def test_a_sal_uncommon_ranks_above_an_equal_lav_uncommon_for_pilar():
    plain = [c.ref for c in found() if c.rarity == "uncommon"]
    assert plain[:2] == ["LAV-08", "SAL-07"]  # equal gain: today the lower asset id wins
    ranked = found(personas=PERSONAS)
    assert [c.ref for c in ranked][:2] == ["SAL-07", "LAV-08"]
    sal = next(c for c in ranked if c.ref == "SAL-07")
    plain_sal = next(c for c in found() if c.ref == "SAL-07")
    assert (sal.floor, sal.expected, sal.value) == (plain_sal.floor, plain_sal.expected, plain_sal.value)


def test_fever_raises_the_rank_of_the_fever_set():
    fever = {"pilar": {"LAV": 20.0}}
    assert [c.ref for c in found(personas=PERSONAS, fever=fever)][:2] == ["LAV-08", "SAL-07"]
    assert [c.ref for c in found(personas=PERSONAS, fever={"chato": {"LAV": 50.0}})][:2] == ["SAL-07", "LAV-08"]


def test_without_personas_the_result_equals_today():
    assert found() == found(personas=None, fever=None)
    assert found() == found(fever={"pilar": {"LAV": 90.0}})  # fever alone never ranks: it needs the persona


def test_fever_by_dealer_keeps_positive_moves_with_a_persona():
    events = [
        fever_event("SAL", 25.0),
        fever_event("LAV", -10.0),
        fever_event("RET", 15.0, persona=None),
        fever_event("RET", 5.0, persona="chato"),
    ]
    assert desk.fever_by_dealer(events, 1.0) == {"pilar": {"SAL": 25.0}, "chato": {"RET": 5.0}}


def test_the_desk_reads_fever_only_from_official_signals_in_force(tmp_path):
    import json
    from dataclasses import asdict

    from bazaar_agent.news import EVENTS_FILE

    path = tmp_path / EVENTS_FILE
    rumour = fever_event("LAV", 40.0, official=False)
    over = MarketEvent("price_move", "LAV", 30.0, 0.0, 0.5, None, "pilar", True, "schedule:0:q", "old fever")
    path.write_text(json.dumps({"events": [asdict(fever_event("SAL", 25.0)), asdict(rumour), asdict(over)]}))
    rules = Guardrails(news_signals_enabled=True)
    assert desk.read_fever(rules, path, 1.0) == {"pilar": {"SAL": 25.0}}
    assert desk.read_fever(Guardrails(news_signals_enabled=False), path, 1.0) == {}
    assert desk.read_fever(rules, None, 1.0) is None


def test_the_desk_takes_personas_from_the_snapshot_behind_the_switch():
    from types import SimpleNamespace

    from tests.agent_fakes import clock

    snap = SimpleNamespace(clock=clock(tick=5), dealers=[PILAR])
    sell_desk = desk.SellDesk(None, Guardrails(), None, False, lambda line: None, lambda c: None)
    personas, fever = sell_desk.persona_inputs(snap)
    assert personas is not None and set(personas) == {"pilar"} and fever is None  # no decisions dir: no fever
    off = SimpleNamespace(persona_model_enabled=False)
    sell_desk.rules = off  # type: ignore[assignment]
    assert sell_desk.persona_inputs(snap) == (None, None)
