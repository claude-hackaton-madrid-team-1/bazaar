# N3 — Learner / auto-evolve with a hybrid RAG  (per-task spec)

- Task id: N3 (local; indexed in [`02-plan.md`](./02-plan.md))
- Status: **approved by the coordinator brief (2026-10-03, P0)**; delivered in small stacked PRs
- Backlog source: local (`.ai/specs`); the coordinator's brief is the source text
- Traces up to: [`01-spec.md`](./01-spec.md) §3 step 6 (feedback), §5 (`learnings`, `trader_behaviors`,
  embeddings), §7.5 (numbers from SQL, analogies from vectors), §9 (untrusted text)
- Scope split with N12 (coordinator, 2026-10-03): N3 owns embeddings, `trader_behaviors`, the single
  hybrid `recall()`, lessons from outcomes, auto-evolve, the Jev/words context and the MCP read tool. N12
  keeps the feed reader, the blockers, the LLM pass over free text and fee notices. N3 stacks on #89 and
  extends its `Learning` / `LearningStore` (kinds `lesson`, `policy`; source `outcome`).

## Goal
The agents learn from their own errors so they close every dealer trade, and at better prices. Every
settled decision becomes a structured lesson. The lesson is recalled at the moment of a similar decision.
Dealer outcomes also move the ladder parameters the taker uses, inside GUARDRAILS.md and logged with
their evidence.

## Constraints
Python only. No new service and no graph DB: it runs in the existing Railway agents and Postgres.
Ticks only, no wall clock. Recall must answer inside the tick budget (Saturday 30 s, Sunday 15 s) and fail
open. Counterparty text is never stored in a lesson and never becomes an instruction. Never raise a
guardrail cap.

## PR A — lessons + hybrid recall
1. `learn/curves.py`: per (dealer, price class), from every team's public threads: fills, opening ask,
   patience before the final, concession per bid, ignored first bids.
2. `learn/lessons.py`: one `lesson` per scored outcome (dealer thread, duel, team trade), with situation
   features, action, outcome, delta vs expected and one advice sentence. One `behaviour` per dealer ×
   class.
3. `learn/behaviours.py`: each dealer move goes to `trader_behaviors` (dedupe key per feed event).
4. `learn/embed.py`: fastembed `BAAI/bge-small-en-v1.5` (384-d) and `Xenova/ms-marco-MiniLM-L-6-v2`.
   Both load in the background and fail open.
5. `learn/recall.py`: the hybrid `recall()`. Hard filters, then BM25 + pgvector cosine, then RRF
   (k = 60), then a cross-encoder rerank of the top 12 with a floor of 0. It runs on a worker thread
   under a deadline and returns no lessons on any error.
6. `learn/outcomes.py`: the pass. It runs in the taker every 5 ticks on a worker, after the sends.
7. `bazaar learnings --lessons [--save]` and `--query "..."`.

## PR B — auto-evolve
Learned ladder parameters per dealer × price class (start, step, walk point): bounded updates, logged with
their evidence, inside the GUARDRAILS caps. A class priced above our cap is skipped instead of opened.
Lessons go into Jev's state and the words context as quoted data. An MCP read tool. Proof: a replay of
the real threads with old vs learned parameters, a simulator end-to-end run, and evals before/after.

## Acceptance criteria (PR A)
1. Real outcomes become lessons whose text names situation, action, result and advice. Fixtures:
   threads 85, 99, 101, 115, 187 and duels.
2. A pass is idempotent: re-running writes no duplicate lesson, behaviour or move.
3. `recall()` returns only valid, relevant lessons (hard filters + reranker floor). On real data the
   right lesson ranks first.
4. Recall answers inside its budget, or returns nothing (timeout, models loading, error).
5. The taker starts a pass after its tick and never waits for it.
6. Gate green: black, ruff, mypy, pytest (≥ 80 % on the new modules).

## Acceptance criteria (PR B)
1. Per dealer × buy class, a learned ladder (start, step, walk). It is searched by replay on every
   team's real threads, inside the GUARDRAILS cap, and moves at most 3 P per pass. Each change is
   logged with its previous values, evidence and history.
2. A class that cannot close under our cap is skipped, from fills above the cap or from walks at the
   cap. The taker records a `dealer_skip` and opens the next buy. A learned ladder never raises the
   strategy's top.
3. Lessons reach Jev's `offer_is_worth_accepting`, `duel_move` and `list_price_choice` states and the
   dealer bid words as quoted data. No lessons leaves the state unchanged.
4. `bazaar learnings --policy` and the MCP tool `learnings` show the ladders and the replay.
5. Proof: a replay on Friday's real threads (today vs learned), and an end-to-end simulator run.
6. Gate green.

## Plan (steps, in order)
A1 curves + lessons + behaviours → A2 embeddings + hybrid recall → A3 learner pass in the taker → A4 CLI,
docs → review fixes (#96) → B1 replay + evolve (grid search, bounded moves, skip) → B2 taker applies
ladders → B3 lessons into Jev + words → B4 MCP tool + `--policy` → B5 simulator e2e → B6 docs, review.
