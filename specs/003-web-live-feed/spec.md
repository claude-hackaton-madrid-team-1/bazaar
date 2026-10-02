# 003 · The web view on the real feed, and the terminal UI gone

## Why

The game is live and the web view (spec 002) still plays a mock game. Spec 001 left the real
publisher out of scope: "the agent itself and the market watcher only need to emit the events
above". The market watcher exists now (`bazaar monitor`), so it should feed the web view. We look
at one screen, the web one: the Textual TUI goes.

## What

### The monitor publishes the web's event contract

`bazaar monitor` appends to `.local/stream.jsonl` (one event per line, the envelope of
001: `{"id", "tick", "t", "type", "scope", "actor", "payload"}`). Events from the live stream are
written the moment they land; every tick adds the rest:

| Event | From | Payload |
|---|---|---|
| `agent.hello` | `GET /api/me` (needs `BAZAAR_KEY`) | `{team: me.id, name: me.name}` |
| `agent.me` | `GET /api/me` (needs `BAZAAR_KEY`) | the `/me` body |
| `clock` | `GET /api/clock` | `{day, tick_seconds}`, envelope `tick` |
| every new feed event | `GET /api/feed` | unchanged |

Feed ids are positive. Events the monitor makes up get negative ids, unique per tick, so they never
collide with a feed id. No new API reads: the monitor already makes every one of them, so the
team still has one reader.

Without `BAZAAR_KEY` there is no `agent.hello`: nothing is "ours", so the Agent, Negotiations and
Album screens stay empty and Market and Debug show the whole market. That is expected, not a bug.

### The server relays it

`tui/serve.py` tails `.local/stream.jsonl` by default (it replays what is already there, then
streams new lines as the monitor writes them). The mock game stays behind `--mock` for when the
doors are closed. A client that joins late first gets the latest `agent.hello`, `agent.me`, `clock`
and `agent.phase`, always, then the last 5000 events (the web reducer bounds its own state), so our
events in that replay are already ours.

```
uv run bazaar monitor                          # writes .local/stream.jsonl every tick
uv run --project tui tui/serve.py              # http://localhost:8777, the real game
uv run --project tui tui/serve.py --mock       # the mock game
```

### The terminal UI is removed

`tui/app.py`, `tui/app.tcss`, `tui/state.py` (its reducer, mirrored by `web/lib/state.ts`) and their
tests go, and `textual` leaves `tui/pyproject.toml`. `tui/` keeps the server and the mock.

## How we'll know it works

- `tests/test_monitor.py`: one tick turns into `clock`, `agent.hello`, `agent.me`, then the feed
  events unchanged; without `/me` only `clock` and the feed events; made-up ids are negative and
  unique across ticks.
- `tests/test_tui_serve.py`: a server fed by a stream file gives a late client `agent.hello` first,
  and a line appended after it connected reaches it.
- `tests/test_tui_state.py`: the mock still only emits types the web reducer knows, with real ids.
- Manual: with `bazaar monitor` and `tui/serve.py` running during a game, `/market/` and `/debug/`
  show the live market tick by tick.

## Out of scope

`agent.phase`, `agent.thought` and `agent.action` come from the trading agent, not the monitor. Folding
the server into the monitor process. Renaming `tui/`.
