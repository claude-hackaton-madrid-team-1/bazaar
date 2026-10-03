"""Jev as the duel player's decision model: code lists the LEGAL moves, Jev picks one, our limit authorizes.

Spec §3 step 4 and §7.1. Per live duel and tick:
  1. `legal_moves`: today's deterministic move (`duelist.duel_move`) plus the other moves that stay inside
     our own limit: accept only a rival offer strictly inside it after the worst-case cost of days,
     counter only strictly inside it after the worst-case cost of its own days, hold only while the deadline
     is not close. In the endgame an inside-limit offer is the only move (any deal beats none,
     `duel_endgame_ticks`).
  2. Jev `duel_move` (questions/duels.json) picks one. It is asked once per duel and round (cached), only
     with `min_budget_s` of the tick left, and for every live duel at once on worker threads, so a duel
     accept still lands inside the taker's duel grace.
  3. `choose`: Jev's pick only when it is a legal move (an early accept also needs `jev_can_accept_early`).
     `undecided`, a timeout, no budget, or an illegal pick keep today's move. Nothing blocks the tick.
In a two-issue session, `rival_cares_about_days` = yes puts the rival's own days on our counter when its
price stays strictly inside the limit after those days at our worst-case weight; otherwise days stay at
today's (`duelist.OUR_DAYS`, 0).

`DuelOutcomes` writes one outcome line per decided verdict when its duel leaves `/api/duels`.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from typing import Any

from bazaar_agent.agents.duel_v2 import V2Params, counter_offer, may_counter, plan_moves, value_of
from bazaar_agent.agents.duelist import (
    DuelMove,
    duel_deadline,
    duel_done,
    duel_id,
    duel_move,
    effective_price,
    inside_limit,
    worth,
)
from bazaar_agent.agents.jev_journal import JevJournal
from bazaar_agent.agents.runtime import JevAdvice, JevFn, no_jev

MOVE_QUESTION = "duel_move"
DAYS_QUESTION = "rival_cares_about_days"
LABELS = {"accept": "accept", "offer": "counter", "hold": "hold"}  # DuelMove.kind -> duel_move option
# An answer the model gave is kept for the round; a failure to get one is asked again next tick.
CACHED_REASONS = (None, "below_threshold")
VALUE_EPSILON = 0.01  # the state rounds values to cents


@dataclass(frozen=True)
class DuelJevConfig:
    min_budget_s: float = 4.0  # ask Jev only with this much of the tick left (jev_timeout_s is 3 s)
    max_calls_per_tick: int = 12  # questions per tick across every live duel (6 duels × 2 questions)


@dataclass(frozen=True)
class DuelPick:
    """One duel's move this tick: today's default, the legal set, Jev's advice and what was chosen."""

    default: DuelMove
    move: DuelMove
    legal: tuple[str, ...]
    why: str
    advice: JevAdvice | None = None  # duel_move
    days: JevAdvice | None = None  # rival_cares_about_days (two-issue sessions)
    state: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- reading a duel


def two_issue(duel: Mapping[str, Any]) -> bool:
    return "days" in (duel.get("issues") or [])


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _limit_role(duel: Mapping[str, Any]) -> tuple[int, str] | None:
    limit, role = duel.get("your_limit"), duel.get("role")
    if isinstance(limit, int) and not isinstance(limit, bool) and role in ("seller", "buyer"):
        return limit, str(role)
    return None


def _offer(duel: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    offer = duel.get(key)
    if not isinstance(offer, dict) or _number(offer.get("price")) is None:
        return None
    return {"price": int(offer["price"]), "days": offer.get("days")}


def ticks_left(duel: Mapping[str, Any], tick: int) -> int | None:
    deadline = duel_deadline(duel)
    return None if deadline is None else deadline - tick


def kept_share(decay: float, rounds: int) -> float:
    """The share of a deal's value left after `rounds` rounds of talk (estimated: the scorer's curve is private)."""
    return round(max(0.0, 1.0 - decay) ** max(0, rounds), 4)


def surplus(worth: float, limit: int, role: str) -> float:
    return worth - limit if role == "seller" else limit - worth


def own_worth(duel: Mapping[str, Any], price: int, days: object) -> float | None:
    """One of OUR offers at the worst-case cost of its days (as `effective_price` values the rival's)."""
    return worth(duel, price, days)


def _rival_history(duel: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The rival's structured offers so far. Their words are left out: untrusted, and they never bind."""
    rival = duel.get("rival")
    return [
        {"tick": m.get("tick"), "price": m.get("price"), "days": m.get("days")}
        for m in duel.get("messages") or []
        if isinstance(m, dict) and m.get("from") == rival and _number(m.get("price")) is not None
    ]


def round_key(duel: Mapping[str, Any]) -> tuple[object, ...]:
    """What makes a new round: the rounds count and both standing offers. Jev is asked once per key."""
    rival, ours = duel.get("rival_offer") or {}, duel.get("your_offer") or {}
    return (
        duel.get("rounds"),
        rival.get("id"),
        rival.get("price"),
        rival.get("days"),
        ours.get("price"),
        ours.get("days"),
    )


# ---------------------------------------------------------------- the legal moves and the choice


def accept_move(duel: Mapping[str, Any]) -> DuelMove | None:
    """Accepting the rival's standing offer, when it is strictly inside our limit after days. Else None."""
    limit_role, rival = _limit_role(duel), _offer(duel, "rival_offer")
    if limit_role is None or rival is None:
        return None
    worth = effective_price(dict(duel), rival["price"])
    if worth is None or not inside_limit(round(worth), *limit_role):
        return None
    return DuelMove("accept", rival["price"], reason="inside our limit")


def _dominated(
    duel: Mapping[str, Any], counter: DuelMove, accept: DuelMove, role: str, signed: bool = False, v2: bool = False
) -> bool:
    """True when the rival's standing offer is already at least as good for us as our own counter."""
    rival_days = (duel.get("rival_offer") or {}).get("days")
    rival = value_of(duel, int(accept.price or 0), rival_days, signed, v2)
    ours = value_of(duel, int(counter.price or 0), counter.days, signed, v2)
    if rival is None or ours is None:
        return False
    return rival >= ours if role == "seller" else rival <= ours


def legal_moves(
    duel: Mapping[str, Any],
    tick: int,
    default: DuelMove,
    counter: DuelMove,
    endgame_ticks: int,
    v2: V2Params | None = None,
) -> dict[str, DuelMove]:
    """The moves that stay inside our limit, by `duel_move` option. Today's move stands for its own option.
    A counter that asks less than the rival already offers (a seller) or more (a buyer) is dominated by
    accepting, so it is not a move. Under v2 (`duel_policy` = v2) an accept is legal only where the accept
    planner gave this duel the team's slot, and is then the only move (Jev cannot see the queue of accepts);
    a counter is legal only within v2's caps on our priced messages."""
    limit_role = _limit_role(duel)
    if limit_role is None or duel_done(duel):
        return {"hold": default}
    left = ticks_left(duel, tick)
    endgame = left is not None and left <= endgame_ticks
    accept = default if default.kind == "accept" else (None if v2 is not None else accept_move(duel))
    if accept is not None and (endgame or v2 is not None):  # v2: the planner timed it across every duel
        return {"accept": accept}
    moves: dict[str, DuelMove] = {} if accept is None else {"accept": accept}
    offer = default if default.kind == "offer" else counter
    signed = v2 is not None and v2.days_signed
    price, days = offer.price, offer.days
    worth = value_of(duel, price, days, signed, v2 is not None) if offer.kind == "offer" and price is not None else None
    priced = worth is not None and inside_limit(worth, *limit_role)  # after the cost of our days
    within_caps = v2 is None or default.kind == "offer" or may_counter(duel, v2)
    rival = accept or accept_move(duel)
    dominated = rival is not None and _dominated(duel, offer, rival, limit_role[1], signed, v2 is not None)
    if priced and within_caps and not dominated:
        moves["counter"] = offer
    if not endgame:
        moves["hold"] = DuelMove("hold", reason="wait for the rival's answer")
    return moves


def choose(
    default: DuelMove, legal: Mapping[str, DuelMove], advice: JevAdvice | None, can_accept_early: bool
) -> tuple[DuelMove, str]:
    """Jev's pick when it is legal; today's move otherwise. `undecided` is never a yes."""
    if advice is None:
        return default, "jev not asked"
    if not advice.decided:
        return default, f"jev undecided ({advice.reason or 'no reason'}): today's move"
    pick = legal.get(advice.verdict)
    if pick is None:
        return default, f"jev {advice.verdict} is not a legal move here: today's move"
    if pick.kind == "accept" and default.kind != "accept" and not can_accept_early:
        return default, "jev accept, but jev_can_accept_early = false: today's move"
    return pick, f"jev {advice.verdict} ({advice.value:.2f})"


def with_rival_days(move: DuelMove, duel: Mapping[str, Any], days: JevAdvice | None) -> tuple[DuelMove, str]:
    """On a yes to `rival_cares_about_days`, give the rival its own days, if our price still holds after them."""
    if days is None or days.verdict != "yes" or move.kind != "offer" or move.price is None:
        return move, ""
    rival, limit_role = _offer(duel, "rival_offer"), _limit_role(duel)
    if rival is None or limit_role is None or _number(rival.get("days")) is None:
        return move, ""
    their_days = int(rival["days"])
    worth = own_worth(duel, move.price, their_days)
    if worth is None or not inside_limit(worth, *limit_role):
        return move, f"; kept days {move.days}: the rival's {their_days} would cross our limit"
    reason = f"{move.reason}; days {their_days}: the rival cares about days (jev {days.value:.2f})"
    return replace(move, days=their_days, reason=reason), f"; days → {their_days}"


def duel_state(
    duel: Mapping[str, Any], tick: int, legal: Mapping[str, DuelMove], default: DuelMove, counter: DuelMove
) -> dict[str, Any]:
    """What Jev reads for `duel_move` and `rival_cares_about_days`: numbers only, never the rival's words."""
    limit, role = _limit_role(duel) or (0, "")
    decay = _number(duel.get("decay_per_round")) or 0.0
    rounds = int(_number(duel.get("rounds")) or 0)
    rival = _offer(duel, "rival_offer")
    worth = effective_price(dict(duel), rival["price"]) if rival is not None else None
    on_table = surplus(worth, limit, role) if worth is not None else None
    take_now = max(0.0, on_table) * kept_share(decay, rounds) if on_table is not None else 0.0
    next_offer = counter if counter.kind == "offer" else default
    next_worth = own_worth(duel, next_offer.price, next_offer.days) if next_offer.price is not None else None
    if_they_take = surplus(next_worth, limit, role) * kept_share(decay, rounds + 1) if next_worth is not None else None
    history = _rival_history(duel)
    return {
        "duel": {
            "item": duel.get("item"),
            "role": role,
            "our_limit": limit,
            "limit_meaning": duel.get("limit_meaning"),
            "issues": duel.get("issues"),
            "rival_last_offer": rival,
            "effective_rival_price": worth,
            "surplus_if_we_accept_now": on_table,
            "our_last_offer": _offer(duel, "your_offer"),
            "our_next_offer": {"price": next_offer.price, "days": next_offer.days},
            "rounds_used": rounds,
            "ticks_left": ticks_left(duel, tick),
            "decay_per_round": decay,
            "kept_share_now": kept_share(decay, rounds),
            "kept_share_after_one_more_round": kept_share(decay, rounds + 1),
            "pie_estimate": {
                "value_if_we_accept_now": round(take_now, 2),
                "value_if_rival_takes_our_next_offer": None if if_they_take is None else round(if_they_take, 2),
            },
            "rival_price_history": [h["price"] for h in history],
            "rival_offer_history": history,
            "your_days_weight": duel.get("your_days_weight"),
            "days_meaning": duel.get("days_meaning"),
            "legal_moves": list(legal),
            "default_move": LABELS[default.kind],
        }
    }


# ---------------------------------------------------------------- outcomes for calibration


@dataclass(frozen=True)
class _Pending:
    digest: str
    choice: str
    role: str
    limit: int
    on_table: float  # the value we could have locked in by accepting when Jev was asked (0 outside the limit)


@dataclass(frozen=True)
class DuelResult:
    status: str  # "deal" | "no_deal" | "unknown"
    worth: float | None
    rounds: int
    note: str


def duel_result(last: Mapping[str, Any], gone_tick: int, accepted: int | None) -> DuelResult:
    """How a duel that left `/api/duels` ended. Explicit fields first; else inferred from what we sent."""
    rounds = int(_number(last.get("rounds")) or 0)
    status, result = last.get("status"), last.get("result")
    final = _number(last.get("price"))
    if "no_deal" in (status, result):
        return DuelResult("no_deal", None, rounds, "no deal (duel payload)")
    if "deal" in (status, result) and final is not None:
        return DuelResult("deal", own_worth(last, int(final), last.get("days")), rounds, f"deal at {final:g} (payload)")
    if accepted is not None:
        return DuelResult("deal", effective_price(dict(last), accepted), rounds, f"we accepted {accepted}")
    deadline, ours = duel_deadline(last), _offer(last, "your_offer")
    if deadline is not None and gone_tick >= deadline:
        return DuelResult("no_deal", None, rounds, "gone at its deadline (inferred no deal)")
    if deadline is not None and ours is not None:
        worth = own_worth(last, ours["price"], ours.get("days"))
        return DuelResult(
            "deal", worth, rounds, f"gone before its deadline: inferred the rival took our {ours['price']}"
        )
    return DuelResult("unknown", None, rounds, "ended without a readable result")


def verdict_outcome(p: _Pending, result: DuelResult, decay: float) -> str:
    """Compare what the duel realised with what accepting locked in when Jev was asked (after decay).
    accept: right unless the end beat it. counter/hold: right when the end matched or beat it."""
    if result.status == "unknown" or (result.status == "deal" and result.worth is None):
        return "unknown"
    realised = 0.0
    if result.status == "deal" and result.worth is not None:
        realised = max(0.0, surplus(result.worth, p.limit, p.role)) * kept_share(decay, result.rounds)
    beaten = realised > p.on_table + VALUE_EPSILON  # the end was better than accepting then
    if p.choice == "accept":
        return "wrong" if beaten else ("right" if p.on_table > 0 else "unknown")
    if realised < p.on_table - VALUE_EPSILON:
        return "wrong"
    return "right" if realised > 0 else "unknown"


class DuelOutcomes:
    """Decided verdicts per duel, settled into outcome lines once the duel leaves `/api/duels`."""

    def __init__(self, journal: JevJournal | None) -> None:
        self.journal = journal
        self._pending: dict[int, dict[str, _Pending]] = {}
        self._last: dict[int, dict[str, Any]] = {}
        self._accepted: dict[int, int] = {}

    def decided(self, did: int, duel: Mapping[str, Any], pick: DuelPick) -> None:
        self._last[did] = dict(duel)
        advice = pick.advice
        limit_role = _limit_role(duel)
        if self.journal is None or advice is None or not advice.decided or not advice.digest or limit_role is None:
            return
        on_table = pick.state.get("duel", {}).get("pie_estimate", {}).get("value_if_we_accept_now") or 0.0
        pending = _Pending(advice.digest, advice.verdict, limit_role[1], limit_role[0], float(on_table))
        self._pending.setdefault(did, {}).setdefault(advice.digest, pending)

    def accepted(self, did: int, price: int) -> None:
        """Our accept for this duel went through (it settles next tick)."""
        self._accepted[did] = price

    def settle(self, live: Iterable[int], tick: int) -> list[str]:
        """Outcome lines for every tracked duel no longer live; returns one summary per duel."""
        live_ids = set(live)
        lines = []
        for did in [d for d in self._last if d not in live_ids or duel_done(self._last[d])]:
            last = self._last.pop(did)
            pending = self._pending.pop(did, {})
            result = duel_result(last, tick, self._accepted.pop(did, None))
            decay = _number(last.get("decay_per_round")) or 0.0
            for p in pending.values():
                outcome = verdict_outcome(p, result, decay)
                if self.journal is not None:
                    self.journal.outcome(p.digest, outcome, MOVE_QUESTION, f"duel {did}: {result.note}")
                lines.append(f"duel {did}: jev {p.choice} was {outcome} ({result.note})")
        return lines


# ---------------------------------------------------------------- the player's Jev


class DuelJev:
    """Asks Jev about every live duel at once each tick and returns each duel's chosen move."""

    def __init__(
        self,
        move_fn: JevFn,
        days_fn: JevFn = no_jev,
        *,
        can_accept_early: bool = True,
        journal: JevJournal | None = None,
        config: DuelJevConfig | None = None,
    ) -> None:
        self.move_fn, self.days_fn, self.can_accept_early = move_fn, days_fn, can_accept_early
        self.config = config or DuelJevConfig()
        self.outcomes = DuelOutcomes(journal)
        self._cache: dict[tuple[int, str, tuple[object, ...]], JevAdvice] = {}

    def pick(
        self,
        duels: Iterable[dict[str, Any]],
        tick: int,
        first_seen: Mapping[int, int],
        *,
        anchor: float,
        floor: float,
        endgame_ticks: int,
        left: Callable[[], float],
        v2: V2Params | None = None,
        slots: int = 1,
    ) -> dict[int, DuelPick]:
        """Each live duel's move this tick (keyed by duel id). `left` is the seconds left in the tick.
        With `v2` (`duel_policy` = v2), today's move is `duel_v2.plan_moves` across every duel at once."""
        duels = list(duels)
        planned = plan_moves(duels, tick, first_seen, v2, slots) if v2 is not None else {}
        plans: dict[int, tuple[dict[str, Any], DuelMove, dict[str, DuelMove], dict[str, Any]]] = {}
        for d in duels:
            did = duel_id(d)
            if did is None:
                continue
            start = first_seen.get(did, tick)
            if v2 is not None:
                default, counter = planned[did], counter_offer(d, tick, start, v2)
            else:
                default = duel_move(d, tick, start, anchor=anchor, floor=floor, endgame_ticks=endgame_ticks)
                no_rival = {**d, "rival_offer": None}
                counter = duel_move(no_rival, tick, start, anchor=anchor, floor=floor, endgame_ticks=0)
            legal = legal_moves(d, tick, default, counter, endgame_ticks, v2)
            plans[did] = (d, default, legal, duel_state(d, tick, legal, default, counter))
        self._cache = {key: advice for key, advice in self._cache.items() if key[0] in plans}  # live duels only
        answers = self._ask(self._questions(plans), left)
        picks = {}
        for did, (d, default, legal, state) in plans.items():
            advice, days = answers.get((did, MOVE_QUESTION)), answers.get((did, DAYS_QUESTION))
            move, why = choose(default, legal, advice, self.can_accept_early)
            move, days_why = with_rival_days(move, d, days)
            picks[did] = DuelPick(default, move, tuple(legal), why + days_why, advice, days, state)
            self.outcomes.decided(did, d, picks[did])
        return picks

    def _questions(
        self, plans: Mapping[int, tuple[dict[str, Any], DuelMove, dict[str, DuelMove], dict[str, Any]]]
    ) -> list[tuple[int, str, JevFn, dict[str, Any], tuple[object, ...]]]:
        asks = []
        for did, (d, _, legal, state) in plans.items():
            if len(legal) > 1 and self.move_fn is not no_jev:
                asks.append((did, MOVE_QUESTION, self.move_fn, state, round_key(d)))
            if two_issue(d) and "counter" in legal and self.days_fn is not no_jev:
                asks.append((did, DAYS_QUESTION, self.days_fn, state, round_key(d)))
        return asks

    def _ask(
        self, asks: list[tuple[int, str, JevFn, dict[str, Any], tuple[object, ...]]], left: Callable[[], float]
    ) -> dict[tuple[int, str], JevAdvice]:
        answers: dict[tuple[int, str], JevAdvice] = {}
        todo: list[tuple[int, str, JevFn, dict[str, Any], tuple[object, ...]]] = []
        budget_ok = left() >= self.config.min_budget_s
        for ask in asks:
            did, question, _, _, key = ask
            cached = self._cache.get((did, question, key))
            if cached is not None:
                answers[(did, question)] = cached
            elif budget_ok and len(todo) < self.config.max_calls_per_tick:
                todo.append(ask)
            else:
                answers[(did, question)] = JevAdvice("undecided", 0.0, reason="no tick budget for jev")
        for (did, question, _, _, key), advice in zip(todo, self._run(todo, left), strict=True):
            answers[(did, question)] = advice
            if advice.reason in CACHED_REASONS:
                self._cache[(did, question, key)] = advice
        return answers

    @staticmethod
    def _run(
        todo: list[tuple[int, str, JevFn, dict[str, Any], tuple[object, ...]]], left: Callable[[], float]
    ) -> list[JevAdvice]:
        """Every call at once; each worker keeps the tick's trace context. A late answer is undecided."""
        if not todo:
            return []
        pool = ThreadPoolExecutor(max_workers=len(todo), thread_name_prefix="duel-jev")
        futures: list[Future[JevAdvice]] = [
            pool.submit(contextvars.copy_context().run, fn, state) for _, _, fn, state, _ in todo
        ]
        wait(futures, timeout=max(0.0, left()))
        pool.shutdown(wait=False, cancel_futures=True)
        late = JevAdvice("undecided", 0.0, reason="jev answered after the tick budget")
        return [f.result() if f.done() and not f.cancelled() and f.exception() is None else late for f in futures]
