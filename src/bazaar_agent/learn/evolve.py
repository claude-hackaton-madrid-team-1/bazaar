"""Auto-evolve the dealer ladder: per (dealer, price class), learn where to start, how fast to climb and
where to walk, from every team's threads, then move our parameters there in bounded, logged steps.

What the curves teach (`curves.CurveStats`): a conversation closes at or above its secret limit, and the
fills show where those limits sit. The dealer names a final offer after ~`patience` bids. So:
- start at the low fills (`START_Q`): a cheap conversation is taken at our first bid;
- climb so the walk point is reached by the dealer's patience, not after it (step = gap / (patience − 1));
- walk at the high fills (`WALK_Q`): above that, closing this conversation costs more than the next one.
- skip the class when too few fills sit at or under what we may pay (`MIN_DEAL_SHARE`): Chato's uncommons
  fill at 28-32 and `max_price_uncommon` is 26, so a thread there only spends a slot and a quota.

Guardrails (GUARDRAILS.md) bound everything: the walk point never exceeds the rarity's cap, and at use time
never exceeds the strategy's own top (value minus the minimum surplus, the cap): a learned ladder can only
make us pay less or skip, never raise a limit. Each pass moves a parameter by at most `MAX_MOVE` from the
current one, and the policy row keeps the previous values, the target, the evidence and a short history.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bazaar_agent.guardrails import Guardrails
from bazaar_agent.intel import DealerThread
from bazaar_agent.learn.curves import CurveStats, quantile
from bazaar_agent.learn.model import TEXT_MAX, Learning

START_Q = 0.10
MAX_SEARCH_THREADS = 200  # the newest conversations of a class the search replays (bounded cost; follows drift)
WALK_Q = 0.90
MIN_FILLS = 5  # fewer fills than this: no learned ladder (today's strategy keeps the thread)
MIN_SKIP_EVIDENCE = 3  # conversations (fills + walks at the cap) before a class may be skipped
MIN_DEAL_SHARE = 0.2  # skip a class when fewer than this share of its fills sit at or under our walk point
DEFAULT_PATIENCE = 5.0  # bids before a final when no final has been seen (Abuela: ~5)
STEPS = (1, 2, 3)  # the steps the search tries (small steps earn small steps)
MAX_MOVE = 3  # primas a parameter may move per pass (the step: 1)
HISTORY = 10
Key = tuple[str, str]  # (dealer, price class)


@dataclass(frozen=True)
class Ladder:
    start: int
    step: int
    walk: int

    def bids(self, n: int) -> list[int]:
        """Our first `n` bids: strictly rising until the walk point, then held there."""
        return [min(self.walk, self.start + k * self.step) for k in range(n)]

    def as_dict(self) -> dict[str, int]:
        return {"start": self.start, "step": self.step, "walk": self.walk}

    def __str__(self) -> str:
        return f"{self.start}→{self.walk} step {self.step}"


def cap_for(price_class: str, rules: Guardrails) -> int | None:
    """The GUARDRAILS.md cap for a buy class: `card:<rarity>` or `pack:<id>`; None for a sale or no cap."""
    if price_class.startswith("card:"):
        return rules.max_price_for(price_class.split(":", 1)[1])
    if price_class.startswith("pack:"):
        return rules.max_price_pack
    return None


@dataclass(frozen=True)
class LadderPolicy:
    dealer: str
    price_class: str
    ladder: Ladder | None  # None: skip this class (see `reason`)
    reason: str
    fills: tuple[int, ...]  # the evidence: sorted fills (every team's)
    threads: int
    patience: float
    tick: int
    target: Ladder | None = None
    previous: Ladder | None = None
    evidence: tuple[int, ...] = ()  # thread ids
    history: tuple[dict[str, Any], ...] = ()
    replay: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> Key:
        return (self.dealer, self.price_class)

    def deal_share(self, top: int) -> float:
        """Share of the fills seen at or under `top`: how often a conversation closed at that price or less."""
        return sum(1 for f in self.fills if f <= top) / len(self.fills) if self.fills else 0.0

    def plan(self, base: tuple[int, int, int]) -> tuple[tuple[int, int, int] | None, str]:
        """The strategy's ladder (start, top, step) evolved by this policy, or None to skip the thread.
        Never above the strategy's top (value and cap): a learned ladder only lowers or skips."""
        start, top, step = base
        if self.ladder is None:
            return None, self.reason
        if self.deal_share(top) < MIN_DEAL_SHARE:
            return None, (
                f"learned: only {self.deal_share(top):.0%} of {self.dealer} {self.price_class} fills "
                f"({self.fills[0]}-{self.fills[-1]}) are at or under our top {top}"
            )
        walk = min(self.ladder.walk, top)
        new_start = min(self.ladder.start, walk)
        return (new_start, walk, max(1, self.ladder.step)), f"learned ladder {self.ladder} (was {start}→{top})"

    def text(self) -> str:
        if self.ladder is None:
            head = f"{self.dealer} {self.price_class}: skip. {self.reason}"
        else:
            was = f" (was {self.previous})" if self.previous and self.previous != self.ladder else ""
            head = (
                f"{self.dealer} {self.price_class} ladder {self.ladder}{was}: fills {self.fills[0]}-{self.fills[-1]} "
                f"over {self.threads} threads, final after ~{self.patience:g} bids"
            )
        if self.replay:
            head += f"; replay share {self.replay.get('old_share')} → {self.replay.get('new_share')}"
        return head[: TEXT_MAX - 1] + "…" if len(head) > TEXT_MAX else head

    def to_learning(self, us: str) -> Learning:
        detail: dict[str, Any] = {
            "mechanic": "dealer",
            "pattern": "ladder",
            "price_class": self.price_class,
            "skip": self.ladder is None,
            "reason": self.reason,
            "ladder": self.ladder.as_dict() if self.ladder else None,
            "target": self.target.as_dict() if self.target else None,
            "previous": self.previous.as_dict() if self.previous else None,
            "fills": list(self.fills[-100:]),
            "threads": self.threads,
            "patience": self.patience,
            "evidence_threads": list(self.evidence[-20:]),
            "history": list(self.history[-HISTORY:]),
            "replay": dict(self.replay),
        }
        return Learning(
            subject_kind="dealer",
            subject=self.dealer,
            kind="policy",
            tick=max(0, self.tick),
            team=us,
            confidence=round(min(0.95, 0.5 + 0.45 * min(len(self.fills), 30) / 30), 3),
            text=self.text(),
            source="outcome",
            detail=detail,
        )

    @classmethod
    def from_learning(cls, lr: Learning) -> LadderPolicy | None:
        """A stored policy row back (None when the row is not a ladder policy or is malformed)."""
        d = lr.detail
        replay = d.get("replay")
        if lr.kind != "policy" or d.get("pattern") != "ladder" or not isinstance(d.get("price_class"), str):
            return None
        try:
            return cls(
                dealer=lr.subject,
                price_class=str(d["price_class"]),
                ladder=_ladder(d.get("ladder")),
                reason=str(d.get("reason") or ""),
                fills=tuple(sorted(int(f) for f in d.get("fills") or ())),
                threads=int(d.get("threads") or 0),
                patience=float(d.get("patience") or DEFAULT_PATIENCE),
                tick=lr.tick,
                target=_ladder(d.get("target")),
                previous=_ladder(d.get("previous")),
                evidence=tuple(int(t) for t in d.get("evidence_threads") or ()),
                history=tuple(h for h in d.get("history") or () if isinstance(h, dict)),
                replay=replay if isinstance(replay, dict) else {},
            )
        except (TypeError, ValueError, KeyError):
            return None


def _ladder(raw: object) -> Ladder | None:
    if not isinstance(raw, dict):
        return None
    start, step, walk = int(raw["start"]), int(raw["step"]), int(raw["walk"])
    if not 1 <= start <= walk or step < 1:
        return None
    return Ladder(start, step, walk)


def target_ladder(
    stats: CurveStats, cap: int | None, threads: Sequence[DealerThread] = ()
) -> tuple[Ladder | None, str]:
    """Where the evidence says the ladder should be (before bounding the move).

    With the class's real threads, a bounded grid search: every (start, step, walk) inside the cap is
    replayed on those conversations (`replay.py`) and the one with the best mean share wins (then more
    deals, a lower walk point, a smaller step). Without threads, the quantile rule: start at the low
    fills, reach the high fills by the dealer's patience."""
    fills = stats.fills
    blocked = above_cap(stats, cap, threads)
    if blocked is not None:
        return None, blocked
    if len(fills) < MIN_FILLS:
        return None, f"only {len(fills)} fills: not enough to learn"
    lo, mid, hi = quantile(fills, START_Q), quantile(fills, 0.5), quantile(fills, WALK_Q)
    assert lo is not None and mid is not None and hi is not None
    walk_cap = math.ceil(hi) if cap is None else min(cap, fills[-1])
    patience = stats.patience or DEFAULT_PATIENCE
    if threads:
        found = search(stats, threads, walk_cap, patience)
        if found is not None:
            ladder, score = found
            return (
                ladder,
                f"best replayed share {score:.3f} over {stats.threads} threads (final after ~{patience:g} bids)",
            )
    walk = min(walk_cap, math.ceil(hi))
    start = max(1, min(walk, math.floor(lo)))
    step = max(1, math.ceil((walk - start) / max(1.0, patience - 1)))
    return Ladder(start, step, walk), f"fills p10 {lo:g} / p90 {hi:g}, final after ~{patience:g} bids"


def above_cap(stats: CurveStats, cap: int | None, threads: Sequence[DealerThread]) -> str | None:
    """Why this class cannot close under our cap, or None. Evidence: fills above the cap, and conversations
    where a team already bid the cap and still got no deal (its limit was higher). Our own walks count, so
    a dealer nobody else trades with is learned from our errors alone."""
    if cap is None:
        return None
    walked = [t for t in threads if t.fill_price is None and t.team_prices and max(t.team_prices) >= cap]
    evidence = len(stats.fills) + len(walked)
    closable = sum(1 for f in stats.fills if f <= cap)
    if evidence < MIN_SKIP_EVIDENCE or closable / evidence >= MIN_DEAL_SHARE:
        return None
    seen = f"fills {stats.fills[0]}-{stats.fills[-1]}" if stats.fills else "no fill"
    return (
        f"skip: {closable} of {evidence} {stats.dealer} {stats.price_class} conversations closed at or under the "
        f"cap {cap} ({seen}; {len(walked)} walked after bidding the cap)"
    )


def search(
    stats: CurveStats, threads: Sequence[DealerThread], walk_cap: int, patience: float
) -> tuple[Ladder, float] | None:
    """The ladder with the best mean replayed share on the class's threads (bounded grid, a few hundred runs)."""
    from bazaar_agent.learn.replay import replay_thread

    floor = stats.floor
    assert floor is not None
    p50 = quantile(stats.fills, 0.5) or floor
    starts = range(floor, max(floor, math.ceil(p50)) + 1)  # below the lowest fill ever seen gains nothing
    walks = range(min(walk_cap, math.ceil(p50)), walk_cap + 1)
    best: tuple[tuple[float, int, int, int, int], Ladder] | None = None
    for start in starts:
        for walk in walks:
            if walk < start:
                continue
            for step in STEPS:
                ladder = Ladder(start, step, walk)
                runs = [r for t in threads if (r := replay_thread(t, ladder, floor, patience)) is not None]
                if not runs:
                    continue
                score = sum(r.share for r in runs) / len(runs)
                deals = sum(r.price is not None for r in runs)
                rank = (round(score, 4), deals, -walk, -step, -start)
                if best is None or rank > best[0]:
                    best = (rank, ladder)
    return (best[1], best[0][0]) if best else None


def bounded(previous: Ladder | None, target: Ladder, cap: int | None) -> Ladder:
    """One bounded move from `previous` toward `target` (at most MAX_MOVE P, the step at most 1), inside the cap."""
    if previous is None:
        moved = target
    else:

        def toward(old: int, new: int, limit: int) -> int:
            return old + max(-limit, min(limit, new - old))

        moved = Ladder(
            toward(previous.start, target.start, MAX_MOVE),
            toward(previous.step, target.step, 1),
            toward(previous.walk, target.walk, MAX_MOVE),
        )
    walk = moved.walk if cap is None else min(cap, moved.walk)
    walk = max(1, walk)
    return Ladder(max(1, min(moved.start, walk)), max(1, moved.step), walk)


def evolve(
    curves: Mapping[Key, CurveStats],
    previous: Mapping[Key, LadderPolicy],
    rules: Guardrails,
    tick: int,
    replay: Mapping[Key, Mapping[str, Any]] | None = None,
    threads: Sequence[DealerThread] = (),
) -> dict[Key, LadderPolicy]:
    """This pass's policy for every buy class with a curve. A class without enough fills keeps no policy.
    `threads`: every dealer conversation in the feed, so the target is searched by replay."""
    from bazaar_agent.evals.dealers import price_class

    out: dict[Key, LadderPolicy] = {}
    for key, stats in sorted(curves.items()):
        cap = cap_for(stats.price_class, rules)
        if stats.price_class == "sell" or (cap is None and not stats.price_class.startswith("card:")):
            continue
        own = [t for t in threads if t.dealer == stats.dealer and t.side == "buy" and price_class(t.item) == key[1]]
        own = sorted(own, key=lambda t: (t.opened_tick, t.thread))[-MAX_SEARCH_THREADS:]  # the newest: cost and drift
        target, why = target_ladder(stats, cap, own)
        old = previous.get(key)
        if target is None and not why.startswith("skip"):
            continue
        ladder = None if target is None else bounded(old.ladder if old else None, target, cap)
        changed = old is None or old.ladder != ladder
        history = (old.history if old else ()) + (
            (
                {
                    "tick": tick,
                    "from": old.ladder.as_dict() if old and old.ladder else None,
                    "to": ladder.as_dict() if ladder else None,
                    "why": why,
                },
            )
            if changed
            else ()
        )
        out[key] = LadderPolicy(
            dealer=stats.dealer,
            price_class=stats.price_class,
            ladder=ladder,
            reason=why,
            fills=stats.fills,
            threads=stats.threads,
            patience=stats.patience or DEFAULT_PATIENCE,
            tick=tick if changed or old is None else old.tick,
            target=target,
            previous=old.ladder if old else None,
            evidence=stats.thread_ids,
            history=history[-HISTORY:],
            replay=(replay or {}).get(key, {}),
        )
    return out


def policies_from(learned: Sequence[Learning]) -> dict[Key, LadderPolicy]:
    out: dict[Key, LadderPolicy] = {}
    for lr in learned:
        policy = LadderPolicy.from_learning(lr)
        if policy is not None and (policy.key not in out or policy.tick >= out[policy.key].tick):
            out[policy.key] = policy
    return out
