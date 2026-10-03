# Live services · integration guide

Every URL a teammate, a dashboard or an agent needs, with the exact contract each one speaks.
Taker and maker are public and read-only: nothing there can trade, change a setting, or reveal a key or
one of our private numbers (see "Public by design"). Phoenix is read-only behind its own login.
`bazaar-mcp` exposes our tools, so every call needs a bearer token (see below).

| Service | URL | What it is |
|---|---|---|
| **Taker** | https://bazaar-taker-production.up.railway.app · `wss://bazaar-taker-production.up.railway.app/events` | Autonomous buyer (`bazaar agent taker`): accepts cheap venue asks, negotiates with dealers |
| **Maker** | https://bazaar-maker-production.up.railway.app · `wss://bazaar-maker-production.up.railway.app/events` | Autonomous market maker (`bazaar agent maker`): posts, reprices and cancels our asks and bids; never accepts |
| **Phoenix** | https://phoenix-production-6aa3.up.railway.app | Traces UI for every negotiation, duel, monitor tick and CLI line (project `bazaar`) |
| **bazaar-mcp** | https://bazaar-mcp-production.up.railway.app/mcp (`GET /health` public) | Team 1's runtime tools as a remote MCP server (Streamable HTTP) for teammates' Claude Code: **bearer token required**, writes are a dry run |
| **Simulator** | https://bazaar-sim-production-1d48.up.railway.app | A simulated Bazaar (`bazaar-sim`): the organiser API's routes and shapes, keys `sim-team1`…`sim-team8`, for testing agents and the dashboard while the game is closed |

Both agents are **LIVE since Sat 2026-10-03 01:45 Madrid**: `BAZAAR_LIVE=1` is set by hand on
`bazaar-taker` and `bazaar-maker` (they trade from the 09:00 opening), and `GET /health` says
`"mode": "live"`. Without that variable an agent is a dry run: it logs what it *would* do and publishes
only its outline (no prices, see "Public by design"). To stop one: first its kill switch, which holds
at once (`railway ssh --service bazaar-taker -- touch /app/.local/PAUSE`; each service has its own),
then `railway variable delete BAZAAR_LIVE --service bazaar-taker` (it redeploys in dry run). Neither
withdraws our open offers: `bazaar sell cancel` does (README, "Production on Railway").

## Taker and maker: HTTP

Both services serve the same three routes (CORS `*`, `GET` only).

### `GET /health`

```json
{"ok": true, "agent": "taker", "mode": "dry",
 "target": {"mode": "real", "url": "https://bazaar.causaprima.ai"}, "tick": null, "last_tick_at": null,
 "doors": "closed", "paused": true, "next_opens": "2026-10-03T09:00:00+02:00",
 "tick_seconds": 60.0, "server_tick": 159}
```

- `mode`: `dry` or `live`.
- `target`: where the agent's requests go: `{"mode": "real", "url": "https://bazaar.causaprima.ai"}`, or
  `{"mode": "simulator", ...}` when it runs with `BAZAAR_SIM=1` (README "Simulator").
- `tick`, `last_tick_at`: the last game tick the agent handled.
- `doors`, `paused`, `next_opens`, `tick_seconds`, `server_tick`: the game clock as the agent sees it.
  The tick length changes every day (60 s Friday, 30 s Saturday, 15 s Sunday) and the organisers may move it.

### `GET /state`

```json
{"agent": "taker", "mode": "dry", "tick": 171, "t_hours": 2.4, "team": "t01",
 "last_tick_at": "...", "threads": [ /* taker */ ], "decisions": [ /* the last 50 decisions, newest last */ ]}
```

The taker returns its active dealer threads, `{dealer, thread, item, ticks, opened_tick, accepted_price}`;
the maker returns our open board offers, `{id, side, ref, price, venue, expires_tick, created_tick}`, and
`posted_this_tick` (card refs).

## Public by design: what these routes never show

There is no token (a browser page reads `/events` directly, so a token would ship in its JS), so the data
itself must be safe to publish. `/state` and `/events` show what an agent **did**, never **why in numbers**:
no card value, max price, bid ladder, surplus, score, cash, guardrail limit or affinity, and no `reason`
or console line (they spell those numbers out). The filter is an allow-list in
`src/bazaar_agent/agents/status.py` (`public_decision`, `public_execution`, `public_view`): a field added
to a decision later stays private until someone lists it there. The full row (inputs, reason, line, Jev's
probabilities) still goes to the Postgres `decisions` table and to Phoenix, both private.

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
| `agent.execution` | every request the agent actually sends to the game | `{agent, decision_id, tick, method, request, ok, error_code, created_id}` |

A decision, as published:

```json
{"agent": "taker", "decision_id": 4180, "tick": 155, "kind": "dealer_bid",
 "chosen": true, "status": "approved", "dry_run": false, "sent": "sending", "thread_id": 812,
 "guardrail": "allowed", "jev": {"verdict": "yes"},
 "inputs": {"dealer": "abuela", "thread": 812, "item": "LAV-08", "her_ask": 30, "final": false},
 "move": {"kind": "bid", "price": 21}}
```

- `kind` is one of `accept_ask`, `dealer_open`, `dealer_bid`, `dealer_accept`, `dealer_walk`, `post_ask`,
  `post_bid`, `cancel_ask`, `cancel_bid`, `hold_ask` / `hold_bid` and `reprice_ask` / `reprice_bid` (the
  maker's `reprice_or_hold` verdict), and `broker_match` from our venue's broker (`agent: "broker"`, run
  inside the maker's tick loop).
- `status` is `approved` (sent, or would be in a dry run), `rejected` or `expired`; `sent` is
  `would-send`, `sending` or `not sent`.
- `guardrail` is only a label: `allowed`, `denied` or `-` (the rules it broke are in the `decisions` table).
- `jev` is `{verdict}` when Jev was asked (a noul's `yes`/`no`, a choice's option such as `quick_sale`, or
  `undecided`), else `null`. `undecided` never authorizes anything.
- `inputs` keeps only `dealer`, `thread`, `item`, `ref`, `card`, `rarity`, `side`, `venue`, `offer_id`,
  `maker`, `ask` / `her_ask` (the counterparty's price), `fee` and `final`, read from the row's inputs
  (and from their `offer` / `listing` part for the maker's Jev rows). Our own `price` and the `move`
  (`{kind, price}`, `{accept, price}`, `{open_thread, topic}`, `{give, want, venue}`, `{cancel}`,
  `{hold}`, `{reprice, price}`) appear only on an `approved` row of a live agent: a price we never sent
  stays private.
- A row that was not sent (`rejected`, `expired`, a skipped accept, and every dry-run row) is cut down
  further: `{agent, tick, kind, status, guardrail, jev: null, inputs: {item | ref | card, venue, side}, move: {}}`.
  No counterparty, offer id, ask or price: otherwise a rival could list a card and learn from our
  `skip ... accept quota` or dry-run `would accept` row that its ask sat below our value.
- An execution shows the `request` we sent (`offer`, `thread`, `with`, `topic`, `price`, `give`, `want`,
  `venue`), whether it worked (`ok`, `error_code`) and the id it created (`created_id`), not the
  game's answer body.

The duel player (`agent: "duels"`, kinds `duel_accept`, `duel_offer`, `duel_hold`) has no HTTP and
publishes nothing here: its rows, with the state Jev read (role, our limit, the rival's offers, rounds,
the legal moves), are in the `decisions` table only. In that table `jev` is the full verdict: `value`
(the noul probability or the choice confidence), `probabilities`, `reason` (why it is `undecided`:
`below_threshold`, `request_timeout`, `no tick budget for jev`, …) and `digest` (the masked decision
line in `jev-decisions/`, which its outcome line points at).

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
"tools": 18, "target": {"mode": "real", "url": "https://bazaar.causaprima.ai"}}` with no token (the
target is a mode and a public URL; nothing about the live/dry mode or the game state).

Tools: the 12 reads (`status`, `clock`, `strategy`, `curves`, `tape`, `teams`, `book`, `traders`, `alerts`,
`rules`, `threads`, `thread`) and 6 writes (`dealer_buy`, `sell_list`, `sell_bid`, `sell_cancel`,
`duel_move`, `steer`). Each answer is one text block holding JSON. A write answers
`{"tool", "tick", "status": "approved"|"rejected"|"expired"|"done"|"failed"|"hold", "sent", "guardrail",
"request", "command", ...}`: `approved` + `sent: false` is a dry run (what WOULD be sent), the default
unless `BAZAAR_LIVE=1` is set on the service by hand. The guardrails run inside the server for every
write; every write call is a `decisions` row with agent `mcp`. Answers never carry a key, token,
password or URL. Add it to Claude Code: README, "The tools as a remote MCP server".
## Evals scorecard (Postgres)

The evals (README "Evals") write one `outcomes` row per settled duel, dealer thread, team trade or
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

## Simulator

https://bazaar-sim-production-1d48.up.railway.app serves the same routes as the organiser API
(`/api/clock`, `/api/feed`, `/api/me`, `/api/events/stream`, ...) with the same JSON, plus
`GET /sim/state` (a public summary). Our CLI and agents reach it with the flag `BAZAAR_SIM=1` (no URL
to type: the targets are hardcoded in `src/bazaar_agent/config.py`), e.g. `BAZAAR_SIM=1 uv run bazaar
status`. A dashboard can point its base URL there to develop against live-looking data; team routes
take `X-Team-Key: sim-team1` (a simulator key, not a secret, refused by the real game). The taker's,
maker's and MCP server's `/health` carry `target: {mode: real|simulator, url}`. README, "Simulator".

## Bazaar Live (the show)

`bazaar-live` (repo [bazaar-live](https://github.com/claude-hackaton-madrid-team-1/bazaar-live)): the
buyer and the seller at a Rastro stall, acting out and voicing every public move. Its public URL is the
Railway-generated domain of service `bazaar-live` (generated once by hand; listed in its README).

- The page reads only the taker's and maker's public `/health`, `/state` and `WS /events` above, from
  the browser, and keeps only the public fields; it sends nothing to the agents or the game and holds no
  team key. `?mock=1` plays recorded fixtures when the doors are closed.
- Its own server answers `GET /health` (`{ok, service, tts}`), `GET /api/tts/providers` and
  `POST /api/tts`: a proxy to ElevenLabs / Gemini TTS with the keys server-side (`ELEVENLABS_API_KEY`,
  `GEMINI_API_KEY`, both optional). It speaks only the show's own template lines, for its own page
  (`Origin`), under per-address and global rate limits and a daily character budget.

## Our venue: opened by the maker at game hour 6.5

Our board venue runs inside the **maker** on Railway (`bazaar-maker`, no new service). Every maker tick,
before its own offers, `agents/venue_keeper.py`:

1. **Finds the venue we run**: `/api/me` `venue` and the public `/api/venues` (owner `t01`, not the house,
   not a starter stall, `open` or `closing`).
2. **Opens it once** when we run none, `allow_venue_open = true` and `/api/clock` `t_hours` has reached
   `venue_open_after_game_hours` (6.5, about 11:30 Madrid, before the h7.0 Market Test at 12:00): a
   `board` venue, 0 bps + 0 P per card, named "Team 1 market". It is tick-driven: no wall clock. The opening
   goes through `guardrails.check()`: cash must stay at or above `cash_floor` (100) after the 250 P bond +
   20 P fee (on the cash our open offers do not already promise), never a second venue, never before that
   game hour. Before the request goes out, the shared Postgres must be able to hold the broker key, must
   show that no venue was ever opened on this target (a venue closed or suspended since is never reopened
   automatically: a human opens it by hand), and must grant this process the one opening claim (a deploy
   overlap or a laptop maker cannot open a second). Otherwise it waits `RETRY_TICKS` = 10 ticks. A refused
   opening costs nothing, gives the claim back and is retried 10 ticks later; `venue_exists` stops it.
   `/api/me` naming a venue next to `starter_broker_key` is the free stall, not ours (the kit's `me()`).
3. **Brokers its book every tick**: `GET /api/broker/book`, then the exact maximum-surplus matching (bench
   first, ties in book order like the stall, never two offers of one maker, never ours, never an order
   already matched), at most 15 sends a tick paced at 5 per second, each inside the maker's tick window.

**The bond reserve.** Until we run a venue, every purchase by every writer (taker, maker, duels, dealer,
MCP/runtime: all through `guardrails.check()`) keeps `cash_floor + venue_bond_reserve` = 370 P in cash;
once `/api/me` shows our venue the floor is 100.

**The broker key** comes back once, in the opening's answer. It is saved at once to the shared Postgres
table `venue_keys` (a redeploy or restart finds it there) and to `<data_dir>/broker.env` (0600), removed
from the answer before anything is logged, and kept in memory if both saves fail (the log then says
"NOWHERE"). It is never logged, printed, put in a decision or execution row, published on `/state` or
`/events` (broker and venue rows show only their kind and status there), or sent to any host but its own.
No public route reads `venue_keys`. A venue we run without its key logs "NO broker key" every 20 ticks:
ask the desk.

**Turn it off**: `allow_venue_open = false` in `GUARDRAILS.md` (redeploy) stops the opening and every
broker match; `uv run bazaar venue close <id> --live` closes it (the bond comes back after a cooldown; a
Market Test session counts the best venue open during it). The kill switch stops all of it.

**By hand** (laptop, dry run unless `--live`): `uv run bazaar venue status | open | close | fee | announce`
and `uv run bazaar broker run`. **Prove it on the simulator**: `uv run python scripts/sim_market_test.py`
(an in-process `bazaar_sim`, the maker live against it, our efficiency next to the stall's per session).

A real broker key (`bk_...`) is only sent to `https://bazaar.causaprima.ai`, a simulator key (`simbk-...`)
only to another host; against the simulator the real `BAZAAR_BROKER_KEY` is never loaded.

## Not public

- **Postgres** (`iriguchi.proxy.rlwy.net:28880`, db `railway`): the shared memory. Credentials only in
  the Railway dashboard; never in chat, git or a browser.
- **`bazaar-duels`**: the duel player, a background worker with no HTTP. Its traces are in Phoenix.
- **The monitor**: runs in the CLI on a laptop (`uv run bazaar monitor --notify`) by team decision.
