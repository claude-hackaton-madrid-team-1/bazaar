"""The runtime LLM facade: RUNTIME.md + keys + pin + Jev model chooser + provider routing, in one object."""

from __future__ import annotations

from dataclasses import dataclass

from bazaar_agent.config import Settings
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.llm.chooser import ModelChoice, ModelChooser, MoveSituation
from bazaar_agent.llm.config import RuntimeConfig
from bazaar_agent.llm.models import ModelRef, Pin, pinned_model, resolve
from bazaar_agent.llm.providers import LLMProvider, ProviderFactory, default_factory, provider_for

CHOICE_LOG = "llm/model-choices.jsonl"


@dataclass(frozen=True)
class Picked:
    """The model chosen for one move and the provider that serves it."""

    choice: ModelChoice
    ref: ModelRef
    provider: LLMProvider


class LLMRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        settings: Settings,
        chooser: ModelChooser,
        factory: ProviderFactory = default_factory,
    ) -> None:
        self.config = config
        self.settings = settings
        self.chooser = chooser
        self._factory = factory

    @property
    def pin(self) -> Pin | None:
        return self.chooser.pin

    def pick(self, situation: MoveSituation, tick: int | None = None, budget_s: float | None = None) -> Picked:
        """Choose the model for this move and route it. Raises `LLMError` when its provider has no key."""
        choice = self.chooser.choose(situation, tick, budget_s)
        ref = resolve(choice.alias)
        return Picked(choice, ref, provider_for(ref, self.settings, self._factory))


def build_runtime(
    settings: Settings,
    config: RuntimeConfig,
    rules: Guardrails,
    *,
    cli_pin: str | None = None,
    factory: ProviderFactory = default_factory,
) -> LLMRuntime:
    """Raises `UnknownModelError` when the flag, env or RUNTIME.md pins a model we cannot route."""
    pin = pinned_model(cli_pin, settings.llm_runtime, config.llm_runtime)
    jev_key = settings.typesafe_api_key.get_secret_value() if settings.typesafe_api_key else None
    chooser = ModelChooser(
        config,
        pin=pin,
        jev_timeout_s=rules.jev_timeout_s,
        jev_api_key=jev_key,
        log_path=settings.data_dir / CHOICE_LOG,
    )
    return LLMRuntime(config, settings, chooser, factory)
