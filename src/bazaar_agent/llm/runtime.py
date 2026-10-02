"""The runtime LLM facade: RUNTIME.md + credentials + pin + Jev model chooser + provider routing, in one object."""

from __future__ import annotations

from dataclasses import dataclass

from bazaar_agent.config import Settings
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.llm.chooser import ModelChoice, ModelChooser, MoveSituation
from bazaar_agent.llm.config import RuntimeConfig
from bazaar_agent.llm.models import ModelRef, Pin, Provider, pinned_model, resolve
from bazaar_agent.llm.providers import (
    LLMProvider,
    ProviderFactory,
    Route,
    credential_for,
    default_factory,
    provider_for,
)

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
        self._providers: dict[Provider, LLMProvider] = {}

    @property
    def pin(self) -> Pin | None:
        return self.chooser.pin

    def provider(self, ref: ModelRef) -> LLMProvider:
        """The provider for `ref`, built once per process (SDK import and client setup stay out of a tick).

        Raises `LLMError("key_missing")` naming the variables when no credential is set.
        """
        if ref.provider not in self._providers:
            self._providers[ref.provider] = provider_for(ref, self.settings, self._factory)
        return self._providers[ref.provider]

    def route(self, provider: Provider) -> Route | None:
        """How this process reaches a model family (Claude API, Claude subscription, OpenAI), or None."""
        credential = credential_for(provider, self.settings)
        return credential.route if credential is not None else None

    def warm(self) -> None:
        """Build every provider we hold a credential for now, before the first tick needs one."""
        for alias in (*self.config.runtime_models, self.config.runtime_model_default):
            ref = resolve(alias)
            if credential_for(ref.provider, self.settings) is not None:
                self.provider(ref)

    def pick(self, situation: MoveSituation, tick: int | None = None, budget_s: float | None = None) -> Picked:
        """Choose the model for this move and route it. Raises `LLMError` when its provider has no credential.

        Without any credential for the default model or a pin, nothing can run: it raises before asking Jev.
        """
        fixed = self.pin.model if self.pin is not None else resolve(self.config.runtime_model_default)
        if not self.chooser.candidates or self.pin is not None:
            self.provider(fixed)
        choice = self.chooser.choose(situation, tick, budget_s)
        ref = resolve(choice.alias)
        return Picked(choice, ref, self.provider(ref))


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
        available=lambda alias: credential_for(resolve(alias).provider, settings) is not None,
    )
    return LLMRuntime(config, settings, chooser, factory)
