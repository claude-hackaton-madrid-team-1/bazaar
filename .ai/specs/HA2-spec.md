# HA2 — Approve big trades from a chat (MCP) and from Bazaar Live

Source: Omar via the coordinator, Sat 3 Oct ~20:00: "You must create an MCP tool to approve this, and make this on
the bazaar-live." Builds on HA1 (`human_approvals`, `approval_needed` rows, `bazaar approve`). Local backlog.
The bazaar-live half is bazaar-live PR #53 (Approvals screen); this spec covers both, this repo ships the MCP half.

## Goal
Omar approves, denies or revokes a big trade from his chat (MCP) or from a Bazaar Live screen instead of a laptop
CLI, and no agent of ours can ever approve its own trade.

## Design (security is the design)
- `runtime/human_tools.py`: three specs, `approvals` (read), `approve`, `revoke` (writes), built on an
  `ApprovalStore` (`PgApprovalStore`: the shared Postgres through `approvals.py`, one short connection per call).
  They are NOT in `tools.TOOLS`: the desk's in-process server, the subagents' allow-lists, the desk's PreToolUse
  hook and `bazaar agent chat` never see them.
- `runtime/mcp_server.py`: served only on a request whose `X-Approver-Token` equals `BAZAAR_APPROVER_TOKEN`
  (sha256 digests, `hmac.compare_digest`), on top of the bearer token. An approver request sees ONLY the three
  human tools (it never reads counterparty text next to `approve`). Bearer alone: not listed, a call reads
  `unknown tool`. A wrong or empty approver token: `403 {"error": "forbidden"}` and a WARN line; no lockout (review
  of #241: a lock keyed on the shared bearer let any bearer holder lock the human out). `BAZAAR_APPROVER_TOKEN` unset, weak (the
  bearer's rule: 32+ chars, 16+ distinct) or equal to the bearer: the tools do not exist (every approver header is a
  403). It is read from the environment only (never `.env`) and scrubbed from every answer like the bearer.
- Validation: card `^[A-Z]{3}-\d{2}$` and in the catalog, side buy|sell, price int in [1, 1000], ttl in [1, 480]
  ticks, reason ≤ 300 chars, `via` a slug (`by` = `human:<via>`, default `human:mcp`), extra fields refused.
- An approval only lifts `human_approval_above`. `approve` refuses (status `refused`, reasons listed) a buy above
  the rarity cap (`max_price_<rarity>`, lifted only by `dealer_final_lift`), above `max_spend_per_game_hour`, above
  the official value of one more copy (`/api/me/value`, unreadable → refused), or of a rarity with no cap; a sell
  of a card we do not hold, of a page's last copy (`protect_page_sets`), or below `sell_min_value_ratio` × our value
  of the copy (unreadable → refused). /me unreadable: the tool fails and writes nothing.
- Writes capped at 10 per minute (server-wide), on top of the tool-call bucket; an approver request has its own
  HTTP and tool-call buckets (keyed on the approver digest), so a bearer holder cannot starve the human's revoke.
  A revoke never waits for a game read; an approve still checking when a revoke of its card and side comes in is
  refused at its write. Each change writes its audit row on the same connection;
  a stored reason is scrubbed of our secrets. A revoke (or a deny) marks the card+side's requests denied. A sell
  approval also releases a sale `max_score_loss_per_move` (MI1) holds.
- Every approve, refusal, revoke and denial writes a `decisions` row (agent `guard`, kinds `approval_granted`,
  `approval_refused`, `approval_revoked`, `approval_denied`), scrubbed, no secret.
- `approvals` answers the requests of the last 2 game hours (`approvals.PENDING_TICKS`) with state
  waiting|approved|denied, why the guardrail asked, our and the official value, album impact (held copies, page
  card, last copy), the cap no approval lifts, who asked (write kind + counterparty), the stale tick; plus the active
  approvals and the input limits.
- Railway: `BAZAAR_APPROVER_TOKEN` declared `preserve()` on bazaar-mcp and bazaar-live only; bazaar-live also
  `APPROVER_PASSWORD`, `BAZAAR_MCP_URL`, `BAZAAR_MCP_TOKEN`. The coordinator sets the values with `--stdin`.

## Acceptance criteria
1. The human tools are in no agent tool set (TOOLS, allow-lists, desk options, subagent definitions) and the desk
   hook denies them.
2. The bearer token alone neither lists nor runs them; `/health` says nothing about them.
3. A wrong approver token is a 403 and a WARN, never a lockout; unset → they do not exist; an approver request
   sees only the human tools.
4. The approver token must be strong and differ from the bearer; the serve command turns the tools off otherwise.
5. Every input is validated before anything is read or written.
6. Buys above a hard cap and sells of a last copy or below our value are refused and logged; others approved with
   `by` from the token's identity and logged.
7. Revoke removes an approval, else records a denial; the list shows each request's state.
8. Writes are capped at 10 per minute; no answer carries the approver token.
9. Railway declares the variables preserve() only where they belong.
10. Full gate passes; bazaar-live's screen (#53) calls these tools with this contract.
