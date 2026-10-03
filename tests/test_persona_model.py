"""persona_model: published dealer personas → negotiation params (pure, no I/O)."""

from __future__ import annotations

import copy
import itertools
from typing import Any

import pytest

from bazaar_agent import persona_model as pm
from bazaar_agent.learn.curves import CurveStats

ABUELA: dict[str, Any] = {
    "id": "abuela",
    "name": "Abuela",
    "kind": "dealer",
    "level": 1,
    "status": "active",
    "traits": {
        "patience": 0.85,
        "generosity": 0.8,
        "shrewdness": 0.2,
        "memory": 0.15,
        "strictness": 0.1,
        "chattiness": 0.75,
    },
    "menu": {
        "sells": [
            {"pack": "sobre_barrio", "list_price": 26, "opening_ask": 30, "per_team_per_hour": 3},
            {"rarity": "common", "list_price": 10, "sets": "released"},
            {"rarity": "uncommon", "list_price": 25},
        ],
        "buys": [{"rarity": ["common", "uncommon"], "sets": "released"}],
        "deals_per_team_per_hour": 8,
    },
    "unlock": {"always": True},
    "open_to_all": True,
}

CHATO: dict[str, Any] = {
    "id": "chato",
    "name": "El Chato",
    "kind": "dealer",
    "level": 2,
    "status": "active",
    "traits": {
        "patience": 0.35,
        "generosity": 0.25,
        "shrewdness": 0.85,
        "memory": 0.9,
        "strictness": 0.85,
        "chattiness": 0.3,
    },
    "menu": {
        "sells": [
            {"pack": "sobre_plata", "list_price": 150, "opening_ask": 188},
            {"rarity": "uncommon", "list_price": 26},
            {"rarity": "rare", "list_price": 77},
        ],
        "buys": [{"rarity": ["uncommon", "rare"]}],
        "deals_per_team_per_hour": 6,
    },
    "unlock": {"early_deals_with": "abuela", "early_min_deals": 3},
    "open_to_all": False,
}

PILAR: dict[str, Any] = {
    "id": "pilar",
    "name": "Pilar",
    "kind": "collector",
    "level": 3,
    "status": "active",
    "traits": {
        "patience": 0.6,
        "generosity": 0.5,
        "shrewdness": 0.75,
        "memory": 0.7,
        "strictness": 0.6,
        "chattiness": 0.55,
    },
    "menu": {
        "sells": [{"pack": "sobre_oro", "list_price": 420, "opening_ask": 504, "per_team_per_hour": 1}],
        "buys": [
            {"rarity": ["uncommon", "rare", "epic"], "sets": ["SAL", "RET"]},
            {"rarity": ["uncommon", "rare", "epic"], "sets": "released"},
        ],
        "deals_per_team_per_hour": 6,
    },
    "unlock": {},
    "open_to_all": False,
}

# Friday's real fill medians (learn.curves over the captured feed): the prior must land near them.
FRIDAY_P50 = {("abuela", "uncommon"): 22.5, ("chato", "uncommon"): 30.0, ("chato", "rare"): 89.5}


def persona(raw: dict[str, Any]) -> pm.Persona:
    p = pm.parse_persona(raw)
    assert p is not None
    return p


@pytest.fixture
def abuela() -> pm.Persona:
    return persona(ABUELA)


@pytest.fixture
def chato() -> pm.Persona:
    return persona(CHATO)


@pytest.fixture
def pilar() -> pm.Persona:
    return persona(PILAR)


# ---------------------------------------------------------------- parsing


def test_parse_persona_reads_the_live_abuela_payload(abuela: pm.Persona) -> None:
    assert abuela.traits.patience == 0.85
    assert abuela.list_price("sobre_barrio") == 26
    assert abuela.list_price("uncommon") == 25
    pack = abuela.sells[0]
    assert (pack.opening_ask, pack.per_team_per_hour, pack.sets) == (30, 3, None)
    assert abuela.deals_per_team_per_hour == 8
    assert abuela.unlock.always and abuela.unlock.open_to_all
    assert abuela.buys_card("common", "LAV") and abuela.buys_card("uncommon", "SAL")
    assert not abuela.buys_card("rare", "LAV")


def test_parse_persona_reads_chato_unlock_rule(chato: pm.Persona) -> None:
    assert chato.unlock.early_deals_with == "abuela"
    assert chato.unlock.early_min_deals == 3
    assert not chato.unlock.open_to_all and not chato.unlock.always


def test_parse_persona_reads_pilar_preferred_sets(pilar: pm.Persona) -> None:
    assert pilar.kind == "collector"
    assert pilar.preferred_sets() == frozenset({"SAL", "RET"})
    assert pilar.sells[0].per_team_per_hour == 1


def test_parse_persona_without_an_id_is_none() -> None:
    assert pm.parse_persona({"name": "nobody"}) is None
    assert pm.parse_persona({"id": ""}) is None
    assert pm.parse_persona({"id": 7}) is None


def test_parse_persona_tolerates_garbage_and_falls_back_to_neutral() -> None:
    raw = {
        "id": "weird",
        "traits": {
            "patience": 1.7,
            "generosity": -0.2,
            "shrewdness": True,
            "memory": "high",
            "strictness": 0.9,
        },
        "menu": {
            "sells": ["junk", None, {"rarity": "rare", "list_price": True}, {"pack": "x", "list_price": -3}],
            "buys": [3, {"rarity": 5}, {"rarity": "rare", "sets": 9}],
            "deals_per_team_per_hour": False,
        },
        "unlock": "nope",
        "level": "two",
        "open_to_all": "yes",
    }
    p = persona(raw)
    t = p.traits
    assert (t.patience, t.generosity, t.shrewdness, t.memory, t.chattiness) == (0.5,) * 5
    assert t.strictness == 0.9  # one bad trait never discards a good one
    assert p.sells == ()
    assert p.buys == (pm.BuyLine("rare", None),)
    assert p.deals_per_team_per_hour is None
    assert p.unlock == pm.Unlock(False, None, 0, 0, False)
    assert (p.level, p.kind, p.name, p.status) == (0, "dealer", "weird", "unknown")


def test_parse_persona_with_no_menu_and_no_traits_is_neutral() -> None:
    p = persona({"id": "bare", "menu": "not a dict", "traits": None})
    assert p.traits == pm.Traits()
    assert p.sells == () and p.buys == ()
    assert p.list_price("uncommon") is None


def test_parse_personas_skips_non_dict_rows_and_missing_ids() -> None:
    out = pm.parse_personas([ABUELA, "junk", None, {"no": "id"}, CHATO])
    assert sorted(out) == ["abuela", "chato"]


# ---------------------------------------------------------------- tone and risk


def test_tone_follows_the_traits(abuela: pm.Persona, chato: pm.Persona, pilar: pm.Persona) -> None:
    assert pm.tone_of(abuela.traits) == "kind"
    assert pm.tone_of(chato.traits) == "terse"
    assert pm.tone_of(pilar.traits) == "terse"
    assert pm.tone_of(pm.Traits()) == "neutral"


def test_cooloff_risk_orders_abuela_below_pilar_below_chato(
    abuela: pm.Persona, chato: pm.Persona, pilar: pm.Persona
) -> None:
    a, p, c = (pm.cooloff_risk(x.traits) for x in (abuela, pilar, chato))
    assert a < p < c
    assert a >= 0 and c <= 1


# ---------------------------------------------------------------- trait prior


@pytest.mark.parametrize(
    ("raw", "item"),
    [
        (ABUELA, "uncommon"),
        (ABUELA, "common"),
        (ABUELA, "sobre_barrio"),
        (CHATO, "uncommon"),
        (CHATO, "rare"),
        (CHATO, "sobre_plata"),
        (PILAR, "sobre_oro"),
    ],
)
def test_trait_prior_never_meets_the_opening_ask(raw: dict[str, Any], item: str) -> None:
    params = pm.trait_prior(persona(raw), item)
    assert params.accept_opening_ask is False
    assert params.never_repeat_price is True
    assert params.step == 1
    assert params.start is not None and params.walk is not None and params.opening_ask is not None
    assert params.start < params.opening_ask
    assert params.start <= params.walk < params.opening_ask
    assert params.ladder() == (params.start, params.walk, 1)


def test_trait_prior_without_a_menu_line_has_no_ladder(abuela: pm.Persona) -> None:
    params = pm.trait_prior(abuela, "epic")
    assert params.start is None and params.walk is None and params.list_price is None
    assert params.ladder() is None
    assert params.accept_opening_ask is False and params.never_repeat_price is True
    assert params.as_row()["ladder"] == "-"


@pytest.mark.parametrize(
    ("raw", "item", "expected"),
    [(ABUELA, "uncommon", 22), (CHATO, "uncommon", 30), (CHATO, "rare", 89)],
)
def test_expected_limit_lands_near_friday_fills(raw: dict[str, Any], item: str, expected: int) -> None:
    params = pm.trait_prior(persona(raw), item)
    assert params.expected_limit == expected
    p50 = FRIDAY_P50[(raw["id"], item)]
    assert abs(params.expected_limit - p50) / p50 <= 0.15


def test_trait_prior_uses_the_published_opening_ask(abuela: pm.Persona) -> None:
    assert pm.trait_prior(abuela, "sobre_barrio").opening_ask == 30


def test_trait_prior_keeps_behaviour_from_the_traits(abuela: pm.Persona, chato: pm.Persona) -> None:
    a, c = pm.trait_prior(abuela, "uncommon"), pm.trait_prior(chato, "uncommon")
    assert a.reply_wait_ticks == 1 and c.reply_wait_ticks == 2
    assert a.reopen_after_ticks < c.reopen_after_ticks
    assert a.bids_before_final >= 3 and c.bids_before_final >= 3


# ---------------------------------------------------------------- derive


def curve(fills: tuple[int, ...], informative: tuple[int, ...], **kw: Any) -> CurveStats:
    base: dict[str, Any] = {
        "dealer": "chato",
        "price_class": "card:uncommon",
        "threads": len(fills),
        "fills": fills,
        "openings": (33, 33, 34),
        "finals": 2,
        "patience": 6.0,
        "concession": 1.0,
        "silent_below": None,
        "thread_ids": tuple(range(len(fills))),
        "informative_fills": informative,
    }
    base.update(kw)
    return CurveStats(**base)


def test_derive_without_a_curve_is_the_trait_prior(chato: pm.Persona) -> None:
    assert pm.derive(chato, "uncommon") == pm.trait_prior(chato, "uncommon")


def test_derive_ignores_a_curve_with_too_few_informative_fills(chato: pm.Persona) -> None:
    fills = (28, 29, 29, 30, 31, 32)
    thin = curve(fills, fills[: pm.MIN_LEARNED_FILLS - 1])
    params = pm.derive(chato, "uncommon", thin)
    assert params.source == "traits"
    assert params == pm.trait_prior(chato, "uncommon")


def test_derive_uses_a_curve_with_enough_informative_fills(chato: pm.Persona) -> None:
    fills = (28, 29, 29, 30, 31, 32)
    params = pm.derive(chato, "uncommon", curve(fills, fills))
    prior = pm.trait_prior(chato, "uncommon")
    assert params.source == "learned"
    assert params.opening_ask == 33
    assert params.expected_limit == 30  # median of the fills
    assert params.bids_before_final == 6
    assert params.start is not None and params.walk is not None
    assert params.start <= params.walk < 33
    assert params.accept_opening_ask is False and params.never_repeat_price is True and params.step == 1
    # behaviour stays with the traits
    assert params.tone == prior.tone
    assert params.cooloff_risk == prior.cooloff_risk
    assert params.reply_wait_ticks == prior.reply_wait_ticks
    assert params.reopen_after_ticks == prior.reopen_after_ticks


# ---------------------------------------------------------------- plan_with_prior


def _all_params() -> list[pm.NegotiationParams]:
    out = []
    for raw in (ABUELA, CHATO, PILAR):
        p = persona(raw)
        out.extend(pm.trait_prior(p, s.item) for s in p.sells)
    return out


def test_plan_with_prior_never_raises_start_top_or_step() -> None:
    for params in _all_params():
        for start, top, step in itertools.product(range(1, 120, 7), range(1, 200, 11), (1, 2, 3, 5)):
            ladder = (start, top, step)
            new, why = pm.plan_with_prior(ladder, params)
            assert new[0] <= start and new[1] <= top and new[2] <= step, (ladder, params.item, new)
            assert min(new) >= 1
            if why is None:
                assert new == ladder
            else:
                assert new != ladder and params.dealer in why


def test_plan_with_prior_returns_none_when_unchanged(abuela: pm.Persona) -> None:
    params = pm.trait_prior(abuela, "uncommon")
    ladder = params.ladder()
    assert ladder is not None
    assert pm.plan_with_prior(ladder, params) == (ladder, None)
    assert pm.plan_with_prior((1, 1, 1), params) == ((1, 1, 1), None)


def test_plan_with_prior_without_params_ladder_is_unchanged(abuela: pm.Persona) -> None:
    params = pm.trait_prior(abuela, "epic")
    assert pm.plan_with_prior((20, 40, 2), params) == ((20, 40, 2), None)


# ---------------------------------------------------------------- budgets, unlocks, sells


def test_deals_left_counts_only_the_rolling_hour(abuela: pm.Persona) -> None:
    # 60 ticks per hour at tick 100: ticks > 40 count
    assert pm.deals_left(abuela, [10, 40, 41, 99, 100], tick=100, ticks_per_hour=60) == 8 - 3
    assert pm.deals_left(abuela, [], tick=100, ticks_per_hour=60) == 8
    assert pm.deals_left(abuela, [90] * 12, tick=100, ticks_per_hour=60) == 0


def test_deals_left_is_none_without_a_budget() -> None:
    p = persona({"id": "free", "menu": {"sells": []}})
    assert pm.deals_left(p, [1, 2, 3], tick=10, ticks_per_hour=60) is None


@pytest.mark.parametrize(("deals", "expected"), [(0, {"abuela": 3}), (2, {"abuela": 1}), (3, {}), (5, {})])
def test_unlock_targets_while_chato_is_locked(abuela: pm.Persona, deals: int, expected: dict[str, int]) -> None:
    personas = {"abuela": abuela, "chato": persona(CHATO)}
    assert pm.unlock_targets(personas, {"abuela"}, {"abuela": deals}) == expected


def test_unlock_targets_nothing_once_chato_is_unlocked(abuela: pm.Persona, chato: pm.Persona) -> None:
    personas = {"abuela": abuela, "chato": chato}
    assert pm.unlock_targets(personas, {"abuela", "chato"}, {"abuela": 0}) == {}


def test_unlock_targets_nothing_when_chato_is_open_to_all(abuela: pm.Persona) -> None:
    raw = copy.deepcopy(CHATO)
    raw["open_to_all"] = True
    personas = {"abuela": abuela, "chato": persona(raw)}
    assert pm.unlock_targets(personas, {"abuela"}, {"abuela": 0}) == {}


def test_unlock_targets_nothing_when_the_gate_dealer_is_not_ours(chato: pm.Persona) -> None:
    assert pm.unlock_targets({"chato": chato}, set(), {}) == {}


def test_sell_weight_for_pilar(pilar: pm.Persona) -> None:
    assert pm.sell_weight(pilar, "common", "SAL", {}) == 0.0
    assert pm.sell_weight(pilar, "uncommon", "SAL", {}) == 1.1
    assert pm.sell_weight(pilar, "uncommon", "LAV", {}) == 1.0
    assert pm.sell_weight(pilar, "uncommon", "SAL", {"SAL": 25}) == pytest.approx(1.1 * 1.25)


def test_sell_weight_ignores_a_negative_fever(pilar: pm.Persona) -> None:
    assert pm.sell_weight(pilar, "rare", "LAV", {"LAV": -40}) == 1.0


def test_sell_weight_zero_when_the_menu_does_not_buy(chato: pm.Persona) -> None:
    assert pm.sell_weight(chato, "common", "LAV", {}) == 0.0
    assert pm.sell_weight(chato, None, None, {}) == 0.0


# ---------------------------------------------------------------- security review of #195: hostile payloads

import json as _json  # noqa: E402

from bazaar_agent.persona_model import parse_personas as _parse  # noqa: E402

HOSTILE = [
    '{"id": "x", "level": 1e400}',
    '{"id": "x", "menu": {"deals_per_team_per_hour": Infinity}}',
    '{"id": "x", "menu": {"sells": 5}}',
    '{"id": "x", "menu": {"buys": true}}',
    '{"id": "x", "menu": {"sells": [{"rarity": "rare", "list_price": 1' + "0" * 400 + "}]}}",
    '{"id": "x", "menu": {"sells": [{"rarity": "rare", "list_price": NaN}]}}',
    '{"id": "\\ud83d", "level": 1}',
    '{"id": "x", "unlock": {"early_deals_with": 7, "early_min_deals": -1e400}}',
]


def test_a_hostile_persona_never_raises_and_never_prices() -> None:
    from bazaar_agent.persona_model import trait_prior

    for text in HOSTILE:
        out = _parse([_json.loads(text)])
        for p in out.values():
            for line in p.sells:
                trait_prior(p, line.item)
            assert p.sells == () or all(0 < s.list_price <= 10**6 for s in p.sells)
    assert _parse([_json.loads(HOSTILE[6])]) == {}


def test_a_repeated_persona_id_keeps_the_first_payload() -> None:
    first = {"id": "abuela", "menu": {"deals_per_team_per_hour": 8}}
    second = {"id": "abuela", "menu": {"deals_per_team_per_hour": 0}}
    assert _parse([first, second])["abuela"].deals_per_team_per_hour == 8
