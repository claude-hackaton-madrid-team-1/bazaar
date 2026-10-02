"""Attach each outcome to its Phoenix trace as a span annotation, so humans review scores on the traces.

Phoenix REST API, checked against our Phoenix 20.19.0's own `/openapi.json` and the docs:
https://arize.com/docs/phoenix/tracing/how-to-tracing/feedback-and-annotations/capture-feedback
https://arize.com/docs/phoenix/sdk-api-reference/rest-api/overview

- `GET /v1/projects/{project}/spans?name=<span name>&attribute=<dot.path>:<value>` finds the span;
  the attribute value is JSON-parsed, so `bazaar.duel.id:85` matches the integer 85.
- `POST /v1/span_annotations?sync=true` with `{"data": [{"span_id", "name", "annotator_kind": "CODE",
  "result": {"label", "score", "explanation"}, "metadata", "identifier"}]}`; a known identifier
  updates the annotation instead of adding a second one.

Which span: a duel's root `duel` span (`bazaar.duel.id`), a dealer thread's `negotiation` root
(`bazaar.thread.id`), a team trade's decision tick (`<agent> tick <tick>`). A Market Test has no
trace. Phoenix being down or the key missing skips annotation with one warning; scoring goes on.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from bazaar_agent.evals.model import ANNOTATION_NAMES
from bazaar_agent.evals.store import Pending

ANNOTATOR_IDENTIFIER = "bazaar-evals"
TIMEOUT_S = 15.0
MAX_SPANS_PER_OUTCOME = 5  # a duel traced by two runner processes has two roots; annotate each


@dataclass(frozen=True)
class SpanRef:
    trace_id: str
    span_id: str


@dataclass(frozen=True)
class SpanQuery:
    name: str
    attributes: tuple[str, ...] = ()


def span_query(p: Pending) -> SpanQuery | None:
    """Where an outcome's trace lives, or None when it has none."""
    d = p.details
    if p.target == "duel" and isinstance(d.get("duel"), int):
        return SpanQuery("duel", (f"bazaar.duel.id:{d['duel']}",))
    if p.target == "dealer" and isinstance(d.get("thread"), int):
        return SpanQuery("negotiation", (f"bazaar.thread.id:{d['thread']}",))
    if p.target == "trade" and d.get("decision_agent") and isinstance(d.get("decision_tick"), int):
        return SpanQuery(f"{d['decision_agent']} tick {d['decision_tick']}")
    return None


def annotation_payload(p: Pending, span: SpanRef) -> dict[str, Any]:
    """One `SpanAnnotationData` (Phoenix REST schema)."""
    return {
        "span_id": span.span_id,
        "name": ANNOTATION_NAMES.get(p.target, f"eval_{p.target}"),
        "annotator_kind": "CODE",
        "identifier": ANNOTATOR_IDENTIFIER,
        "result": {"label": p.label, "score": p.score, "explanation": p.explanation},
        "metadata": {"target": p.target, "subject": p.subject, "day": p.day, "tick": p.tick, "source": "bazaar evals"},
    }


class PhoenixAnnotator:
    """Finds spans and posts annotations over Phoenix's REST API (an httpx client, fakeable in tests)."""

    def __init__(self, client: httpx.Client, project: str) -> None:
        self.client, self.project = client, project

    def find(self, query: SpanQuery) -> list[SpanRef]:
        params: dict[str, Any] = {"name": [query.name], "limit": MAX_SPANS_PER_OUTCOME}
        if query.attributes:
            params["attribute"] = list(query.attributes)
        reply = self.client.get(f"/v1/projects/{self.project}/spans", params=params)
        if reply.status_code == httpx.codes.NOT_FOUND:
            return []
        reply.raise_for_status()
        spans = reply.json().get("data") or []
        refs = []
        for s in spans:
            ctx = s.get("context") or {}
            if isinstance(ctx.get("span_id"), str) and isinstance(ctx.get("trace_id"), str):
                refs.append(SpanRef(ctx["trace_id"], ctx["span_id"]))
        return refs

    def annotate(self, payloads: list[dict[str, Any]]) -> list[str]:
        """Post annotations; returns the ids Phoenix gave them."""
        reply = self.client.post("/v1/span_annotations", params={"sync": "true"}, json={"data": payloads})
        reply.raise_for_status()
        return [str(row.get("id")) for row in reply.json().get("data") or [] if isinstance(row, Mapping)]

    def close(self) -> None:
        self.client.close()


def annotator_from(
    endpoint: str | None, api_key: str | None, project: str, warn: Callable[[str], None]
) -> PhoenixAnnotator | None:
    """An annotator when Phoenix answers with the key, else None after one warning (never the key)."""
    if not api_key:
        warn("phoenix: PHOENIX_API_KEY is not set, scores stay in Postgres only")
        return None
    if not endpoint:
        warn("phoenix: no PHOENIX_COLLECTOR_ENDPOINT, scores stay in Postgres only")
        return None
    client = httpx.Client(
        base_url=endpoint.rstrip("/"), headers={"authorization": f"Bearer {api_key}"}, timeout=TIMEOUT_S
    )
    try:
        reply = client.get(f"/v1/projects/{project}/spans", params={"limit": 1})
    except httpx.HTTPError as e:
        warn(f"phoenix: {endpoint} unreachable ({type(e).__name__}), scores stay in Postgres only")
        client.close()
        return None
    if reply.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        warn(f"phoenix: the key was refused (HTTP {reply.status_code}), scores stay in Postgres only")
        client.close()
        return None
    return PhoenixAnnotator(client, project)
