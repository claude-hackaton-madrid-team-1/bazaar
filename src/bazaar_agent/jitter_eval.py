"""B12: what a seeded step jitter (`dealer.StepJitter`) costs at the dealer, and what it hides from a rival.

Our dealer bids are public in the feed. Today's ladder is `start, start + 1, ...`, so anyone reading it
knows our next price. A jitter makes the steps random within bounds; this module measures both sides:

- **Cost.** Our real `decide()` with a jittered plan against the dealers W3 fitted to every team's Friday
  threads (`ladder_replay`), against the same dealers under the stricter rule B12 states (`StepCapped`:
  the dealer never moves faster than our last step, and a step below its minimum earns nothing), and on
  the real threads replayed (`ladder_replay.episode_from`). Share, deal rate and fill as `ladder_replay`.
- **Predictability.** A rival that has read our past threads of the same class predicts each next bid
  from the bids it has seen in this thread (`predictability`). Its exact-hit rate is 1.0 on today's
  ladder; the lower, the less a reader of the feed knows about our next price.

Pure functions with explicit seeds, so every number is reproducible.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from statistics import mean

from bazaar_agent.agents.dealer import BidPlan, Negotiation, StepJitter, decide, make_jitter
from bazaar_agent.ladder import Conversation
from bazaar_agent.ladder_replay import DealerModel, Episode, Result, Summary, draw, episode_from, play, summarise

# The dealer's minimum move, 2 % of book (B12), with book = the dealer's list price per class
# (`GET /api/dealers`, Friday; El Chato from the #55 simulator's dealers.json).
LIST_PRICE = {
    ("abuela", "card:common"): 10,
    ("abuela", "card:uncommon"): 25,
    ("abuela", "pack:sobre_barrio"): 26,
    ("chato", "card:uncommon"): 30,
    ("chato", "card:rare"): 90,
}
MIN_STEP_PCT = 0.02


def dealer_min_step(dealer: str, price_class: str, pct: float = MIN_STEP_PCT) -> int:
    return max(1, math.ceil(pct * LIST_PRICE.get((dealer, price_class), 0) - 1e-9))


@dataclass(frozen=True)
class Level:
    """One jitter setting (the `dealer_jitter_*` knobs of STRATEGY.md)."""

    name: str
    start_spread: int = 0
    jump_share: float = 0.0
    band_jump_share: float = 0.0
    jump_max: int = 3
    min_step_pct: float = MIN_STEP_PCT
    band_gap: int = 0
    force: bool = False  # build the jitter even with every share at 0: the minimum step alone

    def plan(self, plan: BidPlan, seed: int) -> BidPlan:
        """`plan` with this level's jitter drawn from `seed`, built as the runtime builds it
        (`dealer.make_jitter`); unchanged when the level is off."""

        def build(spread: int) -> StepJitter | None:
            return make_jitter(
                start_spread=spread,
                jump_share=self.jump_share,
                band_jump_share=self.band_jump_share,
                jump_max=self.jump_max,
                band_gap=self.band_gap,
                min_step_pct=self.min_step_pct,
                seed=seed,
                max_price=plan.max_price,
            )

        jitter = build(self.start_spread)
        if jitter is None and self.force:  # the minimum step alone: built on, then the spread taken back
            on = build(1)
            jitter = None if on is None else replace(on, start_spread=0)
        return plan if jitter is None else replace(plan, jitter=jitter)


OFF = Level("off")


class StepCapped:
    """W3's fitted dealer (`ladder_replay._Dealer`) under B12's rule: it never concedes more than our
    last step, and a step below `min_step` earns no concession at all (a bid that reaches its limit is
    still taken). Same interface: `ask`, `final`, `offer_id`, `answer(bid)`."""

    def __init__(self, ep: Episode, min_step: int) -> None:
        self.ep, self.min_step = ep, min_step
        self.ask: int | None = ep.opening if ep.opens_first else None
        self.final = False
        self.bids = 0
        self.counters = 0
        self.last_bid: int | None = None
        self.offer_id = 1

    def answer(self, bid: int) -> str | None:
        ep, first = self.ep, self.last_bid is None
        step = 0 if self.last_bid is None else bid - self.last_bid
        self.bids += 1
        self.last_bid = bid
        if bid >= ep.limit or (self.ask is not None and bid >= self.ask):
            return "deal"
        if self.final:
            return "walk"
        if self.ask is None:
            self.ask = ep.opening
        else:
            self.counters += 1
            if ep.matches_moves:
                give = 0 if self.counters <= 1 else (step if step >= 4 else 1)
            elif self.counters == 1:
                give = ep.first_drop
            else:
                give = ep.later_drops[(self.counters - 2) % len(ep.later_drops)]
            if not first:  # our first price is a move from nothing: her first counter to it is not capped
                give = min(give, step) if step >= self.min_step else 0
            self.ask = max(ep.limit, self.ask - give)
        if self.bids >= ep.patience:
            self.final = True
        self.offer_id += 1
        return None


def play_capped(
    plan: BidPlan,
    ep: Episode,
    min_step: int,
    *,
    max_ticks: int = 14,
    reply_lag: int = 0,
    asks: list[int | None] | None = None,
) -> Result:
    """`ladder_replay.play` against a `StepCapped` dealer (same loop, same tick accounting). `asks`, when
    given, gets her standing ask (public in the feed) as each of our bids is sent."""
    neg, dealer = Negotiation(plan), StepCapped(ep, min_step)
    tick = 0
    while tick < max_ticks:
        tick += 1
        offer = dealer.offer_id if dealer.ask is not None else None
        move = decide(neg, dealer.ask, offer, dealer.final)
        if move.kind == "accept" and move.price is not None:
            how = "her_final" if dealer.final else "her_ask"
            return Result(move.price, tick + 1, tuple(neg.bids), ep.limit, ep.opening, how)
        if move.kind == "walk":
            return Result(None, tick, tuple(neg.bids), ep.limit, ep.opening, "walk")
        if move.kind == "bid" and move.price is not None:
            if asks is not None:
                asks.append(dealer.ask)
            neg.bids.append(move.price)
            verdict = dealer.answer(move.price)
            tick += reply_lag
            if verdict == "deal":
                return Result(move.price, tick + 1, tuple(neg.bids), ep.limit, ep.opening, "our_bid")
            if verdict == "walk":
                return Result(None, tick, tuple(neg.bids), ep.limit, ep.opening, "walk")
    return Result(None, max_ticks, tuple(neg.bids), ep.limit, ep.opening, "timeout")


Rule = str  # "w3": the dealer as fitted; "capped": `StepCapped`


def run(
    plan: BidPlan,
    level: Level,
    episodes: Sequence[Episode],
    *,
    rule: Rule = "w3",
    min_step: int = 1,
    seed: int = 0,
    max_ticks: int = 14,
    reply_lag: int = 0,
) -> list[Result]:
    """One conversation per episode, each with its own jitter draw (as each thread has its own salt)."""
    out = []
    for i, ep in enumerate(episodes):
        p = level.plan(plan, seed * 1_000_003 + i + 1)
        if rule == "capped":
            out.append(play_capped(p, ep, min_step, max_ticks=max_ticks, reply_lag=reply_lag))
        else:
            out.append(play(p, ep, max_ticks=max_ticks, reply_lag=reply_lag))
    return out


def episodes(model: DealerModel, n: int, seed: int) -> list[Episode]:
    rng = random.Random(seed)
    return [draw(model, rng) for _ in range(n)]


# ---------------------------------------------------------------- predictability


@dataclass(frozen=True)
class Predictability:
    """How well a rival that read our past threads predicts our next bid in a new one."""

    predicted: int  # bids predicted (every bid after a thread's first)
    hit_rate: float  # exact next price
    mae: float  # mean absolute error in primas
    first_hit_rate: float  # our opening bid, predicted as the most common opening seen
    step_entropy_bits: float  # of the next step given what the rival conditions on, averaged
    overall_hit_rate: float  # every bid, the opening one included

    def as_dict(self) -> dict[str, float | int]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


Key = tuple[str, int, int]  # (class, bid index, last bid)


def _contexts(seq: Sequence[int]) -> Iterable[tuple[int, int, int]]:
    """(index, last bid, next bid) for every bid after the first."""
    for i in range(1, len(seq)):
        yield i, seq[i - 1], seq[i]


def _gap(asks: Sequence[Sequence[int | None]] | None, thread: int, i: int, last: int) -> int | None:
    """Her standing ask minus our last bid when bid `i` went out, capped at 6 (None: not read)."""
    if asks is None:
        return None
    ask = asks[thread][i] if i < len(asks[thread]) else None
    return -1 if ask is None else min(6, ask - last)


def predictability(
    train: Sequence[tuple[str, Sequence[int]]],
    test: Sequence[tuple[str, Sequence[int]]],
    *,
    train_asks: Sequence[Sequence[int | None]] | None = None,
    test_asks: Sequence[Sequence[int | None]] | None = None,
) -> Predictability:
    """A rival learns, per price class, the next bid after (bid index, last bid) from `train` (our past
    threads in the feed), backing off to (last bid) and then to the most common step, and predicts every
    bid of `test`. That is the best a reader of our bids can do against a stationary policy; on a fixed
    ladder it is always right. With `*_asks` (her standing ask as each bid went out, aligned with the
    threads) the rival also conditions on how far her ask stood above our last bid, as a reader of the
    whole feed can: a `band_gap` jitter jumps only when that gap is wide."""
    by_ask: dict[tuple[str, int, int, int | None], Counter[int]] = defaultdict(Counter)
    by_full: dict[Key, Counter[int]] = defaultdict(Counter)
    by_last: dict[tuple[str, int], Counter[int]] = defaultdict(Counter)
    steps: dict[str, Counter[int]] = defaultdict(Counter)
    firsts: dict[str, Counter[int]] = defaultdict(Counter)
    for t, (cls, seq) in enumerate(train):
        if seq:
            firsts[cls][seq[0]] += 1
        for i, last, nxt in _contexts(seq):
            if train_asks is not None:
                by_ask[(cls, i, last, _gap(train_asks, t, i, last))][nxt] += 1
            by_full[(cls, i, last)][nxt] += 1
            by_last[(cls, last)][nxt] += 1
            steps[cls][nxt - last] += 1
    hits = n = first_hits = first_n = 0
    err = 0.0
    entropies: list[float] = []
    for t, (cls, seq) in enumerate(test):
        if not seq:
            continue
        first_n += 1
        first_hits += bool(firsts[cls]) and firsts[cls].most_common(1)[0][0] == seq[0]
        for i, last, nxt in _contexts(seq):
            seen = by_ask.get((cls, i, last, _gap(test_asks, t, i, last))) if test_asks is not None else None
            counts = seen or by_full.get((cls, i, last)) or by_last.get((cls, last))
            if counts:
                guess = counts.most_common(1)[0][0]
            else:
                guess = last + (steps[cls].most_common(1)[0][0] if steps[cls] else 1)
            n += 1
            hits += guess == nxt
            err += abs(guess - nxt)
            entropies.append(_entropy(counts) if counts else 0.0)
    return Predictability(
        predicted=n,
        hit_rate=hits / n if n else 1.0,
        mae=err / n if n else 0.0,
        first_hit_rate=first_hits / first_n if first_n else 1.0,
        step_entropy_bits=mean(entropies) if entropies else 0.0,
        overall_hit_rate=(hits + first_hits) / (n + first_n) if n + first_n else 1.0,
    )


def _entropy(counts: Counter[int]) -> float:
    total = sum(counts.values())
    return -sum(c / total * math.log2(c / total) for c in counts.values() if c)


def silent_sequences(plan: BidPlan, level: Level, n: int, seed: int) -> list[list[int]]:
    """Every bid we would send to a dealer that never answers: the whole ladder a rival can learn from."""
    out = []
    for i in range(n):
        neg = Negotiation(level.plan(plan, seed * 1_000_003 + i + 1))
        while (move := decide(neg, None, None, False)).kind == "bid" and move.price is not None:
            neg.bids.append(move.price)
        out.append(list(neg.bids))
    return out


# ---------------------------------------------------------------- the grid


@dataclass(frozen=True)
class Cell:
    """One (class, plan, level, dealer rule) of the grid."""

    dealer: str
    price_class: str
    plan: tuple[int, int, int]
    level: str
    rule: Rule
    summary: Summary
    lagged: Summary  # the same conversations when every round costs two ticks
    mean_bids: float
    predict: Predictability

    def ratio(self, base: Cell, *, lagged: bool = False) -> float:
        mine, theirs = (self.lagged, base.lagged) if lagged else (self.summary, base.summary)
        return mine.mean_share / theirs.mean_share if theirs.mean_share else 1.0  # nothing to lose


def replay_episodes(convs: Iterable[Conversation], dealer: str, price_class: str, at: str) -> list[Episode]:
    """Every real buy conversation of this dealer and class, rebuilt with its own counters and patience
    and its limit at the top (`hi`) or the bottom (`lo`) of its bracket (`ladder_replay.episode_from`)."""
    mine = [c for c in convs if c.dealer == dealer and c.price_class == price_class and c.side == "buy"]
    return [ep for c in mine if (ep := episode_from(c, at=at)) is not None]


def evaluate(
    dealer: str,
    price_class: str,
    plan: BidPlan,
    eps: Sequence[Episode],
    levels: Sequence[Level],
    *,
    rules: Sequence[Rule] = ("w3", "capped"),
    seed: int = 0,
    within: int = 8,
    on_cell: Callable[[Cell], None] | None = None,
) -> list[Cell]:
    """Every level against the same episodes (common random numbers), per dealer rule. `eps` is a
    fitted model's draws (`episodes`) or the real threads replayed (`replay_episodes`)."""
    min_step = dealer_min_step(dealer, price_class)
    out = []
    for rule in rules:
        for level in levels:
            results = run(plan, level, eps, rule=rule, min_step=min_step, seed=seed)
            lagged = run(plan, level, eps, rule=rule, min_step=min_step, seed=seed, reply_lag=1)
            half = len(results) // 2
            seqs = [(price_class, list(r.bids)) for r in results]
            cell = Cell(
                dealer,
                price_class,
                (plan.start, plan.step, plan.max_price),
                level.name,
                rule,
                summarise(results, within=within),
                summarise(lagged, within=within),
                mean(len(r.bids) for r in results),
                predictability(seqs[:half], seqs[half:]),
            )
            out.append(cell)
            if on_cell is not None:
                on_cell(cell)
    return out
