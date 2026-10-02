"""The live feed: ONE server-sent-events connection to `GET /api/events/stream`, read on a thread.

The vendored SDK has no SSE method, so this is the documented Plan B: raw `httpx` streaming,
checked against `docs/api/openapi.json` and a real connection (2026-10-02, tick 105):

    event: hello                      data: {"tick": 105, "scope": "team:t01"}
    event: <feed type>                data: <the same event object /api/feed returns>
    : keep-alive                      (a comment line about every 15 s)

plus `event: tick` events that `/api/feed` never carries. The server sends no `id:` lines, so a
reconnect cannot resume: the monitor's per-tick `/api/feed` poll stays the gap-filler and the
source of truth for dedupe.

The cap is 6 open streams per team key (without a key: per address), and it is shared by every
process on every teammate's laptop and every browser tab showing the live game. So: one stream
per process (`EventStream.start` refuses a second), reconnects back off 0.6 s → 10 s, and a
`429` (`too_many_streams`) or `503` drops to polling until the tick loop says try again.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

Event = dict[str, Any]
StreamAction = Literal["reconnect", "fallback", "stop"]
StreamState = Literal["idle", "connecting", "live", "backoff", "fallback", "stopped"]

STREAM_PATH = "/api/events/stream"
BACKOFF_FIRST_S = 0.6
BACKOFF_MAX_S = 10.0
STABLE_S = 30.0  # a connection that lived this long resets the backoff
READ_TIMEOUT_S = 45.0  # three missed keep-alives: the connection is dead, reconnect
CONNECT_TIMEOUT_S = 10.0
LINGER_S = 0.25  # after the first queued event, wait this long for its burst-mates: one DB write per burst
STREAM_ONLY_TYPES = frozenset({"tick"})  # the feed never carries these: storing them would diverge from /api/feed


# ---------------------------------------------------------------- SSE wire format


@dataclass(frozen=True)
class SseMessage:
    event: str
    data: str
    id: str | None = None


class SseParser:
    """Incremental `text/event-stream` parser (WHATWG rules): feed it raw text chunks as they arrive.

    A chunk may end mid-line or between `\\r` and `\\n`; the tail waits for the next chunk.
    Comment lines (`: keep-alive`) are counted, never dispatched.
    """

    def __init__(self) -> None:
        self._tail = ""
        self._event = ""
        self._data: list[str] = []
        self._id: str | None = None
        self.comments = 0

    def feed(self, chunk: str) -> list[SseMessage]:
        text = self._tail + chunk
        hold_cr = text.endswith("\r")  # maybe the first half of "\r\n"
        if hold_cr:
            text = text[:-1]
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self._tail = lines.pop() + ("\r" if hold_cr else "")
        return [m for m in map(self._line, lines) if m is not None]

    def _line(self, line: str) -> SseMessage | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            self.comments += 1
            return None
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        elif name == "id" and "\0" not in value:
            self._id = value
        return None

    def _dispatch(self) -> SseMessage | None:
        message = SseMessage(self._event or "message", "\n".join(self._data), self._id) if self._data else None
        self._event, self._data = "", []
        return message


def feed_event(message: SseMessage) -> Event | None:
    """The feed event a message carries, or None (hello, a malformed or foreign payload)."""
    if message.event == "hello":
        return None
    try:
        value = json.loads(message.data)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("id"), int) or not isinstance(value.get("type"), str):
        return None
    if not isinstance(value.get("payload") or {}, dict):
        return None  # every reader does `payload.get(...)`: a non-object payload would crash the monitor
    return value


# ---------------------------------------------------------------- reconnect policy (pure)


class Backoff:
    """Reconnect delays: 0.6 s, doubling, capped at 10 s; `reset()` after a healthy connection."""

    def __init__(self, first: float = BACKOFF_FIRST_S, cap: float = BACKOFF_MAX_S) -> None:
        self.first, self.cap = first, cap
        self._next = first

    def next(self) -> float:
        delay, self._next = self._next, min(self.cap, self._next * 2)
        return delay

    def reset(self) -> None:
        self._next = self.first


def after_refusal(status: int) -> StreamAction:
    """What a non-200 answer means for the stream.

    429 (`too_many_streams`, `rate_limited`, `wait_for_tick`) and 503: poll `/api/feed` and try the
    stream again at the next tick, never in a loop. 401/403: a wrong key; retrying would count toward
    `too_many_failures`, so stop. Another 4xx will not fix itself: stop. 5xx: reconnect with backoff.
    """
    if status in (429, 503):
        return "fallback"
    if 400 <= status < 500:
        return "stop"
    return "reconnect"


# ---------------------------------------------------------------- the reader thread


@dataclass(frozen=True)
class Note:
    """A stream status line for the tick loop's thread to print (the reader thread never prints)."""

    text: str


_SLOT = threading.Lock()  # one stream per process: the cap is 6 per team key across every laptop and tab


class EventStream:
    """One SSE connection on a daemon thread. Every feed event goes to `emit` as it arrives; status
    changes go to `emit` as a `Note`. `emit` must be thread-safe (an `Inbox.put`)."""

    def __init__(
        self,
        base_url: str,
        key: str | None,
        emit: Callable[[Event | Note], None],
        *,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        headers = {"Accept": "text/event-stream"}
        if key:
            headers["X-Team-Key"] = key  # a header, never `?key=`: URLs end up in logs and errors
        self._scope = "team" if key else "public"
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S),
            transport=transport,
        )
        self._emit, self._now = emit, now
        self.backoff = Backoff()
        self.state: StreamState = "idle"
        self.hello: dict[str, Any] = {}
        self._stop, self._retry = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._holds_slot = False

    # -- lifecycle (called from the tick loop's thread)

    def start(self) -> None:
        if not _SLOT.acquire(blocking=False):
            raise RuntimeError("an event stream is already open in this process (one per process)")
        self._holds_slot = True
        self._thread = threading.Thread(target=self.run, name="bazaar-event-stream", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._retry.set()
        self._client.close()  # unblocks a read in progress
        if self._thread is not None:
            self._thread.join(timeout)
        if self._holds_slot:
            self._holds_slot = False
            _SLOT.release()

    def on_tick(self) -> None:
        """The tick loop's nudge: after a 429/503 fallback, the stream is tried again once per tick."""
        if self.state == "fallback":
            self._retry.set()

    # -- the thread

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                action = self._connect_once()
                if action == "stop" or self._stop.is_set():
                    break
                if action == "fallback":
                    self.state = "fallback"
                    self._retry.wait()
                    self._retry.clear()
                    continue
                self.state = "backoff"
                delay = self.backoff.next()
                self._note(f"stream reconnecting in {delay:.1f} s (the per-tick poll fills any gap)")
                self._stop.wait(delay)
        finally:
            self.state = "stopped"

    def _connect_once(self) -> StreamAction:
        self.state = "connecting"
        opened = self._now()
        try:
            with self._client.stream("GET", STREAM_PATH, params={"scope": self._scope}) as reply:
                if reply.status_code != 200:
                    return self._refused(reply)
                self._read(reply)
            if self._stop.is_set():
                return "stop"
            self._note("stream closed by the server")
        except httpx.HTTPError as e:
            if self._stop.is_set():
                return "stop"
            self._note(f"stream dropped ({type(e).__name__})")
        except Exception as e:  # never let the reader thread die silently: report it and reconnect
            if self._stop.is_set():
                return "stop"
            self._note(f"stream reader failed ({type(e).__name__}: {str(e)[:80]})")
        if self._now() - opened >= STABLE_S:
            self.backoff.reset()
        return "reconnect"

    def _read(self, reply: httpx.Response) -> None:
        parser = SseParser()
        for chunk in reply.iter_text():
            for message in parser.feed(chunk):
                if message.event == "hello":
                    self._hello(message)
                elif (event := feed_event(message)) is not None:
                    self._emit(event)
            if self._stop.is_set():
                return

    def _hello(self, message: SseMessage) -> None:
        try:
            hello = json.loads(message.data)
        except json.JSONDecodeError:
            hello = {}
        self.hello = hello if isinstance(hello, dict) else {}
        self.state = "live"
        self._note(f"stream live (scope {self.hello.get('scope', '?')}, tick {self.hello.get('tick', '?')})")

    def _refused(self, reply: httpx.Response) -> StreamAction:
        reply.read()
        try:
            code = str((reply.json() or {}).get("error") or "")
        except (json.JSONDecodeError, AttributeError):
            code = ""
        action = after_refusal(reply.status_code)
        then = {
            "fallback": "polling /api/feed each tick; the stream is retried at the next tick",
            "stop": "stream off for this run; polling /api/feed each tick",
            "reconnect": "retrying with backoff",
        }[action]
        self._note(f"stream refused {reply.status_code} {code or '-'}: {then}")
        return action

    def _note(self, text: str) -> None:
        self._emit(Note(text))


# ---------------------------------------------------------------- hand-off to the tick loop


class Inbox:
    """Hands stream items from the reader thread to the tick loop's thread.

    `wait` replaces the tick loop's sleep: it sleeps until the next tick exactly as before, but
    handles every item the moment it arrives, so all processing stays on one thread (one DB
    connection, one FeedStore, no locks) and an alert fires within seconds instead of next tick.
    """

    def __init__(self, linger: float = LINGER_S) -> None:
        self._queue: queue.Queue[Event | Note] = queue.Queue()
        self._linger = linger

    def put(self, item: Event | Note) -> None:
        self._queue.put(item)

    def wait(self, seconds: float, handle: Callable[[list[Event | Note]], None]) -> None:
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            try:
                batch = [self._queue.get(timeout=left)]
            except queue.Empty:
                return
            batch += self._drain(min(self._linger, max(0.0, deadline - time.monotonic())))
            handle(batch)

    def flush(self, handle: Callable[[list[Event | Note]], None]) -> None:
        """Handle whatever is still queued (at shutdown), without waiting."""
        if batch := self._drain(0.0):
            handle(batch)

    def _drain(self, linger: float) -> list[Event | Note]:
        out: list[Event | Note] = []
        until = time.monotonic() + linger
        while True:
            try:
                out.append(self._queue.get(timeout=max(0.0, until - time.monotonic())))
            except queue.Empty:
                return out
