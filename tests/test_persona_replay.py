"""Calibration evidence for the persona trait prior: on Friday's real dealer threads (public feed, ticks 0-159),
the ladder derived from a dealer's published traits alone must land near the ladder learned from the fills.

Fixture: `tests/fixtures/friday_dealer_threads.jsonl`, the public `/api/feed` events `intel.dealer_threads` reads
for Abuela and Chato (thread.opened, thread.message with a cash offer, settlement), stripped to the fields it
reads. Run with `-s` to see the table."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bazaar_agent.intel import DealerThread, dealer_threads
from bazaar_agent.learn.curves import CurveStats, curve_stats, known_class
from bazaar_agent.learn.evolve import DEFAULT_PATIENCE, Ladder, target_ladder
from bazaar_agent.learn.replay import compare
from bazaar_agent.persona_model import parse_personas, trait_prior

FIXTURE = Path(__file__).parent / "fixtures" / "friday_dealer_threads.jsonl"
LIMIT_TOLERANCE = 0.15  # the prior's expected limit vs the fills' median
SHARE_TOLERANCE = 0.12  # the prior's mean replayed share vs the learned ladder's

# The live `GET /api/dealers` persona payloads (the fields the persona model reads).
PERSONAS: list[dict[str, Any]] = [
    {
        "id": "abuela",
        "name": "La Abuela",
        "kind": "persona",
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
                {"pack": "sobre_barrio", "list_price": 26, "opening_ask": 30},
                {"rarity": "common", "list_price": 10},
                {"rarity": "uncommon", "list_price": 25},
            ],
            "buys": [{"rarity": "common"}],
        },
    },
    {
        "id": "chato",
        "name": "El Chato",
        "kind": "persona",
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
        },
    },
]

CASES = [
    ("abuela", "card:uncommon", "uncommon"),
    ("abuela", "pack:sobre_barrio", "sobre_barrio"),
    ("chato", "card:uncommon", "uncommon"),
    ("chato", "card:rare", "rare"),
]


@pytest.fixture(scope="module")
def threads() -> list[DealerThread]:
    events = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]
    return dealer_threads(events, None)


@pytest.fixture(scope="module")
def stats(threads: list[DealerThread]) -> dict[tuple[str, str], CurveStats]:
    return curve_stats(threads)


def _class_threads(threads: list[DealerThread], dealer: str, cls: str) -> list[DealerThread]:
    return [t for t in threads if t.dealer == dealer and t.side == "buy" and known_class(t.item) == cls]


def _evidence(threads: list[DealerThread], stats: dict[tuple[str, str], CurveStats], dealer: str, cls: str, item: str):
    curve = stats[(dealer, cls)]
    pool = _class_threads(threads, dealer, cls)
    params = trait_prior(parse_personas(PERSONAS)[dealer], item)
    shape = params.ladder()
    assert shape is not None, f"{dealer} {item}: no list price in the persona"
    start, walk, step = shape
    prior = Ladder(start, step, walk)
    learned, why = target_ladder(curve, None, pool)
    assert learned is not None, f"{dealer} {cls}: no learned ladder ({why})"
    assert curve.floor is not None
    found = compare(pool, dealer, cls, prior, learned, curve.floor, curve.patience or DEFAULT_PATIENCE)
    assert found is not None, f"{dealer} {cls}: no replayable thread"
    return curve, params, found


def test_fixture_is_small_and_public() -> None:
    raw = FIXTURE.read_text()
    assert len(raw.encode()) < 400_000
    assert "tk-" not in raw and "key" not in raw.lower() and "token" not in raw.lower()


@pytest.mark.parametrize(("dealer", "cls", "item"), CASES, ids=[f"{d}-{i}" for d, _, i in CASES])
def test_trait_prior_lands_near_the_learned_ladder(threads, stats, dealer: str, cls: str, item: str) -> None:
    curve, params, found = _evidence(threads, stats, dealer, cls, item)
    median_fill = curve.fill_q(0.5)
    assert median_fill is not None and params.expected_limit is not None
    gap = abs(params.expected_limit - median_fill) / median_fill
    print(
        f"\n{dealer:7} {cls:18} fills {len(curve.fills):3} p50 {median_fill:6.1f}"
        f" prior limit~ {params.expected_limit:4} ({gap:5.1%})"
        f" | prior {str(found.old):18} share {found.old_share:.3f} deals {found.old_deals:3}"
        f" | learned {str(found.new):18} share {found.new_share:.3f} deals {found.new_deals:3}"
        f" | real {found.real_share:.3f} | threads {found.threads}"
    )
    assert gap <= LIMIT_TOLERANCE, f"limit {params.expected_limit} vs fills p50 {median_fill}"
    assert found.old_share >= found.new_share - SHARE_TOLERANCE, f"prior {found.old_share} vs {found.new_share}"
