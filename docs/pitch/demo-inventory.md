<!-- Draft from the B29 demo inventory (read-only checks, Sat 3 Oct ~05:00). Credentials are kept out on purpose: Phoenix login is in Railway. -->
# Sunday 5-minute demo · asset inventory (read-only, Sat 2026-10-03 ~04:30)

Everything below was read or run read-only. Nothing was committed, no key was sent to
bazaar.causaprima.ai. Commands marked "ran" were run on this laptop and worked.

## 1. Explainer site (game-explainer-site)

- Worktree: `/Users/mariusserban/orca/workspaces/bazaar/game-explainer-site`, branch `game-explainer-site`.
- **Not pushed, no PR**: no upstream, 7 local commits ahead of `origin/main` (head `6801ec3`).
  `gh pr list --search explainer` finds nothing related. If the demo laptop is not this Mac, push first.
- Files: `site/index.html` (612 lines), `site/styles.css`, `site/js/{core,world,negotiation,market,system}.js`.
  **Fully offline**: no CDN, no web font, favicon is a data: URI. Works from `file://` too.
- Serve: `python3 -m http.server 8000 -d /Users/mariusserban/orca/workspaces/bazaar/game-explainer-site/site`
  then http://127.0.0.1:8000/ (or `open .../site/index.html`). Dark-mode toggle, chapters menu (TOC button), progress bar.
- Structure: hero ("A card market run by AI agents, explained") then
  - **Part 1 · How the game works**: `#rule` Words persuade, structure binds (words-vs-structure widget) ·
    `#world` tabs Cards/Pages/Primas/Dealers/Venues (sets explorer) · `#clock` heartbeat demo + weekend timeline ·
    `#value` card value playground · `#dealers` concession-curve shaper + **Haggle with a simulated Abuela** ·
    `#duels` split the pie (ZOPA), price + delivery days, **Play a duel against a bot** ·
    `#market` order book, best-prices-first counter-case, **Be the broker: mini Market Test** ·
    `#scoring` 100-point bar, you vs the top three · `#fairplay` Spot the trick quiz.
  - **Part 2 · What Team 1 built**: `#built` Five ideas + **clickable system diagram** (16 boxes + 2 ghosts: API, Monitor,
    Postgres+pgvector, Strategy, Jev, LLM words-only, Guardrails, Ledger, Taker, Maker, Duels, Dealer, Runtime+MCP,
    Phoenix, Evals, Railway; ghosts Venue/Learner) · `#tick` One tick, eight steps · `#guardrails`
    **Try to break the rules** checker + one-accept-per-tick race · `#shield` **A naive agent versus ours**
    (prompt-injection toggle) · `#status` status board · `#glossary`.
- Strongest 3 moments: (a) `#shield` naive vs ours toggle (the "hostile text in, nothing binding out" story);
  (b) `#built` click Jev → Guardrails → LLM boxes (the five ideas); (c) `#dealers` Haggle with Abuela or
  `#duels` play a duel (judges can touch the game in 20 s).
- **Stale**: `#status` and the diagram labels say taker/maker "Dry run", Jev/LLM/runtime "Being built"; they
  went LIVE Sat 01:45. Skip Chapter 14 or say "status as of Saturday morning".

## 2. Dashboard: what replaced PR #43

- #43 `feat/web-live` (Next.js dashboard) CLOSED Sat 00:54Z: repo is Python-only, 9 Greptile P1s, conflicts;
  "TypeScript UIs live in their own repo (bazaar-live) and read the public taker/maker /state and /events".
  The branch still exists.
- Replacements:
  1. **Bazaar Live (the show)**: https://bazaar-live-production.up.railway.app (repo
     claude-hackaton-madrid-team-1/bazaar-live; declared in bazaar PR #85, MERGED). `GET /health` (ran) →
     `{"ok":true,"service":"bazaar-live","tts":[]}`; `/?mock=1` → HTTP 200 (ran). Shows the BUYER (taker) and SELLER
     (maker) as animated characters at a Rastro stall, cork board of our offers, every public move acted out and
     spoken; Jev verdict meter, guardrail denials, LIVE/DRY badge. Params: `?mock=1` recorded afternoon,
     `?speed=2`, `?mode=dry`, `?tts=webspeech|off`; key **M** mutes; opens with a "Start the show with sound /
     Watch muted" gate (needs a click). Deployed = v1 + #2; RPG scene (#4) and real transcripts (#5) are OPEN.
     `tts: []` means browser Web Speech only (no ElevenLabs key on the service).
  2. **Public agent endpoints** (`src/bazaar_agent/agents/status.py`, allow-list `public_decision` /
     `public_execution` / `public_view`): https://bazaar-taker-production.up.railway.app/{health,state},
     https://bazaar-maker-production.up.railway.app/{health,state}, `wss://…/events` (last 200 then live).
     Ran `/health`: taker `mode: live`. Maker `/state` now: empty decisions (doors closed).
     **PR #121 (OPEN)** "public /state and /events must not reveal our limits": check the payload before
     projecting raw `/state` or `wscat`.
  3. `bazaar evals report --json` is documented as "the dashboard" data feed (docs/services.md "Evals scorecard").
  4. `bazaar cockpit` (below) is the operator's screen.
- Architecture page (`docs/architecture.html` on main) lists all live links incl. Bazaar Live.

## 3. Phoenix

- URL: https://phoenix-production-6aa3.up.railway.app (ran: HTTP 200). Project **`bazaar`**.
  the Railway dashboard (project `heartfelt-warmth`). Log in **before** the demo.
- Best traces (README "Observability"):
  - `duel` (AGENT) root per duel: role, limit, a `duel tick N` child per tick with rival offer, our move,
    `jev_verdict`, `jev_choice` (default, chosen, legal moves, why), guardrail, refusals. Annotation **`duel_pie_share`**.
  - `negotiation` (AGENT) root per dealer thread: `tick N` children with `message`, `dealer_offer`,
    `jev_verdict`, `guardrail`, `our_move`; root has full transcript. Annotation **`ladder_share`**.
  - `taker tick N` / `maker tick N` (deciding tick): annotation **`trade_surplus`**.
  - Desk (Claude Agent SDK runtime): `runtime.tool_call` and `runtime.tool.<name>` spans.
  - Also `duels tick N`, `monitor tick N`, `monitor stream`, `cli <command>`, `thread.view`.
- Evals annotate spans: annotator `CODE`, identifier `bazaar-evals:<subject>` (e.g. `duel:85`, `thread:101`),
  label good (≥0.6) / ok (≥0.3) / bad, score 0..1, explanation. Written by `bazaar evals run` (Phoenix on by default).
- Tip: a running `negotiation`/`duel` root lands only when it ends; while live, use the **Spans** tab.
  Pre-demo count by name: `uv run bazaar obs spans` (needs `PHOENIX_API_KEY`, `BAZAAR_TRACING` config).
  Pick 1 duel with a `good` duel_pie_share and 1 negotiation with ladder_share beforehand; keep tabs open.

## 4. Read-only CLI views

Run from `/Users/mariusserban/orca/workspaces/bazaar/night-b6-saturday-playbook` (branch `night/b29-pitch-kit`,
stacked on `night/b22-cockpit` #122 OPEN → `night/b6-saturday-playbook` #102 OPEN). **None of cockpit /
timeline / score-sim is in main yet.**

| Command | Ran? | What it shows | Network |
|---|---|---|---|
| `BAZAAR_SIM=1 uv run bazaar cockpit` | ran (sim) | 10 panels with ok/WARN/BAD: Clock, Next (playbook), Cash vs floor (`cash_floor` in GUARDRAILS.md) + headroom, Ledger, Agents (/health), Caps, Duels, Ladder, Market Test, Alerts | sim + Railway /health |
| `uv run bazaar cockpit --no-key` | not run | keyless reads only (clock, schedule, dealers, /health) | real game, keyless |
| `uv run bazaar cockpit --watch` | not run | refresh every 2 ticks mid-tick (~30 s on Sunday's 15 s ticks) | **real game + team key**: only for the live demo |
| `uv run bazaar timeline` | ran | every scheduled event in game hours and Madrid time (Duels I/II/III, Market Tests, rounds, Grand Final) | offline, but the fixture clock anchors to *now*: on Sunday the Madrid times shift a day |
| `uv run bazaar timeline --from-api` | ran (Sat 04:30: `resume: h2.65 = Sat 09:00`, resume/jump columns while closed) | same, live keyless `/api/clock` + `/api/schedule` | keyless GETs only: use this on Sunday |
| `uv run bazaar evals report` | **not run** (no `.env` in the b6 worktree, so no `DATABASE_URL`) | scorecard per target/day, dealer ladder best-3, worst 5, Jev calibration, annotations | Postgres only |
| `uv run bazaar evals score-sim` | ran, in `/Users/mariusserban/orca/workspaces/bazaar/night-w5w6-score-redteam-morning` (#78 OPEN) | board-formula model vs official: RMSE 0.34 over 38 snapshots, board MAE 0.47 over 18 teams; value of one more dealer deal; Saturday levers | **offline** (Friday fixture) |
| `BAZAAR_SIM=1 uv run bazaar status` | ran | target banner SIMULATOR, cash, level, cards, score breakdown, album | simulator |
| `uv run bazaar status` | not run | same on the real game | **real game + team key** |
| `BAZAAR_SIM=1 uv run bazaar agent taker --max-ticks 5` | not run | dry run, WOULD-moves only | simulator |
| `BAZAAR_SIM=1 uv run bazaar dealer buy LAV-03 --start 6 --max 10 --live` | not run | haggle with simulated Abuela (~4 ticks, 10 s each) | simulator, safe |

- Simulator: https://bazaar-sim-production-1d48.up.railway.app (ran `/api/health`: doors open, tick 849, 10 s ticks).
  `BAZAAR_SIM=1` = public sim (key `sim-team1`), `BAZAAR_SIM=local` = `SIM_TICK_SECONDS=2 SIM_DATABASE_URL=memory uv run bazaar-sim serve` on 127.0.0.1:8765. Every command prints `target: SIMULATOR …` first.
- Cockpit in sim mode shows **overall BAD** because the Ledger panel is "file ledger.jsonl (THIS machine only)";
  on the real game with Postgres it should be ok (unverified). Say so, or show it real.

## 5. Architecture boxes to point at

`docs/architecture.html` (on main, auto-refreshed): open it locally or on GitHub. Point at:
1. **JEV (decision / orchestrator)**: jev-1.13.0; negotiation_move, offer_is_worth_accepting, duel_move, maker prices; "undecided is never a yes".
2. **Guardrails / hard rules**: GUARDRAILS.md checked before every write; 1 accept per tick (Postgres ledger); prompt injection quoted.
3. **Claude Agent SDK runtime + LLM → Sonnet 5.5**: desk + 4 subagents, MCP tools + guardrail hook, bazaar-mcp (bearer, dry run), LLM writes words only.
4. **Observability + evals**: Phoenix every negotiation/duel/tick; outcomes → Postgres + Phoenix annotations; Bazaar Live deployed.

`docs/agent-harness.md` is the **dev** harness (`.ai/` source → `scripts/sync-ai-docs.sh` → AGENTS.md/CLAUDE.md/
`.claude/` skills, agents, commands; lifecycle `/spec → /plan → /build → /test → /review`, Honest Implementation
Report). One sentence max ("how we built it with Claude Code"); the runtime story is RUNTIME.md "The desk" +
`src/bazaar_agent/runtime/`.

## Demo assets table

| # | Asset | URL / command | What to show | Risk if offline | Fallback |
|---|---|---|---|---|---|
| 1 | Explainer site | `python3 -m http.server 8000 -d …/game-explainer-site/site` → http://127.0.0.1:8000/#shield | `#shield` naive vs ours; `#built` click Jev/Guardrails/LLM; `#dealers` haggle | none (static, no CDN); only on this Mac (unpushed) | `open …/site/index.html` (file://) |
| 2 | Bazaar Live show | https://bazaar-live-production.up.railway.app (live) | buyer/seller acting out public moves, Jev meter, guardrail denial | needs internet; quiet if agents idle; sound gate | `/?mock=1&speed=2` (recorded afternoon), `?tts=off` |
| 3 | Phoenix | https://phoenix-production-6aa3.up.railway.app, project `bazaar` | one `duel` root with `duel_pie_share`, one `negotiation` root with `ladder_share`, Jev verdict events | internet + login | screenshots taken beforehand; `uv run bazaar thread <id>` in terminal |
| 4 | Cockpit | `uv run bazaar cockpit` (real) or `BAZAAR_SIM=1 uv run bazaar cockpit` | one screen of gates: cash vs floor, ledger, agents live, duels, ladder | real needs key + internet; sim shows Ledger BAD | `--no-key`, `BAZAAR_SIM=1`, or `--json` saved earlier |
| 5 | Timeline | `uv run bazaar timeline --from-api` | the weekend in game hours ↔ Madrid time | keyless GETs need internet; the fixture default anchors to now (wrong day on Sunday) | `uv run bazaar timeline --compare docs/night/saturday-schedule.json`, or a saved `--json` |
| 6 | Score model | `uv run bazaar evals score-sim` (w5w6 worktree) | model vs official RMSE 0.34; what one more deal is worth | none (fixture) | screenshot |
| 7 | Evals report | `uv run bazaar evals report` | scorecard good/ok/bad, ladder best-3, Jev calibration | needs Postgres `DATABASE_URL` (no `.env` in the b6 worktree) | Phoenix annotations, docs/services.md example |
| 8 | Architecture page | `docs/architecture.html` (main) | 4 boxes above, status colours, live links | none (local file) | explainer `#built` diagram |
| 9 | Simulator | `BAZAAR_SIM=1 uv run bazaar status` / `dealer buy … --live` | safe live-looking play | sim on Railway | `BAZAAR_SIM=local` + `bazaar-sim serve` |

## Suggested 5-minute order

| Time | Beat | Asset |
|---|---|---|
| 0:00–0:40 | The game in one breath + "structure binds" | Explainer hero → `#rule` (or skip straight to `#built`) |
| 0:40–1:30 | Five ideas, click Jev → Guardrails → LLM words-only | Explainer `#built` diagram (or architecture.html boxes 1–4) |
| 1:30–2:10 | Hostile text in, nothing binding out | Explainer `#shield` toggle naive ↔ ours |
| 2:10–3:00 | It is live: the agents trading right now, voiced | Bazaar Live (fallback `?mock=1`) |
| 3:00–3:50 | Every decision is a trace and is graded | Phoenix: `duel` root + `duel_pie_share`, `negotiation` + `ladder_share` |
| 3:50–4:35 | The operator's screen + numbers | `bazaar cockpit` (second window, `--watch`), then `evals score-sim` or `evals report` |
| 4:35–5:00 | What's next / honest gaps | architecture.html Roadmap + "Not started" |

Pre-flight (Sunday 08:30): push explainer branch or demo from this Mac; log in to Phoenix and open 2 chosen
traces; open bazaar-live once and click the sound gate; confirm #121 status before showing raw `/state`;
have `DATABASE_URL` available for `evals report` (never paste it on screen); unmute laptop.
