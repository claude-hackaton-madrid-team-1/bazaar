# Live services · integration guide

Every URL a teammate, a dashboard or an agent needs, with the exact contract each one speaks.
The taker and maker status endpoints are public and read-only: they cannot trade or change a setting,
and filter out private state (see "Public by design"). The agent processes themselves can trade when enabled.
Phoenix requires its own login.
`bazaar-mcp` exposes our tools, so every call needs a bearer token (see below).

| Service | URL | What it is |
|---|---|---|
| **Taker** | https://bazaar-taker-production.up.railway.app · `wss://bazaar-taker-production.up.railway.app/events` | Autonomous buyer (`bazaar agent taker`): accepts cheap venue asks, negotiates with dealers |
| **Maker** | https://bazaar-maker-production.up.railway.app · `wss://bazaar-maker-production.up.railway.app/events` | Autonomous market maker (`bazaar agent maker`): posts, reprices and cancels asks and bids; runs our venue broker and an optional dealer sell desk |
| **Phoenix** | https://phoenix-production-6aa3.up.railway.app | Traces UI for every negotiation, duel, monitor tick and CLI line (project `bazaar`) |
| **bazaar-mcp** | https://bazaar-mcp-production.up.railway.app/mcp (`GET /health` public) | Team 1's runtime tools as a remote MCP server (Streamable HTTP) for teammates' Claude Code: **bearer token required**, writes default to dry run |
| **Simulator** | https://bazaar-sim-production-1d48.up.railway.app | A simulated Bazaar (`bazaar-sim`): the organiser API's routes and shapes, keys `sim-team1`…`sim-team8`, for testing agents and the dashboard while the game is closed |

These are the configured service addresses. Check each agent's
`GET /health` for its current `mode`, target and clock. `BAZAAR_LIVE=1` enables trading; otherwise an agent
is a dry run and publishes only tick events. Deployment configuration is in
[`.railway/railway.py`](../.railway/railway.py).

The maker's dealer sell desk can accept offers when `dealer_sell_enabled` is on; the checked-in
[guardrails](../GUARDRAILS.md) currently disable it. Those accepts use the same shared quota as duels
and the taker (`agents/dealer_sell_desk.py`).

To stop new writes, pause each service separately; pausing does not withdraw existing offers.
See [pause writes](operations.md#pause-writes) and [Railway operations](operations.md#production-on-railway).

## One key, staggered ticks (`BAZAAR_TICK_OFFSET_S`)

All our processes share the key's 5 req/s (bursts of 20). On Sat ticks 646-650 the taker, maker, duels and
MCP loops all read at the tick boundary and the server answered `429 rate_limited` (`tick 647 maker: read
refused rate_limited … nothing sent`; `tick 646: /api/duels refused rate_limited`, a lost duel tick). Each
tick loop (`ticks.run_per_tick`) therefore wakes `BAZAAR_TICK_OFFSET_S` seconds after each tick: a float,
default 0 (unset = everyone at the boundary), capped at 10 s and at 40 % of the tick so a 15 s Sunday tick
still keeps 9 s for its work. Not a number ≥ 0: the process stops at start and says so.

| Service | Offset (s) | Order |
|---|---|---|
| `bazaar-duels` | 0 | first: duels have deadlines |
| `bazaar-taker` | 2.5 | after the duels' reads |
| `bazaar-maker` | 5 | broker, listings and optional dealer sales |
| `bazaar-mcp` | 7.5 | request-driven tools; inert unless it runs a tick loop |

Set by hand (`railway variable set BAZAAR_TICK_OFFSET_S --service <svc>`), with the coordinator; the variable is
declared `preserve()` in `.railway/railway.py`, so an apply keeps it. `bazaar budget --stagger` models the burst
with these offsets (`rate_budget.PROPOSED_STAGGER`).

A `429` on `duel run`'s `/api/duels` read is sent once more after the server's wait (`retry_after` in the body,
else 1.2 s), only while at least 8 s of the tick's action budget is left after that wait; a second refusal loses
the tick as before. No other read re-sends a 429 (`sdk.TeamBazaar`). **Laptop CLI commands use the same key: run
one at a time.**

## Taker and maker: HTTP

Both services serve the same three routes (CORS `*`, `GET` only).

### `GET /health`

```json
{"ok": true, "agent": "taker", "mode": "dry",
 "target": {"mode": "real", "url": "https://bazaar.causaprima.ai"}, "ledger": "shared", "tick": null, "last_tick_at": null,
 "doors": "closed", "paused": true, "next_opens": "2026-10-03T09:00:00+02:00",
 "tick_seconds": 60.0, "server_tick": 159}
```

- `mode`: `dry` or `live`.
- `target`: where the agent's requests go: `{"mode": "real", "url": "https://bazaar.causaprima.ai"}`, or
  `{"mode": "simulator", ...}` when it runs with `BAZAAR_SIM=1` ([simulator setup](operations.md#simulator)).
- `ledger`: the guardrail ledger it counts on: `shared` (the team's Postgres), `down` (a live agent sends
  nothing until it answers, then resumes by itself), or `local file` (dry run or simulator only). No host.
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

Since N12 the taker may log a `dealer_skip` decision (it skipped a dealer under a learned blocker:
cooloff, hourly quota, sold out, locked). Like every row that was not sent, `/state` shows only its tick,
kind, status and card: never the dealer or the blocker. The taker also writes two private Postgres
tables: `learnings` (what the live-feed reader learned) and `feed_events` (the public feed window it
reads every tick, so the archive keeps growing while the laptop monitor sleeps). `bazaar learnings`
reads them.

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

- One JSON message per event, in the same envelope as the game feed that
  [bazaar-live](https://github.com/claude-hackaton-madrid-team-1/bazaar-live) relays to its game screens (agent, negotiations, album, market, debug): `{id, tick, t, type, scope, actor, agent, payload}`. Ids are negative and made up,
  so they never collide with game event ids.
- A client that joins late first receives the last **200** events, then everything new as it happens.
- Event types:

| `type` | When | `payload` |
|---|---|---|
| `agent.tick` | once per game tick the agent handles | `{mode}` |
| `agent.decision` | every move the agent proposes, cut down unless sent; unsent accepts are not published | a decision (below) |
| `agent.execution` | every request the agent actually sends to the game | `{agent, decision_id, tick, method, request, ok, error_code, created_id}` |

A decision, as published:

```json
{"agent": "taker", "decision_id": 4180, "tick": 155, "kind": "dealer_bid",
 "chosen": true, "status": "approved", "dry_run": false, "sent": "sending", "thread_id": 812,
 "guardrail": "allowed", "jev": null,
 "inputs": {"dealer": "abuela", "thread": 812, "item": "LAV-08", "her_ask": 30, "final": false},
 "move": {"kind": "bid", "price": 21}}
```

- `kind` is one of `accept_ask`, `accept_bid` (the taker sells a free copy into a standing bid: off by
  default, `--accept-bids`), `team_open` / `team_offer` / `team_walk` / `team_accept` (the taker's swap
  threads with other teams, N17: on since Sat 3 Oct by team decision with a Jev gate, `team_threads_enabled` in GUARDRAILS.md), `dealer_open`, `dealer_bid`, `dealer_accept`, `dealer_walk`, `post_ask`,
  `post_bid`, `cancel_ask`, `cancel_bid`, `hold_ask` / `hold_bid` and `reprice_ask` / `reprice_bid` (the
  maker's `reprice_or_hold` verdict), and `broker_match` / `venue_open` from our venue's broker (`agent:
  "broker"`, inside the maker's tick loop): a sent one shows only its kind and status, no inputs or move.
- `status` is `approved`, `rejected` or `expired`. `sent` and `dry_run` are only on a **sent** row (see below),
  where `sent` is `sending`; read the agent's `mode` (`dry` or `live`) from `agent.tick` or `/health`.
- `guardrail` is `allowed` on a sent row and `-` on every other: a denial would name the limit we hit.
- `jev` is always `null`: a Jev label (`quick_sale`) next to a listed price marks our walk-away price. The key
  stays so readers keep working; the verdicts are in the `decisions` table.
- A row is **sent** only when it is `approved`, `chosen` and from a live agent. Only a sent row shows `inputs`
  `dealer`, `thread`, `item`, `ref`, `card`, `rarity`, `side`, `venue`, `offer_id`, `maker`, `ask` / `her_ask`
  (the counterparty's price), `fee`, `final` and our own `price`, and the `move` (`{kind, price}`,
  `{accept, price}`, `{open_thread, topic}`, `{give, want, venue}`, `{cancel}`, `{hold}`). Nested values
  (`topic`, `give`, `want`) keep only card, pack and cash keys.
- Any other row is **not published at all**: `rejected`, `expired`, every dry-run row, an unsent accept (it would
  say the ask sat below our value), a `hold_*` row, and a maker `reprice_*` row (approved but not chosen: its
  price is the strategy's target). Even their kind and status would say which limit or quota bound us, so a dry
  agent publishes only `agent.tick`.
- `agent.execution.error_code` is `null` or `refused`: the game's codes (`insufficient_cash`, `persona_quota`,
  `rate_limited`) name our cash and quota, and stay in the `decisions` table.
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
there, create your own key in Phoenix (Settings → API Keys) and follow the [monitoring setup](operations.md#monitoring-learning-and-evals).

## bazaar-mcp: the runtime tools over MCP (not read-only: bearer token)

`POST /mcp`, MCP Streamable HTTP (stateless, JSON responses), `Authorization: Bearer <BAZAAR_MCP_TOKEN>`
on every request: missing or wrong → `401 {"error": "unauthorized"}`; more than 5 requests/s per token
(burst 20) → `429` with `Retry-After`; more than `mcp_calls_per_minute` (RUNTIME.md, 30) tool calls per
minute per token → an error result `rate limited: …`. `GET /health` → `{"ok": true, "server": "bazaar",
"tools": 21, "target": {"mode": "real", "url": "https://bazaar.causaprima.ai"}}` with no token (the
target is a mode and a public URL; nothing about the live/dry mode or the game state).

Tools: the 15 reads (`learnings`, `status`, `holdings`, `cards`, `clock`, `strategy`, `curves`, `tape`, `teams`, `book`,
`traders`, `alerts`, `rules`, `threads`, `thread`) and 6 writes (`dealer_buy`, `sell_list`, `sell_bid`, `sell_cancel`,
`duel_move`, `steer`). Each answer is one text block holding JSON. A write answers
`{"tool", "tick", "status": "approved"|"rejected"|"expired"|"done"|"failed"|"hold", "sent", "guardrail",
"request", "command", ...}`: `approved` + `sent: false` is a dry run (what WOULD be sent), the default
unless `BAZAAR_LIVE=1` is set on the service by hand. The guardrails run inside the server for every
write; every write call is a `decisions` row with agent `mcp`. Answers never carry a key, token,
password or URL. See the [desk and MCP setup](operations.md#models-and-the-desk).

**Human approval tools (HA2), for Omar's chat and the Bazaar Live Approvals screen only.** `approvals`
(read), `approve` and `revoke` (writes) exist only when the service has `BAZAAR_APPROVER_TOKEN`, and only on a
request that also sends `X-Approver-Token: <BAZAAR_APPROVER_TOKEN>`. Such a request sees ONLY these three tools (an
approver connection is its own MCP entry and never reads counterparty text); the bearer alone neither lists nor runs
them (`unknown tool`); a wrong or empty approver token is `403 {"error": "forbidden"}` and a WARN line, never a
lockout. An approver request has its own rate buckets (5 req/s burst 20, 30 calls/min), so no bearer holder can
drain the human's budget. No agent tool set has them (`runtime/human_tools.py`). `approve`
`{card, side: buy|sell, price: 1..1000, ttl_ticks: 1..480 (240), reason?, via?}` answers `{"status": "approved",
"max_price"|"min_price", "until_tick", "by": "human:<via|mcp>"}` or `{"status": "refused", "reasons": [...]}` when an
approval could only loosen a hard cap (rarity cap, hourly spend, official value), sell a page's last copy or sell
below our value. A sell approval also releases a sale `max_score_loss_per_move` holds. `revoke` `{card, side,
reason?, via?}` answers `revoked`, or `denied` (there was none); either way the request reads denied. A revoke
never waits for a game read (it works with the clock unreadable, its row then has no tick), and an approve of the
same card and side still checking when it comes in is refused ("approve again"). `approvals` lists the requests of the last 2 game hours (state, why, our and official value, album
impact, the cap, who asked) and the active approvals. At most 10 approval writes a minute; each is a `decisions` row
(agent `guard`, kind `approval_granted|refused|revoked|denied`). Spec: `.ai/specs/HA2-spec.md`.

`status`, `holdings` and `strategy` (and every write's album-first read) answer from the shared Postgres
snapshot of `/api/me` while it is provably current, else from `/api/me` itself ([shared state](operations.md#shared-database)). Each
answer carries where it came from:

```json
"holdings": {"source": "db", "tick": 812, "age_s": 0.4, "epoch": 57, "digest": "3f9c0a1b2c3d4e5f",
             "read_by": "taker", "why": "fresh"}
```

`source: "live"` with `why` (`no snapshot this tick`, `a write of ours since it was read`, `a thread message
of ours this tick`, `older than 5 s`, `the tick is about to end`, `stored row does not match itself`, `postgres
busy or not connected`, `postgres error`, ...) means the server read `/api/me` itself.
`holdings` answers `{team, cash, level, affinity, pages, missing: {rows: [{set, ref, name, rarity,
value_to_us}]}, duplicates: {ref: [asset ids]}, packs: [{asset, pack}], cards: {rows: [{asset, ref, set,
rarity, serial, your_value}]}, holdings}`. `cards` (`set`, `rarity`, `ref` filters) answers `{source: "db" |
"live", rows: [{ref, set, set_name, name, rarity, book, print_run, minted, released, page, hidden,
updated_tick}]}`.

## Holdings and catalog tables (Postgres)

`me_snapshots` (key `(world, team, tick)`; `world` is `real` or `sim:<host:port>`): `epoch`, `digest` (etag of cash, level, assets and album counts),
`read_at`, `read_by` (taker | maker | mcp | cli | runtime), `cash`, `level`, `cards`, `duplicates`, `packs`,
`pages`, `affinity`, `score`, `me` (the whole `/api/me` payload). A newer epoch, or the same epoch read
later, wins; a row never moves backwards. `holdings_state` (one row per world, key `scope`): `epoch`, `written_at`,
`thread_message_at`, `last_write`, `last_writer`. `cards` (key `id`, the card ref): `set_code`,
`set_name`, `name`, `rarity`, `book`, `print_run`, `minted`, `released`, `page`, `hidden`, `updated_tick`.
The evals' `snapshots` (one row per tick) follows the winning `me_snapshots` row of the real game (or of a
simulator in its own database).

## Other teams' multipliers (Postgres, AF1)

`team_affinity` (key `(team, set_code, source)`): `multiplier`, `source` (`said` | `inferred`), `confidence`,
`tick`, `thread_id`, `quote`, `updated_at`. **said**: what a team wrote in a team thread, parsed from untrusted
words (`team_affinity.parse`; confidence 0.5, 0.25 with an injection shape); `quote` is their message, scrubbed and
cut to 200 characters. Words may lie: nothing reads these rows back into a decision. **inferred**: one consistent
assignment per team from the rival affinity map, with each set's probability, every 10 ticks (teams with no signal
have no rows). The taker's team desk writes both off the tick, and asks each team once per round (`/api/clock` `round`) in its
first message of a team thread: "Por cierto, ¿qué barrio es vuestro ×1,6? / By the way, which set is your ×1.6?".
`team_affinity_board` puts said beside inferred per team and set (DataGrip; bazaar-live's game screens read it
through a `show.game_*` view behind `GAME_VIEW_TOKEN`). CLI, read-only: `uv run bazaar affinity --teams [--json]`.

## Rival board (Postgres, RV1)

`rival_board` (a view, one row per OTHER team; `sql/schema.sql`): `team, tick, rank, score, negotiating, market, level,
pages, deals, venue, rank_change, score_change, trend_ticks, trend, our_team, our_rank, our_score, our_negotiating,
our_market, our_pages, dealer_deals, venue_trades, top_set, set_interest, strengths, weaknesses, they_want, they_have,
we_have_for_them, they_have_for_us, match_count, guarded, guard_reason, move_kind, move_give, move_get, move_price,
our_gain, their_gain, suggested_move, why_climbed, why_climbed_tick`, in that order: bazaar-live's
`db/rival_board.sql` passes exactly these through `show.rival_board`, so a new column goes last and a changed type
needs both repos. Gains are estimates (their side at book × the top multiplier, our fee on bids and asks we take); a
team in the top 5, near us (3 ranks) or above us is guarded: a move only when our gain is at least twice theirs.
Read-only (DataGrip `bazaar_team_ro`); private (our spares and moves), so bazaar-live serves it only behind
`GAME_VIEW_TOKEN`. `init_schema` replaces it only when `board_version` (its comment) is newer, with a 2 s lock wait,
and a failure only logs `schema: rival_board vN not applied (...)`. Any function the view calls runs as the caller
(`bazaar_live_reader` holds no table grant): keep it plain SQL.

## Evals scorecard (Postgres)

The [evals](operations.md#monitoring-learning-and-evals) write one `outcomes` row per settled duel, dealer thread, team trade or
Market Test and keep three views current. They run inside the agents, every 6 ticks: the duel player
scores duels, the taker the ladder and trades, the maker the Market Test. A dashboard reads them with
plain SQL, or runs `uv run bazaar evals report --json`. Scores are 0..1; labels `good` (≥ 0.6) · `ok` (≥ 0.3) · `bad`.

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
maker's and MCP server's `/health` carry `target: {mode: real|simulator, url}`. See [simulator setup](operations.md#simulator).

## Bazaar Live (separate repository)

`bazaar-live` (repo [bazaar-live](https://github.com/claude-hackaton-madrid-team-1/bazaar-live)): the
buyer and the seller at a Rastro stall, acting out and voicing every public move. Its public URL is the
Railway-generated domain of service `bazaar-live` (generated once by hand; listed in its README).

- The public show reads the taker's and maker's public `/health`, `/state` and `WS /events` above, from
  the browser, and keeps only the public fields; it sends nothing to the agents or the game and holds no
  team key. `?mock=1` plays recorded fixtures when the doors are closed.
- Its own server answers `GET /health` (`{ok, service, tts}`), `GET /api/tts/providers` and
  `POST /api/tts`: a proxy to ElevenLabs / Gemini TTS with the keys server-side (`ELEVENLABS_API_KEY`,
  `GEMINI_API_KEY`, both optional). It speaks only the show's own template lines, for its own page
  (`Origin`), under per-address and global rate limits and a daily character budget.

The same service also hosts private game screens and a human approvals screen. Its server keeps the
team key and MCP credentials out of the browser: game views use `GAME_VIEW_TOKEN`, and approvals use a
separate login plus the MCP approver credentials. See the
[bazaar-live repository](https://github.com/claude-hackaton-madrid-team-1/bazaar-live) for that application's
routes and access controls. The environment contract is declared in `.railway/railway.py`.

## Our venue and broker

The checked-in `allow_venue_open` policy enables venue management. The maker uses exact matching
by default; `BAZAAR_BENCH_POLICY=edge` selects the optional edge broker. Check the current venue and
scores with `bazaar venue status` and `bazaar evals report`.
See [the scoring model](briefing.md) for how market-making contributes to the game.

Our board venue runs inside the **maker** on Railway (`bazaar-maker`, no new service). Every maker tick,
before its own offers, `agents/venue_keeper.py`:

1. **Finds the venue we run**: `/api/me` `venue` and the public `/api/venues` (owner `t01`, not the house,
   not a starter stall, `open` or `closing`).
2. **Opens it once** when we run none, `allow_venue_open = true` and `/api/clock` `t_hours` has reached
   `venue_open_after_game_hours` (GUARDRAILS.md; the hour team venues start trading): a
   `board` venue, 0 bps + 0 P per card, named "Team 1 market". It is tick-driven: no wall clock. The opening
   goes through `guardrails.check()`: cash must stay at or above `cash_floor` after the 250 P bond +
   20 P fee (on the cash our open offers do not already promise), never a second venue, never before that
   game hour. Before the request goes out, the shared Postgres must be able to hold the broker key, must
   show that no venue was ever opened on this target (a venue closed or suspended since is never reopened
   automatically: a human opens it by hand), and must grant this process the one opening claim (a deploy
   overlap or a laptop maker cannot open a second). Otherwise it waits `RETRY_TICKS` = 10 ticks. A refused
   opening costs nothing, gives the claim back (even inside the Postgres backoff) and is retried 10 ticks
   later; `venue_exists` stops it.
   `/api/me` naming a venue next to `starter_broker_key` is the free stall, not ours (the kit's `me()`); a
   stored /me (`holdings.without_secrets`) keeps `has_starter_stall: true` in the key's place. The claim
   row holds a random per-process owner id (never a key): a process renews and gives back only its own.
   A 408, a 5xx or no answer may have opened the venue: the claim is kept until the lists say it did not.
   A refused opening is never published on `/state` or `/events`.
3. **Brokers its book every tick**: `GET /api/broker/book`, then the exact maximum-surplus matching (bench
   first, ties in book order like the stall, never two offers of one maker, never ours, never an order
   already matched), at most 15 sends a tick paced at 5 per second, each inside the maker's tick window.

**The bond reserve.** While `allow_venue_open` is true and we run no venue yet, every purchase by every writer
(taker, maker, duels, dealer, MCP/runtime: all through `guardrails.check()`) keeps `cash_floor +
venue_bond_reserve` (GUARDRAILS.md; bond 250 + opening fee 20) in cash. Once our venue is open, the floor is
`cash_floor` alone; the other limits still bind every buy (`max_spend_per_game_hour`, the official-value cap
`official_value_margin`, human approval above `human_approval_above`).

**The broker key** comes back once, in the opening's answer. It is saved at once to the shared Postgres
table `venue_broker_keys` (a redeploy or restart finds it there) and to `<data_dir>/broker.env` (0600), removed
from the answer before anything is logged, and kept in memory if both saves fail (the log then says
"NOWHERE"). It is never logged, printed, put in a decision or execution row, published on `/state` or
`/events` (broker and venue rows show only their kind and status there), or sent to any host but its own.
No public route reads `venue_broker_keys`. A venue we run without its key logs "NO broker key" every 20 ticks:
ask the desk.

**Once, for the whole game.** The automatic opening happens once per target: a venue closed or suspended
later (by hand, by the organisers, or between days) is reopened only by hand (`bazaar venue open --live`).
The once-only claim lives in the database the maker writes to, so run the LIVE maker only on Railway (a
laptop maker on the local default database does not share it). If the maker logs "we run a venue but
/api/me does not show it as ours", `/me` still carries `starter_broker_key` after our opening: set
`venue_bond_reserve = 0` so purchases stop keeping the reserve.

**Turn it off**: `allow_venue_open = false` in `GUARDRAILS.md` (redeploy) stops the opening and every
broker match; `uv run bazaar venue close <id> --live` closes it (the bond comes back after a cooldown; a
Market Test session counts the best venue open during it, and a session with no venue open counts 0, per
RULES.md). The kill switch stops all of it.

**By hand** (laptop, dry run unless `--live`): `uv run bazaar venue status | open | close | fee | announce`
and `uv run bazaar broker run`. **Prove it on the simulator**: `uv run python scripts/sim_market_test.py`
(an in-process `bazaar_sim`, the maker live against it, our efficiency next to the stall's per session).

A real broker key (`bk_...`) is only sent to `https://bazaar.causaprima.ai`, a simulator key (`simbk-...`)
only to another host; against the simulator the real `BAZAAR_BROKER_KEY` is never loaded.

## Not public

- **Postgres** (`iriguchi.proxy.rlwy.net:28880`, db `railway`): the shared memory. Credentials only in
  the Railway dashboard; never in chat, git or a browser.
- **Read-only login for teammates** (`bazaar_team_ro`, for DataGrip or psql): SELECT on every table and
  sequence in `public` (tables created later included) except `venue_broker_keys` (our broker key), no write
  privilege, sessions read-only by default, 30 s statement timeout, idle sessions closed (60 s in a transaction,
  10 min otherwise), at most 10 connections. The coordinator creates or rotates it with the admin
  `DATABASE_URL`: `uv run bazaar db readonly-user` (generates a password) or `... --password-stdin` (prompt, or
  one piped line). It prints `postgresql://bazaar_team_ro:<password>@<host>:<port>/railway?sslmode=require`
  once; the password reaches the server only as a SCRAM hash, and re-running rotates it. In DataGrip: New →
  Data Source → PostgreSQL, paste the URL, SSL required, auto-commit on. Ask the coordinator for it privately,
  never in chat or git. Limits: the role is cluster-wide and PUBLIC still lets it connect to the server's other
  databases (`bazaar_sim`, `postgres`) and create temp tables; a session may turn its read-only default and
  timeouts off (the privileges still refuse every write); and PUBLIC's `pg_advisory_lock` lets it take our
  writers' lock keys, which would stall accepts (they fail closed). Hand the URL only to teammates. Any new
  table or view that exposes a secret (default privileges grant SELECT on it) must be added to
  `readonly_user.SECRET_TABLES_DDL` and revoked in `sql/readonly_user.sql`, then the command re-run.
- **`bazaar-duels`**: the duel player, a background worker with no HTTP. Its traces are in Phoenix.
- **The monitor**: runs in the CLI on a laptop (`uv run bazaar monitor --notify`) by team decision.
