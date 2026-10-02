"""`bazaar llm`, `bazaar ask`, `bazaar steer` and `--llm-runtime` through the real CLI, with fakes: no network."""

import pytest
from typer.testing import CliRunner

from bazaar_agent.cli import app
from bazaar_agent.llm import cli as llm_cli
from bazaar_agent.llm.chooser import ModelChooser
from bazaar_agent.llm.config import RuntimeConfig
from bazaar_agent.llm.runtime import LLMRuntime
from bazaar_agent.ticks import Clock
from tests.test_llm import FakeJudge, FakeProvider, verdict

runner = CliRunner()
SECRET = "sk-ant-THIS-MUST-NOT-PRINT"


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("BAZAAR_KEY", "OPENAI_API_KEY", "BAZAAR_LLM_RUNTIME", "TYPESAFE_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("BAZAAR_DATA_DIR", str(tmp_path))
    # An empty env var falls back to the repo's .env, so a developer's real keys would leak in: isolate.
    monkeypatch.setattr("bazaar_agent.config.read_env_file", lambda path: {})
    monkeypatch.setattr(llm_cli.console, "width", 240)
    monkeypatch.setattr(llm_cli, "_public_clock", lambda settings: Clock(tick=300, tick_seconds=60, next_tick_in=40))
    return tmp_path


def fake_runtime(monkeypatch, provider):
    def build(settings, loaded, rules):
        config = RuntimeConfig()
        chooser = ModelChooser(
            config,
            pin=None,
            jev_timeout_s=3.0,
            jev_api_key="ts",
            log_path=settings.data_dir / "llm" / "model-choices.jsonl",
            judge_fn=FakeJudge(verdict("sonnet-5-5", 0.83, {"sonnet-5-5": 0.83, "haiku-4-5": 0.17})),
        )
        return LLMRuntime(config, settings, chooser, factory=lambda prov, key: provider)

    monkeypatch.setattr(llm_cli, "_runtime", build)


def test_llm_shows_config_keys_set_never_values_and_the_flag_pin(env):
    out = runner.invoke(app, ["--llm-runtime", "opus-5-5", "llm"])
    assert out.exit_code == 0, out.output
    assert "pinned by flag" in out.output and "ANTHROPIC_API_KEY set" in out.output
    assert SECRET not in out.output and "OPENAI_API_KEY not set" in out.output
    auto = runner.invoke(app, ["llm"])
    assert "Jev chooses among" in auto.output and "No model choices logged yet" in auto.output
    bad = runner.invoke(app, ["--llm-runtime", "llama-3", "llm"])
    assert bad.exit_code == 1 and "unknown model" in bad.output


def test_ask_without_a_key_prints_which_variable_to_set(env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    out = runner.invoke(app, ["ask", "buy LAV-09 under 90"])
    assert out.exit_code == 1 and "set ANTHROPIC_API_KEY" in out.output


def test_ask_prints_the_jev_floats_the_intent_and_the_command_without_trading(env, monkeypatch):
    draft = {
        "kind": "buy",
        "item": "LAV-09",
        "max_price": 90,
        "min_price": None,
        "counterparty": None,
        "constraints": [],
        "question": None,
    }
    fake_runtime(monkeypatch, FakeProvider(draft=draft))
    out = runner.invoke(app, ["ask", "buy LAV-09 under 90"])
    assert out.exit_code == 0, out.output
    assert "sonnet-5-5 ← jev" in out.output and "sonnet-5-5 0.830" in out.output
    assert "guardrail check skipped: BAZAAR_KEY" in out.output
    assert "uv run bazaar dealer buy LAV-09 --max 90 --start 45" in out.output and "dry run" in out.output
    vague = {**draft, "kind": "clarify", "item": None, "max_price": None, "question": "Which card?"}
    fake_runtime(monkeypatch, FakeProvider(draft=vague))
    assert "Which card?" in runner.invoke(app, ["ask", "buy something"]).output


def test_steer_saves_shows_and_clears_a_clamped_steering(env, monkeypatch):
    answer = {
        "deltas": [{"param": "scarcity_weight", "delta": 9.0}],
        "ttl_ticks": 60,
        "summary": "chase rares",
        "question": None,
    }
    fake_runtime(monkeypatch, FakeProvider(draft=answer))
    out = runner.invoke(app, ["steer", "be more aggressive with rares tonight"])
    assert out.exit_code == 0, out.output
    assert "steering saved" in out.output and "ticks 300..360" in out.output
    assert (env / "steering.json").is_file()
    shown = runner.invoke(app, ["steer", "--show"])
    assert "active" in shown.output and "1.5" in shown.output and "yes" in shown.output  # clamped to ±50 %
    assert "steering cleared" in runner.invoke(app, ["steer", "--clear"]).output
    assert "no steering" in runner.invoke(app, ["steer", "--show"]).output


def test_words_for_returns_the_template_unless_llm_words_is_on(env):
    from bazaar_agent.agents.dealer import template_words
    from bazaar_agent.config import load_settings
    from bazaar_agent.guardrails import Guardrails

    assert llm_cli.words_for(load_settings(), Guardrails(), template_words) is template_words
