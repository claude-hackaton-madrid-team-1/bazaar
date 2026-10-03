# B13: wake for the opening (bite X4)

**Verdict: GO.** A pure bug fix with no new parameter. It is only useful if it is deployed before 09:00, which
means merging to main, and every merge to main redeploys the live agents (bite X16). If it is not merged in time,
the ops fallback stands: restart the Railway services just *after* 09:00:00.

## The bug
With the doors closed, `ticks.seconds_until_next_tick` returned `CLOSED_POLL_MAX_S = 300` whatever `next_opens`
said. Every loop (taker, maker, duels, monitor, capture, evals, dealer) sleeps through `run_per_tick`, so the
first read after the 09:00 opening landed 0–300 s late. That is 0–10 Saturday ticks at 30 s, or 0–20 Sunday
ticks at 15 s, and it falls right when the grant, El Retiro and the 09:00 ladder/trade plans land.

## The fix (`src/bazaar_agent/ticks.py`)
When the doors are closed, the loop sleeps until just after the next opening and still polls at least every 300 s:

| closed clock says | sleep |
|---|---|
| next opening in `x` s (the earliest of `next_opens` and any future `days[*].opens`) | `clamp(x + 0.3, 1, 300)` |
| the announced opening has passed (our clock is ahead, or the server opens late) | 5 s, the same as a pause |
| `now` is inside a `days[*]` window but the doors are still closed (`next_opens` already rolled to the next day) | 5 s |
| no usable timestamp (missing, naive, malformed) | 300 s, today's behaviour |

Any exception while parsing falls back to 300 s. The sleep runs outside `run_per_tick`'s `try`, so a bad
timestamp must never kill a live loop. `days` is read from the model's extras and is not typed, so a change in its
shape cannot make the clock fail validation.

**Deviation from the backlog wording:** "floor 1 s" is the floor on the sleep *before* the opening. Once the
opening is overdue, the loop polls every 5 s, not every 1 s. With about 6 loops on one key, 1 s polls would take
6 req/s, above the 5 req/s limit, for as long as a late opening lasts. 5 s matches the existing pause poll (on
Friday the clock sat at `paused: true` at tick 0 for over an hour, docs/briefing.md:96).

## Evidence
I ran the real `run_per_tick` from main and from this branch in virtual time, with no network
(`docs/night/b13_wake_sim.py`). Each run starts at a random moment about 6 h before 09:00, with 1,000 starts per
row and 30 s ticks.

| code | server opens | our clock | late p50 | late max | ticks missed (mean / max) | clock reads over 6 h |
|---|---|---|---|---|---|---|
| main | on time | exact | 161.8 s | 299.9 s | 4.68 / 9 | 74 |
| main | 90 s late | exact | 153.5 s | 299.6 s | 4.61 / 9 | 74 |
| B13 | on time | exact | **0.3 s** | 1.0 s | **0 / 0** | 74 |
| B13 | on time | 3 s fast | 2.3 s | 2.7 s | 0 / 0 | 75 |
| B13 | on time | 3 s slow | 3.3 s | 3.3 s | 0 / 0 | 74 |
| B13 | 90 s late | exact | 0.3 s | 0.8 s | 0 / 0 | 92 (+18 polls at 5 s) |

The request cost overnight is unchanged (74 reads). A late opening costs one read per 5 s per loop.

## Tests (each one fails on main, passes here)
- `tests/bites/test_doors_open_wakeup.py`: r2's two bite tests with the strict `xfail` markers dropped. On main,
  both fail (300 s sleeps).
- `tests/test_ticks.py`: 5 new tests. They cover the sleep to the opening with its 1 s floor and 300 s cap, the
  overdue case (5 s), a day window with a rolled `next_opens`, bad timestamps and `days` shapes (300 s), and the
  loop handling tick 160 after one short sleep. All 5 fail on main.
- Gates: 829 passed / 34 skipped; ruff, black and mypy are clean.

## Risks / for Marius
- **Real closed-clock payload unverified offline.** docs/services.md:33 shows `doors: closed, paused: true,
  next_opens: …+02:00`, which is the shape I rely on. If the server ever sends a naive `next_opens`, we keep
  today's 300 s poll.
- **The simulator never closes its doors** (`clock_view` always says `open`), so a sim run does not exercise this
  path. The tests and the virtual-time replay do.
- **Merge conflict to expect:** `tests/bites/test_doors_open_wakeup.py` also exists on `night/r2-bite-hunter`
  with strict xfails. If that branch is merged after this one, keep this version (without the markers), or the
  strict xfails turn into failures.
- **Decision:** merge before 09:00 (a redeploy, bite X16), or skip the merge and restart the services at
  ~09:00:05.
