"""Bounded Opus sales wording. Code owns terms; model failures use neutral templates."""

from __future__ import annotations

from collections.abc import Callable

from bazaar_agent import telemetry as tm
from bazaar_agent.agents.words import WordsFn, WordsRequest
from bazaar_agent.config import Settings
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.llm.chooser import injection_flags
from bazaar_agent.llm.config import load_runtime
from bazaar_agent.llm.models import resolve
from bazaar_agent.llm.providers import LLMError, TextRequest, provider_for
from bazaar_agent.llm.words import (
    _GIFTS_OR_IMPERSONATION,
    MIN_WORDS_S,
    WORDS_MARGIN_S,
    WORDS_MAX_TOKENS,
    WORDS_SYSTEM,
    forbidden_in,
    guard_text,
    words_prompt,
)


def sales_template(request: WordsRequest) -> str:
    return "Hola: te propongo una operación con los términos exactos adjuntos. Si te encaja, puedes aceptarla."


def sales_words(settings: Settings, rules: Guardrails, log: Callable[[str], None]) -> WordsFn:
    config = load_runtime().config
    try:
        provider = provider_for(resolve("opus-5-5"), settings)
    except LLMError as error:
        log(f"sales words: template ({error.reason})")
        return sales_template

    def say(request: WordsRequest) -> str:
        budget = min(config.subscription_words_timeout_s, max(0.0, request.budget_s - WORDS_MARGIN_S))
        if budget < MIN_WORDS_S:
            return sales_template(request)
        with tm.span("sales.words", tm.CHAIN, {"bazaar.counterparty": request.counterparty}) as span:
            try:
                raw = provider.complete(
                    TextRequest(
                        "claude-opus-5-5",
                        WORDS_SYSTEM,
                        words_prompt(request, config.words_max_chars),
                        WORDS_MAX_TOKENS,
                        budget,
                        purpose="words",
                    )
                )
                text = guard_text(raw, config.words_max_chars)
                if (
                    text
                    and not injection_flags(text)
                    and not _GIFTS_OR_IMPERSONATION.search(text)
                    and not forbidden_in(text, request)
                ):
                    return text
                tm.set_attributes(span, {"bazaar.llm.fallback": "unsafe_words"})
            except LLMError as error:
                tm.set_attributes(span, {"bazaar.llm.error": error.reason})
            except Exception as error:
                log(f"sales words: template ({type(error).__name__})")
            return sales_template(request)

    return say
