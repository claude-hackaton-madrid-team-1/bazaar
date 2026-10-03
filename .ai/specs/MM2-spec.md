# MM2 — A venue notice that names cards, on the server's cadence (+ SDK parity audit)

Source: local backlog (`.ai/specs/02-plan.md`). Asked by Omar (Sat 3 Oct, 20:14): "THE GAME HAS TWO LEGS:
NEGOTIATIONS AND MARKET MAKING. WE NEED TO IMPROVE THE MARKET MAKING TO DO POINTS", built on the official SDK
mechanics. Stop-building rule in force: this is the one lever, no matching change.

## Ground truth (vendor kit, read before coding)
- `Broker.announce(text)` = `POST /api/broker/announce` (bazaar_sdk.py). The server refuses a notice that comes too
  soon with code `wait` (our `executions` rows: 12 refusals 2-8 ticks after an accepted notice; accepted gaps as
  short as 10 ticks). The feed shows every accepted notice as `venue.announcement {venue, name, text}`.
- Market points = 22.5 × bench + 7.5 × organic, organic = gains other teams realise on our venue (RULES.md "Your own
  market"). v19 had 0 organic trades all game; we cannot trade on our own venue with our team key.
- Our notice was the same generic text 33 times (ticks 332-1166), naming no card; the venues that fill name cards.

## Requirements
1. The notice names the 3-4 page cards the most OTHER teams miss for a page close to complete
   (`team_matrix.Cell.missing_for_page`, the matrix the taker stores and the maker reads through `LatestMatrix`),
   our venue id, name and 0 % fee, what the broker does, and the house market's fee when dearer.
2. At most 240 characters (the feed clips there); printable ASCII only; card ids only when they match the catalog
   shape (`[A-Z]{2,4}-[0-9]{2}`); no team, value, multiplier or cash; a card only podium rivals (`Summary.rival`)
   miss is left out; only cards we hold a copy of (/api/me assets), never one we miss ourselves: a public "wanted"
   would raise its asks to us and send its sellers to a venue our key cannot trade on. An empty /me names nothing.
3. No matrix, a stale one (`LatestMatrix.current`, 30 ticks) or no card that fits: the existing generic notice.
   A process's first notice waits up to `MATRIX_GRACE_TICKS` (3) for the matrix's first read.
4. Cadence: one notice every `ANNOUNCE_EVERY_TICKS` (24) and at most `ANNOUNCE_MAX_PER_GAME_HOUR` (5) per game
   hour. The newest `venue.announcement` for our venue in the feed (shared `feed_events`) counts as our last notice,
   so a restart never retries one. A `wait` refusal that names a later tick (`next_tick`/`until_tick`/`retry_tick`,
   at most 120 ticks ahead) is honoured; any refusal waits the normal cadence.
5. Still gated by `guardrails.check(Action("venue_announce"))` (`allow_venue_open`, kill switch); dry run sends
   nothing.
6. SDK parity audit (findings in the PR body, no code change): `agents/broker.py`, `matcher.py`, `venue_keeper.py`
   vs `vendor/bazaar-kit/starter_broker.py`.

## Out of scope
Matching logic, bench policy (`BAZAAR_BENCH_POLICY=exact` stays), fees, opening or closing the venue.

## Acceptance
Tests without network: `tests/test_venue_notice.py` (card choice, text bounds and cleaning, fallback, cadence,
restart memory, `wait` refusal), `tests/test_venue_keeper.py` (existing notice tests on the tick cadence).
