"""LLM spans: every provider call as one `LLM` span (model, provider, route, purpose, latency, error code).

Text is recorded only for `purpose="words"`: those words are sent to a counterparty anyway, so the prompt
(which quotes the counterparty: untrusted data) and the answer carry nothing of ours. Every other purpose
(intent, steering, model choice) records lengths only, because its prompt holds our parameters. Text is
truncated and scrubbed. Token counts are not exposed by our providers, so none are recorded.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from bazaar_agent import telemetry as tm
from bazaar_agent.llm.providers import LLMError, LLMProvider, TextRequest

T = TypeVar("T", bound=BaseModel)
TEXT_PURPOSES = frozenset({"words"})
MAX_TEXT = 512  # characters of a prompt or an answer kept in a span


class TracedProvider:
    """Wraps any `LLMProvider`; with tracing off it only forwards the call."""

    def __init__(self, inner: LLMProvider, route: str) -> None:
        self._inner, self._route = inner, route
        self.name = inner.name

    def complete(self, request: TextRequest) -> str:
        with tm.span(f"llm {request.model_id}", tm.LLM, self._values(request)) as current:
            text = self._guarded(current, lambda: self._inner.complete(request))
            tm.set_attributes(current, self._answer(request, text))
            return text

    def structured(self, request: TextRequest, schema: type[T]) -> T:
        with tm.span(f"llm {request.model_id}", tm.LLM, self._values(request)) as current:
            parsed = self._guarded(current, lambda: self._inner.structured(request, schema))
            tm.set_attributes(current, self._answer(request, parsed.model_dump_json()))
            return parsed

    def _values(self, request: TextRequest) -> dict[str, object]:
        values: dict[str, object] = {
            "llm.model_name": request.model_id,
            "llm.provider": self._inner.name,
            "bazaar.llm.route": self._route,
            "bazaar.llm.purpose": request.purpose or None,
            "bazaar.llm.input_chars": len(request.user),
        }
        if request.purpose in TEXT_PURPOSES:
            values[tm.INPUT] = request.user[:MAX_TEXT]
        return values

    @staticmethod
    def _answer(request: TextRequest, text: str) -> dict[str, object]:
        values: dict[str, object] = {"bazaar.llm.output_chars": len(text)}
        if request.purpose in TEXT_PURPOSES:
            values[tm.OUTPUT] = text[:MAX_TEXT]
        return values

    @staticmethod
    def _guarded(current: Any, call: Any) -> Any:
        try:
            return call()
        except LLMError as e:
            tm.set_attributes(current, {"bazaar.error.code": e.reason})
            raise
