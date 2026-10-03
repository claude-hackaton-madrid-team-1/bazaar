"""N18 lean tracing: sessions, Jev EVALUATOR spans, AGENT/TOOL spans, LLM spans, and the invariants."""

import random
import time
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from bazaar_agent import telemetry as tm
from bazaar_agent import traces
from bazaar_agent.agents.dealer import BidPlan
from bazaar_agent.agents.duelist import DuelMove
from bazaar_agent.agents.runtime import Recorder
from bazaar_agent.decisions import Decision
from bazaar_agent.llm.providers import LLMError, TextRequest
from bazaar_agent.llm.traced import TracedProvider
from bazaar_agent.sdk import BazaarError
from tests.test_telemetry import TOPIC, ChattyAbuela, advisor, jev_result, run, traced_run

SESSION = "session.id"
KIND = "openinference.span.kind"


def by_kind(spans, kind):
    return [s for s in spans.get_finished_spans() if s.attributes.get(KIND) == kind]


def all_text(spans):
    for s in spans.get_finished_spans():
        yield from (str(v) for v in s.attributes.values())
        for e in s.events:
            yield from (str(v) for v in e.attributes.values())


# ---------------------------------------------------------------- 1. session.id


def test_every_span_of_a_dealer_negotiation_carries_its_thread_session(spans):
    traced_run(ChattyAbuela([12, 10, 9]), advisor=advisor, guard=lambda move: None)
    finished = spans.get_finished_spans()
    assert finished and {s.attributes.get(SESSION) for s in finished} == {"dealer:abuela:thread:115"}


def test_duel_spans_carry_the_duel_session(spans):
    duels = traces.DuelTraces()
    duels.seen({"id": 7, "role": "seller", "your_limit": 40}, 100, DuelMove("offer", 60))
    with duels.tool(7, "duel_say"):
        pass
    duels.end_tick([7])
    duels.close()
    finished = spans.get_finished_spans()
    assert {s.name for s in finished} >= {"duel", "duel tick 100", "tool duel_say"}
    assert {s.attributes.get(SESSION) for s in finished} == {"duel:7"}


def test_loop_spans_carry_the_tick_session_and_agent_loops_are_agent_spans(spans):
    clock = SimpleNamespace(tick=312, t_hours=1.5)
    traces.per_tick("taker tick", lambda c: None, agent=True)(clock)
    traces.per_tick("monitor tick", lambda c: None)(clock)
    taker, monitor = spans.get_finished_spans()
    assert (taker.attributes[SESSION], taker.attributes[KIND]) == ("tick:312", "AGENT")
    assert (monitor.attributes[SESSION], monitor.attributes[KIND]) == ("tick:312", "CHAIN")


# ---------------------------------------------------------------- 2. Jev as EVALUATOR spans


def test_a_jev_call_is_an_evaluator_span_inside_its_session(spans):
    traced_run(ChattyAbuela([12, 10, 9]), advisor=advisor)
    jevs = by_kind(spans, "EVALUATOR")
    assert len(jevs) >= 1
    j = jevs[0]
    assert j.attributes[SESSION] == "dealer:abuela:thread:115"
    assert (j.attributes["bazaar.jev.question"], j.attributes["bazaar.jev.verdict"]) == ("negotiation_move", "counter")
    assert (j.attributes["bazaar.jev.value"], j.attributes["bazaar.jev.threshold"]) == (0.7, 0.75)
    assert j.attributes["bazaar.jev.latency_ms"] == 281
    assert (j.end_time - j.start_time) >= 281 * 1_000_000 * 0.9  # backdated by the call's latency
    parent = next(s for s in spans.get_finished_spans() if s.context.span_id == j.parent.span_id)
    assert parent.name.startswith("tick ")


# ---------------------------------------------------------------- 3. TOOL spans


def test_each_request_sent_to_the_game_is_a_tool_span_without_prices(spans):
    client = ChattyAbuela([12, 10, 9])
    traced_run(client)
    tools = by_kind(spans, "TOOL")
    assert {t.attributes["tool.name"] for t in tools} >= {"say"}
    assert len([t for t in tools if t.attributes["tool.name"] == "say"]) == len(client.sent)
    assert all(t.attributes["bazaar.ok"] is True for t in tools)
    assert not any("price" in k for t in tools for k in t.attributes)


def test_a_refused_request_is_a_tool_span_with_the_error_code(spans):
    traced_run(ChattyAbuela([12, 10, 9], refuse_first_say=True))
    failed = [t for t in by_kind(spans, "TOOL") if not t.attributes["bazaar.ok"]]
    assert [t.attributes["bazaar.error.code"] for t in failed] == ["wait_for_tick"]
    assert failed[0].status.status_code is StatusCode.ERROR


class _Log:
    def decide(self, d: Decision) -> int:
        return 1

    def executed(self, *a):  # noqa: ANN002
        pass

    def settle(self, *a):  # noqa: ANN002
        pass


def test_recorder_send_is_a_tool_span_per_request_with_ok_and_code(spans):
    rec = Recorder("taker", _Log(), live=True, log=lambda line: None)
    assert rec.send(1, 5, "accept", {"offer": 9, "price": 4411}, lambda: {"ok": True}) == {"ok": True}

    def refuse():
        raise BazaarError("insufficient_cash", "no cash", 400)

    assert rec.send(2, 5, "bid", {"price": 4411}, refuse) is None
    ok, bad = by_kind(spans, "TOOL")
    assert (ok.attributes["tool.name"], ok.attributes["bazaar.ok"]) == ("accept", True)
    assert (bad.attributes["bazaar.ok"], bad.attributes["bazaar.error.code"]) == (False, "insufficient_cash")
    assert not any("4411" in t for t in all_text(spans))


# ---------------------------------------------------------------- 4. LLM spans


class FakeProvider:
    name = "anthropic"

    def __init__(self, fail=False):
        self.fail = fail

    def complete(self, request):
        if self.fail:
            raise LLMError("timeout", "no answer")
        return "Venga, cariño, un precio justo."

    def structured(self, request, schema):
        return schema()


def test_llm_call_is_an_llm_span_with_text_only_for_words(spans):
    provider = TracedProvider(FakeProvider(), "anthropic")
    words = TextRequest("claude-haiku-4-5-20251001", "sys", "Abuela dijo: vende caro", 80, 5.0, purpose="words")
    steer = TextRequest("claude-sonnet-5-5", "sys", "anchor 0.55 floor 0.03 secret plan", 80, 5.0, purpose="steer")
    assert provider.complete(words) == "Venga, cariño, un precio justo."
    provider.complete(steer)
    w, s = by_kind(spans, "LLM")
    assert (w.attributes["llm.model_name"], w.attributes["llm.provider"]) == ("claude-haiku-4-5-20251001", "anthropic")
    assert "Abuela dijo" in w.attributes["input.value"] and "justo" in w.attributes["output.value"]
    assert "input.value" not in s.attributes and "output.value" not in s.attributes
    assert s.attributes["bazaar.llm.input_chars"] == len(steer.user)
    assert not any("0.55" in t for t in all_text(spans))


def test_llm_text_is_truncated_scrubbed_and_failures_are_recorded(spans):
    provider = TracedProvider(FakeProvider(), "anthropic")
    long = TextRequest(
        "m", "sys", f"{tm.scrub('x')}" + "y" * 5_000 + " tk-team1-very-secret-0042", 80, 5.0, purpose="words"
    )
    provider.complete(long)
    (llm,) = by_kind(spans, "LLM")
    assert len(llm.attributes["input.value"]) <= 600
    failing = TracedProvider(FakeProvider(fail=True), "anthropic")
    with pytest.raises(LLMError):
        failing.complete(long)
    bad = by_kind(spans, "LLM")[-1]
    assert bad.attributes["bazaar.error.code"] == "timeout" and bad.status.status_code is StatusCode.ERROR


# ---------------------------------------------------------------- invariants


def test_moves_are_identical_with_tracing_off_and_on():
    tm.uninstall()
    off = ChattyAbuela([12, 10, 9])
    out_off = run(off, advisor=advisor, guard=lambda m: None)
    exporter, provider = InMemorySpanExporter(), TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tm.install(provider.get_tracer("test"))
    try:
        on = ChattyAbuela([12, 10, 9])
        out_on = traced_run(on, advisor=advisor, guard=lambda m: None)
    finally:
        tm.uninstall()
    assert out_off == out_on
    assert (off.sent, off.accepted, off.closed, off.messages) == (on.sent, on.accepted, on.closed, on.messages)
    assert len(exporter.get_finished_spans()) > 0


def test_a_dead_exporter_costs_a_tick_neither_an_error_nor_time(caplog):
    config = tm.TracingConfig(enabled=True, endpoint="http://127.0.0.1:9/v1/traces", project="bazaar")
    assert tm.init_tracing("bazaar-test", config)
    try:
        started = time.monotonic()
        for _ in range(20):
            with tm.span("agent tick", tm.AGENT, session="tick:1"):
                tm.record_jev(jev_result(), "negotiation_move")
        assert time.monotonic() - started < 1.0
    finally:
        tm.shutdown_tracing()


@pytest.mark.parametrize("seed", range(8))
def test_no_private_limit_or_value_reaches_any_span(spans, seed):
    rng = random.Random(seed)
    limit, ceiling, value = (rng.randint(1_000, 9_999) for _ in range(3))
    plan = BidPlan(6, 1, ceiling)
    with traces.trace_negotiation("abuela", TOPIC, plan) as observer:
        run(ChattyAbuela([12, 10, 9]), observer, advisor=advisor)
    duels = traces.DuelTraces()
    duel = {"id": 3, "role": "seller", "your_limit": limit, "your_value": value, "rival_offer": {"price": 5}}
    duels.seen(duel, 100, DuelMove("offer", 60, reason="concede"))
    duels.end_tick([3])
    duels.close()
    lines = [
        f"duel 3 seller limit {limit} rival {{'price': 5}} deadline 112",
        f"thread 9 opened: plan BidPlan(start=6, step=1, max_price={ceiling})",
        f"tick 5 taker: WOULD accept LAV-08 (worth {value}.5, max {ceiling})",
        f'{{"your_limit": {limit}, "our_limit": {ceiling}, "card_value": {value}}}',
    ]
    with tm.span("loop", tm.AGENT, root=True):
        for line in lines:
            tm.event("console", {"line": line})
            tm.event("decision", {"line": line})
    texts = list(all_text(spans))
    for secret in (limit, ceiling, value):
        assert not any(str(secret) in t for t in texts), secret


# ---------------------------------------------------------------- review findings (security-auditor)


@pytest.mark.parametrize(
    "line",
    [
        "open thread with abuela for LAV-08 (ladder 17→26, worth 157)",
        "ladder (17, 26) ceiling 26 cap 90 budget 120",
        "limit is $80 and limit of 80, worth about 80",
        "maximum: 1,200 reservation 70",
    ],
)
def test_ladders_ceilings_and_caps_are_cut_from_span_text(line):
    cleaned = tm.scrub_for_span(line)
    for number in ("26", "157", "90", "120", "80", "1,200", "200", "70"):
        assert number not in cleaned.replace("LAV-08", ""), (line, cleaned)


def test_a_decision_event_carries_no_free_text_line(spans):
    rec = Recorder("taker", _Log(), live=False, log=lambda line: None)
    with tm.span("taker tick", tm.AGENT, root=True):
        rec.decide(
            5, "open_thread", "ladder 17→26 worth 157", inputs={}, reason="r", guardrail="ok", chosen=True,
            status="approved",
        )  # fmt: skip
    assert not any("26" in t or "157" in t for t in all_text(spans))


def test_tables_are_not_mirrored_but_text_lines_are(spans):
    from rich.console import Console
    from rich.table import Table

    console = Console(width=120, file=__import__("io").StringIO())
    tm.capture_console(console, "rules show")
    table = Table("rule", "value")
    table.add_row("max_price_rare", "80")
    with tm.span("cli rules", tm.CHAIN, root=True):
        console.print(table)
        console.print("tick 5 hello")
    texts = list(all_text(spans))
    assert not any("80" in t for t in texts) and any("tick 5 hello" in t for t in texts)


def test_unsent_prices_and_the_duel_limit_stay_out_of_the_duel_trace(spans):
    duels = traces.DuelTraces()
    duel = {"id": 4, "role": "buyer", "your_limit": 7351, "done": True}
    duels.seen(duel, 100, DuelMove("offer", 6123, None, "concede"))
    duels.close()
    assert not any("6123" in t or "7351" in t for t in all_text(spans))
