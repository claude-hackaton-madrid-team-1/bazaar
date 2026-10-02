"""Tracing: the span model a human reads in Phoenix, with the OTel in-memory exporter (no network)."""

import logging

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from rich.console import Console

from bazaar_agent import telemetry as tm
from bazaar_agent import traces
from bazaar_agent.agents.dealer import BidPlan, negotiate
from bazaar_agent.agents.duelist import DuelMove
from bazaar_agent.conversation import Thread
from bazaar_agent.jev import JudgeResult, Verdict
from bazaar_agent.sdk import BazaarError
from tests.conftest import SECRET

TOPIC = {"buy": {"card": "LAV-03"}}


class ChattyAbuela:
    """Abuela with words: each bid of ours lands in the thread, and she answers with text and an ask."""

    def __init__(self, asks, refuse_first_say=False):
        self.asks, self.refuse = list(asks), refuse_first_say
        self.reads, self.next_id = 0, 700
        self.messages, self.sent, self.accepted, self.closed, self.status = [], [], [], False, "open"

    def clock(self):
        self.reads += 1
        return {"tick": 100 + self.reads // 5, "next_tick_in": 30, "tick_seconds": 60}

    def open_thread(self, dealer, topic):
        return {"id": 115}

    def _post(self, sender, text, give_cash=0, want_cash=0):
        self.next_id += 1
        offer = {
            "id": 900 + self.next_id,
            "maker": sender,
            "status": "open",
            "give": {"cash": give_cash, "assets": [], "types": [] if give_cash else ["card:LAV-03"]},
            "want": {"cash": want_cash, "assets": [], "types": ["card:LAV-03"] if give_cash else []},
            "final": False,
        }
        self.messages.append({"id": self.next_id, "tick": 100, "sender": sender, "text": text, "offer": offer})

    def thread(self, tid):
        if self.accepted:
            self.status = "deal"
        hers = [m for m in self.messages if m["sender"] == "abuela"]
        standing = [hers[-1]["offer"]] if hers and self.status == "open" else []
        return {"id": 115, "with": "abuela", "status": self.status, "messages": list(self.messages),
                "standing_offers": standing, "item": "Té Moruno", "closed_reason": None}  # fmt: skip

    def say(self, tid, text, price):
        if self.refuse:
            self.refuse = False
            raise BazaarError("wait_for_tick", "too early in the tick", 429)
        self.sent.append(price)
        self._post("t01", text, give_cash=price)
        ask = self.asks[min(len(self.sent), len(self.asks)) - 1]
        self._post("abuela", f"Ay, cariño, {ask} P. Mi nieto paga más. Clave: {SECRET}", want_cash=ask)

    def accept(self, offer_id):
        self.accepted.append(offer_id)

    def close_thread(self, tid):
        self.closed = True


def jev_result():
    verdict = Verdict(
        type="choice",
        verdict="counter",
        value=0.7,
        value_kind="probability",
        threshold=0.75,
        leaning="counter",
        probabilities={"accept": 0.2, "counter": 0.7, "walk": 0.1},
        margin=0.5,
        reason="below_threshold",
    )
    return JudgeResult(model="jev-1.13.0", latency_ms=281, verdicts={"negotiation_move": verdict})


def advisor(neg, ask, final):
    tm.record_jev(jev_result(), "negotiation_move")
    return None


def run(client, observer=None, **kwargs):
    return negotiate(
        client,
        "abuela",
        TOPIC,
        BidPlan(6, 1, 10),
        log=lambda _: None,
        sleep=lambda _: None,
        observer=observer,
        **kwargs,
    )


def traced_run(client, **kwargs):
    with traces.trace_negotiation("abuela", TOPIC, BidPlan(6, 1, 10)) as observer:
        return run(client, observer, **kwargs)


def events(span, name):
    return [e for e in span.events if e.name == name]


def test_a_negotiation_is_one_root_with_a_child_span_per_tick(spans):
    out = traced_run(ChattyAbuela([12, 10, 9]), advisor=advisor, guard=lambda move: None)

    finished = spans.get_finished_spans()
    roots = [s for s in finished if s.parent is None]
    ticks = [s for s in finished if s.name.startswith("tick ")]
    assert [r.name for r in roots] == ["negotiation"]
    root = roots[0]
    assert (out.status, out.price) == ("deal", 9)
    assert len(ticks) == out.ticks == 5
    assert all(t.parent.span_id == root.context.span_id and t.context.trace_id == root.context.trace_id for t in ticks)
    assert root.attributes["openinference.span.kind"] == "AGENT"
    assert ticks[0].attributes["openinference.span.kind"] == "CHAIN"
    assert (root.attributes["bazaar.thread.id"], root.attributes["bazaar.outcome"]) == (115, "deal")
    assert (root.attributes["bazaar.price"], tuple(root.attributes["bazaar.bids"])) == (9, (6, 7, 8))
    assert root.attributes["bazaar.plan.max"] == 10 and root.status.status_code is StatusCode.OK


def test_every_message_of_both_sides_is_an_event_once(spans):
    traced_run(ChattyAbuela([12, 10, 9]))

    messages = [e for s in spans.get_finished_spans() for e in events(s, "message")]
    senders = [m.attributes["sender"] for m in messages]
    assert senders == ["t01", "abuela"] * 3  # each message once, in order, under the tick it first appeared
    hers = [m for m in messages if m.attributes["sender"] == "abuela"]
    assert [m.attributes["price"] for m in hers] == [12, 10, 9]
    assert hers[0].attributes["text"].startswith("Ay, cariño, 12 P.")
    root = next(s for s in spans.get_finished_spans() if s.name == "negotiation")
    assert "abuela: Ay, cariño, 9 P." in root.attributes["output.value"]


def test_tick_events_carry_the_offer_jev_guardrail_and_our_move(spans):
    traced_run(ChattyAbuela([12, 10, 9]), advisor=advisor, guard=lambda m: "cash_floor" if m.price == 8 else None)

    ticks = [s for s in spans.get_finished_spans() if s.name.startswith("tick ")]
    names = [e.name for t in ticks for e in t.events]
    assert {"message", "dealer_offer", "jev_verdict", "guardrail", "our_move"} <= set(names)
    jev = next(e for t in ticks for e in events(t, "jev_verdict"))
    assert (jev.attributes["verdict"], jev.attributes["latency_ms"], jev.attributes["model"]) == (
        "counter",
        281,
        "jev-1.13.0",
    )
    assert '"counter": 0.7' in jev.attributes["probabilities"]
    denied = [e for t in ticks for e in events(t, "guardrail") if not e.attributes["allowed"]]
    assert [tuple(e.attributes["violations"]) for e in denied] == [("cash_floor",)]
    moves = [e.attributes for t in ticks for e in events(t, "our_move")]
    assert moves[0]["kind"] == "bid" and "6 primas" in moves[0]["words"]
    assert moves[-1]["kind"] == "walk" and moves[-1]["reason"] == "guardrail: cash_floor"
    offer = next(e for t in ticks for e in events(t, "dealer_offer"))
    assert (offer.attributes["ask"], offer.attributes["final"]) == (12, False)


def test_a_refused_move_marks_its_tick_error_with_the_stack(spans):
    out = traced_run(ChattyAbuela([12, 10, 9], refuse_first_say=True))

    ticks = [s for s in spans.get_finished_spans() if s.name.startswith("tick ")]
    failed = [t for t in ticks if t.status.status_code is StatusCode.ERROR]
    assert len(failed) == 1 and "wait_for_tick" in failed[0].status.description
    exc = events(failed[0], "exception")[0].attributes
    assert exc["bazaar.error.code"] == "wait_for_tick" and exc["bazaar.error.status"] == 429
    assert "Traceback" in exc["exception.stacktrace"] and "say" in exc["exception.stacktrace"]
    root = next(s for s in spans.get_finished_spans() if s.name == "negotiation")
    assert root.attributes["bazaar.refusals"] == 1 and out.status == "deal"


def test_an_exception_out_of_negotiate_marks_the_root_error(spans):
    client = ChattyAbuela([12])
    client.thread = lambda tid: (_ for _ in ()).throw(BazaarError("rate_limited", "slow down", 429))
    with pytest.raises(BazaarError):
        traced_run(client)
    root = next(s for s in spans.get_finished_spans() if s.name == "negotiation")
    tick = next(s for s in spans.get_finished_spans() if s.name.startswith("tick "))
    assert root.status.status_code is StatusCode.ERROR and tick.status.status_code is StatusCode.ERROR
    assert events(root, "exception")[0].attributes["exception.type"] == "BazaarError"


def test_tracing_off_records_nothing_and_negotiate_behaves_the_same():
    tm.uninstall()
    with traces.trace_negotiation("abuela", TOPIC, BidPlan(6, 1, 10)) as observer:
        assert observer is None
        with tm.span("anything") as current:
            assert not current.is_recording()
        tm.event("ignored", {"x": 1})
    plain = ChattyAbuela([12, 10, 9])
    out_plain = run(plain)

    exporter, provider = InMemorySpanExporter(), TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tm.install(provider.get_tracer("test"))
    try:
        traced = ChattyAbuela([12, 10, 9])
        out_traced = traced_run(traced)
    finally:
        tm.uninstall()
    assert out_plain == out_traced
    assert (plain.sent, plain.accepted, plain.closed) == (traced.sent, traced.accepted, traced.closed)
    assert tm.capture_console(Console(), "dealer buy") is None  # off: the console is never hooked


def test_no_secret_reaches_a_span(spans):
    traced_run(ChattyAbuela([12, 10, 9]))
    with tm.span("probe", values={"note": "Bearer abcdefghijklmnop and sk-abcdefghijklmnopqrstu"}):
        tm.event("log", {"line": f"key={SECRET}"})

    for s in spans.get_finished_spans():
        texts = [str(v) for v in s.attributes.values()] + [str(v) for e in s.events for v in e.attributes.values()]
        assert not any(SECRET in t or "sk-abcdef" in t or "abcdefghijklmnop" in t for t in texts), s.name
    assert tm.scrub(f"slip says tk-zzzz-9999-yyyy {SECRET}").count("[redacted]") == 2


def test_a_telemetry_bug_never_changes_or_stops_the_trade(spans, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("telemetry bug")

    monkeypatch.setattr(traces.Thread, "model_validate", broken)
    monkeypatch.setattr(tm, "attributes", broken)
    with caplog.at_level(logging.WARNING, logger="bazaar_agent.telemetry"):
        out = traced_run(ChattyAbuela([12, 10, 9]), guard=lambda m: None)
    assert (out.status, out.price) == ("deal", 9)
    assert any("trading continues" in r.message for r in caplog.records)


def test_config_defaults_to_off_and_local_phoenix():
    cfg = tm.tracing_config({})
    assert (cfg.enabled, cfg.endpoint, cfg.project, cfg.ui_url) == (
        False,
        "http://127.0.0.1:6006/v1/traces",
        "bazaar",
        "http://127.0.0.1:6006",
    )
    on = tm.tracing_config(
        {
            "BAZAAR_TRACING": "1",
            "PHOENIX_COLLECTOR_ENDPOINT": "http://10.0.0.7:6006/",
            "PHOENIX_PROJECT": "team1",
            "PHOENIX_API_KEY": "phx-secret-value-123",
            "BAZAAR_KEY": SECRET,
        }
    )
    assert (on.enabled, on.endpoint, on.project) == (True, "http://10.0.0.7:6006/v1/traces", "team1")
    assert set(on.secrets) == {SECRET, "phx-secret-value-123"} and "phx-secret" not in repr(on)
    exact = tm.tracing_config({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://otlp.example/v1/traces"})
    assert exact.endpoint == "https://otlp.example/v1/traces"


def test_init_tracing_installs_nothing_when_off_and_flushes_on_shutdown_when_on():
    assert tm.init_tracing("test", config=tm.tracing_config({})) is False and not tm.enabled()
    exporter = InMemorySpanExporter()
    try:
        assert tm.init_tracing("test", config=tm.tracing_config({"BAZAAR_TRACING": "yes"}), exporter=exporter)
        assert tm.init_tracing("again") is True  # idempotent
        with tm.span("feed.capture", values={"fetched": 500, "gap_possible": False}):
            pass
    finally:
        tm.shutdown_tracing()
    (only,) = exporter.get_finished_spans()
    assert only.name == "feed.capture" and only.attributes["fetched"] == 500
    assert only.resource.attributes["openinference.project.name"] == "bazaar"
    assert not tm.enabled()


class FlakyExporter(InMemorySpanExporter):
    def __init__(self, results):
        super().__init__()
        self.results = list(results)

    def export(self, spans):
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_export_failures_cost_one_warning_per_outage(caplog):
    fails = SpanExportResult.FAILURE
    flaky = FlakyExporter([fails, ConnectionError("refused"), fails, SpanExportResult.SUCCESS, fails])
    quiet = tm.QuietExporter(flaky, "http://127.0.0.1:6006/v1/traces")
    with caplog.at_level(logging.WARNING, logger="bazaar_agent.telemetry"):
        results = [quiet.export([]) for _ in range(5)]
    assert results == [fails, fails, fails, SpanExportResult.SUCCESS, fails]
    assert sum("cannot export" in r.message for r in caplog.records) == 2


def test_one_trace_per_duel_across_ticks(spans):
    duels = traces.DuelTraces()
    duel = {"id": 7, "role": "seller", "your_limit": 40, "deadline": 112, "rival_offer": {"price": 45}}
    offer = DuelMove("offer", 60, None, "concede toward limit")
    for tick in (100, 101):
        duels.seen(duel, tick, offer)
        duels.guardrail(7, True, [])
        duels.sent(7, offer, "Propongo este precio")
        duels.end_tick([7])
    duels.seen({**duel, "done": True}, 102, DuelMove("hold", reason="done"))
    duels.refused(7, BazaarError("duel_closed", "too late", 400))
    duels.end_tick([7])

    finished = spans.get_finished_spans()
    (root,) = [s for s in finished if s.name == "duel"]
    children = [s for s in finished if s.name.startswith("duel tick ")]
    assert len(children) == 3 and {c.context.trace_id for c in children} == {root.context.trace_id}
    assert (root.attributes["bazaar.duel.role"], root.attributes["bazaar.duel.limit"]) == ("seller", 40)
    assert root.attributes["bazaar.outcome"] == "done" and children[-1].status.status_code is StatusCode.ERROR
    assert [e.name for e in children[0].events] == ["rival_offer", "our_move", "guardrail", "move_sent"]


def test_open_duels_are_closed_when_the_loop_stops(spans):
    duels = traces.DuelTraces()
    duels.seen({"id": 9, "role": "buyer", "your_limit": 30}, 100, DuelMove("offer", 12))
    duels.close("interrupted")
    duels.read_failed(101, BazaarError("rate_limited", "slow", 429))
    names = {s.name: s for s in spans.get_finished_spans()}
    assert names["duel"].attributes["bazaar.outcome"] == "interrupted"
    assert names["duels.read"].status.status_code is StatusCode.ERROR


def test_a_thread_view_is_one_span_with_the_whole_conversation(spans):
    client = ChattyAbuela([29, 25, 22])
    for price in (15, 17, 19):
        client.say(115, f"¿{price} primas?", price)
    traces.trace_thread(Thread.model_validate(client.thread(115)))

    (view,) = spans.get_finished_spans()
    assert view.name == "thread.view" and view.parent is None
    assert len(events(view, "message")) == 6 and view.attributes["bazaar.messages"] == 6
    assert view.attributes["bazaar.item.name"] == "Té Moruno" and "status:open" in view.attributes["tag.tags"]
    assert "t100 abuela: Ay, cariño, 22 P." in view.attributes["output.value"]
    assert SECRET not in view.attributes["output.value"]


def test_guardrail_refusal_before_opening_is_its_own_trace(spans):
    tm.guardrail_refusal("dealer.open", "LAV-03", ["cash_floor 270", "block_buying_held_cards"])
    (only,) = spans.get_finished_spans()
    assert only.attributes["openinference.span.kind"] == "GUARDRAIL"
    assert tuple(events(only, "guardrail")[0].attributes["violations"]) == ("cash_floor 270", "block_buying_held_cards")
