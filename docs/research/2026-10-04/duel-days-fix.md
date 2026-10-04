# Duels days latch stuck at `conflict`: root cause, fix, reset (Sun 4 Oct 2026)

Branch `fix/duel-days-stuck` (local, from `origin/main` 235f296e). Written 09:10–09:45 Madrid.

## TL;DR

- **Deadline.** Duels III is at game hour 15.367. Since about 09:20 the clock runs 1:1 with wall time (15 s ticks;
  tick 1485 = 13.796 h at 09:24:52), so Duels III starts at about **10:59**. The fix must be deployed by about
  **10:50**. Benches start at 14.65 h (about 10:16) and 15.0 h (about 10:37) and last 16 ticks (4 min) each. Deploy
  in a gap outside them: now to 10:14, 10:21 to 10:35, or 10:42 to 10:50.
- **Root cause.** The real rule depends on the role. Both roles get a positive weight. The seller's text says "each
  delivery day adds this much cash to your side" and the buyer's says "each delivery day costs you this much cash".
  The code had one latch for both roles. During Duels II it read the buyer text as `cost`, then read a scored seller
  deal as `signed`, and the mismatch became `conflict`, which it never leaves. The latch file sits on the
  `bazaar-duels` volume, so a redeploy keeps it. Duels III would have started at `conflict`, with every offer at day 0.
- **Fix (commit `48e7546c`).** There is now one latch per role and per session. A verdict from an earlier session is
  dropped at the first live duel of the next session. The old single file is no longer read. The real seller text now
  counts as a gain. A new guardrail, `duel_days_signed_roles` (default `none`), carries the sign per role into the v2
  policy, Jev and the guard.
- **Replay on Saturday's 68 real payloads.** Old code: `cost`, then `conflict`. New code: sellers `signed`, buyers
  `cost`, no conflict. The seller sign would have turned on by itself at duel 5744 (deadline tick 1316), roughly 77
  ticks into Duels II.
- **Your decision.** With `none` (the default), sellers turn signed only after this session produces two agreeing
  signals: the seller text plus one scored seller deal at a day other than 5. With the 12-tick duels of Duels III that
  may come late in the session. Setting `duel_days_signed_roles = seller` (one line in GUARDRAILS.md, a second local
  commit ready to merge) values seller days from the first tick, and any contrary evidence in the session still turns
  it off. Dealing estimated this upside at about +2 duel points (+7 %) on Duels II.

## 1. The state machine (before the fix)

`src/bazaar_agent/agents/duel_days.py` on `origin/main`:

| State | Set by | What the policy and guard do (`effective_rules`, :342) |
|---|---|---|
| `unknown` | start; null text; simulator text; a text with no sign tied to a gain or cost | worst case: every day costs \|w\|, so v2 offers day 0 |
| `signed` | a real text tying a gain to "(+)"/"positive", or a scored deal fitting `+w·days` | `duel_days_signed` turns on only with `duel_days_auto` **and** two signals from the current session (`corroborated`, :286) |
| `cost` | a real text with only a loss word ("costs you"), or a scored deal fitting `−\|w\|·days` | forces `duel_days_signed` **off**, even when it was set by hand |
| `reversed` | a loss tied to "(+)" | off |
| `conflict` | two known verdicts that differ (`_merge`, :179), a score that fits no model, or an unreadable file | off, **for good**: `_merge` keeps `conflict` against anything (:181), and the docstring says "A conflict stays for good, every session" (:23) |

- **Persistence.** The latch is stored in `<data_dir>/duels/days_sign.json` (:329, :338). On Railway,
  `BAZAAR_DATA_DIR=/app/.local` is the `bazaar-duels-data` volume (`.railway/railway.py:54, 251, 279`). The process
  merges the file into memory on every observe and rewrites the file when it lags (:256–259).
- **Why Saturday ended at `conflict`.** I fed the code's own functions every real session-3 payload from the shared
  DB (read-only) in the order the runner saw them (`git show
  research/sat-review-crinoid:docs/research/2026-10-04/dealing/days_latch_replay.py`). Only two texts occur in
  session 3:
  - Seller: "each delivery day adds this much cash to your side" reads as `unknown`, because "adds" is not a gain
    word and there is no "(+)".
  - Buyer: "each delivery day costs you this much cash" reads as `cost`.
  - Duel 5619 (buyer, tick 1239) set `cost`. Duel 5744 (seller, scored at 10 days, `signed`) then made it
    `conflict`.
  - The scored evidence agrees with the role-aware model on all 4 deals that can be scored: 5744 (seller, 10 days)
    and 5854 (seller, 3 days) are `signed`; 5759 and 5999 (buyers, 10 days) are `cost`. Dealing §3.5 also shows that
    duel 5632's result matches price − limit + w × days to 0.1 P.
  - Effect (dealing §3.5): 372 of 372 offers went out at day 0, about 98 P of seller result was left on the table,
    and duel 5730 was lost.
- **Not seen.** The live content of `/app/.local/duels/days_sign.json`. `railway logs` (read-only, project
  heartfelt-warmth) returns only the current deployment: 32 lines from this morning's container start, with the
  volume mounted and "target: real game", then "0 live duel(s)" ticks. Saturday's "duel days sign" lines are not in
  that window. The `conflict` is therefore shown by the replay, not by the file.

## 2. The fix (`48e7546c`)

- **Per role.** `DaysLatch` holds a seller `DaysSwitch` and a buyer `DaysSwitch`. Each switch reads only its own
  role's payloads and has its own file: `duels/days_sign_seller.json` and `duels/days_sign_buyer.json`. Seller
  evidence and buyer evidence never merge, so the Duels II mismatch can no longer become a conflict.
- **Per session.** Each verdict records `formed`, the session it came from.
  - `_expire()` drops a verdict from another session, or from a file without `formed` (an old or unreadable file),
    as soon as the live duels name the current session.
  - Only rows of the current session move the verdict. Older rows from `?done=true` are still counted as signals
    under their own session, but they never vouch for a later one, as before.
  - In `refresh()`, verdicts from different sessions never merge.
  - A conflict now lasts only for the rest of its session. The legacy `days_sign.json` is never read again.
- **Restart mid-session.** The file keeps the verdict, its `formed`, and the per-session signals. After the first
  live read, a restarted process is back in the same state (tested).
- **Real seller text.** In `evidence()`, `ADDS_TO_YOU` reads "adds … to your" as a gain (`signed`). "nothing",
  "none" and "zero" now count as negations, so "adds nothing to your side" stays `unknown`. Buyer text is unchanged
  (`cost`).
- **Per-role sign in the policy and guard.**
  - New guardrail `duel_days_signed_roles` = none | seller | buyer | both (GUARDRAILS.md, `ENFORCED_BY`).
  - `guardrails.days_signed_for(rules, role)` resolves it, and `_duel_limit_violations` uses it with the action's
    role.
  - `V2Params.signed_for(duel)` is used by `duel_plan` and the counter-offer. `duel_jev` uses it in both places it
    read `days_signed`.
  - `effective_rules` decides each role:
    - A role is signed if it was set by hand (`duel_days_signed` for both roles, or `duel_days_signed_roles`), or if
      auto mode is on and the role is corroborated.
    - A role is off whenever its own current-session verdict is `cost`, `reversed` or `conflict`.
    - It returns the same rules object when nothing changes.
  - The buyer keeps the worst case, which for buyers is the truth.
- **Unchanged.**
  - The two-signal rule (#150).
  - The day-5 and rounding exclusions in `scored_evidence`.
  - v1 always uses the worst case.
  - The defaults of existing params.
  - Corrupt files read as `conflict` until a session is known.
  - The rollback on a failed observe, now per role (`DaysLatch.keep_safer`).
- **Not shipped.** A "non-zero days when unsure" fallback. Days are a true cost for buyers, and only the seller side
  was measured. It would be untested logrolling, so it is left as future work.

Tests (`tests/test_duel_days_sessions.py`, plus the updated latch tests):

- Saturday's sequence no longer conflicts (seller signed, buyer cost).
- A session-3 conflict on disk does not reach session 4.
- The old global file is ignored.
- Two seller scores at two different days arm sellers, and v2 then offers days = 10 to sellers and 0 to buyers, both
  inside the guard.
- The guard values each role by its own sign.
- A role set by hand turns on at the first tick, and contrary evidence still wins.
- A restart mid-session keeps the session's state.
- A missing file and a corrupt file are both handled per role.
- Rows from older sessions never move the verdict.
- `reads_done` stays on while any role can still learn.

Gates: `uv run pytest` passes 5317 tests (152 skipped, 2 xfailed). `ruff check src tests scripts`, `black --check
src tests scripts` and `mypy src` are all clean.

Replay script: `docs/research/2026-10-04/duel-days-fix/replay_by_role.py` (read-only SELECT). Output:

```
duel 5618 (seller, appeared tick 1239): seller=unknown buyer=unknown -> seller=signed buyer=unknown
duel 5619 (buyer, appeared tick 1239): seller=signed buyer=unknown -> seller=signed buyer=cost
final: seller=signed buyer=cost | seller signed (auto) after duel/deadline: (5744, 1316)
session 4, first live duel (no text): seller=unknown buyer=unknown
```

## 3. No-deploy reset (only Marius runs this)

The file is `/app/.local/duels/days_sign.json` on the `bazaar-duels-data` volume.

- **Order matters.** Deleting the file while `duel run` is up does nothing: the process holds `conflict` in memory
  and rewrites the file on the next observe that finds the disk lagging. Delete first, then restart at once.
- **Warning: without the code fix, this reset is worth about nothing.** The old code re-latches the same way within
  the first ticks of Duels III. The first buyer text sets `cost`, which already forces worst-case days for everyone.
  The first scored seller deal then sets `conflict`. That is exactly Saturday's sequence, ending again at day 0
  everywhere. No hand-set variable gets around it on the old code either: `effective_rules` forces
  `duel_days_signed` off on `cost`/`conflict`, and one global sign would be wrong for buyers anyway. **With the fix
  deployed, no reset is needed:** the new code never reads that file.

If you still want it (for example, to check the file):

1. `railway ssh -p 05a9de65-622b-4754-a0f0-be4d7f54ec51 -e production -s bazaar-duels -- cat /app/.local/duels/days_sign.json`
   (read it; expect `"verdict": "conflict"`).
2. `railway ssh -p 05a9de65-622b-4754-a0f0-be4d7f54ec51 -e production -s bazaar-duels -- rm /app/.local/duels/days_sign.json`
3. `railway restart -p 05a9de65-622b-4754-a0f0-be4d7f54ec51 -e production -s bazaar-duels -y` (a restart, not a
   redeploy: the code and the volume stay).

The MCP runtime's `duel_move` (`runtime/actions.py:196`) keeps its own copy on the `bazaar-mcp-data` volume, at the
same path. It runs dry unless `BAZAAR_LIVE=1`.

## 4. Deploy steps

1. Choose the seller setting:
   - (a) Merge `fix/duel-days-stuck` as is (`duel_days_signed_roles = none`, auto only).
   - (b) Also take the one-line commit on `fix/duel-days-stuck-seller` (`duel_days_signed_roles = seller`). That
     option is not committed on `fix/duel-days-stuck`.
2. Push, open a PR, wait for CI to pass, and merge in a gap outside the benches (see the deadline in the TL;DR).
   Every merge to main redeploys bazaar-duels, bazaar-taker, bazaar-maker and bazaar-mcp, so a 1–2 minute restart of
   all of them is the usual cost.
3. Check, read-only, after the deploy and before 10:59: `railway logs -p 05a9de65-622b-4754-a0f0-be4d7f54ec51 -e
   production -s bazaar-duels --lines 50` shows the new container's ticks.
4. At Duels III start (first live tick), the log should show a line like `duel days sign: seller=signed buyer=cost
   (session N; …)`, with N being Duels III's session. Afterwards, `railway ssh … -- cat
   /app/.local/duels/days_sign_seller.json` shows `"formed": N`.
5. With (a), seller offers move to day 10 once a seller deal at a day other than 5 has been scored in the session.
   With (b), they move from the first tick. Buyer offers stay at day 0.

## 5. Risks

- **Signed seller valuation trusts the role model.** If Duels III changed how seller days are scored while keeping
  the text, the guard would treat a deal as inside our limit when it is not.
  - Auto mode needs a scored deal from this session.
  - With (b) set by hand, the trust comes from Saturday's evidence until the first scored seller deal, which then
    either confirms it or turns it off (`cost`/`reversed`/`conflict` for sellers).
  - A changed or unrecognised seller text never counts as signed. With (a) it stays at the worst case.
- **Conflict still sticks for the rest of its session**, per role. That is the safe side, by design.
- **Between sessions,** an old verdict can stay in the file until the next session's first live duel. It can only
  keep days off, never turn them on.
- **The deploy restarts all agents.** Avoid the bench windows and the Duels III start.
- **Not done here:** a live check of the volume file, or of Saturday's deploy logs (§1).

## 6. Rollback

Revert the merge commit on main, and the redeploy brings the old code back. The old code reads the legacy
`days_sign.json` again (still `conflict` unless it was deleted), which means worst-case days, exactly Saturday's
behaviour, and safe. The new per-role files are ignored by the old code. To turn off only the hand-set seller sign
with (b), set `duel_days_signed_roles = none` (one line, redeploy).
