# 001 · Agent TUI: a live terminal view of what our agent is doing

## Why

During the weekend we need to see, at a glance, what our agent is thinking, which negotiations it has
open, how the album is filling, how the score moves, and what the rest of the market is trading. The
same screen is the demo for the judges (40 % of the score is "ideas and craft").

## What

A terminal UI (`tui/app.py`, Textual) fed by a stream of JSON events. One event per WebSocket message.
Until the agent and the market watcher publish over a socket, a mock source plays a believable game.

```
uv run --project tui tui/app.py                  # mock game
uv run --project tui tui/app.py --ws ws://…      # real stream
uv run --project tui tui/app.py --seed 7 --speed 0.2
```

### Web view

The same screen as a web page (`web/`), for more room and flexibility. The page is only a WebSocket client:

```
cd web && npm ci && npm run build                 # once per change: static export to web/out (spec 002)
uv run --project tui tui/serve.py                 # http://localhost:8777, mock game streamed on /events
http://localhost:8777/?ws=wss://…                 # same page, real stream
cd web && npm run dev                             # hot reload on http://localhost:3000, reads ws://localhost:8777/events
```

`tui/serve.py` serves the static export in `web/out/` and streams the mock game on `/events`. A client that joins late first gets
the latest `agent.hello`, `agent.me`, `clock` and `agent.phase`, then the last 500 events.

The web view shows every identifier the game hands us: event id, tick and actor on every row; thread,
message and offer ids, maker → to and created/expires ticks on every offer; settlement id, asset id and serial
on every trade; duel ids; team ids. Two extra panels: a raw **event stream** (every envelope, filterable)
and an **inspector** with the full JSON of whatever row is clicked. The reducer (`web/lib/state.ts`, spec 002) mirrors
`tui/state.py` and keeps those ids.

### Panels

| Panel | Shows |
|---|---|
| Header | team, day, tick and a bar counting down to the next tick, connection state, cash, score and rank |
| Agent loop | Observe → Decide → Act, the current phase lit; current goal; the agent's reasoning log and actions |
| Negotiations | our open threads: counterparty, topic, their ask vs our bid on a price rail, rounds, `final`, expiry; duels |
| Album | one row per barrio page: 10 page slots by rarity (owned / missing), epic and legendary marks, completion |
| Score | live breakdown: duels, ladder, negotiation, market-making |
| Market tape | every settlement seen in the market: tick, venue, seller → buyer, card, price. Our trades are highlighted with the value we gained at our private values |

### Event contract

Every message is the envelope `/api/feed` already uses:
`{"id", "tick", "t", "type", "scope", "actor", "payload"}`.

Game events, same payload as the API (see `docs/api/openapi.json`):

- `thread.message`: `payload.{thread, team, with, sender, text, offer.{maker, give, want, final, expires_tick}}`
- `settlement`: `payload.{parties, venue, persona, items[{ref, name, frm, to}], price}`
- `thread.closed`: `payload.{thread}`
- `duel.message`: `payload.{duel, role, sender, price, days, text}`
- `duel.result`: `payload.{duel, deal, price, points}`

Our own events, published by our agent and the market watcher:

- `agent.hello`: `payload.{team, name}`
- `clock`: `payload.{day, tick_seconds}` plus the envelope `tick`
- `agent.phase`: `payload.{phase: observe | decide | act, goal?}`
- `agent.thought`: `payload.{text}`
- `agent.action`: `payload.{kind, summary}`
- `agent.me`: the body of `GET /api/me` (`cash`, `score`, `album.pages`, `assets[].your_value`)

Unknown types are ignored, so the stream can grow without breaking the screen.

### Trust

Counterparty text is untrusted: it is shown as text only, never parsed for markup, and flagged when it looks like an injection.

A `settlement` may carry `your_value` (our value for the card, from `/api/me/value`) so the tape can show what we gained; without it the last `agent.me` snapshot is used.

## How we'll know it works

- `tests/test_tui_state.py`: the reducer turns each event above into the right state (ask vs bid,
  `final`, our trades and their gain, album, score, unknown types ignored), and the mock source only
  emits types the reducer knows. These run in CI with no extra dependencies.
- `uvx --with textual pytest tests/test_tui_app.py`: the app mounts every panel, plays mock
  events and shows the tick in the header (skipped when Textual is not installed).
- `tests/test_tui_state.py`: the mock carries real ids (unique event ids, settlement ids, asset ids and
  serials on items and in `agent.me`, separate message ids, our sell offers give asset ids we hold).
- `cd web && npm test`: the web reducer keeps ask vs bid, `final`, our gain, duels, the bounded tape and every id.
- `tests/test_tui_serve.py`: the server serves the page and a late client gets `agent.hello` first
  (skipped when `websockets` is not installed).
- Manual: `uv run --project tui tui/app.py` shows a game moving. Keys: `space` pause, `o` only our trades, `+`/`-` speed, `q` quit.

## Out of scope

The real WebSocket server, the agent itself and the market watcher. They only need to emit the
events above.
