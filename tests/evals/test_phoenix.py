"""Phoenix annotations against a fake Phoenix (httpx.MockTransport): payload shape, span lookup, refusals."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from bazaar_agent.evals.phoenix import (
    ANNOTATOR_IDENTIFIER,
    PhoenixAnnotator,
    SpanQuery,
    SpanRef,
    annotation_payload,
    annotator_from,
    span_query,
)
from bazaar_agent.evals.store import Pending

DUEL = Pending("duel", "duel:85", 0.6483, "good", "Deal at 138 ...", {"duel": 85}, "fri", 156)
THREAD = Pending("dealer", "thread:101", 0.6, "good", "Bought LAV-04 ...", {"thread": 101}, "fri", 60)
TRADE = Pending(
    "trade", "settlement:67", 0.3, "ok", "Bought ...", {"decision_agent": "taker", "decision_tick": 39}, None, 40
)
MARKET = Pending("market_test", "market_test:sat", 0.7, "good", "...", {}, "sat", 300)
# The keys `SpanAnnotationData` requires in our Phoenix 20.19.0 (/openapi.json).
REQUIRED = {"name", "annotator_kind", "span_id"}


class FakePhoenix:
    """Records requests; answers span searches from `spans` and annotation posts with ids."""

    def __init__(self, spans: dict[str, list[dict[str, Any]]], status: int = 200) -> None:
        self.spans, self.status = spans, status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "nope"})
        if request.method == "GET" and request.url.path == "/v1/projects/bazaar/spans":
            key = ",".join(request.url.params.get_list("attribute")) or request.url.params.get("name", "")
            return httpx.Response(200, json={"data": self.spans.get(key, []), "next_cursor": None})
        if request.method == "POST" and request.url.path == "/v1/span_annotations":
            body = json.loads(request.content)
            return httpx.Response(200, json={"data": [{"id": f"ann-{i}"} for i, _ in enumerate(body["data"])]})
        return httpx.Response(404)


def span(trace: str, span_id: str) -> dict[str, Any]:
    return {"name": "duel", "context": {"trace_id": trace, "span_id": span_id}, "attributes": {}}


def client(fake: FakePhoenix) -> httpx.Client:
    return httpx.Client(base_url="http://phoenix.test", transport=httpx.MockTransport(fake))


def test_each_target_knows_where_its_trace_lives() -> None:
    assert span_query(DUEL) == SpanQuery("duel", ("bazaar.duel.id:85",))
    assert span_query(THREAD) == SpanQuery("negotiation", ("bazaar.thread.id:101",))
    assert span_query(TRADE) == SpanQuery("taker tick 39")
    assert span_query(MARKET) is None
    assert span_query(Pending("trade", "settlement:1", None, "ok", "", {}, None, 1)) is None


def test_the_annotation_payload_matches_phoenix_span_annotation_data() -> None:
    payload = annotation_payload(DUEL, SpanRef("t" * 32, "8290db9711b5b8f0"))
    assert payload.keys() >= REQUIRED
    assert payload == {
        "span_id": "8290db9711b5b8f0",
        "name": "duel_pie_share",
        "annotator_kind": "CODE",
        "identifier": f"{ANNOTATOR_IDENTIFIER}:duel:85",
        "result": {"label": "good", "score": 0.6483, "explanation": "Deal at 138 ..."},
        "metadata": {"target": "duel", "subject": "duel:85", "day": "fri", "tick": 156, "source": "bazaar evals"},
    }


def test_two_trades_on_one_tick_span_keep_two_annotations() -> None:
    other = Pending("trade", "settlement:68", 0.5, "good", "Sold ...", dict(TRADE.details), None, 41)
    tick_span = SpanRef("c" * 32, "3" * 16)
    first, second = annotation_payload(TRADE, tick_span), annotation_payload(other, tick_span)
    assert first["span_id"] == second["span_id"] and first["identifier"] != second["identifier"]


def test_the_annotator_finds_spans_by_name_and_attribute_and_posts_synchronously() -> None:
    fake = FakePhoenix({"bazaar.duel.id:85": [span("a" * 32, "1" * 16), span("b" * 32, "2" * 16)]})
    annotator = PhoenixAnnotator(client(fake), "bazaar")
    refs = annotator.find(SpanQuery("duel", ("bazaar.duel.id:85",)))
    assert refs == [SpanRef("a" * 32, "1" * 16), SpanRef("b" * 32, "2" * 16)]
    search = fake.requests[0].url.params
    assert (search.get_list("name"), search.get_list("attribute")) == (["duel"], ["bazaar.duel.id:85"])
    ids = annotator.annotate([annotation_payload(DUEL, r) for r in refs])
    post = fake.requests[1]
    assert (post.url.path, post.url.params.get("sync"), ids) == ("/v1/span_annotations", "true", ["ann-0", "ann-1"])
    assert [a["span_id"] for a in json.loads(post.content)["data"]] == ["1" * 16, "2" * 16]
    assert annotator.find(SpanQuery("negotiation", ("bazaar.thread.id:1",))) == []
    annotator.close()


def test_a_project_phoenix_has_not_seen_yet_has_no_spans() -> None:
    annotator = PhoenixAnnotator(client(FakePhoenix({}, status=404)), "bazaar")
    assert annotator.find(SpanQuery("duel")) == []


def test_without_a_key_or_endpoint_annotation_is_skipped_with_one_warning() -> None:
    warnings: list[str] = []
    assert annotator_from("http://phoenix.test", None, "bazaar", warnings.append) is None
    assert annotator_from(None, "k" * 20, "bazaar", warnings.append) is None
    assert len(warnings) == 2 and "Postgres only" in warnings[0]


def test_an_unreachable_or_refusing_phoenix_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "phx-secret-key-0042"
    warnings: list[str] = []

    def refused(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {secret}"
        return httpx.Response(401)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    real = httpx.Client
    for handler, url in ((refused, "http://phoenix.test"), (down, "http://user:pa55word@phoenix.test")):

        def fake_client(*args: Any, _h: Any = handler, **kwargs: Any) -> httpx.Client:
            return real(*args, transport=httpx.MockTransport(_h), **kwargs)

        monkeypatch.setattr(httpx, "Client", fake_client)
        assert annotator_from(url, secret, "bazaar", warnings.append) is None
    assert len(warnings) == 2 and not any(secret in w or "pa55word" in w for w in warnings)
    assert "phoenix.test unreachable" in warnings[1]  # the host, never the whole URL


def test_a_phoenix_that_answers_gives_an_annotator(monkeypatch: pytest.MonkeyPatch) -> None:
    real = httpx.Client
    fake = FakePhoenix({})

    def fake_client(*args: Any, **kwargs: Any) -> httpx.Client:
        return real(*args, transport=httpx.MockTransport(fake), **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    annotator = annotator_from("http://phoenix.test/", "k" * 20, "bazaar", lambda m: None)
    assert isinstance(annotator, PhoenixAnnotator)
    annotator.close()
