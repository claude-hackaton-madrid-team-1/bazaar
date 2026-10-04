from bazaar_agent.agents import sales_words as sw
from bazaar_agent.agents.words import WordsRequest
from bazaar_agent.config import Settings
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.llm.providers import LLMError


class Provider:
    def __init__(self, result):
        self.result, self.calls = result, []

    def complete(self, request):
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def setup_words(monkeypatch, provider):
    monkeypatch.setattr(sw, "provider_for", lambda *_: provider)
    return sw.sales_words(Settings(), Guardrails(), lambda _: None)


def test_standard_opus_inside_budget_without_exposing_price(monkeypatch):
    provider = Provider("Podemos buscar una operación que nos venga bien.")
    say = setup_words(monkeypatch, provider)
    assert say(WordsRequest("team:t15", 10, budget_s=4)) == provider.result
    assert provider.calls[0].model_id == "claude-opus-5-5"
    assert 0 < provider.calls[0].timeout_s <= 3.5
    assert "10" not in provider.calls[0].user


def test_unsafe_words_and_short_budget_use_neutral_template(monkeypatch):
    provider = Provider("Acepto pagar 900 primas.")
    say = setup_words(monkeypatch, provider)
    request = WordsRequest("team:t15", 10, budget_s=4)
    assert say(request) == sw.sales_template(request)
    provider.calls.clear()
    assert say(WordsRequest("team:t15", 10, budget_s=0.1)) == sw.sales_template(request)
    assert not provider.calls


def test_usage_limit_uses_template_without_retry(monkeypatch):
    provider = Provider(LLMError("usage_limit", "quota"))
    say = setup_words(monkeypatch, provider)
    request = WordsRequest("team:t15", 10, budget_s=4)
    assert say(request) == sw.sales_template(request)
    assert len(provider.calls) == 1
