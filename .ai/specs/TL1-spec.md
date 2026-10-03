# TL1 — The Workshop hardened on SA1  (per-task spec)

- Task id: TL1 (the #236 reviews; #236 closed, SA1 + #239 + #252 are on main)
- Status: 🔵 PR #259 (feat/taller-harden)
- Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
What main still lacks for the Workshop (`POST /api/taller`, SA1's `agents/taller.py`), smallest first.

## Acceptance criteria
1. The taker reads /me and our offers again right before a craft and plans on them. An accept of this or the last
   two ticks that cannot name its copy (`team:<thread>`, any `<kind>:<id>` but `sell:`/`duel:`) holds every
   craft: before any request in the taker, and in `guardrails.check` (`Context.taller_hold`) for the CLI too.
2. One hourly cap for every process, the CLI included: a craft is booked in the shared ledger before its send
   (kind `spend`, price 0, item `taller:<refs>`: the table only takes spend, accept and listing), counted by prefix.
3. The taker waits while `deploy_guard.verdict` is unsafe (a live duel near its deadline, a Market Test, a scheduled
   event), until its next safe tick.
4. `max_score_loss_per_move` credits nothing for the card a craft brings (a pull is luck and never scores): a craft
   of team-bought copies stays refused unless a human approves it. A triple kept back (or a dry run) rests 10 ticks
   with no request; `bazaar taller` uses the taker's busy set (`agents.taller.busy_copies`).
5. Tests with no network for each, including the Postgres ledger's kind check (fake) and the CLI.

## Out of scope
`max_copies_kept`, the maker's hold on common asks, the MCP tool.
