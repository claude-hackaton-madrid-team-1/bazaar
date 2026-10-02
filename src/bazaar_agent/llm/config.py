"""RUNTIME.md: the runtime LLM parameters, in the same `` - `param` = value — why `` format as GUARDRAILS.md.

A missing RUNTIME.md means defaults (the LLM layer is optional); a bad line or value fails fast.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bazaar_agent.config import REPO_ROOT
from bazaar_agent.guardrails import GuardrailsError, RuleLine, parse_md_config, validated
from bazaar_agent.llm.models import AUTO, UnknownModelError, resolve

RUNTIME_FILE = REPO_ROOT / "RUNTIME.md"


class RuntimeConfigError(ValueError):
    """RUNTIME.md has an unknown parameter, a bad value, or an unknown model."""


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    llm_runtime: str = AUTO
    runtime_model_default: str = "haiku-4-5"
    runtime_models: tuple[str, ...] = ("haiku-4-5", "sonnet-5-5", "opus-5-5", "gpt-6-1-sol")
    model_choice_cache_ticks: int = Field(default=5, ge=1, le=120)
    llm_words: bool = False
    words_timeout_s: float = Field(default=2.5, gt=0, le=10)
    subscription_words_timeout_s: float = Field(default=6.0, gt=0, le=10)
    words_max_chars: int = Field(default=300, ge=40, le=1200)
    ask_timeout_s: float = Field(default=30.0, gt=0, le=120)
    steer_timeout_s: float = Field(default=30.0, gt=0, le=120)

    @field_validator("runtime_models", mode="before")
    @classmethod
    def _split(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip().lower() for part in value.split(",") if part.strip())
        return value

    @field_validator("runtime_model_default", "llm_runtime")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("runtime_models")
    @classmethod
    def _distinct(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("needs at least one model")
        if len(set(value)) != len(value):
            raise ValueError("lists a model twice")
        return value


@dataclass(frozen=True)
class LoadedRuntime:
    config: RuntimeConfig
    lines: tuple[RuleLine, ...]
    path: Path


def parse_runtime(text: str, path: Path = RUNTIME_FILE) -> LoadedRuntime:
    """Same line format and parser as GUARDRAILS.md and STRATEGY.md (`guardrails.parse_md_config`)."""
    try:
        lines, _, values = parse_md_config(text, path)
        config = validated(RuntimeConfig, values, path)
    except GuardrailsError as e:
        raise RuntimeConfigError(str(e)) from None
    _check_models(config, path)
    return LoadedRuntime(config, lines, path)


def _check_models(config: RuntimeConfig, path: Path) -> None:
    names = [config.runtime_model_default, *config.runtime_models]
    if config.llm_runtime != AUTO:
        names.append(config.llm_runtime)
    for name in names:
        try:
            resolve(name)
        except UnknownModelError as e:
            raise RuntimeConfigError(f"{path.name}: {e}") from None


def load_runtime(path: Path = RUNTIME_FILE) -> LoadedRuntime:
    if not path.is_file():
        return LoadedRuntime(RuntimeConfig(), (), path)
    return parse_runtime(path.read_text(encoding="utf-8"), path)
