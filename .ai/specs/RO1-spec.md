# RO1 — Read-only Postgres login for teammates  (per-task spec)

- Task id: RO1 (coordinator brief, 2026-10-03)
- Status: 🔵 merged #184
- Backlog source: local (`.ai/specs`). Indexed in: [`02-plan.md`](./02-plan.md)

## Goal
Teammates browse the shared Railway Postgres (database `railway`, schema `public`) in DataGrip without being
able to change it or read a secret.

## Acceptance criteria
- [ ] 1. `src/bazaar_agent/sql/readonly_user.sql`, idempotent: role `bazaar_team_ro` LOGIN if missing, CONNECT on
  the database, USAGE on `public`, SELECT on all tables and sequences, default privileges for future ones, no
  write privilege, `default_transaction_read_only = on`, `statement_timeout = 30s`, connection limit 10.
- [ ] 2. Secret tables (`venue_broker_keys`) are never readable, also after a re-run.
- [ ] 3. `uv run bazaar db readonly-user` generates a strong password or reads one from stdin, applies the SQL with
  the admin DATABASE_URL (password never on a command line, in a statement or in a log), prints only the connection
  URL with the admin URL's host and port, once.
- [ ] 4. Tests on the local docker Postgres: the role can SELECT and cannot INSERT/UPDATE/DELETE/TRUNCATE/CREATE.
- [ ] 5. Teammate docs in `docs/services.md`.
- Out of scope: running it on Railway (the coordinator does, after the merge); revoking PUBLIC's TEMP/CONNECT.
