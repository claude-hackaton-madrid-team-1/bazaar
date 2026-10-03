"""Which Claude model runs the desk and each of its subagents: Jev's call, before every desk request.

Precedence: `agent chat --model` > a pinned Claude model (`--llm-runtime`, BAZAAR_LLM_RUNTIME, RUNTIME.md
`llm_runtime`) > RUNTIME.md `desk_model` (unless `auto`) > Jev (`model_for_desk_role`, one question per
role in ONE call, cached per role for `model_choice_cache_ticks`) > RUNTIME.md `desk_role_defaults`.
Only Claude models are candidates: the desk runs on the Claude Code CLI (the Claude subscription).

The request's situation comes from the operator's text: its length, the injection shapes in it, and the
largest price-like number as the value at risk (card codes such as LAV-09, copy numbers such as #7/30,
team ids such as t07, and the number after "thread", "tick", "duel", "offer" and the like left out). It
only informs Jev and buckets the cache; it never sets a price.

A subagent's model can change between two requests of one `agent chat` conversation without a new
session: the CLI resolves a family alias (`opus`, `sonnet`, `haiku`, `fable`) through
ANTHROPIC_DEFAULT_<FAMILY>_MODEL (code.claude.com/docs/en/model-config), so the desk's session pins each
family to our exact model id (`family_env`) and the PreToolUse hook puts the chosen subagent's alias on
every `Agent` call (`DeskModels.invocation_aliases`), which wins over the definition
(code.claude.com/docs/en/sub-agents#choose-a-model). A model whose family is ambiguous among the desk's
models keeps the definition the session started with.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from bazaar_agent.config import Settings
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.jev import judge
from bazaar_agent.llm.chooser import DESK_QUESTION_ID, Judge, ModelChoice, ModelChooser, MoveSituation, injection_flags
from bazaar_agent.llm.config import DESK_ROLES, DeskRole, RuntimeConfig
from bazaar_agent.llm.models import AUTO, ModelRef, Pin, UnknownModelError, pinned_model, resolve
from bazaar_agent.llm.runtime import CHOICE_LOG
from bazaar_agent.ticks import Clock

ROLES: tuple[DeskRole, ...] = DESK_ROLES
SUBAGENT_ROLES: tuple[DeskRole, ...] = tuple(role for role in ROLES if role != "desk")
_NOT_PRICES = re.compile(
    r"\b[A-Za-z]{3}-\d{1,3}\b"  # card codes: LAV-09
    r"|#\d+(?:/\d+)?"  # copy numbers and ids: #7/30, #12
    r"|\b(?:threads?|ticks?|duels?|offers?|assets?|ids?|teams?|levels?|pages?|rounds?|days?)\s*#?\s*\d+",
    re.IGNORECASE,
)
_PRICE = re.compile(r"\b(\d+)(?:p|primas)?\b", re.IGNORECASE)  # whole numbers only: t07 or 1.5e3 do not count
MAX_RISK = 10_000  # far above any card or our cash: a longer digit run (a pasted key?) never reaches Jev
FAMILY_ENV: Mapping[str, str] = {
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "fable": "ANTHROPIC_DEFAULT_FABLE_MODEL",
}


def value_at_risk(text: str) -> int:
    """The largest price-like number in the request (`buy LAV-09 under 90` → 90, `thread 1234` → 0)."""
    numbers = [int(n) for n in _PRICE.findall(_NOT_PRICES.sub(" ", text))]
    return min(max(numbers, default=0), MAX_RISK)


def family_of(model_id: str) -> str | None:
    """`claude-opus-5-5` → `opus`; None for an id outside the four Claude families."""
    parts = model_id.split("-")
    return parts[1] if len(parts) > 2 and parts[0] == "claude" and parts[1] in FAMILY_ENV else None


def family_env(model_ids: Iterable[str]) -> dict[str, str]:
    """ANTHROPIC_DEFAULT_<FAMILY>_MODEL for each family that has exactly ONE of the desk's models, so its
    alias names that model and nothing else. A family with two of our models is left out (ambiguous)."""
    by_family: dict[str, set[str]] = {}
    for model_id in model_ids:
        if (family := family_of(model_id)) is not None:
            by_family.setdefault(family, set()).add(model_id)
    return {FAMILY_ENV[f]: next(iter(ids)) for f, ids in by_family.items() if len(ids) == 1}


def request_situation(text: str, tick_seconds: float, timeout_s: float) -> MoveSituation:
    return MoveSituation(
        kind="desk_request",
        value_at_risk=value_at_risk(text),
        tick_seconds=tick_seconds,
        seconds_left=timeout_s,
        counterparty_chars=len(text),
        flags=injection_flags(text),
    )


@dataclass(frozen=True)
class DeskModels:
    """One model per role for one request, with how each was chosen."""

    choices: Mapping[str, ModelChoice]

    def ref(self, role: str) -> ModelRef:
        return resolve(self.choices[role].alias)

    @property
    def orchestrator(self) -> ModelRef:
        return self.ref("desk")

    def subagent_ids(self) -> dict[str, str]:
        """subagent name -> the model id its `AgentDefinition` runs."""
        return {role: self.ref(role).model_id for role in SUBAGENT_ROLES}

    def invocation_aliases(self, pinned: Mapping[str, str]) -> dict[str, str]:
        """subagent name -> the family alias the hook puts on its `Agent` call, for the subagents whose
        model is the one its family is pinned to in the session (`pinned`: `family_env()`)."""
        aliases: dict[str, str] = {}
        for role, model_id in self.subagent_ids().items():
            family = family_of(model_id)
            if family is not None and pinned.get(FAMILY_ENV[family]) == model_id:
                aliases[role] = family
        return aliases

    def summary(self) -> str:
        return " · ".join(f"{role} {_label(choice)}" for role, choice in self.choices.items())


def _label(choice: ModelChoice) -> str:
    """`opus-5-5 (jev 0.91)`, `sonnet-5-5 (default, jev 0.55)`, `sonnet-5-5 (default)`, `haiku-4-5 (flag)`."""
    how: str = choice.source
    if choice.probabilities and choice.confidence is not None:
        how += f" {choice.confidence:.2f}" if choice.source == "jev" else f", jev {choice.confidence:.2f}"
    if choice.cached:
        how += ", cached"
    return f"{choice.alias} ({how})"


def desk_pin(override: str | None, pin: Pin | None, desk_model: str) -> Pin | None:
    """`agent chat --model` > a pinned Claude model > RUNTIME.md `desk_model` unless `auto`. A pinned
    OpenAI model is ignored here (the desk runs Claude only); an OpenAI `--model` is refused."""
    if override:
        ref = resolve(override)
        if ref.provider != "anthropic":
            raise UnknownModelError(f"the desk runs Claude models only, not {override!r}")
        return Pin(ref, "flag")
    if pin is not None and pin.model.provider == "anthropic":
        return pin
    if desk_model != AUTO:
        return Pin(resolve(desk_model), "runtime.md")
    return None


class DeskModelPicker:
    """`pick(text)` before each desk request; never raises (any failure → the role defaults)."""

    def __init__(
        self,
        chooser: ModelChooser,
        *,
        timeout_s: float,
        clock: Callable[[], Clock] | None = None,
        log: Callable[[str], None] = lambda line: None,
    ) -> None:
        self.chooser, self.timeout_s, self._clock, self._log = chooser, timeout_s, clock, log

    def initial(self) -> DeskModels:
        """The models a session starts with before its first request: the pin, else each role's default."""
        pin = self.chooser.pin
        return DeskModels(
            {
                role: (
                    ModelChoice(pin.model.alias, pin.source, "pinned")
                    if pin is not None
                    else ModelChoice(self.chooser.default_for(role), "default", "before the first request")
                )
                for role in ROLES
            }
        )

    def model_ids(self) -> tuple[str, ...]:
        """Every model this picker can hand a role: the pin, else the candidates and the role defaults."""
        pin = self.chooser.pin
        if pin is not None:
            return (pin.model.model_id,)
        names = (*self.chooser.candidates, *(self.chooser.default_for(role) for role in ROLES))
        return tuple(dict.fromkeys(resolve(name).model_id for name in names))

    def describe(self) -> str:
        pin = self.chooser.pin
        if pin is not None:
            return f"{pin.model.alias} pinned by {pin.source}"
        return f"Jev picks per request among {', '.join(self.chooser.candidates)}"

    def pick(self, text: str) -> DeskModels:
        clock = self._read_clock()
        situation = request_situation(text, clock.tick_seconds if clock else 60.0, self.timeout_s)
        try:
            return DeskModels(self.chooser.choose_roles(situation, ROLES, clock.tick if clock else None))
        except Exception as e:  # a choice-log write failed, say: the request still runs, on the defaults
            self._log(f"desk models: role defaults ({type(e).__name__})")
            return self.initial()

    def _read_clock(self) -> Clock | None:
        """The game tick keys the choice cache; without it every request asks Jev once."""
        if self._clock is None:
            return None
        try:
            return self._clock()
        except Exception as e:
            self._log(f"desk models: game clock unavailable ({type(e).__name__}), no choice cache")
            return None


def build_picker(
    settings: Settings,
    config: RuntimeConfig,
    rules: Guardrails,
    *,
    cli_pin: str | None,
    override: str | None = None,
    clock: Callable[[], Clock] | None = None,
    log: Callable[[str], None] = lambda line: None,
    judge_fn: Judge | None = None,
    log_path: Path | None = None,
) -> DeskModelPicker:
    """Raises `UnknownModelError` for a pinned or `--model` name we cannot route to Claude."""
    pin = desk_pin(override, pinned_model(cli_pin, settings.llm_runtime, config.llm_runtime), config.desk_model)
    jev_key = settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else None
    chooser = ModelChooser(
        config,
        pin=pin,
        jev_timeout_s=rules.jev_timeout_s,
        jev_api_key=jev_key,
        log_path=log_path if log_path is not None else settings.data_dir / CHOICE_LOG,
        available=lambda alias: resolve(alias).provider == "anthropic",
        question_id=DESK_QUESTION_ID,
        defaults=config.desk_role_defaults,
        judge_fn=judge_fn or judge,
    )
    return DeskModelPicker(chooser, timeout_s=config.desk_timeout_s, clock=clock, log=log)
