"""RUNTIME.md: the runtime LLM parameters, in the same `` - `param` = value — why `` format as GUARDRAILS.md.

A missing RUNTIME.md means defaults (the LLM layer is optional); a bad line or value fails fast.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bazaar_agent.config import REPO_ROOT
from bazaar_agent.guardrails import GuardrailsError, RuleLine, parse_md_config, validated
from bazaar_agent.llm.models import AUTO, UnknownModelError, resolve

RUNTIME_FILE = REPO_ROOT / "RUNTIME.md"
# The desk's roles (`runtime.agents`: the orchestrator and its four subagents); Jev picks a model for each.
DeskRole = Literal["desk", "strategist", "buyer", "seller", "duelist"]
DESK_ROLES: tuple[DeskRole, ...] = get_args(DeskRole)
DESK_ROLE_DEFAULTS: Mapping[str, str] = {role: "sonnet-5-5" for role in DESK_ROLES}


class RuntimeConfigError(ValueError):
    """RUNTIME.md has an unknown parameter, a bad value, or an unknown model."""


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    llm_runtime: str = AUTO
    runtime_model_default: str = "haiku-4-5"
    runtime_models: tuple[str, ...] = ("haiku-4-5", "sonnet-5-5", "opus-5-5", "gpt-6-1-sol")
    model_choice_cache_ticks: int = Field(default=5, ge=1, le=120)
    llm_words: bool = False
    llm_read_feed: bool = False
    read_feed_every_ticks: int = Field(default=10, ge=1, le=600)
    words_timeout_s: float = Field(default=2.5, gt=0, le=10)
    subscription_words_timeout_s: float = Field(default=6.0, gt=0, le=10)
    words_max_chars: int = Field(default=300, ge=40, le=1200)
    ask_timeout_s: float = Field(default=30.0, gt=0, le=120)
    steer_timeout_s: float = Field(default=30.0, gt=0, le=120)
    desk_model: str = AUTO
    desk_role_defaults: Mapping[str, str] = Field(default_factory=lambda: dict(DESK_ROLE_DEFAULTS))
    desk_max_turns: int = Field(default=16, ge=2, le=60)
    desk_timeout_s: float = Field(default=180.0, gt=0, le=900)
    mcp_calls_per_minute: int = Field(default=30, ge=1, le=600)

    @field_validator("runtime_models", mode="before")
    @classmethod
    def _split(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip().lower() for part in value.split(",") if part.strip())
        return value

    @field_validator("runtime_model_default", "llm_runtime", "desk_model")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("desk_role_defaults", mode="before")
    @classmethod
    def _pairs(cls, value: Any) -> Any:
        """`desk:sonnet-5-5, buyer:opus-5-5, ...` → {role: model}; every desk role exactly once."""
        if not isinstance(value, str):
            return value
        pairs = [part.split(":", 1) for part in value.split(",") if part.strip()]
        if any(len(pair) != 2 for pair in pairs):
            raise ValueError("expected role:model pairs separated by commas")
        roles = [role.strip().lower() for role, _ in pairs]
        if len(set(roles)) != len(roles):
            raise ValueError("lists a role twice")
        return {role: model.strip().lower() for role, (_, model) in zip(roles, pairs, strict=True)}

    @field_validator("desk_role_defaults")
    @classmethod
    def _every_role(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        if set(value) != set(DESK_ROLES):
            raise ValueError(f"needs exactly the roles {', '.join(DESK_ROLES)}")
        return {role: value[role] for role in DESK_ROLES}

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
    desk = [*config.desk_role_defaults.values(), *([config.desk_model] if config.desk_model != AUTO else [])]
    for name in [*names, *desk]:
        try:
            ref = resolve(name)
        except UnknownModelError as e:
            raise RuntimeConfigError(f"{path.name}: {e}") from None
        if name in desk and ref.provider != "anthropic":
            raise RuntimeConfigError(f"{path.name}: the desk runs Claude models only (Claude Agent SDK), not {name!r}")


def load_runtime(path: Path = RUNTIME_FILE) -> LoadedRuntime:
    if not path.is_file():
        return LoadedRuntime(RuntimeConfig(), (), path)
    return parse_runtime(path.read_text(encoding="utf-8"), path)
