"""Claude on the subscription (CLAUDE_CODE_OAUTH_TOKEN) through a fake Agent SDK `query()`: no CLI, no network."""

import asyncio
import time

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLINotFoundError,
    ProcessError,
    RateLimitEvent,
    RateLimitInfo,
    ResultError,
    ResultMessage,
)
from pydantic import SecretStr
from typer.testing import CliRunner

from bazaar_agent import telemetry as tm
from bazaar_agent.agents.dealer import template_words
from bazaar_agent.agents.words import WordsRequest
from bazaar_agent.cli import app
from bazaar_agent.config import Settings, load_settings
from bazaar_agent.guardrails import Guardrails
from bazaar_agent.llm import cli as llm_cli
from bazaar_agent.llm import intent as it
from bazaar_agent.llm import words as wd
from bazaar_agent.llm.config import LoadedRuntime, RuntimeConfig, load_runtime
from bazaar_agent.llm.models import resolve
from bazaar_agent.llm.providers import SUBSCRIPTION, LLMError, TextRequest, default_factory, provider_for
from bazaar_agent.llm.runtime import LLMRuntime, build_runtime
from bazaar_agent.runtime import claude as cl
from tests.test_llm import FakeJudge, FakeProvider, chooser, verdict

TOKEN = "sk-fake-oat01-THIS-TOKEN-MUST-NEVER-PRINT"
API_KEY = "sk-fake-api03-api-key-value"
RULES = Guardrails()
HAIKU = resolve("haiku-4-5").model_id
SONNET = resolve("sonnet-5-5").model_id
DRAFT = {
    "kind": "buy",
    "item": "LAV-09",
    "max_price": 90,
    "min_price": None,
    "counterparty": None,
    "constraints": [],
    "question": None,
}


# ---------------------------------------------------------------- fake SDK


def result(**kw):
    base = {
        "subtype": "success",
        "duration_ms": 900,
        "duration_api_ms": 800,
        "is_error": False,
        "num_turns": 1,
        "session_id": "s",
        "stop_reason": "end_turn",
        "result": "Buenos días, Carmen.",
    }
    return ResultMessage(**{**base, **kw})


def rejected(resets_at=None, kind="five_hour"):
    info = RateLimitInfo(status="rejected", resets_at=resets_at, rate_limit_type=kind)
    return RateLimitEvent(rate_limit_info=info, uuid="u", session_id="s")


def assistant_error(code):
    return AssistantMessage(content=[], model=HAIKU, error=code)


class FakeQuery:
    """Stands in for `claude_agent_sdk.query`: yields scripted messages, then raises, or hangs."""

    def __init__(self, *messages, raise_after=None, delay_s=0.0):
        self.messages, self.raise_after, self.delay_s = messages, raise_after, delay_s
        self.calls, self.closed = [], 0

    def __call__(self, *, prompt, options):
        self.calls.append((prompt, options))
        return self._stream()

    async def _stream(self):
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            for message in self.messages:
                yield message
            if self.raise_after is not None:
                raise self.raise_after
        finally:
            self.closed += 1


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def provider(fake, clock=None, token=TOKEN, tmp_path=None):
    return cl.SubscriptionProvider(token, query_fn=fake, clock=clock or Clock(), cwd=tmp_path)


def words_request(timeout_s=4.0, model=HAIKU, retries=0):
    return TextRequest(model, "system", "user", 2048, timeout_s, retries=retries)


def failure(message, reason, *, fake=None):
    with pytest.raises(LLMError) as caught:
        provider(fake or message).complete(words_request())
    assert caught.value.reason == reason and TOKEN not in str(caught.value)
    return caught.value


def settings(tmp_path, **values):
    return Settings(data_dir=tmp_path, **{k: SecretStr(v) for k, v in values.items()})


# ---------------------------------------------------------------- routing


def test_claude_uses_the_api_key_first_then_the_subscription_token(tmp_path):
    seen = []
    factory = lambda route, secret: seen.append((route, secret)) or FakeProvider()  # noqa: E731
    both = settings(tmp_path, anthropic_api_key=API_KEY, claude_code_oauth_token=TOKEN)
    provider_for(resolve("opus-5-5"), both, factory)
    provider_for(resolve("opus-5-5"), settings(tmp_path, claude_code_oauth_token=TOKEN), factory)
    assert seen == [("anthropic", API_KEY), (SUBSCRIPTION, TOKEN)]
    with pytest.raises(LLMError, match="set OPENAI_API_KEY") as openai:
        provider_for(resolve("gpt-6-1-sol"), settings(tmp_path, claude_code_oauth_token=TOKEN), factory)
    with pytest.raises(LLMError, match="set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN") as none:
        provider_for(resolve("haiku-4-5"), settings(tmp_path), factory)
    assert openai.value.reason == none.value.reason == "key_missing" and TOKEN not in str(openai.value)


def test_the_default_factory_builds_the_agent_sdk_provider_for_the_subscription_route():
    built = default_factory(SUBSCRIPTION, TOKEN)
    assert isinstance(built, cl.SubscriptionProvider) and built.name == "claude-subscription"
    assert TOKEN not in repr(built) and "token=set" in repr(built)


def test_jev_is_offered_only_the_models_a_credential_can_reach(tmp_path):
    rt = build_runtime(settings(tmp_path, claude_code_oauth_token=TOKEN), RuntimeConfig(), RULES)
    assert rt.chooser.candidates == ("haiku-4-5", "sonnet-5-5", "opus-5-5")  # gpt-6-1-sol needs OPENAI_API_KEY
    assert rt.route("anthropic") == SUBSCRIPTION and rt.route("openai") is None
    keyed = settings(tmp_path, anthropic_api_key=API_KEY, claude_code_oauth_token=TOKEN)
    both = build_runtime(keyed, RuntimeConfig(), RULES)
    assert both.route("anthropic") == "anthropic"


def test_the_token_is_a_secret_in_settings_and_loads_from_env_or_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}\n")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    loaded = load_settings(env_file)
    assert loaded.claude_code_oauth_token is not None
    assert loaded.claude_code_oauth_token.get_secret_value() == TOKEN
    for text in (repr(loaded), str(loaded), loaded.model_dump_json()):
        assert TOKEN not in text


# ---------------------------------------------------------------- options


def test_options_lock_the_cli_down_and_follow_the_request(tmp_path):
    opts = cl.agent_options(words_request(timeout_s=6.0, retries=2), TOKEN, tmp_path)
    assert (opts.model, opts.system_prompt, opts.tools, opts.max_turns) == (HAIKU, "system", [], cl.TEXT_TURNS)
    assert opts.setting_sources == [] and opts.strict_mcp_config and opts.cwd == tmp_path
    assert opts.thinking == {"type": "disabled"} and opts.effort is None  # Haiku 4.5 takes no effort
    assert opts.extra_args == {"no-session-persistence": None} and opts.output_format is None
    assert opts.env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN and opts.env["API_TIMEOUT_MS"] == "2000"
    assert opts.env["CLAUDE_CODE_MAX_RETRIES"] == "2" and opts.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "2048"
    assert opts.env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com" and opts.env["ANTHROPIC_API_KEY"] == ""
    ask = TextRequest(SONNET, "s", "u", 8000, 30.0, effort="medium")
    sonnet = cl.agent_options(ask, None, tmp_path, it.IntentDraft)
    assert sonnet.effort == "medium" and sonnet.thinking is None and sonnet.max_turns == cl.STRUCTURED_TURNS
    assert sonnet.output_format == {"type": "json_schema", "schema": it.IntentDraft.model_json_schema()}
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in sonnet.env  # no token: the CLI keeps its own `claude` login


def test_the_token_never_prints_with_the_options():
    opts = cl.agent_options(words_request(), TOKEN, cl.Path("."))
    for text in (repr(opts), str(opts), repr(opts.env), str(opts.env)):
        assert TOKEN not in text
    assert "CLAUDE_CODE_OAUTH_TOKEN" in repr(opts.env)


# ---------------------------------------------------------------- calls


def test_complete_returns_the_result_text_and_closes_the_stream(tmp_path):
    fake = FakeQuery(result())
    assert provider(fake, tmp_path=tmp_path).complete(words_request()) == "Buenos días, Carmen."
    assert fake.closed == 1 and fake.calls[0][0] == "user"


def test_structured_output_is_validated_by_our_pydantic_model():
    fake = FakeQuery(result(num_turns=2, structured_output=DRAFT))
    draft = provider(fake).structured(words_request(model=SONNET), it.IntentDraft)
    assert (draft.kind, draft.item, draft.max_price) == ("buy", "LAV-09", 90)
    for bad in (None, {**DRAFT, "kind": "steal"}, {**DRAFT, "extra": 1}):
        with pytest.raises(LLMError, match="IntentDraft") as caught:
            provider(FakeQuery(result(structured_output=bad))).structured(words_request(), it.IntentDraft)
        assert caught.value.reason == "bad_output"
    retries = FakeQuery(result(subtype="error_max_structured_output_retries", is_error=True, result=None))
    with pytest.raises(LLMError) as gave_up:
        provider(retries).structured(words_request(), it.IntentDraft)
    assert gave_up.value.reason == "bad_output"


def test_a_slow_answer_times_out_inside_the_deadline_and_the_stream_is_closed():
    fake = FakeQuery(result(), delay_s=5.0)
    started = time.monotonic()
    with pytest.raises(LLMError) as caught:
        provider(fake).complete(words_request(timeout_s=0.2))
    assert caught.value.reason == "timeout" and time.monotonic() - started < 1.0
    time.sleep(0.3)  # the worker thread cancels the call: closing the stream is what ends the CLI process
    assert fake.closed == 1


def test_a_usage_limit_stops_calls_until_the_window_resets():
    clock = Clock()
    fake = FakeQuery(rejected(resets_at=int(clock.now) + 3600), assistant_error("rate_limit"), raise_after=None)
    subscription = provider(fake, clock)
    with pytest.raises(LLMError, match="five-hour limit reached; resets") as first:
        subscription.complete(words_request())
    with pytest.raises(LLMError) as again:
        subscription.complete(words_request())
    assert first.value.reason == again.value.reason == "usage_limit" and len(fake.calls) == 1  # no CLI start
    clock.now += 3601
    fake.messages = (result(),)
    assert subscription.complete(words_request()) == "Buenos días, Carmen." and len(fake.calls) == 2


def test_a_rejected_token_falls_back_and_pauses_names_the_variable_never_the_value():
    clock = Clock()
    bad = ResultError(f"Failed to authenticate {TOKEN}", {"is_error": True}, exit_code=1)
    fake = FakeQuery(
        assistant_error("authentication_failed"),
        result(is_error=True, api_error_status=401, result="Failed to authenticate"),
        raise_after=bad,
    )
    subscription = provider(fake, clock)
    with pytest.raises(LLMError, match="CLAUDE_CODE_OAUTH_TOKEN") as caught:
        subscription.complete(words_request())
    assert caught.value.reason == "auth" and TOKEN not in str(caught.value)
    with pytest.raises(LLMError):
        subscription.complete(words_request())
    clock.now += cl.AUTH_PAUSE_S + 1
    with pytest.raises(LLMError):
        subscription.complete(words_request())
    assert len(fake.calls) == 2


def test_every_cli_failure_becomes_a_fallback_reason():
    failure(FakeQuery(raise_after=CLINotFoundError()), "cli_missing")
    failure(FakeQuery(raise_after=ProcessError(f"died {TOKEN}", exit_code=2, stderr=TOKEN)), "api_error")
    failure(FakeQuery(assistant_error("model_not_found"), result(is_error=True, api_error_status=404)), "unknown_model")
    failure(FakeQuery(result(is_error=True, api_error_status=429)), "rate_limited")
    failure(FakeQuery(assistant_error("billing_error"), result(is_error=True)), "usage_limit")
    failure(FakeQuery(assistant_error("server_error"), result(is_error=True, api_error_status=529)), "api_error")
    failure(FakeQuery(result(stop_reason="refusal")), "refused")
    failure(FakeQuery(result(subtype="error_max_turns", is_error=True, result=None)), "bad_output")
    failure(FakeQuery(result(result="   ")), "bad_output")
    failure(FakeQuery(), "bad_output")  # the stream ended without a result


# ---------------------------------------------------------------- words, ask, the CLI


def subscription_runtime(tmp_path, fake, config=None):
    cfg = config or RuntimeConfig(llm_words=True)
    s = settings(tmp_path, claude_code_oauth_token=TOKEN)
    jev = chooser(tmp_path, FakeJudge(verdict("haiku-4-5", 0.5, {})), config=cfg)
    return LLMRuntime(cfg, s, jev, factory=lambda route, secret: provider(fake, token=secret))


def test_words_on_the_subscription_get_their_own_timeout_and_fall_back_on_failure(tmp_path):
    request = WordsRequest("abuela", 17, 1, "LAV-08", None, budget_s=12.0, tick=3, tick_seconds=15.0)
    ok = FakeQuery(result(result="Buenos días, Carmen, qué cromos tan bonitos tiene usted."))
    rt = subscription_runtime(tmp_path, ok)
    said = wd.llm_words(rt, template_words)(request)
    assert said.startswith("Buenos días") and ok.calls[0][1].env["API_TIMEOUT_MS"] == "6000"
    for fake in (FakeQuery(rejected()), FakeQuery(result(), delay_s=5.0), FakeQuery(raise_after=CLINotFoundError())):
        fast = RuntimeConfig(llm_words=True, subscription_words_timeout_s=0.9)
        assert wd.llm_words(subscription_runtime(tmp_path, fake, fast), template_words)(request) == template_words(
            request
        )


def test_ask_on_the_subscription_validates_the_intent(tmp_path):
    fake = FakeQuery(result(num_turns=2, structured_output=DRAFT))
    out = it.parse_request("buy LAV-09 under 90", subscription_runtime(tmp_path, fake))
    assert out.error is None and isinstance(out.outcome, it.Intent) and out.outcome.max_price == 90
    assert fake.calls[0][1].output_format["schema"] == it.IntentDraft.model_json_schema()


def test_the_committed_runtime_md_sets_a_subscription_words_budget_inside_a_sunday_tick():
    config = load_runtime().config
    assert config.words_timeout_s < config.subscription_words_timeout_s <= 15.0 / 2


runner = CliRunner()


@pytest.fixture
def subscription_env(tmp_path, monkeypatch):
    for name in ("BAZAAR_KEY", "OPENAI_API_KEY", "BAZAAR_LLM_RUNTIME", "TYPESAFE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    monkeypatch.setenv("BAZAAR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("bazaar_agent.config.read_env_file", lambda path: {})
    monkeypatch.setattr(llm_cli.console, "width", 240)
    return tmp_path


def test_bazaar_llm_names_the_active_claude_auth_and_never_prints_the_token(subscription_env):
    out = runner.invoke(app, ["llm"])
    assert out.exit_code == 0, out.output
    assert "Claude: Claude subscription via the Claude Agent SDK (CLAUDE_CODE_OAUTH_TOKEN)" in out.output
    assert "CLAUDE_CODE_OAUTH_TOKEN set" in out.output and "claude-subscription" in out.output
    assert "OPENAI_API_KEY not set" in out.output and TOKEN not in out.output


def test_the_startup_line_names_the_route_never_the_token(subscription_env, monkeypatch, capsys):
    on = LoadedRuntime(RuntimeConfig(llm_words=True), (), subscription_env / "RUNTIME.md")
    monkeypatch.setattr(llm_cli, "load_runtime", lambda: on)
    words = llm_cli.words_for(load_settings(), RULES, template_words)
    assert words is not template_words
    printed = capsys.readouterr().out
    assert "Claude subscription" in printed and TOKEN not in printed


def test_telemetry_cuts_the_token_out_of_every_span(monkeypatch):
    cfg = tm.tracing_config({"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "BAZAAR_TRACING": "1"})
    assert TOKEN in cfg.secrets and TOKEN not in repr(cfg)
    tm.install(None, secrets=cfg.secrets)  # type: ignore[arg-type]
    try:
        assert TOKEN not in tm.scrub(f"Failed to authenticate with {TOKEN}")
    finally:
        tm.uninstall()
    assert TOKEN not in tm.scrub(f"token {TOKEN}")  # tracing off: still cut out by shape (sk-…)
