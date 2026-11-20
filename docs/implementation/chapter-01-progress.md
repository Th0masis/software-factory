# Chapter 1 — Progress

- **Overall status:** Contract approved; implementation has not started.
- **Current branch:** `docs/chapter-01-contract`
- **Contract:** [`chapter-01.md`](./chapter-01.md)
- **Design deltas:** [`design-deviations.md`](./design-deviations.md)

## Checkpoint summary

- **Completed:** Chapter 1 specification review and implementation contract approval; direct-mode lifecycle and design deltas recorded.
- **Verified:** Repository was clean before work; branch was created from fetched `origin/main`. No production code, tests, or runtime data were changed.
- **Remaining:** Implement CH1-01, then proceed in dependency order. TDD test authorship remains deferred to Chapter 6.
- **Blockers:** None for CH1-01. No implementation slice has started.

## Slice status

| Slice | Status | Tests | Commit SHA | Notes |
|---|---|---|---|---|
| CH1-01 — Creating metadata-only jobs | Not started | — | — | Contract approved; ready |
| CH1-02 — Enforcing transitions and journal recovery | Not started | — | — | Depends on CH1-01 |
| CH1-03 — Attaching and recovering a direct worktree | Not started | — | — | Depends on CH1-01 and CH1-02 |

## Checkpoint policy

After each slice, record the exact RED/GREEN/regression commands, results, reviewed file list, and verified commit SHA. Commit only after all required tests pass, using a focused Conventional Commit with the slice ID and verification summary in its body. A slice is not complete if a required test fails. Do not amend, push, or merge without explicit approval.
