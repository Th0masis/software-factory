# Chapter 1 — Progress

- **Overall status:** CH1-01 complete; CH1-02 not started.
- **Current branch:** `docs/chapter-01-contract`
- **Contract:** [`chapter-01.md`](./chapter-01.md)
- **Design deltas:** [`design-deviations.md`](./design-deviations.md)

## Checkpoint summary

- **Completed:** Chapter 1 specification review and contract approval; CH1-01 metadata-only job creation, `status`, and `list`.
- **Verified:** `uv run --python 3.14 python -m unittest discover -s tests -v` (8 tests, PASS); `uv run --python 3.14 python -m compileall -q factory tests` (PASS). Job creation tests use temporary repositories.
- **Remaining:** Implement CH1-02, then CH1-03. TDD test authorship remains deferred to Chapter 6.
- **Blockers:** None.

## Slice status

| Slice | Status | Tests | Commit SHA | Notes |
|---|---|---|---|---|
| CH1-01 — Creating metadata-only jobs | Complete | PASS (8 tests) | Pending checkpoint | Metadata-only creation and public CLI queries |
| CH1-02 — Enforcing transitions and journal recovery | Not started | — | — | Depends on CH1-01 |
| CH1-03 — Attaching and recovering a direct worktree | Not started | — | — | Depends on CH1-01 and CH1-02 |

## CH1-01 verification

- **RED:** Invalid base branch was accepted; missing project path returned an operational error; invalid project config was not handled as a configuration error.
- **GREEN/regression:** `uv run --python 3.14 python -m unittest discover -s tests -v` — 8 tests passed.
- **Additional check:** `uv run --python 3.14 python -m compileall -q factory tests` — passed.
- **Reviewed scope:** CLI/package setup, strict config and job creation, temporary-repository CLI tests, factory config, ignore rule, and this contract/progress/deviation documentation.
- **Checkpoint SHA:** To be recorded after the CH1-01 commit.

## Checkpoint policy

After each slice, record the exact RED/GREEN/regression commands, results, reviewed file list, and verified commit SHA. Commit only after all required tests pass, using a focused Conventional Commit with the slice ID and verification summary in its body. A slice is not complete if a required test fails. Do not amend, push, or merge without explicit approval.
