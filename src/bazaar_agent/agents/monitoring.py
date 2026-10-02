"""The monitoring agent's loop: the per-tick work, and the live-stream bursts in between.

`bazaar monitor` builds one `MonitorLoop` and runs it with `run_per_tick`, whose sleep is the stream
inbox's `wait`: the clock is still read once per tick, and between ticks every stream burst is
handled the moment it lands, on this same thread (one DB connection, one JSONL store, no locks).
Logic lives in `bazaar_agent.monitor`; this module wires reads, writes, alerts and spans.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.markup import escape

from bazaar_agent import db, traces
from bazaar_agent import monitor as mon
from bazaar_agent import telemetry as tm
from bazaar_agent.feed import DEFAULT_WINDOW, CaptureResult, Event
from bazaar_agent.identity import remember_team_id, valid_team_id
from bazaar_agent.intel import is_ours
from bazaar_agent.pgconn import Reconnector
from bazaar_agent.sdk import BazaarError
from bazaar_agent.stream import EventStream, Note
from bazaar_agent.ticks import Clock

ALERTS_FILE = "alerts.jsonl"


@dataclass(frozen=True)
class Options:
    db_enabled: bool = True
    notify: bool = False
    refresh_every: int = 5  # rebuild dealer curves and competitor profiles every N ticks
    show_events: bool = False  # print every streamed event as it lands


def stamp() -> str:
    """Wall-clock time for the console only (latency evidence); never used to schedule anything."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class MonitorLoop:
    def __init__(
        self,
        public: Any,
        team: Any,
        watcher: mon.Watcher,
        data_dir: Path,
        options: Options,
        say: Callable[[str], None],
        stream: EventStream | None = None,
    ) -> None:
        self.public, self.team, self.watcher = public, team, watcher
        self.data_dir, self.options, self.say, self.stream = data_dir, options, say, stream
        self.alerts_path = data_dir / ALERTS_FILE
        self.dealers: dict[str, mon.TraderSnapshot] = {}
        self.levels: list[Any] = []
        self.teams_seen: dict[str, mon.TraderSnapshot] = {}
        self.ticks = 0
        self.streamed_since_tick = 0
        self.pg = Reconnector(self._open_pg, lambda m: say(f"[yellow]{m}[/yellow]"))

    @property
    def stream_state(self) -> str:
        return self.stream.state if self.stream is not None else "off"

    # ---------------------------------------------------------------- between ticks: the live stream

    def on_stream(self, batch: list[Event | Note]) -> None:
        """The inbox handler: status notes are printed, feed events go through the pipeline at once."""
        events: list[Event] = []
        for item in batch:
            if isinstance(item, Note):
                self.say(f"{stamp()} {escape(item.text)}")
            else:
                events.append(item)
        if not events:
            return
        with tm.span("monitor stream", tm.CHAIN, {"bazaar.loop": "monitor stream"}, root=True):
            try:
                self._stream_events(events)
            except Exception as e:  # one bad burst must not stop a monitor that runs all game
                self.say(f"[yellow]{stamp()} stream burst failed ({type(e).__name__}: {escape(str(e)[:80])})[/yellow]")
                tm.fail_current(e)

    def _stream_events(self, events: list[Event]) -> None:
        ingested = self.watcher.from_stream(events)
        self.streamed_since_tick += len(ingested.fresh)
        traces.stream_batch(len(events), ingested, self.stream_state)
        if self.options.show_events:
            for e in ingested.fresh + ingested.private:
                mine = " (us)" if is_ours(e, self.watcher.ours) else ""
                line = f"{stamp()} stream #{e['id']} tick {e.get('tick')} {e.get('type')} {e.get('actor') or '-'}{mine}"
                self.say(escape(line))

        def store(cx: Any) -> None:
            db.load_events(cx, ingested.fresh)
            if ingested.alerts:
                db.insert_alerts(cx, ingested.alerts)

        self._write(store, "stream")
        self.raise_alerts(ingested.alerts, "stream")

    # ---------------------------------------------------------------- once per tick

    def on_tick(self, c: Clock) -> None:
        self.ticks += 1
        if self.stream is not None:
            self.stream.on_tick()  # after a 429/503 fallback, try the stream again (once per tick)
        result, ingested, lead = self.watcher.from_poll(self._read_feed(c), DEFAULT_WINDOW)
        traces.feed_capture(result)
        traces.stream_lead(lead, self.stream_state, self.streamed_since_tick)
        refresh = self.ticks == 1 or self.ticks % self.options.refresh_every == 0
        history = list(self.watcher.store.events()) if refresh else None
        dealers_after, levels_after = self._read_dealers(c)
        alerts = list(ingested.alerts)
        if self.ticks > 1:  # the first sync is a baseline, not news
            ours = self.watcher.ours
            alerts += mon.detect_changes(c.tick, self.dealers, dealers_after, self.levels, levels_after, ours)
        teams_now = dict(self.watcher.teams)
        traces.trader_changes({**self.dealers, **self.teams_seen}, {**dealers_after, **teams_now})
        self.dealers, self.levels, self.teams_seen = dealers_after, levels_after, teams_now
        me = self._read_me(c)
        self._write(lambda cx: self._store_tick(cx, c.tick, ingested.fresh, alerts, history, me), f"tick {c.tick}")
        self.raise_alerts(alerts, "poll")
        traces.monitor_summary(len(ingested.fresh), len(dealers_after), len(teams_now), len(levels_after), me)
        self.say(self._tick_line(c, result, lead, me))
        self.streamed_since_tick = 0

    def _store_tick(
        self, cx: Any, tick: int, fresh: list[Event], alerts: list[mon.Alert], history: Any, me: Any
    ) -> None:
        db.load_events(cx, fresh)
        db.upsert_traders(cx, [*self.dealers.values(), *self.teams_seen.values()], tick)
        if me is not None:
            db.save_snapshot(cx, tick, me)
        if alerts:
            db.insert_alerts(cx, alerts)
        if history is not None and not db.load_history(cx, history, tick, self.watcher.ours):
            self.say(f"tick {tick}: curves/competitors left to the monitor with older history")

    def _tick_line(self, c: Clock, result: CaptureResult, lead: mon.Lead, me: dict[str, Any] | None) -> str:
        gap = " [red]GAP POSSIBLE[/red]" if result.gap_possible else ""
        live = ""
        if self.stream is not None:
            live = f" by poll · stream {self.stream_state}: +{self.streamed_since_tick} live, {lead.describe()}"
        cash = (
            f" · cash {me.get('cash')} lvl {me.get('level')} score {(me.get('score') or {}).get('score')}" if me else ""
        )
        return (
            f"{stamp()} tick {c.tick}: +{result.new} events{live} (id {result.newest_id}){gap} · "
            f"{len(self.dealers)} dealers, {len(self.teams_seen)} teams, {len(self.levels)} levels{cash}"
        )

    # ---------------------------------------------------------------- reads (a refusal never stops the loop)

    def _read_feed(self, c: Clock) -> list[Event]:
        try:
            window: list[Event] = self.public.feed_window(DEFAULT_WINDOW)
        except BazaarError as e:
            self.say(f"tick {c.tick}: feed refused {e.code}")
            tm.fail_current(e)
            return []
        return window

    def _read_dealers(self, c: Clock) -> tuple[dict[str, mon.TraderSnapshot], list[Any]]:
        try:
            return mon.dealer_snapshots(self.public.dealers()), self.public.levels().get("levels") or []
        except BazaarError as e:
            self.say(f"tick {c.tick}: dealers/levels refused {e.code}")
            tm.fail_current(e)
            return self.dealers, self.levels

    def _read_me(self, c: Clock) -> dict[str, Any] | None:
        if self.team is None:
            return None
        try:
            me: dict[str, Any] = self.team.me()
        except BazaarError as e:
            self.say(f"tick {c.tick}: /me refused {e.code}")
            tm.fail_current(e)
            return None
        self._confirm_identity(me)
        return me

    def _confirm_identity(self, me: dict[str, Any]) -> None:
        """/api/me is the authority on which team our key is: a stale cache or override is corrected."""
        team = valid_team_id(me.get("id"))
        if team is None or team == self.watcher.ours:
            return
        self.say(f"[yellow]our team id is {team} (was {self.watcher.ours}): tagging {team} as us[/yellow]")
        remember_team_id(self.data_dir, team)
        self.watcher.ours = team
        if team in self.watcher.teams:
            self.watcher.teams[team] = dataclasses.replace(self.watcher.teams[team], status="us")

    # ---------------------------------------------------------------- writes and alerts

    def _open_pg(self) -> Any:
        try:
            return db.connect_ready("bazaar-monitor")
        except Exception as e:  # recorded on the current span; Reconnector keeps the loop going on JSONL
            tm.fail_current(e)
            raise

    def _write(self, store: Callable[[Any], None], where: str) -> None:
        cx = self.pg.get() if self.options.db_enabled else None
        if cx is None:
            return
        try:
            store(cx)
        except Exception as e:  # JSONL already holds the events; the next tick's history reload backfills
            self.say(f"[yellow]{where}: DB write failed ({type(e).__name__}: {escape(str(e)[:80])})[/yellow]")
            tm.fail_current(e)
            self.pg.drop()

    def raise_alerts(self, alerts: list[mon.Alert], via: str) -> None:
        mon.append_alerts(self.alerts_path, alerts)
        traces.alert_events(alerts)
        for a in alerts:
            self.say(
                f"[bold red]ALERT[/bold red] tick {a.tick} {a.kind} {escape(a.subject)}: {escape(a.detail)} "
                f"(via {via} {stamp()})"
            )
            if self.options.notify:
                text = f"{a.subject}: {a.kind}".replace("\\", "").replace('"', "'")
                subprocess.run(
                    ["osascript", "-e", f'display notification "{text}" with title "Bazaar"'],
                    check=False,
                    capture_output=True,
                )
