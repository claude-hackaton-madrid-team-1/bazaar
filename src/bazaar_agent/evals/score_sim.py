"""Score simulator: the board formula of RULES.md "Scoring", fitted to Friday's official numbers.

The organisers publish the shares (negotiating 30, market-making 30, judges 40) but not the formula
behind each number. This module is a model of it, calibrated on two real observations and nothing
else: the public board at tick 30 (`tests/fixtures/api/get_api_leaderboard.anon.json`, 18 teams,
ladder only: the first team trade settled at tick 40) and our own `/me` score from tick 122 to 159
(10.76 → 8.34 while our raw components never moved, so the drop is the other teams' progress).

The model, and the evidence for each piece:
- A component scores `weight × min(cap, raw / top-3 mean of raw)`: full points at the mean of the
  best three (RULES.md says it of the Market Test). Tick 30: two teams with different deals both
  show exactly 12.5, the cap; the third shows 10.41 (`W ≥ 11.8` is forced, `W = 12.5`, cap 1 fits).
- The ladder's raw is additive over levels: `Σ_level weight × (best three deal shares) / 3`, a
  missing deal counting zero. A deal's share is where its price sits in the dealer's range for that
  price class (list price → best fill any team got), learned from every team's public threads.
  The official `ladder_points` 0.058 × 12.5 = 0.725 ≈ our level-1 best-three mean 0.733: our raw
  is level 1 only, as the model says (we never dealt with Chato).
- Level 2 (Chato, open from tick 98): a fitted effective weight of 0.5 reproduces our five board
  refreshes within 0.65 (RMSE 0.36). It is below 1 although "higher levels weigh more": Chato's
  range is learned from 19 fills, so the floor sits above his secret limits and the shares are
  overstated; the weight absorbs that. Treat it as weight × share-scale, not as the rules' weight.
- Rounds: each day is a round; the board is the average weighted by `round weight × share of the
  day played` (Friday 0.5, Saturday 1, Sunday 1: the schedule's `round` actions).
Unverified, kept as parameters: the duel/trade split of the remaining 17.5 negotiating points and
the bench/venue split of the 30 market points (no team had either on Friday's board).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from bazaar_agent.config import REPO_ROOT
from bazaar_agent.evals.dealers import card_rarity

LEVEL_OF: dict[str, int] = {"abuela": 1, "chato": 2}
TOP_N = 3  # "the full points go to the mean of the top three"
FRIDAY_DATA = REPO_ROOT / "tests" / "fixtures" / "evals" / "friday_score.json"


@dataclass(frozen=True)
class DealerDeal:
    """One settled deal with a dealer, as the public feed shows it."""

    tick: int
    team: str
    dealer: str
    price_class: str  # "card:uncommon", "pack:sobre_barrio", "sell:common"
    price: int
    opening: int | None  # the dealer's first price in that conversation, when the feed has it

    @property
    def selling(self) -> bool:
        return self.price_class.startswith("sell:")


@dataclass(frozen=True)
class PriceRange:
    top: int  # buy: the highest opening ask; sell: the lowest opening bid
    floor: int  # buy: the lowest fill; sell: the highest fill


@dataclass(frozen=True)
class ScoreModel:
    ladder_weight: float = 12.5  # fitted: the cap on Friday's board
    duel_weight: float = 12.5  # unverified (negotiating 30 = ladder + duels + trades)
    trade_weight: float = 5.0  # unverified
    bench_weight: float = 15.0  # unverified (market 30 = Market Test + value created on our venue)
    venue_weight: float = 15.0  # unverified
    cap: float = 1.0  # a component never scores above its weight
    level_weights: Mapping[int, float] = field(default_factory=lambda: {1: 1.0, 2: 0.5})  # level 2 fitted
    round_weights: Mapping[str, float] = field(default_factory=lambda: {"fri": 0.5, "sat": 1.0, "sun": 1.0})
    refresh_ticks: int = 5  # the board (and /me's normaliser) refreshes every five ticks


# ---------------------------------------------------------------- deals from the public feed


def _price_class(kind: str, ref: str, buying: bool) -> str | None:
    if kind == "pack":
        return f"pack:{ref}"
    rarity = card_rarity(ref)
    if rarity is None:
        return None
    return f"card:{rarity}" if buying else f"sell:{rarity}"


def deals_from_feed(events: Iterable[Mapping[str, Any]], dealers: Iterable[str] = LEVEL_OF) -> list[DealerDeal]:
    """Every dealer settlement in a feed capture, with the opening price of its conversation.

    A settlement does not name its thread: it is matched to the newest unused thread of that team
    and dealer whose last message came at or before the settlement tick.
    """
    known = set(dealers)
    threads: dict[int, dict[str, Any]] = {}
    deals: list[DealerDeal] = []
    for e in sorted(events, key=lambda ev: (ev.get("tick") or 0, ev.get("id") or 0)):
        p = e.get("payload") or {}
        kind = e.get("type")
        if kind == "thread.opened" and p.get("with") in known:
            threads[p["thread"]] = {"team": p["team"], "dealer": p["with"], "asks": [], "bids": [], "last": e["tick"]}
        elif kind == "thread.message" and p.get("thread") in threads and p.get("offer"):
            th = threads[p["thread"]]
            th["last"] = e["tick"]
            if p.get("sender") == th["dealer"]:
                th["asks"].append(p["offer"].get("want", {}).get("cash") or 0)
                th["bids"].append(p["offer"].get("give", {}).get("cash") or 0)
        elif kind == "settlement" and p.get("persona") in known and p.get("items"):
            dealer = p["persona"]
            team = next((x for x in p.get("parties", []) if x != dealer), None)
            item = p["items"][0]
            buying = item.get("to") == team
            cls = _price_class(item.get("kind", "card"), item.get("ref", ""), buying)
            if team is None or cls is None:
                continue
            candidates = [
                (th["last"], tid)
                for tid, th in threads.items()
                if th["team"] == team and th["dealer"] == dealer and th["last"] <= e["tick"] and not th.get("used")
            ]
            opening = None
            if candidates:
                th = threads[max(candidates)[1]]
                th["used"] = True
                prices = [x for x in (th["asks"] if buying else th["bids"]) if x]
                opening = prices[0] if prices else None
            deals.append(DealerDeal(e["tick"], team, dealer, cls, int(p["price"]), opening))
    return deals


# ---------------------------------------------------------------- the ladder


def learned_ranges(deals: Iterable[DealerDeal], upto: int | None = None) -> dict[tuple[str, str], PriceRange]:
    """Per dealer and price class: list price → best fill any team got (up to a tick, or all)."""
    groups: dict[tuple[str, str], list[DealerDeal]] = defaultdict(list)
    for d in deals:
        if upto is None or d.tick <= upto:
            groups[(d.dealer, d.price_class)].append(d)
    out = {}
    for key, members in groups.items():
        opens = [d.opening for d in members if d.opening is not None]
        fills = [d.price for d in members]
        if members[0].selling:
            out[key] = PriceRange(min(opens) if opens else min(fills), max(fills))
        else:
            out[key] = PriceRange(max(opens) if opens else max(fills), min(fills))
    return out


def deal_share(deal: DealerDeal, ranges: Mapping[tuple[str, str], PriceRange]) -> float:
    """The share of the dealer's range a deal captured, 0..1 (0 when the range is unknown or empty)."""
    r = ranges.get((deal.dealer, deal.price_class))
    if r is None or r.top == r.floor:
        return 0.0
    gained = deal.price - r.top if deal.selling else r.top - deal.price
    return min(1.0, max(0.0, gained / abs(r.floor - r.top)))


def ladder_raw(
    deals: Sequence[DealerDeal],
    upto: int,
    model: ScoreModel,
    ranges: Mapping[tuple[str, str], PriceRange] | None = None,
    teams: Iterable[str] = (),
    since: int = 0,
) -> dict[str, float]:
    """Each team's ladder raw at a tick: Σ level weight × (best three shares) / 3, deals from `since` on."""
    ranges = learned_ranges(deals) if ranges is None else ranges
    shares: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for d in deals:
        level = LEVEL_OF.get(d.dealer)
        if since <= d.tick <= upto and level is not None:
            shares[d.team][level].append(deal_share(d, ranges))
    out = {t: 0.0 for t in teams}
    for team, levels in shares.items():
        out[team] = sum(
            model.level_weights.get(level, 1.0) * sum(sorted(s, reverse=True)[:TOP_N]) / TOP_N
            for level, s in levels.items()
        )
    return out


# ---------------------------------------------------------------- normalisation and rounds


def top_mean(raw: Iterable[float], n: int = TOP_N) -> float:
    best = sorted(raw, reverse=True)[:n]
    return sum(best) / n if best else 0.0


def component_points(value: float, top: float, weight: float, cap: float = 1.0) -> float:
    """`weight × min(cap, value / top-3 mean)`; nothing to score while nobody has any."""
    if top <= 0:
        return 0.0
    return weight * min(cap, max(0.0, value) / top)


def bench_fraction(efficiency: float, stall: float, top3: float) -> float:
    """Market Test: half the points at the free stall's efficiency, full points at the top-3 mean.

    Linear in between (and from 0 up to the stall); RULES.md gives the two anchors, not the curve.
    """
    if efficiency >= top3:
        return 1.0
    if efficiency >= stall:
        return 0.5 + 0.5 * (efficiency - stall) / (top3 - stall) if top3 > stall else 1.0
    return 0.5 * max(0.0, efficiency) / stall if stall > 0 else 0.0


def board_score(rounds: Sequence[tuple[float, float, float]]) -> float:
    """The board from (round weight, share of its day played, round score): a weighted average."""
    total = sum(w * phase for w, phase, _ in rounds)
    return sum(w * phase * s for w, phase, s in rounds) / total if total > 0 else 0.0


def snapshot_tick(tick: int, model: ScoreModel) -> int:
    return tick - tick % model.refresh_ticks


def our_negotiating(
    deals: Sequence[DealerDeal], tick: int, team: str, model: ScoreModel, ranges_upto: int | None = None
) -> float:
    """Our ladder points at a tick (our trades and Friday's practice duels scored nothing)."""
    snap = snapshot_tick(tick, model)
    ranges = learned_ranges(deals, ranges_upto)
    raw = ladder_raw(deals, snap, model, ranges, teams=[team])
    return component_points(raw[team], top_mean(raw.values()), model.ladder_weight, model.cap)


# ---------------------------------------------------------------- calibration


@dataclass(frozen=True)
class Calibration:
    board: list[tuple[str, float, float]]  # (team, official, model) on the tick-30 board
    ours: list[tuple[int, float, float]]  # (tick, official, model) for our /me score
    level2_weight: float
    rmse_ours: float

    @property
    def board_mae(self) -> float:
        return sum(abs(o - m) for _, o, m in self.board) / len(self.board) if self.board else 0.0

    @property
    def max_err_ours(self) -> float:
        return max((abs(o - m) for _, o, m in self.ours), default=0.0)


def _rmse(errors: Sequence[float]) -> float:
    return (sum(e * e for e in errors) / len(errors)) ** 0.5 if errors else 0.0


def fit_level2_weight(
    deals: Sequence[DealerDeal], ours: Mapping[int, float], team: str, model: ScoreModel, steps: int = 100
) -> tuple[float, float]:
    """Grid-search the level-2 weight (0..2) that best reproduces our official series: (weight, RMSE)."""
    best = (model.level_weights.get(2, 1.0), float("inf"))
    for i in range(steps + 1):
        trial = replace(model, level_weights={**model.level_weights, 2: 2.0 * i / steps})
        rmse = _rmse([our_negotiating(deals, t, team, trial) - o for t, o in ours.items()])
        if rmse < best[1] - 1e-12:
            best = (trial.level_weights[2], rmse)
    return best


def calibrate(
    deals: Sequence[DealerDeal],
    board30: Mapping[str, float],
    ours: Mapping[int, float],
    team: str,
    model: ScoreModel | None = None,
    fit: bool = False,
) -> Calibration:
    """Compare the model with the two official observations (optionally refitting level 2 first)."""
    model = model or ScoreModel()
    if fit:
        w2, _ = fit_level2_weight(deals, ours, team, model)
        model = replace(model, level_weights={**model.level_weights, 2: w2})
    raw30 = ladder_raw(deals, 30, model, teams=board30)
    top30 = top_mean(raw30.values())
    board = [
        (t, o, round(component_points(raw30.get(t, 0.0), top30, model.ladder_weight, model.cap), 2))
        for t, o in sorted(board30.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    series = [(t, o, round(our_negotiating(deals, t, team, model), 2)) for t, o in sorted(ours.items())]
    return Calibration(board, series, model.level_weights.get(2, 1.0), _rmse([m - o for _, o, m in series]))


# ---------------------------------------------------------------- what-if


@dataclass(frozen=True)
class Marginal:
    move: str
    raw_delta: float
    points: float  # round points it adds (at the given top-3 mean, before the cap bites)


def ladder_marginals(our_raw: float, top: float, model: ScoreModel) -> list[Marginal]:
    """What one more dealer deal is worth in round points, at today's top-3 mean."""
    w2 = model.level_weights.get(2, 1.0)
    w3 = model.level_weights.get(3, w2)
    moves = [
        ("level-1 deal at share 1.0 replacing a 0.6", (1.0 - 0.6) / TOP_N),
        ("first level-2 (Chato) deal at share 0.5", w2 * 0.5 / TOP_N),
        ("three level-2 deals at share 0.5", w2 * 1.5 / TOP_N),
        ("first level-3 deal at share 0.5 (weight assumed = level 2)", w3 * 0.5 / TOP_N),
    ]
    base = component_points(our_raw, top, model.ladder_weight, model.cap)
    return [
        Marginal(
            name,
            round(delta, 4),
            round(component_points(our_raw + delta, top, model.ladder_weight, model.cap) - base, 2),
        )
        for name, delta in moves
    ]


def final_game_points(day_scores: Mapping[str, float], model: ScoreModel) -> float:
    """The 60 game points at the end (judges' 40 apart): rounds averaged, Friday counting half."""
    return board_score([(model.round_weights[d], 1.0, s) for d, s in day_scores.items()])


@dataclass(frozen=True)
class RoundOutlook:
    """One round's components, each as a ratio to the top-3 mean (the bench as its 0..1 fraction)."""

    ladder: float = 0.0
    duels: float = 0.0
    trades: float = 0.0
    bench: float = 0.0
    venue: float = 0.0

    def points(self, model: ScoreModel) -> dict[str, float]:
        cap = model.cap
        return {
            "ladder": model.ladder_weight * min(cap, self.ladder),
            "duels": model.duel_weight * min(cap, self.duels),
            "trades": model.trade_weight * min(cap, self.trades),
            "bench": model.bench_weight * min(1.0, self.bench),
            "venue": model.venue_weight * min(cap, self.venue),
        }

    def total(self, model: ScoreModel) -> float:
        return sum(self.points(model).values())


def final_points_per_round_point(day: str, model: ScoreModel) -> float:
    """What one point of a day's round score adds to the final 60 game points."""
    return model.round_weights[day] / sum(model.round_weights.values())


# ---------------------------------------------------------------- the calibration data


@dataclass(frozen=True)
class FridayData:
    """What the model is checked against (`tests/fixtures/evals/friday_score.json`)."""

    team: str
    deals: list[DealerDeal]
    board30: dict[str, float]  # the public board at tick 30: team → negotiating
    ours: dict[int, float]  # our official /me negotiating per tick
    ladder_points: float  # our official ladder_points at the last tick


def load_data(path: Path = FRIDAY_DATA, feed: Path | None = None) -> FridayData:
    """The calibration fixture; with `feed` (a JSONL capture), the deals are rebuilt from it instead."""
    raw = json.loads(path.read_text())
    deals = [DealerDeal(*row) for row in raw["deals"]]
    if feed is not None:
        deals = deals_from_feed(json.loads(line) for line in feed.read_text().splitlines() if line.strip())
    return FridayData(
        team=raw["team"],
        deals=deals,
        board30={k: float(v) for k, v in raw["board30"].items()},
        ours={int(k): float(v) for k, v in raw["ours"].items()},
        ladder_points=float(raw["ours_ladder_points"]),
    )


# ---------------------------------------------------------------- the live check (Saturday morning)


@dataclass(frozen=True)
class LiveRow:
    """One /me snapshot next to the model: what the official numbers say the formula is doing."""

    tick: int
    negotiating: float | None  # official
    ladder_points: float | None  # official raw ladder (Friday: model raw / ladder weight)
    duel_points: float | None
    model_ladder_points: float  # model raw / ladder weight: compare with ladder_points
    model_ladder: float  # model ladder component (normalised)

    @property
    def unexplained(self) -> float | None:
        """Official negotiating minus the model's ladder: duels + trades + model error."""
        return None if self.negotiating is None else round(self.negotiating - self.model_ladder, 2)


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def live_check(
    deals: Sequence[DealerDeal],
    snapshots: Sequence[tuple[int, Mapping[str, Any]]],
    team: str,
    model: ScoreModel,
    round_start: int = 0,
) -> list[LiveRow]:
    """Our /me snapshots against the model, counting dealer deals from `round_start` on.

    If the ladder restarts each round, `ladder_points` falls to 0 when a round opens and only the
    run with `round_start` set to that tick keeps matching it.
    """
    ranges = learned_ranges(deals)
    rows = []
    for tick, score in snapshots:
        if not isinstance(score, Mapping):  # a /me without a score is stored as JSON null
            continue
        raw = ladder_raw(deals, snapshot_tick(tick, model), model, ranges, teams=[team], since=round_start)
        rows.append(
            LiveRow(
                tick=tick,
                negotiating=_num(score.get("negotiating")),
                ladder_points=_num(score.get("ladder_points")),
                duel_points=_num(score.get("duel_points")),
                model_ladder_points=round(raw[team] / model.ladder_weight, 4),
                model_ladder=round(
                    component_points(raw[team], top_mean(raw.values()), model.ladder_weight, model.cap), 2
                ),
            )
        )
    return rows
