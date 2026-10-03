# HA1 — Human approval for big-price trades

Source: Omar's request via the coordinator, 2026-10-03 ("price negotiation for big prices with human approvals").
Local backlog (no external tracker).

## Goal
A card trade at a big price goes out only after a human approved it, without slowing the per-tick loop.

## Design
- `human_approval_above` (GUARDRAILS.md, 60 P; model default 0 = off). Any card buy (board accept, dealer bid or
  final, maker bid, team swap: cash plus the copy given) or card sell (maker ask, bid accept, dealer sell, swap
  leg) at or above it needs an approval.
- Pre-approval: a dealer's final lapses in 2 ticks, so a human cannot answer inside a tick. Table
  `human_approvals(card, side, max_price, min_price, until_tick, by, reason, created_at)`, key (card, side).
- `guardrails.check()` runs the rule after every other rule (and never for a ranking check), reading the table
  once per tick per process (`approvals.ApprovalBoard`, a `breakers.TickBoard`), 1 s budget, fail CLOSED.
- The first refusal per (card, side, game hour) writes an `approval_needed` decisions row and a WARN line.
- CLI: `bazaar approve <card> --buy --max P | --sell --min P [--ttl-ticks] [--reason] [--revoke]`,
  `bazaar approvals`. Every approve and revoke writes a decisions row.
- Duels and packs excluded; an approval never loosens another cap.

## Acceptance criteria
1. A big buy without approval is refused with `needs human approval: <card> <side> <price>`.
2. A covering approval lets it through; an expired one does not.
3. Sell side works with `min_price`.
4. Duels, packs and trades under the threshold are untouched.
5. Unreadable approvals fail closed, and the taker holds its dealer thread instead of walking.
6. One request row per (card, side, game hour), across processes.
7. CLI approves, lists and revokes, each writing a decisions row.
8. Full gate and the sim smoke pass.
