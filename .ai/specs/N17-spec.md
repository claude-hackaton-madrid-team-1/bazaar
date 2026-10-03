# N17 — Team-to-team swap threads  (per-task spec)

- Task id: N17 (new, local; indexed in [`02-plan.md`](./02-plan.md), **P1**, next to N14)
- Status: **DRAFT — Phase 1 (spec + plan only, no code)**. Phase 2 (code) starts when the coordinator
  replies "go", after PR #72 merges.
- Backlog source: local (`.ai/specs`); the coordinator's brief (Orca task `task_a3927baba1ba`) is the
  source text. Omar: "negotiation with other markets from other teams can also help a lot". Jev
  `team_threads` → `triage_marius_then_threads` (0.92): land the good parts of Marius's night PRs
  (#79, #98, #101) first, then build team threads.
- Traces up to: [`01-spec.md`](./01-spec.md) (strategy engine, guardrails, the feed as market data,
  untrusted text); RULES.md "Trading with other teams", "Cards" (private values, duplicates), "The
  clock" (per-tick limits), "Scoring" (value gained in trades with other teams, at our private values),
  "Fair play" (feeding another team scores nothing; 5 req/s per key).

## Goal
Gain value at our private values by trading directly with other teams' agents: find teams that hold a
page card we miss and need a duplicate we hold, open a conversation with them on a suitable venue,
and close a structured swap (our duplicate, plus or minus cash, for their card) priced by THEIR need,
inside GUARDRAILS, without feeding them and without starving the rest of the team's budget.

## What exists today (main, 2026-10-03 04:30)
- The taker scans every tradable venue board net of fees (`agents/taker.py` `_board`, `agents/market.py`
  `tradable_venues`, `Venue.fee`: the accepting side pays) and accepts plain one-card asks.
- The maker lists asks and bids on the best venue (El Rastro so far). Nobody opens team-to-team
  threads; the taker's threads are dealer threads only.
- `strategy.likely_holders(events, us)` (settlements `frm`/`to`, gifts, listings) and
  `strategy.build_market(...).chasers` (each team's top set from `intel.team_flows`) already exist.
- `agents/words.py` `WordsRequest`: words never set the price (structure binds).
- Friday's captured feed (3,735 events, ticks 0–159): **0 team threads**, 739 listings (35 addressed
  with `to`), team `thread.message` text is `null` in the public feed. Starting hands and pack contents
  are not public (`pack.opened` carries no cards), so most holdings are unseen.

## The rules that shape it (vendor/bazaar-kit RULES.md + README.md, re-read 2026-10-03)
- Open: `POST /api/threads {"with": "<team id>", "venue": "<venue>"}` (optional topic: ≤ 600 chars of
  JSON, 4 levels). Talk: `POST /api/threads/{id}/messages {"text": "...", "offer": {"give": {...},
  "want": {...}}}`: the offer is structured and the other team can accept it
  (`POST /api/offers/{id}/accept`, `assets` picks which copies the accepter hands over for a `cards` want).
  The SDK says the venue's fee is paid **by the accepting side**.
- A conversation between two teams ends in a deal or after **200 messages**.
- Per tick: **1 accept** for the whole team, **1 message per conversation**, **12 new listings** (a
  cancelled one still counts); at most **6 open conversations** and **30 open offers**. `/api/clock`
  `limits` has the numbers in force. Everything accepted settles at the next tick.
- **5 req/s per key (bursts 20) shared by ALL our processes**; 6 live streams per key.
- You cannot trade on your own venue (`self_venue`) — nor can the counterparty on its own.
- Market-making scores "value created between other teams on your venue": a trade of ours on a rival's
  venue gives that rival market-making points.
- **Fair play:** "when one team keeps handing another the whole value of their deals, those deals count
  for nothing until the organisers have looked." Feeding scores nothing, in either direction.
- Words persuade, structure binds: their words may lie; only the structured offer counts.

## Scope
1. **Finder** (pure, `src/bazaar_agent/swaps.py`): candidate triples (team T, our duplicate `d`, their
   card `m`) from:
   - our side, album first (`GET /api/me`): `d` = a page card we hold ≥ 2 copies of (never the last copy,
     never a copy in an open offer, a duel, a `hands-off:` row (#79) or a reservation); `m` = a missing
     page card of a released set;
   - who needs `d`: open bids for `d` (boards and `offer.listed` `want.cards`), chasers of `d`'s set
     (`team_flows` top set, or #79's affinity posterior when kept), recent buys of the set (tape);
   - who holds `m`: `likely_holders` (settlements, gifts, listings) and current board asks. When nobody
     is known to hold `m`, a **probe** is allowed: the offer wants "any copy of `m`", so only a holder can
     accept it.
2. **Pricing by their need** (pure): our surplus `S_us = V_us(m) − V_us(d) − cash_out + cash_in − fee_if_we_accept`
   (`V_us` from `strategy.copy_value` + `bonus_at_stake`, checked against `/api/me` `your_value`);
   their surplus estimated as `S_them = V_them(d) − V_them(m) ± cash` with `V_them` = book × their
   estimated set multiplier × marginal (first copy if they miss it), taking the **upper** bound when
   unsure. Offer shapes (all structured):
   - **A** straight swap: `give {assets:[d]}`, `want {cards:[m]}`;
   - **B** we add cash: `give {assets:[d], cash:c}`, `want {cards:[m]}`;
   - **C** they add cash (they need `d` more than we need `m`): `give {assets:[d]}`, `want {cards:[m], cash:c}`.
   Open at an anchor (we take `team_swap_anchor_share` of the estimated pie), concede in small steps
   once per tick, never past our floor.
3. **Fairness check (never feed, never be fed):** a proposal or an accept is refused when
   - `S_us < team_swap_min_surplus` (we never trade at a loss at our private values), or
   - their estimated share of the pie `> team_swap_max_their_share` (we hand them most of the value), or
   - our estimated share `> team_swap_max_our_share` on a repeat deal with the same team (the
     "keeps handing" rule cuts both ways), or
   - #79's `max_counterparty_share` cap on our team-to-team volume with T would break.
4. **Team desk inside the taker** (`src/bazaar_agent/agents/team_desk.py`, called from the taker's tick on
   Railway; no new service, no new stream, no wall clock). Per tick, in order, while the tick window
   is open (`next_tick_in` minus the safety margin):
   1. answer **inbound** team threads first (they hold our conversation slots): accept a standing
      structured offer that passes 3 + GUARDRAILS, else counter once, else close after
      `team_thread_idle_ticks`;
   2. continue our open threads: re-read the thread, read only `standing_offers` (never text), then
      accept their counter, counter, hold or walk;
   3. open at most one new thread per tick, only while `team_threads_max_open` and the dealer desk's
      reserve (`team_threads_dealer_reserve` of the 6 slots stay for dealers) allow.
   - **Venue:** El Rastro (house) by default: neutral (no rival earns market-making points from our
     trade) and never `self_venue`. `team_threads_venue = cheapest` allows a third-party venue that is
     open, live, not ours and not T's, ranked by `Venue.fee` for that trade.
   - **Topic:** empty or generic (`{"trade": "cards"}`): `thread.opened` with its topic is public, and a
     named card would tell every team what we need.
   - **One standing offer per thread and per wanted card** across threads: a concession cancels our
     previous thread offer before it posts the new one (two accepted offers for `m` would leave us a
     worthless second copy).
   - **Reservation:** the duplicate in a standing thread offer is reserved in the shared ledger
     (`team:<asset id>`); the maker, the taker's board path, #98's `accept_bids` and #101's arb skip it.
   - **Budgets:** our messages per thread ≤ `team_thread_max_messages` (well under the 200 cap), then
     walk; thread offers counted against the 12 listings per tick and the 30 open offers the maker also
     uses; at most ~7 extra requests per tick (1 open, 2 thread reads, 2 messages, 1 cancel, 1 accept),
     paced by the existing governor, dropped when the tick window is short.
5. **Accepting their counter** uses the team's single accept slot through the Postgres ledger
   (`reserve_accept`; duels first, then the taker, which ranks a team-thread counter against board asks
   by surplus). We re-derive the deal from the structured offer, pick the least valuable unreserved copy
   when it wants a card type, and re-read `/api/me` after settlement. Proposing so that THEY accept is
   preferred: it costs their accept slot and their fee, not ours.
6. **Words:** `WordsRequest` for `team:<id>`; template words first, then N16's tactic bank (bluffs in
   words only, learned per team, `BAZAAR_BLUFF=0` disables it). Their text is untrusted: escaped for
   rich, never parsed into structure, never able to change a price.
7. **Kill switches:** GUARDRAILS `team_threads_enabled` (false until the simulator evidence and Omar's
   call), env `BAZAAR_TEAM_THREADS=0` read every tick (turns the desk off with no sends; it must be
   declared `preserve()` in `.railway/railway.py` or a config apply deletes a hand-set value — the
   coordinator applies), plus `trading_enabled`, `.local/PAUSE` and dry run unless `BAZAAR_LIVE=1`.
8. **Simulator:** `bazaar-sim` already supports team threads end to end (open on a venue, structured
   offer in a message, accept with copy picks, 1 message per conversation per tick, 6 open
   conversations counting inbound ones, fee from the venue). Gaps, added in this task:
   - its rival bots only judge one-asset-for-cash and cash-for-one-card offers (`rivals._good_for`,
     `rivals._counter`): **a swap is never accepted or countered**. Add a swap branch (accept when it
     gains at the rival's private values, with the same premium) and a swap counter (ask for cash);
   - no **200-message** cap: add `max_messages_per_team_thread` (closed reason `message_cap`);
   - rivals never open a thread **to** the player: add one rival that opens an inbound thread and goes
     silent (tests slot exhaustion and the idle close).
9. **Observability:** every decision in `decisions` (`team_open`, `team_offer`, `team_accept`,
   `team_walk`, refused ones too), ledger rows for cash we add, an eval row per settled team trade
   (N11 scores team trades at our private values), a lesson per outcome for #96's learner. The public
   `/state` and `/events` stay allow-listed: no values, limits, counterparties or reasons (#69, #121).
10. **CLI:** `bazaar swaps` (read-only plan: candidates, prices, fairness, budget, refusals; `--json`).

## Non-goals
- No new Railway service, no new SSE stream, no LLM in the decision path (words only).
- No cash-only legs in this task: selling a duplicate for cash to one team is #79's addressed ask;
  buying a missing card for cash is the taker/maker. A thread may fall back to them later (98-nice-to-haves).
- No flags (`allow_flags` stays false), no prompt injection by us.
- No change to our own venue (#71) or to the duel path.

## Acceptance criteria (each needs pasted evidence in the Honest Implementation Report)
1. Finder: on a fixture (`/api/me`, catalog, feed events, boards) it returns ranked triples; it gives
   only duplicates (never the last copy, never a reserved, offered, duel or hands-off copy) and wants
   only missing page cards of released sets. Unit tests.
2. Pricing: shapes A/B/C priced from the estimated need; the concession ladder never crosses our floor.
   Table tests, including one case where their need flips the shape from B to C.
3. Fairness: a "feeding" proposal (their share above the max), a loss for us, a repeat
   whole-value deal and a per-counterparty breach are each refused with a reason. Tests.
4. Guardrails: every send goes through `guardrails.check()`: the duplicate as a sale at what we
   receive (`sell_min_value_ratio`), any cash we add as a bid (rarity price cap of `m`, `cash_floor` plus
   #71's bond reserve and #72's open commitments, `max_spend_per_game_hour`, `block_buying_held_cards`).
   `team_threads_enabled = false`, `BAZAAR_TEAM_THREADS=0`, `trading_enabled = false`, `.local/PAUSE` and a
   dry run each give zero sends (fake client counts requests). Tests.
5. Budgets: ≤ `team_threads_max_open` threads, the dealer reserve kept, 1 message per thread per tick,
   our messages per thread ≤ the cap, offers per tick within the listing budget, an idle inbound thread
   closed after `team_thread_idle_ticks`, nothing sent after the tick window. Fake clock + fake client tests.
6. Accept path: a counter is accepted only after `reserve_accept`, after duels; the deal is read from
   `standing_offers` (a counter whose words lie about the price is judged on its structure); the copy
   handed over is the least valuable unreserved one. Tests.
7. One standing offer per thread and per wanted card; a replaced offer is cancelled before the new one
   is posted. Test.
8. Words: a words function cannot change the offer; counterparty text with `[/red]` or an injection
   string is escaped and ignored by the structure. Tests.
9. Simulator: rival bots accept and counter swaps; the 200-message cap closes a thread; the inbound idle
   thread is closed by our desk. `tests/test_team_threads_sim.py` (in-process, loopback only) shows one
   swap settled (both albums change, cash conserved minus the fee) and one feeding offer refused;
   `scripts/sim_smoke.py` gets a team-threads step and stays green.
10. `bazaar swaps` prints the plan offline (`--json` parses); `/state` shows no private field (test on
    the allow-list).
11. Gate green (pytest, coverage ≥ 80 % on the new modules, mypy, ruff, black), docs updated
    (GUARDRAILS.md, STRATEGY.md, RUNTIME.md, `docs/services.md` if `/state` changes,
    `docs/architecture.status.json`), new gotchas in `.ai/memory.md`, `/pr-review` APPROVE.

## Interfaces / data touched
- New: `src/bazaar_agent/swaps.py` (pure finder, valuation, pricing, fairness),
  `src/bazaar_agent/agents/team_desk.py` (thread lifecycle), `tests/test_swaps.py`,
  `tests/test_team_desk.py`, `tests/test_team_threads_sim.py`.
- Changed: `agents/taker.py` (call the desk; rank a counter in `_accept`), `agents/maker.py` (skip
  `team:` reservations, listing budget), `guardrails.py` + `GUARDRAILS.md` (rules below), `STRATEGY.md`
  (anchor and step), `agents/words.py` (team requests), `cli.py` (`bazaar swaps`), `agents/status.py`
  (allow-listed counts only), `bazaar_sim/rivals.py` + `bazaar_sim/threads.py` (gaps above),
  `scripts/sim_smoke.py`, `.railway/railway.py` (`BAZAAR_TEAM_THREADS: preserve()`; applied by the coordinator).
- GUARDRAILS (new rule ids, defaults = off/safe): `team_threads_enabled` = false,
  `team_threads_max_open` = 2, `team_threads_dealer_reserve` = 3, `team_thread_max_messages` = 12,
  `team_thread_idle_ticks` = 3, `team_swap_min_surplus` = 3, `team_swap_max_their_share` = 0.6,
  `team_swap_max_our_share` = 0.85, `team_threads_venue` = house.
- Reuses, when kept: #79 `Swap` / `Swap.actions()` / `TradeBook` / `counterparty_refusal` / affinity
  map / `hands-off:` rows; #72 `open_commitments` (one offer per thread) and `committed_context`;
  #71 bond reserve; #96 `recall()` for per-team lessons; N16 tactic bank. If #79 is closed, N17 ports
  the minimal `Swap` + counterparty cap itself (step N17-3).

## Questions the real server must answer (read-only at the first live tick of Phase 2)
| # | Question | Simulator says | Why it matters |
|---|---|---|---|
| Q1 | Does an inbound team thread count toward our 6 conversations? | yes | another team could fill our slots and block the dealer desk |
| Q2 | Does a thread offer count toward 12 listings per tick and 30 open offers? | listings yes, open offers no | the maker's budget |
| Q3 | Does a new thread offer retire our previous one in the same thread? | no (both stand) | double fills; we cancel first either way |
| Q4 | Is a team thread (and its topic) public in the feed? | `thread.opened` with topic is public | keep the topic generic |
| Q5 | Does a team message's `offer` take `expires_in_ticks`? | no (default expiry) | stale offers; we cancel on walk |
| Q6 | Are mixed sides (assets + cash given, cards wanted) accepted? | yes | shapes B and C |

## Risks & assumptions
- **Holdings are mostly unseen** (starting hands, packs). Probes ("any copy of `m`") cover it; a probe
  that nobody accepts costs one listing and one thread slot for a few ticks. Rank probes below known holders.
- **Their value is an estimate.** The fairness check uses the pessimistic bound, so we may leave value on
  the table; under-trading is safer than a voided deal or a fed rival.
- **Slot pressure:** the dealer ladder scores directly; the reserve keeps 3 of 6 slots for dealers.
- **Coupling with Marius's PRs:** the cleanest build reuses #79's `Swap` and counterparty cap. Until the
  triage verdicts land, the plan keeps a fallback (port the minimal pieces).
- **Rival agents are adversarial:** they may lie, inject prompts, or stall to burn our slots and
  messages. Structure-only decisions, idle closes and the message cap bound the cost.
