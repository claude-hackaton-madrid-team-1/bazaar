"""Jev picks the runtime LLM per move: a `choice` question whose options are the candidate models.

The verdict carries a probability (0..1) per candidate. A decided verdict (design stakes, 0.75)
picks the top model; anything else falls back to RUNTIME.md `runtime_model_default`. The full float
map is logged on every fresh decision. A choice is cached per (move kind, tick-length bucket,
injection flags present, stakes bucket) for `model_choice_cache_ticks` ticks, so a 15 s tick never
pays for two model-choice calls, and a harmless move's cheap model is never reused for a risky one.
Only candidates whose provider has a credential set are offered to Jev. A pinned model (flag, env,
RUNTIME.md) skips Jev entirely and is logged as pinned.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from bazaar_agent.config import REPO_ROOT
from bazaar_agent.jev import JevUsageError, JudgeResult, judge, load_questions
from bazaar_agent.llm.config import RuntimeConfig, RuntimeConfigError
from bazaar_agent.llm.models import Pin

QUESTION_FILE = REPO_ROOT / "questions" / "runtime_model.json"
QUESTION_ID = "model_for_move"
MoveKind = Literal["buy", "sell", "words", "parse_request", "steer"]
ChoiceSource = Literal["flag", "env", "runtime.md", "jev", "default"]
MIN_JEV_BUDGET_S = 1.0  # less time than this left for Jev: use the default instead of a late answer
WARM_LINES = 200

INJECTION_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "instruction_override": re.compile(
        r"\b(ignore|disregard|forget|ignora|olvida)\b.{0,40}\b(instructions?|rules?|previous|prompt|instrucciones|reglas)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "role_play": re.compile(r"\b(you are now|act as|pretend|system prompt|eres ahora|act[uú]a como)\b", re.IGNORECASE),
    "role_tag": re.compile(r"</?\s*(system|assistant|user|tool)\s*>|\b(system|assistant|developer)\s*:", re.IGNORECASE),
    "code_or_json": re.compile(r"```|\{\s*\""),
    "url": re.compile(r"https?://", re.IGNORECASE),
    "money_command": re.compile(
        r"\b(accept|acepta|pay|paga|transfer|send|env[ií]a)\b.{0,30}\d", re.IGNORECASE | re.DOTALL
    ),
}


def injection_flags(text: str | None) -> tuple[str, ...]:
    """Names of the prompt-injection shapes found in a counterparty's text (untrusted input)."""
    if not text:
        return ()
    return tuple(name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(text))


def stakes_bucket(value_at_risk: int) -> str:
    """The value bands the question's criteria use: under about 20 primas, 20 to 60, above 60."""
    if value_at_risk <= 20:
        return "low"
    return "mid" if value_at_risk <= 60 else "high"


CacheKey = tuple[str, str, bool, str]


def cache_key(situation: MoveSituation) -> CacheKey:
    return (
        situation.kind,
        tick_bucket(situation.tick_seconds),
        bool(situation.flags),
        stakes_bucket(situation.value_at_risk),
    )


def tick_bucket(tick_seconds: float) -> str:
    """Sunday's 15 s ticks, Saturday's 30 s, Friday's 60 s: the cache never mixes them."""
    if tick_seconds <= 20:
        return "fast"
    return "medium" if tick_seconds <= 40 else "slow"


@dataclass(frozen=True)
class MoveSituation:
    kind: MoveKind
    value_at_risk: int = 0
    tick_seconds: float = 60.0
    seconds_left: float = 60.0
    counterparty_chars: int = 0
    flags: tuple[str, ...] = ()

    def state(self, candidates: tuple[str, ...]) -> dict[str, Any]:
        return {
            "move_kind": self.kind,
            "value_at_risk_primas": self.value_at_risk,
            "tick_seconds": self.tick_seconds,
            "seconds_left_in_tick": round(self.seconds_left, 1),
            "counterparty_text_chars": self.counterparty_chars,
            "injection_risk_flags": list(self.flags),
            "candidates": list(candidates),
        }


@dataclass(frozen=True)
class ModelChoice:
    alias: str
    source: ChoiceSource
    reason: str
    probabilities: Mapping[str, float] = field(default_factory=dict)
    confidence: float | None = None
    tick: int | None = None
    cached: bool = False


def model_question(pack: Mapping[str, Mapping[str, object]], candidates: tuple[str, ...]) -> dict[str, object]:
    """The pack's question with criteria for exactly the candidates (each one must be described)."""
    question = pack.get(QUESTION_ID)
    if not isinstance(question, Mapping):
        raise RuntimeConfigError(f"questions/runtime_model.json has no `{QUESTION_ID}` question")
    criteria = question.get("criteria")
    described = criteria if isinstance(criteria, Mapping) else {}
    missing = [c for c in candidates if c not in described]
    if missing:
        raise RuntimeConfigError(
            f"runtime_models lists {', '.join(missing)} but questions/runtime_model.json has no criteria for it"
        )
    return {**question, "criteria": {c: described[c] for c in candidates}}


def top_model(probabilities: Mapping[str, float], fallback: str) -> str:
    if not probabilities:
        return fallback
    return max(probabilities.items(), key=lambda item: item[1])[0]


Judge = Callable[..., JudgeResult]


class ModelChooser:
    """Pinned model, cached Jev choice, fresh Jev choice, or the default, in that order."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        pin: Pin | None,
        jev_timeout_s: float,
        jev_api_key: str | None,
        log_path: Path | None,
        judge_fn: Judge = judge,
        question_pack: Mapping[str, Mapping[str, object]] | None = None,
        clock: Callable[[], float] = time.time,
        available: Callable[[str], bool] | None = None,
    ) -> None:
        self.config = config
        self.pin = pin
        self.jev_timeout_s = jev_timeout_s
        self._key = jev_api_key
        self.log_path = log_path
        self._judge = judge_fn
        self._pack = question_pack
        self._clock = clock
        self._available = available
        self._cache: dict[CacheKey, ModelChoice] = {}
        self._warm()

    @property
    def candidates(self) -> tuple[str, ...]:
        """The RUNTIME.md candidates we can call (credential set): Jev never picks one we cannot use."""
        models = self.config.runtime_models
        return models if self._available is None else tuple(m for m in models if self._available(m))

    def question(self) -> dict[str, object]:
        pack = self._pack if self._pack is not None else load_questions(QUESTION_FILE)
        return model_question(pack, self.candidates)

    def choose(self, situation: MoveSituation, tick: int | None = None, budget_s: float | None = None) -> ModelChoice:
        if self.pin is not None:
            choice = ModelChoice(self.pin.model.alias, self.pin.source, "pinned", tick=tick)
            self._log(choice, situation)
            return choice
        if len(self.candidates) < 2:
            return self._default(situation, tick, "fewer than two callable candidates in runtime_models")
        key = cache_key(situation)
        cached = self._cached(key, tick)
        if cached is not None:
            return cached
        timeout = self.jev_timeout_s if budget_s is None else min(self.jev_timeout_s, budget_s)
        if timeout < MIN_JEV_BUDGET_S:
            return self._default(situation, tick, f"no time for Jev ({timeout:.1f}s left)")
        choice = self._ask_jev(situation, tick, timeout)
        if tick is not None:
            self._cache[key] = choice
        self._log(choice, situation)
        return choice

    def _ask_jev(self, situation: MoveSituation, tick: int | None, timeout_s: float) -> ModelChoice:
        try:
            result = self._judge(
                situation.state(self.candidates),
                {QUESTION_ID: self.question()},
                api_key=self._key or "",
                timeout_s=timeout_s,
            )
        except (JevUsageError, RuntimeConfigError) as e:
            return ModelChoice(self.config.runtime_model_default, "default", f"jev usage error: {e}", tick=tick)
        verdict = result.verdicts[QUESTION_ID]
        probabilities = dict(verdict.probabilities or {})
        top = top_model(probabilities, verdict.verdict)
        if verdict.decided and top in self.candidates:
            reason = f"jev decided ({verdict.value:.2f} ≥ {verdict.threshold:.2f})"
            return ModelChoice(top, "jev", reason, probabilities, verdict.value, tick)
        why = verdict.reason or f"top {top!r} is not a candidate"
        default = self.config.runtime_model_default
        return ModelChoice(default, "default", f"jev undecided: {why}", probabilities, verdict.value, tick)

    def _default(self, situation: MoveSituation, tick: int | None, reason: str, log: bool = True) -> ModelChoice:
        choice = ModelChoice(self.config.runtime_model_default, "default", reason, tick=tick)
        if log:
            self._log(choice, situation)
        return choice

    def _cached(self, key: CacheKey, tick: int | None) -> ModelChoice | None:
        entry = self._cache.get(key)
        if entry is None or entry.tick is None or tick is None:
            return None
        if 0 <= tick - entry.tick < self.config.model_choice_cache_ticks:
            return ModelChoice(**{**asdict(entry), "cached": True})
        return None

    def _log(self, choice: ModelChoice, situation: MoveSituation) -> None:
        if self.log_path is None:
            return
        record = {
            "ts": self._clock(),
            "tick": choice.tick,
            "kind": situation.kind,
            "bucket": tick_bucket(situation.tick_seconds),
            "flagged": bool(situation.flags),
            "stakes": stakes_bucket(situation.value_at_risk),
            "model": choice.alias,
            "source": choice.source,
            "reason": choice.reason,
            "confidence": choice.confidence,
            "probabilities": dict(choice.probabilities),
            "situation": situation.state(self.candidates),
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _warm(self) -> None:
        """Reuse fresh Jev decisions logged by another process (one `bazaar ask` per process)."""
        for record in read_choices(self.log_path, WARM_LINES):
            try:
                key, choice = _cached_record(record)
            except (KeyError, TypeError, ValueError):
                continue  # a malformed log line is skipped, never fatal at startup
            if choice.source in ("jev", "default") and choice.alias in (
                *self.candidates,
                self.config.runtime_model_default,
            ):
                self._cache[key] = choice


def _cached_record(record: Mapping[str, Any]) -> tuple[CacheKey, ModelChoice]:
    tick, source = record["tick"], record["source"]
    if not isinstance(tick, int) or isinstance(tick, bool) or source not in ("jev", "default"):
        raise ValueError("not a cacheable choice")
    probabilities = {str(k): float(v) for k, v in dict(record.get("probabilities") or {}).items()}
    confidence = record.get("confidence")
    key: CacheKey = (str(record["kind"]), str(record["bucket"]), bool(record["flagged"]), str(record["stakes"]))
    choice = ModelChoice(
        alias=str(record["model"]),
        source=source,
        reason=str(record.get("reason") or ""),
        probabilities=probabilities,
        confidence=None if confidence is None else float(confidence),
        tick=tick,
    )
    return key, choice


def read_choices(path: Path | None, limit: int) -> list[dict[str, Any]]:
    """The last `limit` logged model choices, oldest first; unreadable lines are skipped."""
    if path is None or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
