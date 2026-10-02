# Live services · integration guide

Every URL a teammate, a dashboard or an agent needs, with the exact contract each one speaks.
Taker, maker and Phoenix are public and read-only: nothing there can trade, change a setting or reveal
a key. `bazaar-mcp` exposes our tools, so every call needs a bearer token (see below).

| Service | URL | What it is |
|---|---|---|
| **Taker** | https://bazaar-taker-production.up.railway.app · `wss://bazaar-taker-production.up.railway.app/events` | Autonomous buyer (`bazaar agent taker`): accepts cheap venue asks, negotiates with dealers |
| **Maker** | https://bazaar-maker-production.up.railway.app · `wss://bazaar-maker-production.up.railway.app/events` | Autonomous market maker (`bazaar agent maker`): posts, reprices and cancels our asks and bids; never accepts |
| **Phoenix** | https://phoenix-production-6aa3.up.railway.app | Traces UI for every negotiation, duel, monitor tick and CLI line (project `bazaar`) |
| **bazaar-mcp** | `https://<generated domain>/mcp` (`GET /health` public) | Team 1's runtime tools as a remote MCP server (Streamable HTTP) for teammates' Claude Code: **bearer token required**, writes are a dry run |

Both agents start in **dry run**: they log and publish what they *would* do. A service trades only
when `BAZAAR_LIVE=1` is set on it by hand (see README, "Production on Railway").

## Taker and maker: HTTP

Both services serve the same three routes (CORS `*`, `GET` only).

### `GET /health`

```json
{"ok": true, "agent": "taker", "mode": "dry", "tick": null, "last_tick_at": null,
 "doors": "closed", "paused": true, "next_opens": "2026-10-03T09:00:00+02:00",
 "tick_seconds": 60.0, "server_tick": 159}
```

- `mode`: `dry` or `live`.
- `tick`, `last_tick_at`: the last game tick the agent handled.
- `doors`, `paused`, `next_opens`, `tick_seconds`, `server_tick`: the game clock as the agent sees it.
  The tick length changes every day (60 s Friday, 30 s Saturday, 15 s Sunday) and the organisers may move it.

### `GET /state`

```json
{"agent": "taker", "mode": "dry", "tick": 171, "t_hours": 2.4, "team": "t01",
 "last_tick_at": "...", "decisions": [ /* the last 50 decisions, newest last */ ]}
```

The maker also returns our open offers; the taker returns its active dealer threads.

## Taker and maker: WebSocket `/events`

`wss://bazaar-taker-production.up.railway.app/events` and `wss://bazaar-maker-production.up.railway.app/events`.

- One JSON message per event, in the same envelope as the web dashboard's live feed (spec 003 on
  `feat/web-live`): `{id, tick, t, type, scope, actor, agent, payload}`. Ids are negative and made up,
  so they never collide with game event ids.
- A client that joins late first receives the last **200** events, then everything new as it happens.
- Event types:

| `type` | When | `payload` |
|---|---|---|
| `agent.tick` | once per game tick the agent handles | `{mode}` |
| `agent.decision` | every move the agent proposes, sent or not | a decision (below) |
| `agent.execution` | every request the agent actually sends to the game | `{decision_id, tick, sdk_method, request, response, error_code}` |

A decision:

```json
{"agent": "maker", "tick": 155, "kind": "post_ask",
 "inputs": {"ref": "LAT-09", "price": 68, "price_candidates": {"aggressive": 68, "fair": 59, "quick_sale": 50}, ...},
 "reason": "ours 35 + page bonus 10.0 ...; jev aggressive (0.87) of {...}",
 "guardrail": "allowed", "chosen": true, "status": "approved", "dry_run": true,
 "jev": {"verdict": "aggressive", "value": 0.87,
         "probabilities": {"aggressive": 0.91, "fair": 0.07, "quick_sale": 0.02},
         "reason": null, "digest": "6914f933..."},
 "thread_id": null, "move": {...}}
```

`kind` is one of `accept_ask`, `dealer_open`, `dealer_bid`, `dealer_accept`, `dealer_walk`, `post_ask`,
`post_bid`, `cancel_ask`, `cancel_bid`, `hold_ask` / `hold_bid` and `reprice_ask` / `reprice_bid` (the
maker's `reprice_or_hold` verdict), and, from the duel player (`agent: "duels"`, no HTTP), `duel_accept`,
`duel_offer`, `duel_hold`; `guardrail` is `allowed` or `denied: <rules>` (from `GUARDRAILS.md`).

`jev` is Jev's verdict when it was asked, else `null`: `verdict` (a noul's `yes`/`no`, a choice's
option such as `accept` or `quick_sale`, or `undecided`), `value` (the noul probability or the choice
confidence), `probabilities` (every option's float for a choice), `reason` (why it is `undecided`:
`below_threshold`, `typesafe_api_key_missing`, `request_timeout`, `no tick budget for jev`, …) and
`digest` (the masked decision line in `jev-decisions/`, which its outcome line points at). `undecided`
never authorizes anything: the agent keeps its deterministic move. A duel row's `inputs` is the state Jev
read (role, our limit, the rival's last offer and price history, rounds, ticks left, decay, the legal
moves, the default move), with `jev_days` for `rival_cares_about_days` in two-issue sessions.

Quick checks:

```sh
curl -s https://bazaar-taker-production.up.railway.app/health
curl -s https://bazaar-maker-production.up.railway.app/state
npx wscat -c wss://bazaar-taker-production.up.railway.app/events
```

```js
const ws = new WebSocket("wss://bazaar-maker-production.up.railway.app/events");
ws.onmessage = (m) => { const e = JSON.parse(m.data); console.log(e.agent, e.type, e.tick, e.payload); };
```

## Phoenix

Open https://phoenix-production-6aa3.up.railway.app and sign in as `admin@localhost`. The password is
the `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` variable of the `phoenix` service in the Railway dashboard
(project `heartfelt-warmth`). Project **`bazaar`** holds one trace per `monitor tick N`, `duels tick N`,
a `duel` trace per duel and a `negotiation` trace per dealer conversation. To send a laptop's traces
there, create your own key in Phoenix (Settings → API Keys) and follow README, "Send your laptop's traces there".

## bazaar-mcp: the runtime tools over MCP (not read-only: bearer token)

`POST /mcp`, MCP Streamable HTTP (stateless, JSON responses), `Authorization: Bearer <BAZAAR_MCP_TOKEN>`
on every request: missing or wrong → `401 {"error": "unauthorized"}`; more than 5 requests/s per token
(burst 20) → `429` with `Retry-After`; more than `mcp_calls_per_minute` (RUNTIME.md, 30) tool calls per
minute per token → an error result `rate limited: …`. `GET /health` → `{"ok": true, "server": "bazaar",
"tools": 18}` with no token (nothing about the mode or the game).

Tools: the 12 reads (`status`, `clock`, `strategy`, `curves`, `tape`, `teams`, `book`, `traders`, `alerts`,
`rules`, `threads`, `thread`) and 6 writes (`dealer_buy`, `sell_list`, `sell_bid`, `sell_cancel`,
`duel_move`, `steer`). Each answer is one text block holding JSON. A write answers
`{"tool", "tick", "status": "approved"|"rejected"|"expired"|"done"|"failed"|"hold", "sent", "guardrail",
"request", "command", ...}`: `approved` + `sent: false` is a dry run (what WOULD be sent), the default
unless `BAZAAR_LIVE=1` is set on the service by hand. The guardrails run inside the server for every
write; every write call is a `decisions` row with agent `mcp`. Answers never carry a key, token,
password or URL. Add it to Claude Code: README, "The tools as a remote MCP server".

## Game endpoints a dashboard can call directly

Organiser API, `https://bazaar.causaprima.ai`, no key: `GET /api/feed?limit=500` (public events, last
500 only), `/api/clock`, `/api/leaderboard`, `/api/dealers`, `/api/levels`, `/api/venues`,
`/api/venues/{id}/offers`. The live stream `GET /api/events/stream?scope=team` needs our team key, so it
stays server-side: a browser never holds the key.

## Not public

- **Postgres** (`iriguchi.proxy.rlwy.net:28880`, db `railway`): the shared memory. Credentials only in
  the Railway dashboard; never in chat, git or a browser.
- **`bazaar-duels`**: the duel player, a background worker with no HTTP. Its traces are in Phoenix.
- **The monitor**: runs in the CLI on a laptop (`uv run bazaar monitor --notify`) by team decision.
