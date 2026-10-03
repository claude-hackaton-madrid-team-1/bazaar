# Night r2 — bite hunter: what could bite us Saturday/Sunday

Adversarial review of the whole system (Railway taker + maker + duels live from 09:00 on `main`), each case
proved or disproved with a test on this branch (`tests/bites/`) or a run against the local simulator. The
full catalogue, with evidence and routing, is `_night/BITES.md` (outside the repo); this page is the summary.
Nothing here touched the live game; the shared Postgres was read with read-only SELECTs only.

## How to read the tests

- A test that proves a bite is an `xfail`; it passes (XPASS) once the bite is fixed. `BITES_STRICT=1 uv run
  pytest tests/bites` turns every XPASS into a failure, so a fixed marker gets dropped. Without the variable the
  suite stays green on any branch, before and after #60/#62/#72 merge.
- Parametrized duel tests are marked from `tests/bites/known_bites_main.txt` (`conftest.py`).
- `tests/bites/chaos.py` runs the real taker and maker LIVE over HTTP against the in-process simulator (30 s
  ticks, rivals on, optional redeploys) and reads ground truth (cash, what we paid) from the simulator.

## Confirmed bites (severity · where · status)

| id | bite | sev | on | status |
|---|---|---|---|---|
| X7 | One tick can breach `cash_floor`; three dealer thread bids can fill at one boundary for 240 P vs the 150 cap | high | main | fixed by #72 (tests XPASS there) |
| X11 | A dropped Postgres connection silently stops the live duel player | high | main | fixed by #62 |
| X5-B1 | v1 two-issue offers at days = 5 unvalued: 10 of 16 ticks outside our limit at \|w\| = 4 | blocker | main | fixed by #60/#86 |
| X15 | An expired maker bid is reposted and its spend booked again: 130 P booked for one 65 P bid after 2 TTLs; in the simulator one expired 70 P bid blocked every buy for the rest of game hour 1 (95 P really paid of 150) | high | main, #72 | open (B14) |
| X3 | A redeploy orphans the taker's dealer threads: never driven or closed; a dealer-side deal is never booked | high | main, #72 | open (B17) |
| X16 | Every merge touching `src/**` (and other watched paths) redeploys the live duels/taker/maker mid-play | high | config | ops: merge windows |
| X17 | The taker takes the one accept 2 s into the tick; a Jev-slow duel books it at ~3.5 s → a deadline duel scores 0 | high | v1 | fixed for v2 (#86 4417c54); v1 open (B15) |
| X20 | A 429 `rate_limited` accept keeps the reserved accept slot: the tick's only accept is wasted | high | main, #72 | open (B18) |
| X19 | The live maker cancels every board offer it did not plan (hand trades); PAUSE does not stop cancels on main | high (ops) | main | #79 hands-off rows; #68 for PAUSE |
| X5-B3 | One accept per tick: six shared-deadline duels inside our limit → 2 deals in v1 | high | v1 | v2: 6/6 |
| X5-B6 | v1 re-anchors after a redeploy (offers 160 instead of 126) | high | v1 | fixed for v2 |
| X5-B2 | Payload shapes: `issues` missing → accept worth 74 < limit 100; one bad row killed the duel tick | medium | all | fixed on #86 f6f4435 |
| X5-B4 | Skipped ticks skip the endgame accept | medium | v1, v2 | v2 fixed on f6f4435; v1 open |
| X4 | Closed doors poll every 300 s: the 09:00 opening is handled up to 5 min late | medium | all | open (B13) |
| X6 | Sunday budget 71 of 75 keyed requests at the ceiling; the SDK re-sends 429-refused GETs and POSTs | medium | all | #78 model updated |
| X2 | One hung keyed read stalls a loop 46.5 s (15 s timeout × 3); sends stay safe | medium | all | open (B18) |
| X8 | `pending_fee` ignored: an accept can settle above `max_price_*` if the server charges the new fee | medium | all | open (B19) |
| X18 | An unsettled accept is not "held": a duplicate can be bought if `/api/me` lags settlement | medium | main, #72 | open (B16) |

Disproved: ledger carry-over across the day boundary (the shared `ledger` table is empty), self-trading between
our taker and maker, extra `want` items in board offers (the parser is strict), rival text driving a duel price.
In the simulator (240 ticks, with and without a redeploy every 10 ticks) the real cash floor and the real hourly
spend held; only our own ledger overbooked (X15).

## Decisions for Marius

1. Merge #62 and #72 (contains #61, #68) before 09:00; #60 before Duels II (h13); decide v2 (#86) for the duels.
2. Merge only before 09:00 or between duel sessions (X16); after any redeploy close orphan dealer threads (X3).
3. X15 stopgap: `MakerConfig.offer_ttl_ticks` ≈ one game hour. It is a code change, so it needs a merge window.
4. Hand trades only via `bazaar sell … --live` with #79's hands-off rows (X19).
5. Run the 09:00 read-only probes (BITES.md; in #102 as P1–P8): `t_hours` jump vs resume, thread bids in
   `/api/me/offers`, settlement timing, fee at settlement, whether duel accepts count per tick, the days-weight sign.
