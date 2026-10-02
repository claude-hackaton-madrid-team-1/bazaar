"""The live stream without a network: SSE parsing, reconnect policy, one stream per process, the inbox.

Server traffic is an `httpx.MockTransport`; the wire format is the one logged from a real connection
(2026-10-02, tick 105): `event: hello`, `event: <feed type>` + one JSON line, `: keep-alive`.
"""

import json
import threading
import time

import httpx
import pytest

from bazaar_agent import stream as st

HELLO = 'event: hello\ndata: {"tick": 105, "scope": "team:t01"}\n\n'
KEEPALIVE = ": keep-alive\n\n"
KEY = "tk-test-never-printed"


def frame(event_id, kind="thread.message", **extra):
    body = {"id": event_id, "tick": 106, "type": kind, "scope": "public", "actor": "t05", "payload": {}} | extra
    return f"event: {kind}\ndata: {json.dumps(body)}\n\n"


def wait_until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition never became true"
        time.sleep(0.01)


# ---------------------------------------------------------------- the wire format


def test_hello_events_and_keepalives_parse_like_the_real_stream():
    parser = st.SseParser()
    messages = parser.feed(HELLO + frame(4919, "gift.given") + KEEPALIVE + frame(4920))
    assert [m.event for m in messages] == ["hello", "gift.given", "thread.message"]
    assert parser.comments == 1
    assert st.feed_event(messages[0]) is None  # hello is not a feed event
    assert st.feed_event(messages[1])["id"] == 4919


def test_a_line_split_across_chunks_waits_for_its_end():
    parser = st.SseParser()
    raw = frame(7)
    assert parser.feed(raw[:23]) == []
    assert parser.feed(raw[23:-1]) == []  # the closing blank line has not arrived
    (message,) = parser.feed(raw[-1:])
    assert st.feed_event(message)["id"] == 7


def test_crlf_split_between_chunks_is_one_line_break():
    parser = st.SseParser()
    raw = frame(8).replace("\n", "\r\n")
    cut = raw.index("\r\n") + 1  # the chunk ends between \r and \n
    assert parser.feed(raw[:cut]) == []
    (message,) = parser.feed(raw[cut:])
    assert message.event == "thread.message" and st.feed_event(message)["id"] == 8


def test_multiline_data_ids_and_events_without_data():
    parser = st.SseParser()
    messages = parser.feed("id: 9\ndata: line one\ndata:line two\n\nevent: empty\n\ndata: x\n\n")
    assert [(m.event, m.data, m.id) for m in messages] == [
        ("message", "line one\nline two", "9"),
        ("message", "x", "9"),  # the last id persists; an event with no data is never dispatched
    ]


@pytest.mark.parametrize(
    "data",
    [
        "not json",
        "[1, 2]",
        '{"type": "x"}',
        '{"id": "5", "type": "x"}',
        '{"id": 5}',
        '{"id": 5, "type": "x", "payload": [1]}',
    ],
)
def test_malformed_or_foreign_payloads_are_not_feed_events(data):
    assert st.feed_event(st.SseMessage("thread.message", data)) is None


# ---------------------------------------------------------------- reconnect policy


def test_backoff_doubles_from_0_6_to_a_10_second_cap_and_resets():
    backoff = st.Backoff()
    assert [backoff.next() for _ in range(7)] == [0.6, 1.2, 2.4, 4.8, 9.6, 10.0, 10.0]
    backoff.reset()
    assert backoff.next() == 0.6


@pytest.mark.parametrize(
    ("status", "action"),
    [(429, "fallback"), (503, "fallback"), (401, "stop"), (403, "stop"), (404, "stop"), (500, "reconnect")],
)
def test_refusals_fall_back_stop_or_reconnect(status, action):
    assert st.after_refusal(status) == action


# ---------------------------------------------------------------- the reader, against a fake server


class Server:
    """A scripted `/api/events/stream`: each connection takes the next reply from the script."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else httpx.Response(401, json={"error": "bad_key"})
        if isinstance(reply, Exception):
            raise reply
        return reply


def sse(*chunks):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=iter(c.encode() for c in chunks))


def make(server, key=KEY, now=time.monotonic):
    got: list = []
    stream = st.EventStream("https://bazaar.test", key, got.append, transport=httpx.MockTransport(server), now=now)
    stream.backoff = st.Backoff(first=0.01, cap=0.02)
    return stream, got


def notes(got):
    return [g.text for g in got if isinstance(g, st.Note)]


def events(got):
    return [g["id"] for g in got if isinstance(g, dict)]


def test_one_connection_emits_events_as_they_arrive_with_the_key_in_a_header_only():
    raw = HELLO + frame(1) + KEEPALIVE + frame(2)
    server = Server(sse(raw[:40], raw[40:90], raw[90:]))
    stream, got = make(server)
    assert stream._connect_once() == "reconnect"  # the server closed the stream
    assert events(got) == [1, 2]
    assert notes(got) == ["stream live (scope team:t01, tick 105)", "stream closed by the server"]
    (request,) = server.requests
    assert request.headers["X-Team-Key"] == KEY and KEY not in str(request.url)
    assert request.url.params["scope"] == "team" and request.url.path == "/api/events/stream"


def test_without_a_key_the_stream_is_public_and_sends_no_key_header():
    server = Server(sse(HELLO))
    stream, _ = make(server, key=None)
    stream._connect_once()
    (request,) = server.requests
    assert "X-Team-Key" not in request.headers and request.url.params["scope"] == "public"


def test_too_many_streams_falls_back_to_polling_and_retries_at_the_next_tick_only():
    server = Server(
        httpx.Response(429, json={"error": "too_many_streams", "message": "6 open"}),
        sse(HELLO, frame(3)),
    )
    stream, got = make(server)
    stream.start()
    try:
        wait_until(lambda: stream.state == "fallback")
        time.sleep(0.05)
        assert len(server.requests) == 1  # no retry loop while waiting for the tick
        assert "stream refused 429 too_many_streams" in notes(got)[0]
        stream.on_tick()  # the tick loop's nudge
        wait_until(lambda: 3 in events(got))
    finally:
        stream.stop()
    assert stream.state == "stopped"


def test_a_dropped_connection_reconnects_with_backoff_and_a_bad_key_stops():
    server = Server(httpx.ReadError("connection reset"), sse(HELLO, frame(4)))  # then 401 bad_key
    stream, got = make(server)
    stream.start()
    try:
        wait_until(lambda: stream.state == "stopped")
    finally:
        stream.stop()
    assert events(got) == [4]
    texts = notes(got)
    assert texts[0] == "stream dropped (ReadError)" and texts[1].startswith("stream reconnecting in 0.0")
    assert texts[-1] == "stream refused 401 bad_key: stream off for this run; polling /api/feed each tick"
    assert len(server.requests) == 3


def test_only_a_long_lived_connection_resets_the_backoff():
    clock = iter([0.0, 5.0, 10.0, 10.0 + st.STABLE_S])
    stream, _ = make(Server(sse(HELLO), sse(HELLO)), now=lambda: next(clock))
    stream.backoff.next(), stream.backoff.next()
    stream._connect_once()  # lived 5 s: a flapping server keeps backing off
    assert stream.backoff.next() == 0.02
    stream._connect_once()  # lived STABLE_S: healthy, start again from the first delay
    assert stream.backoff.next() == 0.01


def test_one_stream_per_process():
    first, _ = make(Server(sse(HELLO, KEEPALIVE)))
    second, _ = make(Server(sse(HELLO)))
    first.start()
    try:
        with pytest.raises(RuntimeError, match="one per process"):
            second.start()
    finally:
        first.stop()
    second.start()  # the slot is free again once the first stream stops
    second.stop()


# ---------------------------------------------------------------- the hand-off to the tick loop


def test_the_inbox_handles_items_the_moment_they_land_and_returns_at_the_deadline():
    inbox = st.Inbox(linger=0.05)
    handled: list[tuple[float, list]] = []
    started = time.monotonic()
    threading.Timer(0.1, lambda: [inbox.put({"id": 1}), inbox.put({"id": 2})]).start()
    threading.Timer(0.3, lambda: inbox.put(st.Note("late"))).start()
    inbox.wait(0.5, lambda batch: handled.append((time.monotonic() - started, batch)))
    elapsed = time.monotonic() - started
    assert [batch for _, batch in handled] == [[{"id": 1}, {"id": 2}], [st.Note("late")]]  # a burst is one batch
    assert handled[0][0] < 0.3 and 0.45 < elapsed < 0.8


def test_flush_handles_what_is_left_at_shutdown():
    inbox = st.Inbox()
    inbox.put({"id": 9})
    seen: list = []
    inbox.flush(seen.extend)
    inbox.flush(seen.extend)  # nothing left: not called again
    assert seen == [{"id": 9}]
