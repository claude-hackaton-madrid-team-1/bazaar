# B23 · Live opportunity alerts in the monitor (night shift, 4 Oct)

**Goal.** Saturday's market will differ from Friday's: 4 team venues, 30 s ticks, more teams at L2+. Collect the
evidence to flip `arb_enabled`, `dup_buy_enabled` (W8) and the taker's `accept_bids` (B4) while the game runs:
alerts for each opportunity, and a log of how long every one of them stood.

**Verdict.** GO to run `bazaar monitor --opportunities` from Saturday 09:00. It is read-only: no write reaches
the game, and its reads are keyless public reads (venues + one board per venue: ~6 per scan, 0.2 req/s at
30 s ticks), so the team key's 5 req/s is untouched. Off by default: without the flag the monitor reads no board
(tested). PR stacked on W8 (#101), with B4 (#98) merged in.

## What it does

| | |
|---|---|
| Scan | every `--opp-every` ticks (default 1): `/api/venues`, then each tradable venue's board; makers named from the feed |
| Scored by | W8's `arb.scan` (`arb`: buy an ask, sell into a bid, net after both fees; `dup`: a held card's next copy worth more than its cost) and B4's `opportunities.scan` (`buy`: a missing page card below its value; `sell`: a bid above what our least valuable copy costs us) |
| Key (dedupe) | the offers: `arb:<ask>:<bid>`, `dup:<ask>`, `buy:<offer>`, `sell:<offer>` |
| Alert | **once** per opportunity, the first scan its value reaches the kind's threshold: `--arb-alert-net` (default `arb_min_net_spread` 3), `--dup-alert-surplus` (`dup_min_surplus` 3), `--opp-alert-surplus` (5). Through the monitor's existing pipeline: `alerts.jsonl`, Postgres `alerts`, console, `--notify`. If the taker would not take it with its switch on, the alert says why (maker unknown, guardrail) |
| Log | `<data dir>/opportunities.jsonl`: an `open` row the first scan, a `closed` row when it is gone (first/last tick, scans seen, best value, alerted, whether the taker would take it), for every opportunity reaching `--opp-log-floor` (0) |
| Decide | `bazaar arb watch-log [--since-tick N]`: per kind, how many were seen and alerted, how many the taker would take, how many stood for ≥ 2 scans, median/max ticks, best |
| Replay | `bazaar arb watch-replay STREAM --me FILE`: the same tracker over a captured day, as if the monitor had scanned every tick |

A failed scan (a refused read, a bad payload) is reported, and the monitor goes on.

## Friday through the tracker (`watch-replay`, ticks 0–149, our album at tick 123)

| kind | seen | alerted | taker would take | stood ≥ 2 scans | median ticks | max ticks |
|---|---:|---:|---:|---:|---:|---:|
| arb | 0 | 0 | 0 | 0 | | |
| dup | 0 | 0 | 0 | 0 | | |
| buy (B4) | 156 | 52 | 80 | 152 | 10 | 60 |
| sell (B4) | 2 | 2 | 2 | 2 | 32.5 | 60 |

Durations, all 158: p10 2 ticks, p50 10 (an offer's default life), p90 10; 4 lasted one tick. A scan every tick catches
158, every 2 ticks 154, every 3 ticks 141, every 5 ticks 123. Keep `--opp-every 1` on Saturday (30 s ticks). On Sunday
(15 s ticks), `--opp-every 2` still catches 97 %.

## How to read Saturday's log for the switches

- `arb_enabled`: flip only if `watch-log` shows arb opportunities the taker would take, standing ≥ 2 scans (the exit
  needs the bid the next tick), more than about one per hour, and with `best` ≥ 3.
- `dup_buy_enabled`: flip only if takeable dup rows show up repeatedly (Friday: none at any tier).
- B4's `accept_bids`: the `sell` rows. Friday had 2 standing for 30+ ticks.

## Tests (13 new, 1074 green on the branch)

Tracker: one alert per lifetime, a later crossing of the threshold, the log floor, the "taker would not" note.
Scanner: crossing + duplicate from public reads only (3 reads), B4's buy. Monitor: off reads no board; alert once,
log open → closed with ticks; a failed scan does not stop the monitor; every N ticks. Summary + `watch-log` CLI.
Replay. Against the in-process simulator over HTTP: a rival-made crossing is alerted, and nothing is bought.

## Integration notes

- B4's branch merged into this one. B4's `accept_bids` and W8's held-card paths share one board read. A copy bought
  for an arbitrage exit is never offered to `accept_bids`.
- B4's and W4's CLI JSON tests now read `stdout`: since the base merged main, the target banner goes to stderr.
- The scan's guardrail verdicts use `/me` only (no ledger, no open offers): they are indicative. The taker
  re-checks everything when it acts.
