# 02 — Task backlog & plan (per-task index)

> **Template.** The local backlog: one row per TASK, each running the full per-task lifecycle
> (`/spec → /plan → /build → /test → /review`). Written/updated by `/plan`. If the repo uses an
> external backlog (Linear / GitHub Projects), THAT is the source of truth — keep this file as a
> thin local mirror and use the external ids. Otherwise tasks use local ids `A1, A2, B1, …`
> (letter = feature/epic, number = task) and each has a `.ai/specs/<id>-spec.md`. `✅ = impl +
> passing test, evidence pasted`. Delete this note once filled.

- Status: <!-- TODO: draft | active -->
- Backlog source: <!-- TODO: local (.ai/specs) | Linear | GitHub Projects -->
- Derives from: [`01-spec.md`](./01-spec.md)

## Task index
| Task id | Title | Spec | Status |
|---------|-------|------|--------|
| A1 | <!-- TODO --> | [`A1-spec.md`](./A1-spec.md) | ⬜ todo |
<!-- Add A2, B1, … (local), or external ids ENG-421 / #123 with a link to the ticket in place
of the local spec link. -->

Status legend: ⬜ todo · 🔵 in progress · ✅ done (impl + passing test) · 🚫 blocked.

---

## Per-task steps
<!-- For each task, decompose into dependency-ordered vertical steps. Each step's Acceptance is
the exact check that proves it — and the row it gets in the /build Honest Implementation Report. -->

### A1 — <task title>  ⟸ start here
Files: <!-- TODO: the files this task touches -->
- Step 1 — <!-- TODO: what it does -->  · **Acceptance:** <!-- exact test/check that proves it -->
- Step 2 — <!-- TODO -->  · **Acceptance:** <!-- TODO -->

<!-- Repeat per task (A2, B1, …). -->

---

## Parallel-work notes (optional)
<!-- TODO: which tasks are file-disjoint and safe to build in parallel by an Agent Team /
sub-agents; which are too coupled or trivial to parallelize. -->
