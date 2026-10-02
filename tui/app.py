import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static

from mock import MockGame
from state import State, apply

SET_COLOR = {"LAV": "#E4572E", "MAL": "#9D4EDD", "LAT": "#F4A259", "SAL": "#2E86AB", "RET": "#3BB273", "CHA": "#C1666B"}
RARITY_COLOR = {"common": "#9AA4B8", "uncommon": "#3DDC97", "rare": "#4C8DFF", "epic": "#B061FF", "legendary": "#FFC44D"}
SLOT_RARITY = ["common"] * 5 + ["uncommon"] * 3 + ["rare"] * 2 + ["epic", "legendary"]
BOOK = [10] * 5 + [25] * 3 + [70] * 2 + [180, 450]
PHASES = ["observe", "decide", "act"]
LOG_STYLE = {
    "thought": ("·", "#8B93A7"), "say": ("↗", "#6FD3B8"), "accept": ("✔", "#3DDC97"), "walk": ("✖", "#FF6B6B"),
    "open": ("+", "#E0A458"), "list": ("≡", "#E0A458"), "flag": ("⚑", "#FFC44D"),
}
SUSPICIOUS = re.compile(r"ignore (all |any )?previous|system:|instructions|transfer \d+", re.I)
SPARK = "▁▂▃▄▅▆▇█"
OURS, THEIRS, GAP = "#6FD3B8", "#E0A458", "#FFC44D"


def spark(values):
    if not values:
        return ""
    lo, hi = min(values), max(values)
    return "".join(SPARK[0 if hi == lo else round((v - lo) / (hi - lo) * 7)] for v in values)


def slot(ref):
    try:
        return int(ref.split("-")[1]) - 1
    except (IndexError, ValueError):
        return None


def money(v):
    return f"{v:+.1f} P" if v is not None else ""


class BazaarApp(App):
    TITLE = "Bazaar · agent"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("space", "pause", "pause"),
        Binding("o", "only_ours", "our trades"),
        Binding("plus,equals_sign", "faster", "faster"),
        Binding("minus", "slower", "slower"),
    ]

    def __init__(self, ws=None, seed=None, speed=0.35):
        super().__init__()
        self.ws, self.speed = ws, speed
        self.game = None if ws else MockGame(seed=seed)
        self.state = State()
        self.paused = False
        self.only_ours = False
        self.connected = ws is None
        self.last_tick_at, self.tick_gap = time.monotonic(), None
        self._timer = None

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="main"):
            yield Static(id="loop", classes="panel")
            yield Static(id="negs", classes="panel")
            with Vertical(id="side"):
                yield Static(id="album", classes="panel")
                yield Static(id="score", classes="panel")
        yield Static(id="tape", classes="panel")
        yield Footer()

    def on_mount(self):
        titles = {"#loop": "agent loop", "#negs": "negotiations", "#album": "album", "#score": "score", "#tape": "market tape"}
        for sel, title in titles.items():
            self.query_one(sel).border_title = title
        if self.game:
            self._timer = self.set_interval(self.speed, self.step)
        else:
            self.run_worker(self.listen(), exclusive=True)
        self.set_interval(0.25, self.redraw)
        self.redraw()

    def feed(self, events):
        for e in events:
            if e.get("type") == "clock" and e.get("tick") != self.state.tick:
                now = time.monotonic()
                self.tick_gap, self.last_tick_at = now - self.last_tick_at, now
            apply(self.state, e)

    def step(self):
        if not self.paused:
            self.feed(self.game.step())
            self.redraw()

    async def listen(self):
        import websockets

        # ponytail: one socket, reconnect with a fixed 2 s backoff, no resume from the last event id
        while True:
            try:
                async with websockets.connect(self.ws) as sock:
                    self.connected = True
                    async for raw in sock:
                        if not self.paused:
                            self.feed([json.loads(raw)])
            except (OSError, websockets.WebSocketException, json.JSONDecodeError):
                self.connected = False
            await asyncio.sleep(2)

    def redraw(self):
        self.query_one("#header", Static).update(self.header_view())
        self.query_one("#loop", Static).update(self.loop_view())
        self.query_one("#negs", Static).update(self.negs_view())
        self.query_one("#album", Static).update(self.album_view())
        self.query_one("#score", Static).update(self.score_view())
        self.query_one("#tape", Static).update(self.tape_view())

    def header_view(self):
        s = self.state
        period = self.tick_gap or s.tick_seconds or 60
        frac = min((time.monotonic() - self.last_tick_at) / period, 1.0)
        width = 16
        bar = "█" * round(frac * width) + "░" * (width - round(frac * width))
        sc = s.score or {}
        t = Text()
        t.append(" BAZAAR ", style="bold #0B1020 on #E0A458")
        t.append(f"  {s.name or '—'} ", style="bold")
        t.append(f"{s.team}  ", style="#8B93A7")
        t.append("│ ", style="#2A3456")
        t.append(f"{(s.day or '—').upper()} · tick {s.tick} ", style="bold")
        t.append(bar, style="#6FD3B8")
        t.append("  │ ", style="#2A3456")
        if self.paused:
            t.append("❚❚ paused", style="bold #FFC44D")
        elif self.ws:
            t.append("● live" if self.connected else "○ reconnecting", style="#3DDC97" if self.connected else "#FF6B6B")
        else:
            t.append("● mock", style="#B061FF")
        t.append("  │ ", style="#2A3456")
        t.append(f"{s.cash} P", style="bold #FFC44D")
        t.append("  │ score ", style="#8B93A7")
        t.append(f"{sc.get('score', 0):.1f}", style="bold #6FD3B8")
        t.append(f"  rank #{sc.get('rank', '—')}", style="#8B93A7")
        return t

    def loop_view(self):
        s = self.state
        pipe = Text()
        for i, ph in enumerate(PHASES):
            on = ph == s.phase
            pipe.append(f" {ph.upper()} ", style="bold #0B1020 on #6FD3B8" if on else "#5A6380")
            if i < 2:
                pipe.append(" ─▶ ", style="#2A3456")
        pipe.append("  ↺", style="#5A6380")
        goal = Text.assemble(("goal  ", "#8B93A7"), (s.goal or "—", "bold #E0A458"))
        room = max(self.query_one("#loop").size.height - 5, 1)
        lines = []
        for line in list(s.log)[-room:]:
            icon, color = LOG_STYLE.get(line.kind, ("›", "#C8CEDC"))
            row = Text(f"{line.tick:>4} ", style="#3A4466", no_wrap=True, overflow="ellipsis")
            row.append(f"{icon} ", style=f"bold {color}")
            row.append(line.text, style="italic #8B93A7" if line.kind == "thought" else color)
            lines.append(row)
        return Group(pipe, goal, Text(""), *lines)

    @staticmethod
    def rail(th, width=26):
        pts = [p for p in (th.our_price, th.their_price) if p is not None]
        if not pts:
            return Text("waiting for the first offer", style="#5A6380")
        lo, hi = min(pts) * 0.8, max(pts) * 1.2 + 1
        pos = lambda p: min(width - 1, max(0, round((p - lo) / (hi - lo) * (width - 1))))
        cells = [("─", "#2A3456")] * width
        if th.our_price is not None and th.their_price is not None:
            a, b = sorted((pos(th.our_price), pos(th.their_price)))
            for i in range(a, b + 1):
                cells[i] = ("━", GAP)
        if th.our_price is not None:
            cells[pos(th.our_price)] = ("●", f"bold {OURS}")
        if th.their_price is not None:
            cells[pos(th.their_price)] = ("◆", f"bold {THEIRS}")
        t = Text()
        t.append(f"{th.our_price if th.our_price is not None else '·':>4} ", style=OURS)
        for ch, st in cells:
            t.append(ch, style=st)
        t.append(f" {th.their_price if th.their_price is not None else '·'}", style=THEIRS)
        if th.our_price is not None and th.their_price is not None:
            t.append(f"  gap {abs(th.their_price - th.our_price)}", style="#8B93A7")
        return t

    @staticmethod
    def thread_view(th, tick, width=26):
        t = Text(no_wrap=True, overflow="ellipsis")
        closed = th.status != "open"
        who = "🧶 Abuela" if th.with_ == "abuela" else th.with_
        t.append(f"#{th.id} ", style="#5A6380")
        t.append(f"{who:<10}", style="bold #5A6380" if closed else "bold")
        t.append(f" {th.side.upper():<4} ", style="bold #0B1020 on " + ("#4C8DFF" if th.side == "buy" else "#E07A5F"))
        t.append(f" {th.topic}", style=f"bold {SET_COLOR.get(th.topic[:3], '#C8CEDC')}")
        t.append(f"  r{th.rounds}", style="#5A6380")
        if th.final and not closed:
            t.append("  FINAL", style="bold #FF6B6B")
        if closed:
            t.append("  closed", style="#5A6380")
        elif th.expires_tick is not None:
            left = th.expires_tick - tick
            t.append(f"  ⏱ {left}t", style="#FF6B6B" if left <= 1 else "#8B93A7")
        t.append("\n   ")
        t.append_text(BazaarApp.rail(th, width))
        their = [p for side, p in th.history if side == "them" and p is not None]
        if len(their) > 1:
            t.append(f"  {spark(their)}", style=THEIRS)
        if th.last_text:
            t.append("\n   ")
            if SUSPICIOUS.search(th.last_text):
                t.append("⚠ injection? ", style="bold #FF6B6B")
            t.append(f"“{th.last_text[:90]}”", style="italic #8B93A7")
        return t

    def negs_view(self):
        s = self.state
        opened = [th for th in s.threads.values() if th.status == "open"]
        recent = [th for th in s.threads.values() if th.status != "open"][-2:]
        parts = [Text.assemble(("● ", OURS), ("us   ", "#8B93A7"), ("◆ ", THEIRS), ("them   ", "#8B93A7"),
                               ("━ ", GAP), ("gap", "#8B93A7"))]
        width = max(8, min(30, self.query_one("#negs").size.width - 30))
        if not opened:
            parts += [Text(""), Text("no open threads", style="#5A6380")]
        for th in sorted(opened, key=lambda x: x.id) + recent[::-1]:
            parts += [Text(""), self.thread_view(th, s.tick, width)]
        duels = sorted(s.duels.values(), key=lambda d: -d.id)[:3]
        if duels:
            parts += [Text(""), Text("DUELS", style="bold #E0A458")]
            for d in duels:
                row = Text(f" #{d.id} {d.role[:4]:<4} ", style="#8B93A7", no_wrap=True, overflow="ellipsis")
                row.append(f"● {d.our_price if d.our_price is not None else '·':>3}", style=OURS)
                if d.our_days is not None:
                    row.append(f"/{d.our_days}d", style="#5A6380")
                row.append(" vs ", style="#5A6380")
                row.append(f"◆ {d.their_price if d.their_price is not None else '·':>3}", style=THEIRS)
                if d.their_days is not None:
                    row.append(f"/{d.their_days}d", style="#5A6380")
                status = {"deal": (f"  ✔ @{d.deal_price} +{d.points or 0:.1f}", "#3DDC97"),
                          "no deal": ("  ✖ no deal", "#FF6B6B")}.get(d.status, (f"  r{d.rounds}", "#5A6380"))
                row.append(*status)
                parts.append(row)
        return Group(*parts)

    def album_view(self):
        s = self.state
        rows = []
        for page in s.pages:
            code = page["set"]
            t = Text()
            t.append(f"{code} ", style=f"bold {SET_COLOR.get(code, '#C8CEDC')}")
            for i in range(12):
                rarity = SLOT_RARITY[i]
                have = s.owned.get(f"{code}-{i + 1:02d}", 0)
                if i == 10:
                    t.append(" ")
                mark = ("◆" if i == 10 else "★" if i == 11 else "■") if have else ("◇" if i == 10 else "☆" if i == 11 else "□")
                t.append(mark, style=RARITY_COLOR[rarity] if have else "#2A3456")
            done = page.get("have", 0)
            t.append(f" {done:>2}/{page.get('of', 10)}", style="bold #3DDC97" if page.get("complete") else "#8B93A7")
            if page.get("complete"):
                t.append(" ✔", style="bold #3DDC97")
            rows.append(t)
        legend = Text()
        for r, c in RARITY_COLOR.items():
            legend.append("■", style=c)
            legend.append(f"{r[:3]} ", style="#5A6380")
        return Group(*(rows or [Text("waiting for /api/me", style="#5A6380")]), legend)

    def score_view(self):
        sc = self.state.score or {}
        parts = [("duels", "duel_points", 10, "#4C8DFF"), ("ladder", "ladder_points", 10, "#E07A5F"),
                 ("trades", "neg_points", 10, "#3DDC97"), ("market", "mm_points", 30, "#B061FF")]
        rows = [Text.assemble(("total ", "#8B93A7"), (f"{sc.get('score', 0):.1f}", "bold #6FD3B8"),
                              (f"   deals {sc.get('deals', 0)}", "#5A6380"))]
        for label, key, cap, color in parts:
            v = sc.get(key) or 0.0
            n = min(round(v / cap * 20), 20)
            rows.append(Text.assemble((f"{label:<7}", "#8B93A7"), ("█" * n, color), ("░" * (20 - n), "#1E2742"),
                                      (f" {v:5.1f}", "bold")))
        return Group(*rows)

    def tape_view(self):
        s = self.state
        trades = [t for t in s.tape if t.ours or not self.only_ours]
        ours = [t for t in s.tape if t.ours]
        gained = sum(t.gain for t in ours if t.gain is not None)
        self.query_one("#tape").border_subtitle = (
            f"{len(s.tape)} trades · {sum(t.price for t in s.tape)} P volume · ours {len(ours)} ({gained:+.1f} P)"
            + ("  [only ours]" if self.only_ours else ""))
        table = Table(box=None, expand=True, padding=(0, 1), header_style="bold #5A6380")
        wide = self.query_one("#tape").size.width >= 140
        for col, kw in [("tick", {"justify": "right", "width": 5}), ("venue", {"width": 14}), ("seller → buyer", {"width": 18}),
                        ("card", {"ratio": 1}), ("price", {"justify": "right", "width": 6}),
                        ("vs book", {"justify": "right", "width": 8}), ("trend", {"width": 12}),
                        ("our gain", {"justify": "right", "width": 9})]:
            if col != "trend" or wide:
                table.add_column(col, no_wrap=True, overflow="ellipsis", **kw)
        room = max(self.query_one("#tape").size.height - 3, 1)
        for t in trades[:room]:
            i = slot(t.ref)
            book = BOOK[i] if i is not None and i < len(BOOK) else None
            delta = Text(f"{(t.price - book) / book:+.0%}", style="#3DDC97" if t.price < book else "#FF6B6B") if book else Text("")
            card = Text.assemble(("▌", RARITY_COLOR.get(SLOT_RARITY[i], "#C8CEDC") if i is not None and i < 12 else "#C8CEDC"),
                                 (f"{t.ref} ", f"bold {SET_COLOR.get(t.ref[:3], '#C8CEDC')}"), (t.name, "#C8CEDC"))
            gain = Text(money(t.gain), style="bold #3DDC97" if (t.gain or 0) >= 0 else "bold #FF6B6B")
            row_style = "on #16213F" if t.ours else ""
            who = Text.assemble((t.seller, "bold #6FD3B8" if t.seller == s.team else "#C8CEDC"), (" → ", "#5A6380"),
                                (t.buyer, "bold #6FD3B8" if t.buyer == s.team else "#C8CEDC"))
            trend = [Text(spark(list(s.prices.get(t.ref, []))[-10:]), style="#3E5C99")] if wide else []
            table.add_row(Text(str(t.tick), style="#5A6380"), Text(t.venue, style="#8B93A7"), who, card,
                          Text(f"{t.price}", style="bold"), delta, *trend, gain, style=row_style)
        return table

    def action_pause(self):
        self.paused = not self.paused
        self.redraw()

    def action_only_ours(self):
        self.only_ours = not self.only_ours
        self.redraw()

    def _retime(self, factor):
        if self._timer:
            self.speed = min(max(self.speed * factor, 0.05), 3.0)
            self._timer.stop()
            self._timer = self.set_interval(self.speed, self.step)

    def action_faster(self):
        self._retime(0.66)

    def action_slower(self):
        self._retime(1.5)


def main():
    ap = argparse.ArgumentParser(description="Live view of the Bazaar agent")
    ap.add_argument("--ws", help="WebSocket URL streaming events; without it a mock game plays")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--speed", type=float, default=0.35, help="seconds per mock step")
    args = ap.parse_args()
    BazaarApp(ws=args.ws, seed=args.seed, speed=args.speed).run()


if __name__ == "__main__":
    main()
