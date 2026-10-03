# B8: Duels II days readiness

Night backlog B8, 3–4 Oct 2026. Two draft PRs:
- **#113 (the wiring)**: `night/b8-days-wiring`, on W2b's `night/b11-endgame`.
- **This branch (the module, the zoo, the numbers)**: `night/b8-days`, on `night/b11-exploiters`.

Numbers are in [b8-days-tables.md](b8-days-tables.md): 2,400 two-issue duels per row, decay 0.08, v2 @ 3ef8ecd. Offline only.

## The question
Duels II (~h13) is the first session that negotiates price and delivery day (0–10). Each side has a private
`your_days_weight`, but nothing official says which way it points:
- RULES.md: "a private weight per day";
- the openapi: "Primas per delivery day";
- Friday's real payloads: `days_meaning: null`;
- only the simulator says "primas you gain (+) or lose (-) per delivery day", and it is not evidence.

PR #60 and v2 therefore value every day at the worst case (|weight| against us) and offer 0 days. v2 can value days with their sign (`duel_days_signed`), but that is off.

## What it does
- **`agents/duel_days.DaysSwitch`** latches the first REAL two-issue payload's `days_meaning`:
  - `signed`: a gain tied to "(+)" or "positive", the convention v2, the guard and the simulator share;
  - `reversed`: a cost tied to "(+)"; off;
  - `cost`: off;
  - `unknown`: null, simulator text, or ambiguous; off;
  - `conflict`: real payloads disagree; off for good.

  The verdict persists in `.local/duels/days_sign.json`, and `real_game` holds only for the official host.
- **New guardrail `duel_days_auto`**, default false, so today's behaviour holds. When true, a `signed` verdict turns `duel_days_signed` on for the tick.
  - The policy (`V2Params`) and the guard (`duel_inside_limit`) read the same rules object, so they always agree (#113).
  - v1 keeps #60's worst case in the guard.
- **`rival_days(duel)`** reads which end of 0–10 the rival prefers from its own offers. A 0 counts half: a rival may send 0 because it ignores days. It also gives a rough weight when the rival's price and days move together.
- **`choose_days` / `reprice` / `days_aware`** pick our day by joint weight and keep our value strictly inside our limit.
- **Zoo**: days-blind rivals (`days_fixed`).

## Findings

| truth | rivals | v1 | v2 | v2 signed | share05 | share05 signed |
|---|---|---|---|---|---|---|
| signed | each picks its end | 16.17 | 24.14 | **26.52** | 24.43 | **27.40** |
| signed | blind, always 0 | 13.67 | 20.44 | 21.10 | 21.13 | 21.69 |
| signed | blind, always 5 | 14.49 | 19.94 | 21.45 | 20.48 | 22.65 |
| worst | each picks its end | 14.49 | 19.91 | 17.34 (**197 outside**) | 21.32 | 17.44 (**226 outside**) |
| worst | blind, always 0 | 13.81 | 20.88 | 17.53 (185 outside) | 21.53 | 16.51 (259 outside) |
| worst | blind, always 5 | 13.40 | 15.95 | 13.43 (232 outside) | 18.02 | 14.21 (233 outside) |

Mean P per duel; "outside" counts deals outside our true limit, out of 2,400. Every other cell has 0 outside.

1. **If the simulator's sign is the real one, valuing it gains 3–10 % P per two-issue duel.**
   - v2: 24.14 → 26.52 (+9.9 %) when rivals pick their end; +7.6 % against blind rivals sending 5; +3.2 % against blind rivals sending 0.
   - share05: +12 % (24.43 → 27.40).
   - Deal rates are unchanged (0.80–0.82).
2. **If the sign is flipped wrongly, it is a disaster.**
   - 170–259 of 2,400 duels (7–11 %) close outside our true limit.
   - P falls 13–23 %.
   - The worst-case default is safe either way: 0 outside under both truths, at the cost of leaving the 3–10 % on the table. Hence the latch on real evidence only.
3. **The rival's days are informative when it handles days.**
   - Against rivals that pick their preferred end, `rival_days` names an end in 82 % of duels and is right in 100 % of those.
   - Against blind rivals sending 0 it is right only 50 % of the time; its confidence drops to 0.38, against 0.58 when right. Blind rivals sending 5 are never named.
4. **Choosing days by joint weight adds nothing measurable to v2** (26.47 vs 26.52). v2 mostly accepts the rival's offer, and when signed it already offers our own preferred end. `days_aware` stays available and is not wired.

## Caveats
- **The real `days_meaning` wording is unknown.** The parser is deliberately narrow: anything it cannot tie to a direction stays `unknown`, the safe side.
- **The zoo's rivals value days with the simulator's sign.** Real rivals' days behaviour is unseen: no real two-issue payload exists yet.
- **One draw, n = 200.** The +10 % / −13 % gaps are an order of magnitude above the ~0.1 P seed noise measured for the zoo (W2a report).

## What Marius must decide
1. **Set `duel_days_auto = true` before Duels II?** It changes nothing until a real payload confirms the sign, then it is worth about +3–10 % P per two-issue duel. It only matters with `duel_policy = v2`.
2. **Read the first real Duels II payload's `days_meaning` by eye**, e.g. with `bazaar duel run` logging to `.local/duels/duels.jsonl`. If its wording is clear but outside the parser's patterns, set `duel_days_signed` by hand.
