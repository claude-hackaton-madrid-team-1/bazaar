"""Provider-agnostic LLM calls: plain text with a hard deadline, or structured output validated by pydantic.

Every failure (missing key, timeout, refusal, bad output, API error) raises `LLMError` with a short
`reason`, so a caller in the tick loop can fall back without catching SDK exceptions. Messages
never carry a key. Anthropic calls follow the claude-api skill: the beta Messages endpoint with
`fallbacks: "default"` on the models that support server-side refusal fallback, `effort` only on
models that accept it, and `stop_reason` checked before reading content. OpenAI calls use the
Responses API (`responses.create` / `responses.parse(text_format=...)`).

Routing: a Claude model goes to the Claude API when ANTHROPIC_API_KEY is set, else to the Claude
subscription (CLAUDE_CODE_OAUTH_TOKEN, through the Claude Agent SDK in `bazaar_agent.runtime.claude`);
an OpenAI model needs OPENAI_API_KEY.
"""

from __future__ import annotations

import queue
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, SecretStr

from bazaar_agent.config import Settings
from bazaar_agent.llm.models import ModelRef, Provider

T = TypeVar("T", bound=BaseModel)
Effort = Literal["low", "medium", "high"]

Route = Literal["anthropic", "claude-subscription", "openai"]
SUBSCRIPTION: Route = "claude-subscription"
KEY_VARIABLES: Mapping[Provider, str] = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
TOKEN_VARIABLE = "CLAUDE_CODE_OAUTH_TOKEN"  # `claude setup-token`: Claude models on the Claude subscription
# What to set for a model family, in the order routing tries them.
CREDENTIAL_VARIABLES: Mapping[Provider, str] = {
    "anthropic": f"ANTHROPIC_API_KEY or {TOKEN_VARIABLE}",
    "openai": "OPENAI_API_KEY",
}
ROUTE_LABELS: Mapping[Route, str] = {
    "anthropic": "Claude API",
    SUBSCRIPTION: "Claude subscription via the Claude Agent SDK",
    "openai": "OpenAI API",
}
# Pinned so a stray ANTHROPIC_BASE_URL / OPENAI_BASE_URL in the shell cannot send a key elsewhere.
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
OPENAI_BASE_URL = "https://api.openai.com/v1"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Server-side refusal fallback (`fallbacks: "default"`) is offered on these Claude API models.
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1")
# `output_config.effort` errors on Haiku 4.5; every other current Claude model accepts it.
NO_EFFORT_PREFIXES = ("claude-haiku-4-5",)
# GPT-6 family and o-series are reasoning models: `reasoning.effort` low keeps a reply fast.
OPENAI_REASONING = re.compile(r"^(gpt-6|o\d)")


class LLMError(RuntimeError):
    """Why an LLM call produced nothing usable. `reason` is a short code; the message is for humans."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class TextRequest:
    model_id: str
    system: str
    user: str
    max_tokens: int
    timeout_s: float
    effort: Effort = "low"
    retries: int = 0
    purpose: str = ""  # "words" is the only purpose whose text may reach a trace (it is sent to a counterparty)


class LLMProvider(Protocol):
    name: str

    def complete(self, request: TextRequest) -> str: ...

    def structured(self, request: TextRequest, schema: type[T]) -> T: ...


def attempt_timeout_s(request: TextRequest) -> float:
    """Per-attempt SDK timeout, so every retry still fits inside the request's hard deadline."""
    return request.timeout_s / (request.retries + 1)


def run_with_deadline(call: Callable[[], Any], timeout_s: float) -> Any:
    """Run `call` on a daemon thread and stop waiting at `timeout_s`, whatever the socket is doing."""
    results: queue.SimpleQueue[tuple[bool, Any]] = queue.SimpleQueue()

    def run() -> None:
        try:
            results.put((True, call()))
        except BaseException as error:  # handed back to the caller and re-raised there
            results.put((False, error))

    threading.Thread(target=run, name="llm-call", daemon=True).start()
    try:
        ok, value = results.get(timeout=timeout_s)
    except queue.Empty:
        raise LLMError("timeout", f"no answer within {timeout_s:.1f}s") from None
    if not ok:
        raise value
    return value


# ---------------------------------------------------------------- Anthropic


def anthropic_request_kwargs(request: TextRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": request.model_id,
        "max_tokens": request.max_tokens,
        "system": request.system,
        "messages": [{"role": "user", "content": request.user}],
    }
    if not request.model_id.startswith(NO_EFFORT_PREFIXES):
        kwargs["output_config"] = {"effort": request.effort}
    if request.model_id in FALLBACK_MODELS:
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"
    return kwargs


def _anthropic_error(error: Exception) -> LLMError:
    import anthropic

    if isinstance(error, LLMError):
        return error
    if isinstance(error, anthropic.APITimeoutError):
        return LLMError("timeout", "Anthropic request timed out")
    if isinstance(error, anthropic.AuthenticationError):
        return LLMError("auth", "Anthropic rejected ANTHROPIC_API_KEY")
    if isinstance(error, anthropic.NotFoundError):
        return LLMError("unknown_model", "Anthropic does not know this model id")
    if isinstance(error, anthropic.RateLimitError):
        return LLMError("rate_limited", "Anthropic rate limit")
    if isinstance(error, anthropic.APIStatusError):
        return LLMError("api_error", f"Anthropic HTTP {error.status_code}")
    if isinstance(error, anthropic.APIConnectionError):
        return LLMError("network", "cannot reach the Anthropic API")
    return LLMError("api_error", f"Anthropic call failed ({type(error).__name__})")


def _check_anthropic_stop(response: Any) -> None:
    if response.stop_reason == "refusal":
        raise LLMError("refused", "the model declined (stop_reason refusal)")
    if response.stop_reason == "max_tokens":
        raise LLMError("bad_output", "the answer hit max_tokens and is incomplete")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        if client is None:
            import anthropic

            # Redirects are not followed: the key goes to the documented endpoint and nowhere else.
            client = anthropic.Anthropic(
                api_key=api_key,
                base_url=ANTHROPIC_BASE_URL,
                max_retries=0,
                http_client=anthropic.DefaultHttpxClient(follow_redirects=False),
            )
        self._client = client

    def _messages(self, request: TextRequest) -> Any:
        return self._client.with_options(timeout=attempt_timeout_s(request), max_retries=request.retries).beta.messages

    def complete(self, request: TextRequest) -> str:
        kwargs = anthropic_request_kwargs(request)
        try:
            response = run_with_deadline(lambda: self._messages(request).create(**kwargs), request.timeout_s)
        except Exception as error:
            raise _anthropic_error(error) from None
        _check_anthropic_stop(response)
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise LLMError("bad_output", "the answer has no text")
        return text

    def structured(self, request: TextRequest, schema: type[T]) -> T:
        kwargs = anthropic_request_kwargs(request)
        try:
            response = run_with_deadline(
                lambda: self._messages(request).parse(**kwargs, output_format=schema), request.timeout_s
            )
        except Exception as error:
            raise _anthropic_error(error) from None
        _check_anthropic_stop(response)
        parsed = response.parsed_output
        if not isinstance(parsed, schema):
            raise LLMError("bad_output", f"the answer does not match {schema.__name__}")
        return parsed


# ---------------------------------------------------------------- OpenAI


def openai_request_kwargs(request: TextRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": request.model_id,
        "instructions": request.system,
        "input": request.user,
        "max_output_tokens": request.max_tokens,
    }
    if OPENAI_REASONING.match(request.model_id):
        kwargs["reasoning"] = {"effort": request.effort}
    return kwargs


def _openai_error(error: Exception) -> LLMError:
    import openai

    if isinstance(error, LLMError):
        return error
    if isinstance(error, openai.APITimeoutError):
        return LLMError("timeout", "OpenAI request timed out")
    if isinstance(error, openai.AuthenticationError):
        return LLMError("auth", "OpenAI rejected OPENAI_API_KEY")
    if isinstance(error, openai.NotFoundError):
        return LLMError("unknown_model", "OpenAI does not know this model id (check the spelling, e.g. gpt-6.1-sol)")
    if isinstance(error, openai.RateLimitError):
        return LLMError("rate_limited", "OpenAI rate limit")
    if isinstance(error, openai.APIStatusError):
        return LLMError("api_error", f"OpenAI HTTP {error.status_code}")
    if isinstance(error, openai.APIConnectionError):
        return LLMError("network", "cannot reach the OpenAI API")
    return LLMError("api_error", f"OpenAI call failed ({type(error).__name__})")


def _check_openai_output(response: Any) -> None:
    if getattr(response, "status", None) == "incomplete":
        raise LLMError("bad_output", "the answer is incomplete (max_output_tokens or a content filter)")
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                raise LLMError("refused", "the model declined")


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        if client is None:
            import openai

            client = openai.OpenAI(
                api_key=api_key,
                base_url=OPENAI_BASE_URL,
                max_retries=0,
                http_client=openai.DefaultHttpxClient(follow_redirects=False),
            )
        self._client = client

    def _responses(self, request: TextRequest) -> Any:
        return self._client.with_options(timeout=attempt_timeout_s(request), max_retries=request.retries).responses

    def complete(self, request: TextRequest) -> str:
        kwargs = openai_request_kwargs(request)
        try:
            response = run_with_deadline(lambda: self._responses(request).create(**kwargs), request.timeout_s)
        except Exception as error:
            raise _openai_error(error) from None
        _check_openai_output(response)
        text = str(response.output_text or "").strip()
        if not text:
            raise LLMError("bad_output", "the answer has no text")
        return text

    def structured(self, request: TextRequest, schema: type[T]) -> T:
        kwargs = openai_request_kwargs(request)
        try:
            response = run_with_deadline(
                lambda: self._responses(request).parse(**kwargs, text_format=schema), request.timeout_s
            )
        except Exception as error:
            raise _openai_error(error) from None
        _check_openai_output(response)
        parsed = response.output_parsed
        if not isinstance(parsed, schema):
            raise LLMError("bad_output", f"the answer does not match {schema.__name__}")
        return parsed


# ---------------------------------------------------------------- routing


@dataclass(frozen=True)
class Credential:
    """Which route serves a model family, and the variable that enables it. The value never prints."""

    route: Route
    variable: str
    secret: str = field(repr=False)


def _value(secret: SecretStr | None) -> str | None:
    value = secret.get_secret_value().strip() if secret is not None else ""
    return value or None


def credential_for(provider: Provider, settings: Settings) -> Credential | None:
    """Claude: ANTHROPIC_API_KEY (the API) first, else CLAUDE_CODE_OAUTH_TOKEN (the subscription).
    OpenAI: OPENAI_API_KEY. None when nothing is set."""
    if provider == "openai":
        key = _value(settings.openai_api_key)
        return Credential("openai", KEY_VARIABLES["openai"], key) if key else None
    key = _value(settings.anthropic_api_key)
    if key:
        return Credential("anthropic", KEY_VARIABLES["anthropic"], key)
    token = _value(settings.claude_code_oauth_token)
    return Credential(SUBSCRIPTION, TOKEN_VARIABLE, token) if token else None


ProviderFactory = Callable[[Route, str], LLMProvider]


def default_factory(route: Route, secret: str) -> LLMProvider:
    if route == SUBSCRIPTION:
        from bazaar_agent.runtime.claude import SubscriptionProvider  # imports the Agent SDK only when used

        return SubscriptionProvider(secret)
    return AnthropicProvider(secret) if route == "anthropic" else OpenAIProvider(secret)


def provider_for(ref: ModelRef, settings: Settings, factory: ProviderFactory = default_factory) -> LLMProvider:
    """The provider that serves `ref`. Raises `LLMError("key_missing")` naming the variables, never a value."""
    credential = credential_for(ref.provider, settings)
    if credential is None:
        variables = CREDENTIAL_VARIABLES[ref.provider]
        raise LLMError("key_missing", f"set {variables} in .env to use {ref.alias} ({ref.provider})")
    from bazaar_agent.llm.traced import TracedProvider

    return TracedProvider(factory(credential.route, credential.secret), credential.route)
