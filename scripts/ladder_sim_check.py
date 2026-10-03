"""Validate the ladder plans on the simulator's dealers (#55): our real `decide()` against `dealers.reply()`.

Needs `bazaar_sim` on the path (the #55 branch, `origin/ogarciarevett/feat-bazaar-sim`); this repo does
not ship it. Run it from a scratch checkout that has both:

    git worktree add ../w3-sim HEAD && cd ../w3-sim && git merge origin/ogarciarevett/feat-bazaar-sim
    uv sync && uv run python scripts/ladder_sim_check.py --runs 2000

Every conversation opens with the simulator's own `dealers.start()` (its secret limit, patience and
opening from `dealers.json`), our bids carry our real template words (the simulator's Abuela lowers her
floor by 1 for a kind team), and the dealer answers each bid at the next tick, as in the real feed.
The share is the captured part of that conversation's range: opening ask → its secret limit.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from statistics import mean
from typing import Any

from bazaar_agent.agents.dealer import BidPlan, Negotiation, decide, template_words
from bazaar_agent.agents.words import WordsRequest
from bazaar_agent.guardrails import load_guardrails
from bazaar_agent.ladder import floor_table, from_rows, main_rows, plan_for, rarity_of_class
from bazaar_agent.ladder_replay import Result, summarise

try:
    from bazaar_sim import catalog, dealers
except ImportError:  # pragma: no cover - the simulator lives on the #55 branch
    sys.exit("bazaar_sim is not importable: run this from a checkout merged with the #55 simulator branch")

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "evals" / "dealer_threads.json"
CLASSES = {  # price class → (dealer, sim item kind, rarity, pack id)
    ("abuela", "card:common"): ("card", "common", None),
    ("abuela", "card:uncommon"): ("card", "uncommon", None),
    ("abuela", "pack:sobre_barrio"): ("pack", None, "sobre_barrio"),
    ("chato", "card:uncommon"): ("card", "uncommon", None),
    ("chato", "card:rare"): ("card", "rare", None),
}


def menu_item(dealer: str, kind: str, rarity: str | None, pack: str | None) -> dict[str, Any]:
    data = catalog.raw_dealers()[dealer]
    if kind == "pack":
        item = catalog.dealer_menu_sells(data, pack=pack)
    else:
        item = catalog.dealer_menu_sells(data, rarity=rarity)
    assert item is not None, (dealer, kind, rarity, pack)
    return dict(item)


def converse(plan: BidPlan, dealer: str, key: tuple[str, str], rng: random.Random, max_ticks: int = 14) -> Result:
    kind, rarity, pack = CLASSES[key]
    item = menu_item(dealer, kind, rarity, pack)
    style = dealers.STYLES[dealer]
    neg_sim = dealers.start(
        style,
        side="sell",
        item=pack or f"{rarity}-card",
        item_kind="pack" if kind == "pack" else "card",
        rarity=rarity,
        list_price=int(item["list_price"]),
        opening=int(item["opening_ask"]) if kind == "pack" and item.get("opening_ask") else None,
        assets=[],
        rng=rng,
    )
    ours, mood, offer_id = Negotiation(plan), 0.0, 0
    for tick in range(1, max_ticks + 1):
        ask = neg_sim.ask
        move = decide(ours, ask, offer_id if ask is not None else None, neg_sim.final)
        limit = neg_sim.limit
        if move.kind == "accept" and move.price is not None:
            how = "her_final" if neg_sim.final else "her_ask"
            return Result(move.price, tick + 1, tuple(ours.bids), limit, neg_sim.opening, how)
        if move.kind == "walk" or move.price is None:
            return Result(None, tick, tuple(ours.bids), limit, neg_sim.opening, "walk")
        if move.kind != "bid":
            continue
        text = template_words(WordsRequest(dealer, move.price, len(ours.bids), pack or rarity))
        ours.bids.append(move.price)
        mood = min(dealers.MOOD_CAP, mood + dealers.mood_delta(text))
        reply = dealers.reply(style, neg_sim, move.price, text, mood, rng, "card")
        neg_sim = reply.neg
        if reply.kind == "accept":
            return Result(move.price, tick + 1, tuple(ours.bids), neg_sim.limit, neg_sim.opening, "our_bid")
        if reply.kind == "walk":
            return Result(None, tick + 1, tuple(ours.bids), neg_sim.limit, neg_sim.opening, "walk")
        offer_id += 1
    return Result(None, max_ticks, tuple(ours.bids), neg_sim.limit, neg_sim.opening, "timeout")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--json", type=Path, help="also write the numbers here")
    args = ap.parse_args()
    rules = load_guardrails().rules
    rows = main_rows(floor_table(_fixture()))
    out = []
    for key in CLASSES:
        row = rows.get(key)
        if row is None:
            continue
        cap = rules.max_price_for(rarity_of_class(key[1]), key[0])
        for label, capped in (("guardrails", cap), ("uncapped", None)):
            choice = plan_for(row, capped)
            entry: dict[str, Any] = {"dealer": key[0], "class": key[1], "caps": label, "cap": capped}
            if choice.plan is None:
                out.append(entry | {"plan": None, "reason": choice.reason})
                continue
            rng = random.Random(args.seed)
            results = [converse(choice.plan, key[0], key, rng) for _ in range(args.runs)]
            s = summarise(results)
            limits = [r.limit for r in results]
            entry |= {
                "plan": [choice.plan.start, choice.plan.step, choice.plan.max_price],
                "sim_limit_range": [min(limits), max(limits)],
                "sim_limit_mean": round(mean(limits), 1),
            }
            out.append(entry | s.as_dict())
    print(f"{'dealer':7} {'class':18} {'caps':10} {'plan':12} {'sim L':8} share  deal  fill8 rep  how")
    for e in out:
        if e.get("plan") is None:
            print(f"{e['dealer']:7} {e['class']:18} {e['caps']:10} {'-':12} {'':8} {e['reason']}")
            continue
        plan, lim = e["plan"], e["sim_limit_range"]
        print(
            f"{e['dealer']:7} {e['class']:18} {e['caps']:10} {plan[0]:>3}→{plan[2]:<8} {lim[0]:>3}–{lim[1]:<4} "
            f"{e['mean_share']:.3f} {e['deal_rate']:.2f}  {e['fill_within']:.2f}  {e['repeated']:<4} {e['hows']}"
        )
    if args.json:
        args.json.write_text(json.dumps(out, indent=1) + "\n")


def _fixture() -> list[Any]:
    return from_rows(json.loads(FIXTURE.read_text())["rows"])


if __name__ == "__main__":
    main()
