# Live services · integration guide

Every URL a teammate, a dashboard or an agent needs, with the exact contract each one speaks.
Taker, maker and Phoenix are public and read-only: nothing there can trade, change a setting or reveal
a key. `bazaar-mcp` exposes our tools, so every call needs a bearer token (see below).

| Service | URL | What it is |
|---|---|---|
| **Taker** | https://bazaar-taker-production.up.railway.app · `wss://bazaar-taker-production.up.railway.app/events` | Autonomous buyer (`bazaar agent taker`): accepts cheap venue asks, negotiates with dealers |
| **Maker** | https://bazaar-maker-production.up.railway.app · `wss://bazaar-maker-production.up.railway.app/events` | Autonomous market maker (`bazaar agent maker`): posts, reprices and cancels our asks and bids; never accepts |
| **Phoenix** | https://phoenix-production-6aa3.up.railway.app | Traces UI for every negotiation, duel, monitor tick and CLI line (project `bazaar`) |
| **bazaar-mcp** | https://bazaar-mcp-production.up.railway.app/mcp (`GET /health` public) | Team 1's runtime tools as a remote MCP server (Streamable HTTP) for teammates' Claude Code: **bearer token required**, writes are a dry run |

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
`duel_offer`, `duel_hold`, and, from our venue's broker (`agent: "broker"`, `bazaar broker run`, no HTTP yet),
`broker_match`; `guardrail` is `allowed` or `denied: <rules>` (from `GUARDRAILS.md`).

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
## Evals scorecard (Postgres)

`bazaar-evals` (README "Evals") writes one `outcomes` row per settled duel, dealer thread, team trade or
Market Test and keeps three views current. A dashboard reads them with plain SQL, or runs
`uv run bazaar evals report --json`. Scores are 0..1; labels `good` (≥ 0.6) · `ok` (≥ 0.3) · `bad`.

`outcomes` (key `(target, subject)`): `target` duel | dealer | trade | market_test · `subject`
(`duel:85`, `thread:101`, `settlement:67`, `market_test:sat`) · `score` (null = settled, not scorable
yet) · `label` · `explanation` · `details` jsonb (the numbers behind it) · `day` fri | sat | sun ·
`recorded_tick` · `realized_surplus` (primas: duels after decay, trades at our values) · `ladder_share` ·
`decision_id` · `jev_question` / `jev_verdict` / `jev_right` · `trace_id` / `span_id` / `annotated_at`
(the Phoenix span it is attached to).

```text
eval_scorecard        target, day, outcomes, scored, mean_score, worst_score, good, ok, bad,
                      surplus, worst (up to 5 subjects, worst first), last_tick
eval_ladder           level, dealers, threads, deals, best3_share (best three, missing = 0), best3
eval_jev_calibration  question, outcomes, decided, n_right, n_wrong, n_unknown
```

`bazaar evals report --json`:

```json
{
  "scorecard": [{"target": "duel", "day": "fri", "outcomes": 20, "scored": 20, "mean_score": 0.279,
                 "worst_score": 0.0, "good": 7, "ok": 5, "bad": 8, "surplus": 176.9,
                 "worst": ["duel:119", "duel:120", "duel:131", "duel:132", "duel:147"], "last_tick": 163}],
  "ladder": [{"level": 1, "dealers": "abuela", "threads": 5, "deals": 4, "best3_share": 0.733,
              "best3": ["thread:99", "thread:101", "thread:110"]}],
  "worst": [{"target": "dealer", "subject": "thread:187", "score": 0.0, "label": "bad", "day": "fri",
             "recorded_tick": 104, "realized_surplus": null, "explanation": "No deal with chato ...",
             "span_id": null}],
  "jev_calibration": [{"question": "offer_is_worth_accepting", "calls": 1, "decided": 0, "undecided": 1,
                       "right": 0, "wrong": 0, "unknown": 0}],
  "official": {"tick": 159, "duel_points": 0.0, "ladder_points": 0.058, "negotiating": 8.34, "...": "..."},
  "annotations": {"annotated": 14, "pending": 12, "no_span": 0}
}
```

In Phoenix, each score is a span annotation on its trace: `duel_pie_share` on a `duel` root,
`ladder_share` on a `negotiation` root, `trade_surplus` on the deciding `<agent> tick N` trace
(annotator `CODE`, identifier `bazaar-evals:<subject>`, so two trades decided in one tick keep two annotations).

## Game endpoints a dashboard can call directly

Organiser API, `https://bazaar.causaprima.ai`, no key: `GET /api/feed?limit=500` (public events, last
500 only), `/api/clock`, `/api/leaderboard`, `/api/dealers`, `/api/levels`, `/api/venues`,
`/api/venues/{id}/offers`. The live stream `GET /api/events/stream?scope=team` needs our team key, so it
stays server-side: a browser never holds the key.

## Our venue: from build only to live

Everything for our own market is built and tested, and blocked: `allow_venue_open = false` in
`GUARDRAILS.md` makes `guardrails.check()` refuse `venue_open`, `venue_fee`, `venue_announce` and every
`broker_match`, even with `--live`. Closing (`venue_close`) never waits for the switch, so a venue opened
by hand can always be closed. The kill switch (`trading_enabled = false`, `touch .local/PAUSE`) stops all of them.

1. **Check the cash.** Opening takes the 250 P bond + 20 P fee, and the guardrail keeps cash at or above
   `cash_floor` (270) afterwards: with today's floor we need **540 P** to open. The floor was set to
   reserve exactly the venue's 270 P, so whoever flips the switch decides whether to lower it first.
2. **Flip the switch** in `GUARDRAILS.md` (`allow_venue_open` = true), run `uv run bazaar rules`, commit.
3. **Open a board venue** (a broker acts only on `board`; on `auto` the engine crosses first and earns
   what the free stall earns, half the bench points). Dry run first, then live:
   `uv run bazaar venue open --name "..." --fee-bps 0` → read the line → add `--live`.
   The broker key comes back once: it is saved to `.local/broker.env` (mode 0600) as
   `BAZAAR_BROKER_KEY` with `BAZAAR_VENUE`, and never printed. For a Railway service, copy it into the
   service's variables by hand (`BAZAAR_BROKER_KEY`, `BAZAAR_VENUE`). Team venues start trading at +3 h.
4. **Run the broker**: `uv run bazaar broker run` (dry run: `decisions` rows with `dry_run = true`,
   `.local/agents/broker_ticks.jsonl` and `broker_sessions.jsonl`), then `uv run bazaar broker run --live`.
   It reads `/api/broker/book` once per tick, never matches our own offers or two offers of one maker, and
   sends the exact maximum-surplus matching (bench first) at the midpoint price.
5. **Fees** change with `uv run bazaar venue fee <bps> [--fee-per-card N] --live` (effective after the
   public notice); `uv run bazaar venue announce "..." --live` posts a notice with the broker key.
6. **Watch** `uv run bazaar venue status` (switch, venue row, what the broker would match) and the
   per-session lines `Market Test bNN over: pairs, quoted surplus`.

A real broker key (`bk_...`) is only sent to `https://bazaar.causaprima.ai`, a simulator key (`simbk-...`)
only to another host: the broker works unchanged against PR #55's `bazaar-sim` (`/api/broker/book`,
`/api/broker/matches`, `/api/broker/announce`, `bench_offers`, `bench.started` / `bench.finished`).

## Not public

- **Postgres** (`iriguchi.proxy.rlwy.net:28880`, db `railway`): the shared memory. Credentials only in
  the Railway dashboard; never in chat, git or a browser.
- **`bazaar-duels`**: the duel player, a background worker with no HTTP. Its traces are in Phoenix.
- **`bazaar-evals`**: the evals loop, a worker with no HTTP. Its output is the Postgres scorecard above.
- **The monitor**: runs in the CLI on a laptop (`uv run bazaar monitor --notify`) by team decision.
