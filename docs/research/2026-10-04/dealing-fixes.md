# Sunday dealing fixes (branch `fix/sunday-dealing`)

Three dealing defects from the Saturday review (`dealing.md`, `quiet.md`, README "Live code defects" rows 2 and 5),
fixed as code on a local branch for Sunday 4 Oct. Session `dealing-fixes`, written Sun 4 Oct ~09:30 Madrid. Nothing here
touched the live game: tests, fixtures, a local simulator on 127.0.0.1, and read-only SQL on the shared Postgres.

Branch, on top of `origin/main` 25b69c56 (rebased; main moved by docs only):

| Commit | What | Redeploys on merge |
|---|---|---|
| a4419cd3 | Defect 1: read and decide offers other teams address to us | all three (`src/**`) |
| 4421c551 | Defect 2: ladder probes plan the empty slots of the round, and say why a level stays empty | all three |
| 6e52cfdf | Defect 3: new rule `dealer_ladder_value_tolerance`, **default 0 = today's cap** (separate commit) | all three (`GUARDRAILS.md`) |
| 296c9698 | Slot count restarts at `round.started` as well as `day.opened` (self-review fix) | all three |
| 5df8f96e | GUARDRAILS wording: the tolerance's real reach and cost | all three |

## TL;DR

1. **Addressed offers are read now, at zero extra requests.** `GET /api/me/offers` is already read every tick
   (`agents/runtime.py:347`) and returns "open offers addressed to you". The taker now feeds them through the same
   path as a board offer, and every one leaves a decision row. Asks are on by default. Selling into addressed bids
   waits for `BAZAAR_ADDRESSED_OFFERS=all`.
2. **Why the ladder slots stayed empty: Pilar (L3) and Banco (L5) sell us no card**, and the taker only buys from
   dealers. Pilar sells only a gold pack; Banco sells a gold pack and legendaries, and we have no cap for those
   (`traders` table). Only a dealer sell fills those levels: a hand `bazaar dealer sell`, or `dealer_sell_enabled`.
   For the levels a buy can fill (Abuela L1, Chato L2, Pícaros L4), the probe now skips a full level, plans the
   emptiest level first, and writes a `ladder_slot` row saying why an empty level gets no probe.
3. **The cap that blocked 17 Pícaros deals is `official_value_margin` = 0** (GUARDRAILS.md:27), enforced in
   `guardrails._official_value_violations` (guardrails.py:1246) → `official_values.cap_violations`
   (official_values.py:137). It is unchanged. A new rule, `dealer_ladder_value_tolerance`, defaults to 0 (no
   change). **Recommended value if Marius wants it: 4.** Pícaros' lowest RET-09/RET-10 fills to other teams were
   52–53, against our official value of about 49.
4. **The ladder probe only runs on a decided yes from its gate.** At 15 s ticks that needs README item 1
   (`BAZAAR_DECIDER_MIN_TICK_S=15` on bazaar-taker). Without it, defect 2's code only writes `ladder_slot` rows.
5. Gates: 5,331 tests pass, plus 25 new ones; ruff, black and mypy are clean. A dry-run taker against a local
   simulator took an addressed ask and logged its reason for skipping an addressed bid.

## 1. Addressed offers (defect 1)

**Root cause** (confirmed on main):
- `Taker._board_of` (taker.py:1133) reads each venue with `self.public.board()`, the keyless `PublicBazaar`
  (sdk.py:51). The real server's keyless board never lists an addressed offer (6 live examples, dealing.md §3.3).
- `market.our_open_offers` (market.py:200) skips the offers `/api/me/offers` returns with `to == us`, on purpose:
  they are not ours.
- Nothing else read them, so 127 offers addressed to us on Saturday reached 0 decisions.

**Change:**
- `market.addressed_to_us` (market.py:219) keeps the open offers whose `to` is us and whose maker is someone else,
  outside a thread. Thread offers stay the team desk's.
- `Taker._addressed_in` (taker.py:1000) parses them with the same `parse_offer` as the board, and keeps only plain
  one-card-for-cash shapes. It merges them into the board offers in `_board_offers`, **before** makers are resolved
  from the feed, so a pseudonymous maker is named the same way and `max_counterparty_share` treats it like any
  other offer. An offer the board already showed is not added twice.
- From there nothing is new:
  - Asks go through `ask_candidates` → `_accept_one`: `guardrails.check()` (rarity caps, cash floor, hourly spend,
    `block_buying_held_cards`, the official-value cap, counterparty share, breakers, approvals), the offer
    inspector, the advisory Jev or decider check, the duel grace, and the shared accept ledger with duels first.
    `_target_asks` sees them too, so an approved buy target's addressed ask is taken.
  - Bids, with `all`, go through `_bids` → `_accept_bid`: our value plus page bonus plus `sell_min_surplus` (5),
    then `check()` (`sell_min_value_ratio` 1.0 × `your_value` on the net after fee, `protect_page_sets`,
    `protect_page_exceptions`, the move-impact guard `max_score_loss_per_move`, counterparty share, approvals),
    the inspector, and the same slot ledger.
- **Audit:** every addressed offer leaves a row.
  - A candidate's accept or skip row carries `inputs.addressed_to_us = true`.
  - Any other gets an `addressed_offer` row (status `rejected`) with the reason. Reasons include: already held;
    not a missing page card; its surplus is under `min_buy_surplus`; a cheaper ask is the candidate; no free copy
    to sell; the sale nets under `sell_min_surplus`; not a plain shape; venue not tradable; selling into addressed
    bids is off.
  - Each reason is recorded once, and again only when it changes.
- **Kill switch:** `BAZAAR_ADDRESSED_OFFERS` (or `--addressed`), read at taker start (cli.py:3111), with three
  values:
  - `asks` (default): addressed asks only.
  - `all`: also sell into addressed bids, even with `--accept-bids` off. Public bids stay behind `--accept-bids`,
    unchanged.
  - `off`: as before.

  Any other value turns it **off** with a warning, rather than crash-looping the taker. The variable is declared
  `preserve()` on bazaar-taker (`.railway/railway.py:285`), so `railway config apply` keeps a hand-set value.
- **Fix found on the way:** `_accept_bid` kept the team's accept slot after a refusal that cost nothing, such as a
  bid that expired or was taken mid-tick (`offer_not_open`). It now releases the slot like the buy path does
  (taker.py:2488), so the slot stays free for the next candidate or a duel.

**Why asks default ON:** an addressed ask runs through exactly the code path that took public asks live all Saturday
(9 accepts). The only new input is which offers reach that path. **Why bids default OFF:** the sell-into-bid path
has never run live (0 `accept_bid` rows on Saturday), so turning it on is Marius's call.

## 2. Empty dealer ladder slots (defect 2)

**Root causes found:**
- **Pilar (L3) and Banco (L5) sell us no card** (`SELECT id, level, menu FROM traders`):
  - Pilar sells only `sobre_oro` (420).
  - Banco sells `sobre_oro` (420, opening 546) and legendaries (585), and `max_price_for("legendary")` is None.
  - Every Saturday L3 deal was a hand sell. L5 needs selling an epic (we held none) to Banco above our value.
  - No taker code path can fill these levels. A taker→dealer sell path was **not** built today: the SAL-07
    incident is why `dealer_sell_enabled` is false, and turning it on is Marius's call.
  - The maker's `SellDesk` already ranks "a level with fewer than 3 scored deals first"
    (dealer_sell_desk.py:132), so `dealer_sell_enabled = true`, or hand runs, fill L3 and L5.
- **Nothing in the taker counted slots.** The ladder probe (one small buy per dealer per game hour) planned every
  unlocked dealer alike, including levels already holding three deals.
- **Chato (L2):** Saturday's `max_price_uncommon` 26 was raised this morning to 30, and `max_price_rare` to 105
  (GUARDRAILS.md:21–22). Chato's Saturday fills (`dealer_curves`):
  - Rares: 33 fills, median 89, all 33 under 105.
  - Uncommons: 18 fills, median 30, 11 of 18 at 30 or less.

  Chato is reachable now, and the official-value cap (defect 3) is the binding limit left.
- **The probe gate:** the probe runs only on a decided yes. Saturday's gate said no 7 times while cash was low,
  and at 15 s ticks Jev decides unless `BAZAAR_DECIDER_MIN_TICK_S=15` is set (README item 1). Not coded around: it
  is a named dependency.
- Learned blockers (cool-off, quota, sold out) are real server refusals and are still respected.

**Change** (`agents/ladder_probe.py`):
- `LadderSlots` (ladder_probe.py:40) holds each dealer's level from `/api/dealers`, and our scored deals per dealer
  this round from `dealer_sell_data.ladder_deals`. That is the maker's existing count: buys and sells since the
  latest `day.opened` **or `round.started`**, filled away from the dealer's opening price (296c9698 added the
  round boundary). It reads the feed history, i.e. the Postgres archive plus the live window.
- `plan_probes` (:274) skips a dealer whose level has all 3 slots scored, and orders the rest emptiest level
  first, the higher level first on a tie.
- The gate's state carries `ladder.slots_scored_this_round` (e.g. `{"L1": "1/3", "L4": "0/3"}`).
- A `ladder_slot` row (status `rejected`) per empty level that no probe fills, once per game hour or when the
  reason changes. Reasons: "sells us no card we may buy: only a dealer sell fills this level", "not unlocked for
  us yet", "no probe inside our caps …", or "the ladder probe gate is not a decided yes". No row is written when the
  taker runs without Jev (`--no-jev`).

## 3. The cap that blocked 17 Pícaros deals (defect 3, separate commit 6e52cfdf)

**The exact cap:**
- `official_value_margin` = 0 (GUARDRAILS.md:27) applies to every card buy (open, bid, final, board accept).
- It is enforced in `guardrails._official_value_violations` (guardrails.py:1246) → `official_values.cap_violations`
  (official_values.py:137): `price + gives_value <= official − margin`, using `GET /api/me/value`.
- Saturday's walk text was `guardrail: denied: price 50 > official value 49 of RET-10 (GET /api/me/value)`, which is
  this function's message.

**Change** (the existing value is untouched):
- New rule `dealer_ladder_value_tolerance` = 0 (GUARDRAILS.md:28; field guardrails.py:114, range 0–10). When it is
  above 0, a taker buy from a dealer may pay up to that many primas **over** the official value while that
  dealer's level has an empty slot this round (`ladder_tolerance`, guardrails.py:1267). This covers the open, each
  bid and its final (new `Action.dealer` field).
- Never for:
  - an epic or legendary (`off_page_min_surplus`, Marius's hard rule, stays);
  - a team trade or a board ask;
  - the maker, or a hand `dealer buy` (no `Action.dealer` and no `ladder_open` there).
- The rarity caps, cash floor, hourly spend, breakers and approvals still bind. Once the level's three slots are
  scored, the cap is back.
- The probe plans its top with the same lift (ladder_probe.py:261), so it plans what the guardrails will let it bid.

**What value Marius would set, and its cost:**

| Value | Effect (Pícaros RET-09/RET-10, our official ≈ 49) | Worst case |
|---|---|---|
| 0 (default) | today's behaviour: every bid above 49 refused | none |
| 2 | bids up to 51. Pícaros' lowest RET fills to other teams were 52–53 (`dealer_curves`: RET-09 6 fills, min 52, median 57; RET-10 5 fills, min 53, median 57; opening 73), so **2 would not have closed Saturday's threads** | 2 P per buy |
| **4 (recommended)** | bids up to 53: reaches Pícaros' lowest fills. A fill at 53 captures (73 − 53) / (73 − 48) ≈ 0.8 of her range | **4 P per dealer buy over our value**, in cash, while the level is open. Scored deals fill slots, so it stops after 3 per level: 3 card-selling levels (L1, L2, L4) × 3 = **9 buys, ≤ 36 P a round** if every deal scores. A deal at the dealer's opening price fills no slot, so more buys are possible in principle, bounded by `max_spend_per_game_hour` 250 and the dealers' hourly deal quotas (Abuela 8, Chato 6, Pícaros 6) |
| 8 | reaches Pícaros' median fill (57) | 8 P per buy, ≤ 72 P a round on the same terms |

**neg_points:** no Saturday dealer buy was above our value, so whether one costs neg_points is **unobserved**. A
dealer *sale* below value did cost them (SAL-07). Assume each tolerant buy may also cost up to the tolerance in
neg_points. At Saturday's 0.03–0.05 board points per neg_point, that is at most 0.2 board per buy at 4, against
+0.55 to +0.70 board per scored L4 slot on Saturday (× 0.6 for Sunday's weighting).

**Consistent with the README "Conflicts" section:**
- This is the dealing rec 4 / quiet rec 3 conflict on the official-value cap. The default keeps dealing's "keep the
  cap" position, and Marius flips one number to take quiet's "lift it for ladder slots".
- Collections' B-CHA (+2 P on Chamberí) is a separate exception and is **not** implemented here. Chamberí dealer
  buys are L1/L4 ladder deals, so with a tolerance ≥ 2 they get the same lift while their level has an empty slot.
- The flips idea (dealing rec 7, against `off_page_min_surplus`) is untouched: epics are excluded.

**Not done:** dealing rec 4 (clamp ladder tops to the official value and skip a card whose clamped top is under the
dealer's lowest fill). It would not have stopped the 23 RET threads: Pícaros' lowest rare fill (48) was under our
cap, so the skip test passes. Since #248, a guardrail walk already rests the card for a game hour (UB1). The
tolerance is the lever that changes the outcome.

## 4. Tests (25 new, all passing)

- `tests/test_taker_addressed.py` (12):
  - Reading: only open plain offers addressed to us are read; the mode parses safely.
  - Asks: a good addressed ask is accepted with no extra request; `off` leaves it unread; a bad one gets an
    `addressed_offer` row with its reason, once; unpriceable shapes and closed venues are recorded, not guessed.
  - Accept slot: a duel holding the slot defers the ask, which is accepted next tick.
  - Dry run: nothing sent, a WOULD row is written.
  - Bids: they wait for `all`, and public bids stay behind `--accept-bids`; a thin bid is rejected with a reason.
  - Floor: **our private value floor holds even with `sell_min_surplus` forced to −100**, because `check()` refuses
    a net under `your_value`.
  - A refused sell into an expired bid gives the slot back.
- `tests/test_ladder_slots.py` (6): slot counting and rank; a full level is not probed and the emptiest level is
  planned first; Pilar and Banco sell no card, so only a dealer sell fills them; the gate sees the slots; the taker
  probes no full level and writes one row per hour; a gate "no" is named on the slot row.
- `tests/test_ladder_tolerance.py` (7, real cap on): 0 keeps the cap exactly; the tolerance applies to
  buy/bid/accept from an open level only; it never applies to a full level, a team trade, a board ask or an epic;
  the margin still counts and the rarity cap is never lifted; the value is bounded; the probe plans with the same
  lift; the taker bids over the official value only while the level has an empty slot.
- `tests/test_dealer_sell_readiness.py` (+1): slot counts restart when a round starts mid-day.

Gates on 5df8f96e:
- `uv run pytest`: 5331 passed, 152 skipped, 2 xfailed.
- `uv run ruff check src tests scripts`: clean.
- `uv run black --check src tests scripts`: clean.
- `uv run mypy src`: no issues in 209 files.

**Simulator smoke** (local `bazaar-sim` on 127.0.0.1:8791, memory store, dry run, `BAZAAR_ADDRESSED_OFFERS=all`,
`--no-jev`, no `DATABASE_URL`):
- sim-team2 posted an ask and a bid addressed to t01.
- The taker logged `WOULD accept LAV-04 on rastro for 3 · guardrails allowed` at ticks 12 and 13. Tick 11 was
  dropped as "tick budget spent" with 3 s sim ticks.
- It wrote one `addressed_offer` row for the bid ("nets −1.3 over what selling costs us (sell_min_surplus 5)").
- Note: the simulator's keyless board *does* list addressed offers, unlike the real server, so this run also
  covered de-duplication by offer id.

## 5. Edge cases reviewed

- **429s:** no new request. Addressed offers come from the snapshot's `/api/me/offers`; slots come from the feed
  already read. A rate-limited accept ends the tick's accepts as before, and the sell path now does the same.
- **Settlement lag (T+1):** an accepted addressed ask is `accepted`, not `open`, in the next `/api/me/offers`, and
  the ledger plus `unsettled_accepts` hold its cash. A copy sold into a bid is excluded for tick − 1 and the current
  tick (`_bids`' `sold` set), so it is never sold twice.
- **Offer expiring mid-tick:** the accept gets a 4xx (`offer_not_open`), which costs nothing. Both the buy and the
  sell path now give the slot back.
- **Duplicate decisions across restarts:** `addressed_offer` and `ladder_slot` dedupe is in memory, so a restart
  records each still-open offer or empty level once more. These are audit rows, never sends. A re-accept of an
  offer already taken is refused by the server and costs nothing.
- **Two services racing for the accept:** unchanged. `ledger.reserve_accept` on the shared Postgres ledger, duels
  first (`duel:` items), and the maker never accepts. Two taker containers during a deploy share the same atomic
  reservation.
- **Kill switch:** the taker's hold returns before any proposal, so no addressed accept and no audit row while it
  holds. `BAZAAR_ADDRESSED_OFFERS=off` disables only defect 1.
- **Pseudonyms:** an addressed offer's maker is resolved from the feed's `offer.listed`, like a board offer. An
  unresolved pseudonym is refused by `max_counterparty_share` only when that cap is below 1. It is 1.0 now.
- **Slot count accuracy:** with Postgres up, `snap.events` is the archived feed plus the live window, so counts are
  complete. Without Postgres only the window (~20 ticks) is seen, levels look emptier, and the probe plans as it
  did before. The tolerance could then apply to a level that is really full, bounded by the table above.

## 6. Deploy steps (Marius)

Merging redeploys **all three services** (`src/**` and GUARDRAILS.md). Under the live schedule (round 3 started
at tick 1446, the 09:00 open, so JUMP), the freeze windows are 08:45–09:40, 10:55 until Duels III ends, 13:16–13:26,
and 13:55 to the close. **Merge in the 09:40–10:55 gap**, outside a bench.

1. Review the branch, push it, open the PR (`fix/sunday-dealing` → main). Nothing was pushed from here.
2. Before merging: `uv run bazaar rules` shows `dealer_ladder_value_tolerance 0`. Run
   `uv run pytest -q tests/test_taker_addressed.py tests/test_ladder_slots.py tests/test_ladder_tolerance.py`.
3. Merge. After the redeploy, check `railway logs -s bazaar-taker --lines 30`: `target: real game`, and **no**
   yellow `BAZAAR_ADDRESSED_OFFERS=… addressed offers off` warning.
4. Variables, bazaar-taker only (each restarts the taker only):
   - Nothing, for addressed asks (default `asks`).
   - Sell into addressed bids (t02-style; the reviewer found ≈ 30 neg_points of above-value bids on Saturday):
     `printf all | railway variable set BAZAAR_ADDRESSED_OFFERS --stdin --service bazaar-taker` (README's pattern).
   - For the probe to run at 15 s ticks: README item 1 (`BAZAAR_DECIDER_MIN_TICK_S=15`,
     `BAZAAR_DECIDER_TIMEOUT_S=8`, `BAZAAR_DECIDER=llm`), if not set already.
5. The tolerance, only if Marius decides: a one-line GUARDRAILS.md commit, `dealer_ladder_value_tolerance` = 4.
   Run `uv run bazaar rules` before merging (an invalid value holds every write; the range is 0–10). This redeploys
   all three services, so put it in the same gap or batch it with this merge.
6. Verify (read-only SQL, 10–20 ticks after the deploy):
   ```sql
   SELECT tick, kind, status, left(reason, 120) FROM decisions
   WHERE agent = 'taker' AND (kind = 'addressed_offer' OR candidates->>'addressed_to_us' = 'true')
   ORDER BY id DESC LIMIT 30;
   SELECT tick, left(reason, 160) FROM decisions WHERE kind = 'ladder_slot' ORDER BY id DESC LIMIT 10;
   SELECT tick, kind, left(reason, 120) FROM decisions WHERE kind = 'dealer_walk' AND reason LIKE '%official value%'
   ORDER BY id DESC LIMIT 10;  -- with a tolerance: "+ dealer_ladder_value_tolerance" in the text
   ```

## 7. Risks

- **Addressed asks (on by default):** the same buys the taker already makes from public asks; no new guard is
  bypassed. A team could address a bait ask, but the official-value cap and the rarity caps bound the price as for
  a public ask.
- **Addressed bids (`all`):** first live use of the sell-into-bid path. Guarded by the sell floor at `your_value`
  (ratio 1.0), `sell_min_surplus` 5 over value plus page bonus, `protect_page_sets` (every set),
  `max_score_loss_per_move` 0.001 and approvals. The SAL-07 class of mistake (a page copy below value) is refused
  three times over.
- **Tolerance:** see the table in §3. Cash ≤ value × buys, and possibly the same in neg_points (unobserved).
- **Ladder probe:** it spends only when the gate says yes; the row-only part is harmless. Request budget unchanged.
- **Redeploy timing:** a merge restarts all three services. Avoid benches and Duels III (taker restarts on Saturday
  landed inside Duels I).

## 8. Rollback

- Addressed offers: `printf off | railway variable set BAZAAR_ADDRESSED_OFFERS --stdin --service bazaar-taker` restarts the taker
  only, with no code deploy.
- Tolerance: set `dealer_ladder_value_tolerance` = 0 in GUARDRAILS.md (all three redeploy), or never raise it.
- Everything: revert the merge commit on main (all three redeploy). No schema, state file or persisted format
  changed: `addressed_offer` and `ladder_slot` are new decision kinds in the existing table.

## 9. Not verified

- Nothing ran against the live game. The real server's keyless board hiding addressed offers rests on 6 live
  examples taken after the close (dealing.md).
- That `/api/me/offers` names the **real** team id as `maker` on an addressed offer was not observed; the code
  resolves pseudonyms either way.
- The ≈ 30 neg_points of above-value addressed bids is the Saturday reviewer's figure, not recomputed here.
- Sunday's official values may differ from Saturday's 49 for RET-09/RET-10, since values move with our album.
- Whether a dealer buy above our value costs neg_points (see §3).
