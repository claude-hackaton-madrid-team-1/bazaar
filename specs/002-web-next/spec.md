# 002 · Web view on Next.js, one screen per question

## Why

The web view from 001 puts every event on one page: our agent's loop, our threads, the album, the
whole market tape and the raw stream, all moving at once. During a match we cannot tell what *our*
agent did this tick, and the judges will not either. We need to see our agent in isolation, and
everything else only when we go looking for it.

## What

`web/` becomes a Next.js (App Router, TypeScript) app, built as a static export (`output: "export"`).
Node is needed to build it, never to run it: `tui/serve.py` serves `web/out/` and keeps streaming
events on `/events`, exactly as in 001. The event contract of 001 is unchanged.

```
uv run --project tui tui/serve.py              # http://localhost:8777, serves web/out + mock stream
cd web && npm run dev                          # http://localhost:3000, reads ws://localhost:8777/events
http://localhost:8777/?ws=wss://…              # any page, real stream
```

### Ours vs market

One function decides whether an event belongs to our agent, `isOurs(event, team)` in
`web/lib/state.ts`, and every screen filters through it:

| Event | Ours when |
|---|---|
| `agent.*`, `clock` | always |
| `thread.message` | `payload.team` is our team |
| `thread.closed` | the thread is one of ours |
| `settlement` | `payload.parties` includes our team |
| `duel.message`, `duel.result` | always (duels are only ever ours) |
| anything else | never |

The reducer keeps our events in their own bounded list, so a noisy market never pushes our history out.

### Screens

| Route | Answers | Shows |
|---|---|---|
| `/` Agent | What is our agent doing, and why? | Current phase and goal; a timeline of our events grouped by tick, each tick read as Observe → Decide → Act → Result (thoughts, actions, our offers, counterparty replies, our settlements with their gain). Nothing from other teams. |
| `/negotiations/` | How is each deal going? | Our threads, open first. The selected thread (`?id=`) as a conversation: our messages vs theirs, each offer with its ids, ask vs bid on a price rail, `final`, expiry, an injection flag on suspicious counterparty text. Duels below. |
| `/album/` | How close are we to completing pages? | One row per barrio page by rarity slot, owned / missing, completion; score breakdown; score and cash over ticks. |
| `/market/` | What is everyone else trading? | Every settlement not ours (ours toggleable), prices per card, most active teams. |
| `/debug/` | What exactly arrived? | The raw event stream, filterable by type and by ours / market, with an inspector showing the full JSON of the clicked row. |

A persistent header on every screen: team, day, tick and countdown bar, connection state, cash, score,
rank. Navigation between the five screens.

### Trust

Counterparty text is rendered as text only (React escapes it) and flagged when it looks like an
injection, as in 001.

## How we'll know it works

- `cd web && npm test` (Node's test runner, TypeScript stripped natively):
  - the reducer port keeps every behaviour the 001 tests covered (ask vs bid, `final`, gain, duels,
    the bounded tape, every id);
  - `isOurs` follows the table above, and a flood of market settlements does not evict our events;
  - each screen's selector (timeline by tick, thread list and conversation, album rows, market tape
    without our trades, debug filters) returns what its screen shows.
- `cd web && npm run build` produces `web/out/` with an `index.html` per route.
- `tests/test_tui_serve.py`: the server serves `web/out` (index per route) and a late client still gets
  `agent.hello` first.
- CI runs the web tests and the build.
- Manual: with `tui/serve.py` running, `/` shows only our agent, tick by tick.

## Out of scope

The real event publisher in the agent, and the Textual TUI (unchanged).
