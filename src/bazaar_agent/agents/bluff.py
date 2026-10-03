"""Strategic bluffing (N16): which tactic each message uses, learned per counterparty.

Omar (2026-10-03): our agent may lie whenever it wins the card and the points. RULES.md: "Words persuade,
structure binds. Your agent may say anything." So a tactic lives ONLY in the words: the caller decides the
move (price, days, accept) and checks it with `guardrails.check()` exactly as before, then asks the book which
tactic carries it. `Choice.words()` wraps the usual `WordsFn`: it never sees our limit, never changes the price,
and falls back to the usual words whenever the tactic does not fit. An accept never asks for a tactic.

The chooser is a deterministic UCB1 bandit per counterparty (a dealer, a duel rival, a team) over the `tactic`
lessons in the N3 store: an untried tactic first (the one that did best with others of its kind first), then
the best mean reward plus an exploration bonus. Ties go to a seeded hash of (counterparty, conversation, step,
tactic), so a given seed and history always give the same pick.

One lesson per scored message (the counterparty's next move) or conversation end:
toward us +1 · held 0 · away −0.5 · deal +1 (+0.5 within 3 of our messages: rounds saved) · they walked −1 ·
a duel with no deal −0.5 · a cooloff, a strike or a flag on our message −10, and that tactic is off for that
counterparty for the rest of the game day (`clock.round`); two penalties in a day mute every tactic to it;
≥ 3 tries today with a mean ≤ 0 (no gain) turn that tactic off for the day.

Kill switches: BAZAAR_BLUFF=0 (env) and `bluff_enabled = false` (GUARDRAILS.md): today's words, no tactic.
Nothing here does I/O except `load()` and `flush()`, which the agents call after the tick's sends.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from bazaar_agent.agents.tactics import BY_ID, CounterpartyKind, Side, eligible, leaks, render
from bazaar_agent.agents.words import WordsFn, WordsRequest
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.learn.model import SUBJECT_PATTERN, Learning

ENV = "BAZAAR_BLUFF"
OFF_VALUES = frozenset({"0", "false", "off", "no"})
REWARDS = {"toward": 1.0, "held": 0.0, "away": -0.5, "deal": 1.0, "walked": -1.0, "no_deal": -0.5}
FAST_BONUS = 0.5  # a deal within FAST_MESSAGES of our tactic messages: rounds saved
FAST_MESSAGES = 3
PENALTY = -10.0
PENALTY_RESULTS = frozenset({"cooloff", "strike", "flag"})
NO_GAIN_TRIES = 3
MUTE_AFTER = 2  # penalties in one day before every tactic to that counterparty stops
EXPLORE = 1.0  # UCB1 exploration weight
RECENT_TICKS = 5  # a cooloff or strike this soon after a tactic message is blamed on that message
LOAD_LIMIT = 2000
LOAD_EVERY = 5  # ticks between reads of the other processes' tactic lessons (our own are in memory at once)
SEEN_EVENTS_MAX = 20_000  # feed event ids remembered against double counting (the window is 500)
CONFIDENCE = 0.8
_SUBJECT = re.compile(SUBJECT_PATTERN)
_SLUG = re.compile(r"[^A-Za-z0-9_.:\-]+")


def enabled(rules: Guardrails | None, env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """(on, why): both kill switches must allow it."""
    if rules is not None and not rules.bluff_enabled:
        return False, "bluff_enabled = false"
    if (env if env is not None else os.environ).get(ENV, "").strip().lower() in OFF_VALUES:
        return False, f"{ENV}=0"
    return True, "on"


def message_id(body: object) -> int | None:
    """Our message's id from the server's answer to a send (`message`, as the simulator answers; unverified on
    the real game, whose write answers are `Ok` objects): a flag on it is then matched to its tactic."""
    if not isinstance(body, Mapping):
        return None
    for key in ("message", "message_id"):
        value = body.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _slug(name: str) -> str:
    cleaned = _SLUG.sub("_", name.strip()).strip("_").lower()[:64]
    return cleaned if cleaned and _SUBJECT.fullmatch(cleaned) else "unknown"


@dataclass(frozen=True)
class Counterparty:
    kind: CounterpartyKind  # also the learning's subject_kind
    id: str

    @classmethod
    def dealer(cls, dealer_id: str) -> Counterparty:
        return cls("dealer", dealer_id if _SUBJECT.fullmatch(dealer_id) else _slug(dealer_id))

    @classmethod
    def rival(cls, alias: object, duel: int | None = None) -> Counterparty:
        """A duel rival by its alias ("Rival Plata" → rival_plata); the duel id when it has none."""
        if isinstance(alias, str) and alias.strip():
            return cls("rival", _slug(alias))
        return cls("rival", f"duel_{duel}" if duel is not None else "unknown")

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.id}"


@dataclass(frozen=True)
class Choice:
    """The tactic for one message, or none (`tactic` None: today's words)."""

    counterparty: Counterparty
    side: Side
    conversation: str  # "thread:123" | "duel:45"
    step: int  # how many messages we already sent in this conversation
    price: int  # the structured price this message carries (decided before the choice)
    tactic: str | None
    reason: str  # private: why this tactic (or why none)
    avoid: frozenset[int] = frozenset()  # our private numbers: an invented number never equals one

    def inputs(self) -> dict[str, Any]:
        """For the decision row, under keys the public status view does not list (private)."""
        return {
            "tactic": self.tactic or "none",
            "tactic_counterparty": self.counterparty.label,
            "tactic_why": self.reason,
        }

    def words(self, base: WordsFn) -> WordsFn:
        """`base` with this tactic's text. Any mismatch (another price, a tactic that cannot render, a private
        number in the text) gives `base`'s words: the tactic never blocks or changes a message."""
        if self.tactic is None:
            return base
        tactic = self.tactic

        def say(request: WordsRequest) -> str:
            if request.price != self.price:
                return base(request)
            text = render(
                tactic,
                request.price,
                side=self.side,
                language=request.language,
                kind=self.counterparty.kind,
                counterparty=self.counterparty.id,
                avoid=self.avoid,
            )
            return text if text is not None and not leaks(text, self.avoid, request.price) else base(request)

        return say


@dataclass(frozen=True)
class _Sent:
    """A tactic message waiting for the counterparty's next move."""

    choice: Choice
    their_price: int | None
    their_offer: int | None
    tick: int
    day: int
    message: int | None


@dataclass
class Arm:
    """One tactic's record with one counterparty."""

    n: int = 0
    total: float = 0.0
    today_n: int = 0
    today_total: float = 0.0
    penalties_today: int = 0

    @property
    def mean(self) -> float:
        return self.total / self.n if self.n else 0.0

    def off_today(self) -> str | None:
        if self.penalties_today:
            return "a cooloff, strike or flag after it today"
        if self.today_n >= NO_GAIN_TRIES and self.today_total / self.today_n <= 0:
            return f"no gain in {self.today_n} tries today"
        return None


@dataclass
class _Agg:
    n: int = 0
    total: float = 0.0
    penalties: int = 0


def _tie(seed: int, cp: Counterparty, conversation: str, step: int, tactic: str) -> str:
    return hashlib.sha256(f"{seed}|{cp.label}|{conversation}|{step}|{tactic}".encode()).hexdigest()


def _reward(learning: Learning) -> tuple[str, float, int | None, str] | None:
    """(tactic, reward, day, result) of a tactic lesson; None for a row in another shape."""
    d = learning.detail
    tactic, reward = d.get("tactic"), d.get("reward")
    if learning.kind != "tactic" or tactic not in BY_ID or not isinstance(reward, int | float):
        return None
    day = d.get("day")
    return str(tactic), float(reward), day if isinstance(day, int) else None, str(d.get("result") or "")


@dataclass
class TacticBook:
    """Per process: picks tactics, scores what followed them, keeps the lessons. `store`: the N3
    `LearningStore` (None: this process's memory only)."""

    store: Any = None
    us: str | None = None
    seed: int = 0
    rules: Guardrails | None = None
    env: Mapping[str, str] | None = None
    log: Callable[[str], None] = lambda message: None
    day: int = 0
    tick: int = 0
    lessons: dict[str, Learning] = field(default_factory=dict)
    pending: dict[str, _Sent] = field(default_factory=dict)  # conversation -> its last unanswered message
    last: dict[str, _Sent] = field(default_factory=dict)  # conversation -> its last tactic message
    counts: dict[str, int] = field(default_factory=dict)  # conversation -> tactic messages sent
    recent: dict[str, str] = field(default_factory=dict)  # counterparty label -> its latest conversation
    messages: dict[int, tuple[Counterparty, str, str, int]] = field(default_factory=dict)  # our message ids
    unwritten: list[Learning] = field(default_factory=list)
    # running sums, kept as lessons arrive or are replaced, so a choice never rescans the history
    _per: dict[tuple[str, str], _Agg] = field(default_factory=dict)  # (counterparty label, tactic)
    _per_day: dict[tuple[str, str, int | None], _Agg] = field(default_factory=dict)  # ... and game day
    _per_kind: dict[tuple[str, str], _Agg] = field(default_factory=dict)  # (counterparty kind, tactic)
    _seen_events: set[int] = field(default_factory=set)
    _loaded_tick: int | None = None
    _failed: set[str] = field(default_factory=set)

    # ---------------------------------------------------------------- per tick

    def begin_tick(self, tick: int, day: int | None, us: str | None = None) -> None:
        self.tick = tick
        self.day = day if day is not None else self.day
        self.us = us or self.us

    def enabled(self) -> tuple[bool, str]:
        return enabled(self.rules, self.env)

    # ---------------------------------------------------------------- the choice

    def arms(self, cp: Counterparty) -> dict[str, Arm]:
        """Each tactic's record with this counterparty (only the tactics it has a lesson for)."""
        out: dict[str, Arm] = {}
        for tactic in BY_ID:
            every = self._per.get((cp.label, tactic))
            if every is None or every.n == 0:
                continue
            today = self._per_day.get((cp.label, tactic, self.day)) or _Agg()
            out[tactic] = Arm(every.n, every.total, today.n, today.total, today.penalties)
        return out

    def _prior(self, kind: str, tactic: str) -> float:
        """How this tactic did with every counterparty of the same kind: the order untried tactics are tried in."""
        agg = self._per_kind.get((kind, tactic))
        return agg.total / agg.n if agg is not None and agg.n else 0.0

    def choose(
        self,
        cp: Counterparty,
        side: Side,
        conversation: str,
        step: int,
        price: int,
        *,
        avoid: Iterable[int] = frozenset(),
    ) -> Choice:
        """The tactic for the message whose structured `price` the caller already decided. Pure: no I/O."""
        private = frozenset(avoid)
        on, why = self.enabled()
        if not on:
            return Choice(cp, side, conversation, step, price, None, f"bluff off ({why})", private)
        arms = self.arms(cp)
        penalties = sum(a.penalties_today for a in arms.values())
        if penalties >= MUTE_AFTER:
            return Choice(cp, side, conversation, step, price, None, f"muted today ({penalties} penalties)", private)
        fits = [
            t
            for t in eligible(cp.kind, cp.id, side)
            if render(t, price, side=side, language="es", kind=cp.kind, counterparty=cp.id, avoid=private) is not None
            and (t not in arms or arms[t].off_today() is None)
        ]
        if not fits:
            return Choice(cp, side, conversation, step, price, None, "no tactic left for this counterparty", private)
        total = sum(arms[t].n for t in fits if t in arms)
        just_used = self.last[conversation].choice.tactic if conversation in self.last else None

        def rank(t: str) -> tuple[int, int, float, str]:
            tie = _tie(self.seed, cp, conversation, step, t)
            if t not in arms or arms[t].n == 0:  # untried: the one still waiting for an answer goes last
                return (0, int(t == just_used), -self._prior(cp.kind, t), tie)
            arm = arms[t]
            return (1, 0, -(arm.mean + EXPLORE * math.sqrt(math.log(total + 1) / arm.n)), tie)

        best = min(fits, key=rank)
        arm = arms.get(best)
        reason = f"untried with {cp.id}" if arm is None or arm.n == 0 else f"mean {arm.mean:+.2f} over {arm.n}"
        return Choice(cp, side, conversation, step, price, best, reason, private)

    # ---------------------------------------------------------------- what followed

    def sent(
        self,
        choice: Choice,
        *,
        their_price: int | None,
        their_offer: int | None,
        tick: int,
        message: int | None = None,
    ) -> None:
        """A message went out. A tactic message waits for the counterparty's next move; one sent while the
        previous was still unanswered scores that one as held."""
        if choice.tactic is None:
            return
        conv = choice.conversation
        if conv in self.pending:
            self._score(self.pending.pop(conv), "held", REWARDS["held"], tick)
        sent = _Sent(choice, their_price, their_offer, tick, self.day, message)
        self.pending[conv] = self.last[conv] = sent
        self.counts[conv] = self.counts.get(conv, 0) + 1
        self.recent[choice.counterparty.label] = conv
        if message is not None:
            self.messages[message] = (choice.counterparty, choice.tactic, conv, choice.step)

    def observe(self, conversation: str, *, their_price: int | None, their_offer: int | None, tick: int) -> None:
        """The conversation as read this tick: a new counterparty offer scores our waiting tactic message."""
        sent = self.pending.get(conversation)
        if sent is None or their_offer is None or their_offer == sent.their_offer or their_price is None:
            return
        del self.pending[conversation]
        if sent.their_price is None:
            return  # nothing to compare with: no lesson
        moved = their_price - sent.their_price
        toward = moved < 0 if sent.choice.side == "buy" else moved > 0
        result = "held" if moved == 0 else "toward" if toward else "away"
        self._score(sent, result, REWARDS[result], tick, f"{sent.their_price}→{their_price}")

    def ended(self, conversation: str, *, status: str | None, closed_reason: str | None, tick: int) -> None:
        """The conversation closed. Its last tactic message takes the end: a deal, a walk, a cooloff."""
        self.pending.pop(conversation, None)
        last = self.last.pop(conversation, None)
        count = self.counts.pop(conversation, 0)
        if last is None:
            return
        if closed_reason == "cooloff":
            self._score(last, "cooloff", PENALTY, tick, suffix="penalty")
        elif status == "deal" or closed_reason == "deal":
            bonus = FAST_BONUS if count <= FAST_MESSAGES else 0.0
            self._score(last, "deal", REWARDS["deal"] + bonus, tick, suffix="end")
        elif closed_reason == "walked":
            self._score(last, "walked", REWARDS["walked"], tick, suffix="end")
        elif status == "no_deal":
            self._score(last, "no_deal", REWARDS["no_deal"], tick, suffix="end")

    def dropped(self, conversation: str) -> None:
        """We walked or timed out: our ladder ended it, so no lesson."""
        for book in (self.pending, self.last, self.counts):
            book.pop(conversation, None)

    def events(self, events: Iterable[Mapping[str, Any]], us: str | None, tick: int) -> None:
        """Feed events that punish a tactic: `persona.cooloff` / `persona.strike` for us soon after a tactic
        message to that dealer, and `flag.raised` on one of our tactic messages."""
        if len(self._seen_events) > SEEN_EVENTS_MAX:  # the newest half is plenty against a 500-event window
            self._seen_events = set(sorted(self._seen_events)[-SEEN_EVENTS_MAX // 2 :])
        for e in events:
            eid, payload = e.get("id"), e.get("payload")
            if not isinstance(eid, int) or eid in self._seen_events or not isinstance(payload, Mapping):
                continue
            self._seen_events.add(eid)
            kind = e.get("type")
            if kind in ("persona.cooloff", "persona.strike") and us and payload.get("team") == us:
                self._punish_dealer(str(payload.get("persona") or ""), kind.split(".")[1], tick)
            elif kind == "flag.raised":
                mid = payload.get("message", payload.get("message_id"))
                if isinstance(mid, int) and mid in self.messages:
                    cp, tactic, conv, step = self.messages[mid]
                    self._lesson(cp, tactic, conv, step, "flag", PENALTY, tick, self.day, "", f"{step}:flag", mid)

    def _punish_dealer(self, dealer: str, result: str, tick: int) -> None:
        conv = self.recent.get(Counterparty.dealer(dealer).label) if dealer else None
        last = self.last.get(conv) if conv else None
        if last is None or tick - last.tick > RECENT_TICKS:
            return
        self._score(last, result, PENALTY, tick, suffix="penalty")

    # ---------------------------------------------------------------- lessons

    def _score(self, sent: _Sent, result: str, reward: float, tick: int, moved: str = "", suffix: str = "") -> None:
        c = sent.choice
        if c.tactic is None:
            return
        key = suffix or str(c.step)
        self._lesson(
            c.counterparty, c.tactic, c.conversation, c.step, result, reward, tick, sent.day, moved, key, sent.message
        )

    def _lesson(
        self,
        cp: Counterparty,
        tactic: str,
        conversation: str,
        step: int,
        result: str,
        reward: float,
        tick: int,
        day: int,
        moved: str,
        key: str,
        message: int | None,
    ) -> None:
        what = f" ({moved})" if moved else ""
        text = (
            f"Tactic {tactic} to {cp.id} on {conversation} (our message {step + 1}): {result}{what}; "
            f"reward {reward:+g}."
        )
        detail = {
            "outcome": f"tactic:{conversation}:{key}",
            "tactic": tactic,
            "reward": reward,
            "result": result,
            "day": day,
            "conversation": conversation,
            "step": step,
        }
        if message is not None:
            detail["message"] = message
        try:
            learning = Learning(
                subject_kind=cp.kind,
                subject=cp.id,
                kind="tactic",
                tick=max(0, tick),
                team=self.us,
                confidence=CONFIDENCE,
                text=text,
                source="outcome",
                detail=detail,
            )
        except ValueError as e:  # an odd counterparty id: the tactic is still sent, only the lesson is lost
            self._fail("lesson", e)
            return
        self._add(learning)
        self.unwritten.append(learning)
        if result in PENALTY_RESULTS:
            self.log(f"bluff: {cp.id} {result} after {tactic}: off for {cp.id} today")

    def _add(self, learning: Learning) -> None:
        """Keep a tactic lesson (a newer version of the same one replaces it) and update the running sums."""
        if _reward(learning) is None:
            return
        key = learning.key()
        old = self.lessons.get(key)
        if old is not None:
            self._count(old, -1)
        self.lessons[key] = learning
        self._count(learning, +1)
        message, d = learning.detail.get("message"), learning.detail
        if isinstance(message, int) and isinstance(d.get("conversation"), str):
            cp = Counterparty(learning.subject_kind, learning.subject)  # type: ignore[arg-type]
            self.messages.setdefault(message, (cp, str(d["tactic"]), str(d["conversation"]), int(d.get("step") or 0)))

    def _count(self, learning: Learning, sign: int) -> None:
        scored = _reward(learning)
        if scored is None:
            return
        tactic, reward, day, result = scored
        label = f"{learning.subject_kind}:{learning.subject}"
        for agg in (
            self._per.setdefault((label, tactic), _Agg()),
            self._per_day.setdefault((label, tactic, day), _Agg()),
            self._per_kind.setdefault((learning.subject_kind, tactic), _Agg()),
        ):
            agg.n += sign
            agg.total += sign * reward
            agg.penalties += sign * (result in PENALTY_RESULTS)

    # ---------------------------------------------------------------- the store (after the sends only)

    def load(self) -> int:
        """Pull every tactic lesson for us from the store (another process's too). Never raises."""
        if self.store is None:
            return 0
        try:
            found = self.store.recall(None, {"tactic"}, None, team=self.us, limit=LOAD_LIMIT)
        except Exception as e:
            self._fail("load", e)
            return 0
        for learning in found:
            if learning.source == "outcome":
                self._add(learning)
        return len(found)

    def flush(self) -> int:
        """Write this tick's lessons, then pull the others'. Called after the tick's sends. Never raises."""
        batch, self.unwritten = self.unwritten, []
        written = 0
        if self.store is not None and batch:
            try:
                written = self.store.record(batch)
            except Exception as e:
                self._fail("write", e)
        if self._loaded_tick is None or not 0 <= self.tick - self._loaded_tick < LOAD_EVERY:
            self._loaded_tick = self.tick
            self.load()
        return written

    def _fail(self, what: str, error: Exception) -> None:
        if what not in self._failed:  # once per kind: a broken store must not flood the log
            self.log(f"bluff: {what} failed ({type(error).__name__}); tactics go on from memory")
        self._failed.add(what)
