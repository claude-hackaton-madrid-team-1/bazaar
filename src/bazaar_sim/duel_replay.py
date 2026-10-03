"""Replay a duel policy on the real practice payloads, and fit the zoo's rival styles to them.

The replay. In 12 of the 26 practice duels (tests/fixtures/evals/duels_done.json) we never sent a message:
the rival's moves there were unilateral, so its recorded path is what it would have done whatever we said
(in a time-based bot; a responsive one is the counterfactual below). `replay` plays a policy against that
path in the zoo engine (`duel_zoo.play`), with the real clock and the real score. Two counterfactuals:

    conservative  the rival never accepts our offers: only our accept of its standing offer closes a deal
    consistent    the rival accepts our standing offer when it is at least as good for it as its own next
                  recorded offer (or its last one, after it went silent)

`conservative` is the go/no-go number. Both assume a rival's standing offer stays acceptable after it went
silent (duels 95, 119, 120, 131, 132, 147, 148): unverified on the real API.

The fit. `features` reads a rival's path relative to OUR limit (the rival's limit is private): its opening and
final gap toward us (fraction of our limit), its pace (fraction of our limit per tick), its cadence, the shape
exponent of its concession curve and how much of it is a final hold. `classify` turns that into a zoo style.
Where we moved every tick, a tit-for-tat rival and a time-based one look alike: those duels get
`tit_for_tat` ("responsive") and the report says the label is not separable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from bazaar_sim import duel_zoo as zoo

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "evals" / "duels_done.json"
PRACTICE_TICKS = 12  # duels.scheduled payload, practice session: duel_ticks 12
HOLD_BAND = 0.02  # a "hold" is a price within 2 % of the rival's final one
MIN_ELAPSED = 4  # a live duel younger than this is too short to label
Counterfactual = Literal["conservative", "consistent"]


def load(path: Path = FIXTURE) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    rows = data["duels"] if isinstance(data, Mapping) else data
    return [dict(r) for r in rows]


def _ours(duel: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [m for m in duel.get("messages") or [] if m.get("from") == zoo.US]


def _theirs(duel: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [m for m in duel.get("messages") or [] if m.get("from") != zoo.US and m.get("price") is not None]


def unanswered(duel: Mapping[str, Any]) -> bool:
    """A finished duel where we sent nothing: the rival's path was unilateral."""
    return duel.get("status") in ("deal", "no_deal") and not _ours(duel) and duel.get("your_offer") is None


def started_tick(duel: Mapping[str, Any], duel_ticks: int = PRACTICE_TICKS) -> int:
    return int(duel["deadline_tick"]) - duel_ticks


# ---------------------------------------------------------------- the replay


def scripted_rival(duel: Mapping[str, Any], counterfactual: Counterfactual = "conservative") -> zoo.Rival:
    """The rival's recorded path as a zoo rival: at each recorded tick it posts its recorded price."""
    path: dict[int, tuple[int, int]] = {}
    for m in _theirs(duel):
        path[int(m["tick"])] = (int(m["price"]), int(m.get("days") or 0))  # several in one tick: the last stands
    rival_sells = duel["role"] == "buyer"

    def rival(view: zoo.RivalView) -> zoo.Act:
        if counterfactual == "consistent" and view.our_offer is not None and view.its_offer is not None:
            later = [p for t, (p, _) in sorted(path.items()) if t >= view.tick]
            bar = later[0] if later else view.its_offer.price
            good = view.our_offer.price >= bar if rival_sells else view.our_offer.price <= bar
            if view.fresh() and good:
                return zoo.Act("accept", view.our_offer.price)
        if view.tick in path:
            price, days = path[view.tick]
            return zoo.Act("offer", price, days)
        return zoo.HOLD

    return rival


def scenario_for(duel: Mapping[str, Any], duel_ticks: int = PRACTICE_TICKS) -> zoo.Scenario:
    """The real duel as a zoo scenario. The rival's limit is private: the pie (and `share`) read as 0."""
    issues = duel.get("issues") or ["price"]
    weight = duel.get("your_days_weight")
    return zoo.Scenario(
        role=duel["role"],
        limit=int(duel["your_limit"]),
        rival_limit=int(duel["your_limit"]),
        style="replay",
        decay=float(duel.get("decay_per_round") or 0.06),
        duel_ticks=duel_ticks,
        two_issues="days" in issues,
        days_weight=float(weight) if isinstance(weight, int | float) else None,
        started_tick=started_tick(duel, duel_ticks),
        duel=int(duel["duel"]),
    )


@dataclass(frozen=True)
class Replayed:
    duel: int
    role: str
    actual: float  # what the real duel scored (0 for no deal)
    oracle: float  # the best rival offer on its path, accepted with no round of talk
    record: zoo.Record

    @property
    def result(self) -> float:
        return self.record.result


def oracle(duel: Mapping[str, Any]) -> float:
    """The most a silent player could have taken: the rival's best offer for us, accepted at once (0 rounds)."""
    limit, role = int(duel["your_limit"]), duel["role"]
    gains = [(m["price"] - limit) if role == "seller" else (limit - m["price"]) for m in _theirs(duel)]
    return float(max([0, *gains]))


def replay(
    policy: zoo.Policy,
    duel: Mapping[str, Any],
    counterfactual: Counterfactual = "conservative",
    team_first: bool = False,
) -> Replayed:
    sc = replace(scenario_for(duel), team_first=team_first)
    record, _ = zoo.play(policy, sc, scripted_rival(duel, counterfactual))
    actual = duel.get("result")
    return Replayed(
        duel=int(duel["duel"]),
        role=str(duel["role"]),
        actual=float(actual) if isinstance(actual, int | float) else 0.0,
        oracle=oracle(duel),
        record=record,
    )


def replay_all(
    policy: zoo.Policy,
    duels: Sequence[Mapping[str, Any]] | None = None,
    counterfactual: Counterfactual = "conservative",
    team_first: bool = False,
) -> list[Replayed]:
    """`policy` on every unanswered real duel (the 12 of the practice session by default)."""
    rows = load() if duels is None else duels
    return [replay(policy, d, counterfactual, team_first) for d in rows if unanswered(d)]


# ---------------------------------------------------------------- the fit


@dataclass(frozen=True)
class Features:
    """A rival's recorded path, relative to our limit. Gaps are positive inside our zone."""

    duel: int
    role: str
    status: str
    n: int  # the rival's priced messages
    elapsed: int  # ticks of the duel the payload covers
    played: bool = False  # we sent a priced message before the rival's last one
    we_first: bool = False  # our first priced message came no later than the rival's first
    open_gap: float | None = None  # first rival price toward us, fraction of our limit
    final_gap: float | None = None
    pace: float | None = None  # concession toward us per tick, fraction of our limit (least squares)
    cadence: float | None = None  # messages per tick between its first and last
    shape: float | None = None  # k in gap(progress) ∝ progress^k: 1 linear, > 1 convex, < 1 concave
    hold_share: float | None = None  # share of its messages within HOLD_BAND of its final price
    label: str = ""


def _toward_us(role: str, limit: int, price: float) -> float:
    return (price - limit) / limit if role == "seller" else (limit - price) / limit


def _slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / den if den else 0.0


def _shape(ticks: Sequence[int], gaps: Sequence[float]) -> float | None:
    """The exponent k that best fits the normalised concession curve (grid search, least squares)."""
    span_t, span_g = ticks[-1] - ticks[0], gaps[-1] - gaps[0]
    if len(ticks) < 3 or span_t <= 0 or abs(span_g) < 1e-9:
        return None
    xs = [(t - ticks[0]) / span_t for t in ticks]
    ys = [(g - gaps[0]) / span_g for g in gaps]
    grid = [k / 20 for k in range(4, 101)]  # 0.2 .. 5.0
    return min(grid, key=lambda k: sum((x**k - y) ** 2 for x, y in zip(xs, ys, strict=True)))


def features(duel: Mapping[str, Any], duel_ticks: int = PRACTICE_TICKS) -> Features:
    limit, role = int(duel["your_limit"]), str(duel["role"])
    theirs = _theirs(duel)
    ours = [m for m in _ours(duel) if m.get("price") is not None]
    start = started_tick(duel, duel_ticks)
    seen = [int(m["tick"]) for m in duel.get("messages") or []]
    live = duel.get("status") == "live"
    elapsed = (max(seen) - start + 1 if seen else 0) if live else duel_ticks
    base = Features(duel=int(duel["duel"]), role=role, status=str(duel.get("status")), n=len(theirs), elapsed=elapsed)
    played = bool(ours) and bool(theirs) and int(ours[0]["tick"]) <= int(theirs[-1]["tick"])
    we_first = bool(ours) and (not theirs or int(ours[0]["tick"]) <= int(theirs[0]["tick"]))
    if not theirs:
        return replace(base, played=played, we_first=we_first)
    ticks = [int(m["tick"]) for m in theirs]
    gaps = [_toward_us(role, limit, float(m["price"])) for m in theirs]
    final = float(theirs[-1]["price"])
    held = sum(1 for m in theirs if abs(float(m["price"]) - final) <= HOLD_BAND * final)
    return replace(
        base,
        played=played,
        we_first=we_first,
        open_gap=round(gaps[0], 4),
        final_gap=round(gaps[-1], 4),
        pace=round(_slope(ticks, gaps), 4) if len(set(ticks)) > 1 else None,
        cadence=round(len(ticks) / (ticks[-1] - ticks[0] + 1), 3),
        shape=_shape(ticks, gaps),
        hold_share=round(held / len(theirs), 3),
    )


def classify(duel: Mapping[str, Any], duel_ticks: int = PRACTICE_TICKS) -> str:
    """The zoo style a real rival's path looks like ("undetermined" for a live duel caught too early)."""
    f = features(duel, duel_ticks)
    if f.status == "live" and f.elapsed < MIN_ELAPSED:
        return "undetermined"
    if f.n == 0:
        return "no_show"
    if f.n >= 3 and (f.hold_share or 0) > 0.5:
        return "holdout"
    if f.n <= 2:
        return "tit_for_tat" if f.we_first else "one_shot"
    if f.played:
        return "tit_for_tat"
    return "convex" if (f.shape or 1.0) >= 1.6 else "linear"


def fit(duels: Sequence[Mapping[str, Any]] | None = None) -> list[Features]:
    """Every real duel's features with its label."""
    rows = load() if duels is None else duels
    out = []
    for d in rows:
        f = features(d)
        out.append(replace(f, label=classify(d)))
    return out


def practice_mix(
    duels: Sequence[Mapping[str, Any]] | None = None,
    waiting_is_tit_for_tat: bool = False,
    responsive_is_linear: bool = False,
) -> dict[str, int]:
    """How often each zoo style appeared in the practice session (undetermined duels left out), and two brackets.

    A rival that posted one offer and then waited for an answer we never sent is a one-shot on paper, but a
    tit-for-tat rival looks the same against silence (`classify` on the zoo's own paths): with
    `waiting_is_tit_for_tat` those single-message one-shots count as tit-for-tat (bad for silent policies).
    A rival that conceded while we countered every tick is labelled tit-for-tat, but a time-based conceder looks
    the same: with `responsive_is_linear` those count as linear conceders (good for silent policies)."""
    counts: dict[str, int] = {}
    for f in fit(duels):
        label = f.label
        if label == "undetermined":
            continue
        if waiting_is_tit_for_tat and label == "one_shot" and f.n == 1 and not f.played:
            label = "tit_for_tat"
        if responsive_is_linear and label == "tit_for_tat" and f.n >= 3:
            label = "linear"
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))
