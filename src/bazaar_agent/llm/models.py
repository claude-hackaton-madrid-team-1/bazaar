"""Which LLM the runtime talks to: the alias registry, provider routing, and the pin precedence.

An alias (`opus-5-5`) or a raw model id (`claude-opus-5-5`, `gpt-6.1-sol`) resolves to a
`ModelRef`. Claude ids route to Anthropic; ids starting with `gpt-` or `o<digit>` route to
OpenAI and pass through unchanged, so a model we have not registered fails at call time with
the provider's own error, never at startup.

Pin precedence: `--llm-runtime` flag > `BAZAAR_LLM_RUNTIME` > RUNTIME.md `llm_runtime` > (Jev
chooses among `runtime_models`) > `runtime_model_default`. `auto` (or empty) means "not pinned".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Provider = Literal["anthropic", "openai"]
PinSource = Literal["flag", "env", "runtime.md"]

# Ids verified on 2026-10-02: Claude ids from the claude-api skill model table (Haiku 4.5 as its
# dated snapshot), `gpt-6.1-sol` from developers.openai.com/api/docs/guides/latest-model.
ALIASES: dict[str, str] = {
    "opus-5-5": "claude-opus-5-5",
    "sonnet-5-5": "claude-sonnet-5-5",
    "haiku-4-5": "claude-haiku-4-5-20251001",
    "fable-5-1": "claude-fable-5-1",
    "gpt-6-1-sol": "gpt-6.1-sol",
}
AUTO = "auto"
_OPENAI_ID = re.compile(r"^(gpt-|o\d)")


class UnknownModelError(ValueError):
    """Neither an alias nor an id we can route to a provider."""


@dataclass(frozen=True)
class ModelRef:
    alias: str  # the name as configured (an alias, or the id itself)
    model_id: str  # what the provider API receives
    provider: Provider


@dataclass(frozen=True)
class Pin:
    model: ModelRef
    source: PinSource


def resolve(name: str) -> ModelRef:
    key = name.strip().lower()
    if not key:
        raise UnknownModelError("empty model name")
    by_id = {model_id: alias for alias, model_id in ALIASES.items()}
    alias = key if key in ALIASES else by_id.get(key, key.replace(".", "-"))
    if alias not in ALIASES:
        alias = key
    model_id = ALIASES.get(alias, key)
    if model_id.startswith("claude-"):
        return ModelRef(alias, model_id, "anthropic")
    if _OPENAI_ID.match(model_id):
        return ModelRef(alias, model_id, "openai")
    known = ", ".join(sorted(ALIASES))
    raise UnknownModelError(f"unknown model {name!r}: use an alias ({known}), a claude-* id, or a gpt-*/o* id")


def pinned_model(flag: str | None, env: str | None, runtime_md: str | None) -> Pin | None:
    """The pinned model by precedence, or None when Jev should choose. Raises on an unknown name."""
    candidates: tuple[tuple[str | None, PinSource], ...] = ((flag, "flag"), (env, "env"), (runtime_md, "runtime.md"))
    for value, source in candidates:
        if value and value.strip().lower() != AUTO:
            return Pin(resolve(value), source)
    return None
