# Chapter 1 — Progress

- **Overall status:** CH1-01 and CH1-02 complete; CH1-03 not started.
- **Current branch:** `docs/chapter-01-contract`
- **Contract:** [`chapter-01.md`](./chapter-01.md)
- **Design deltas:** [`design-deviations.md`](./design-deviations.md)

## Checkpoint summary

- **Completed:** Chapter 1 specification review and contract approval; CH1-01 metadata-only job creation, `status`, and `list`.
- **Verified:** CH1-01 and CH1-02 acceptance checks pass; all tests use temporary repositories.
- **Remaining:** Implement CH1-03. TDD test authorship remains deferred to Chapter 6.
- **Blockers:** None.

## Slice status

| Slice | Status | Tests | Commit SHA | Notes |
|---|---|---|---|---|
| CH1-01 — Creating metadata-only jobs | Complete | PASS (8 tests) | `4be2bde` | Metadata-only creation and public CLI queries |
| CH1-02 — Enforcing transitions and journal recovery | Complete | PASS (18 tests) | `7aed742` | Escalation/resume, validated journal replay, recovery, and process lock |
| CH1-03 — Attaching and recovering a direct worktree | Not started | — | — | Depends on CH1-01 and CH1-02 |

## CH1-01 verification

- **RED:** Invalid base branch was accepted; missing project path returned an operational error; invalid project config was not handled as a configuration error.
- **GREEN/regression:** `uv run --python 3.14 python -m unittest discover -s tests -v` — 8 tests passed.
- **Additional check:** `uv run --python 3.14 python -m compileall -q factory tests` — passed.
- **Reviewed scope:** CLI/package setup, strict config and job creation, temporary-repository CLI tests, factory config, ignore rule, and this contract/progress/deviation documentation.
- **Checkpoint SHA:** `4be2bde`.

## CH1-02 verification

- **RED commands:** `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_transition_to_needs_human_persists_resume_state_and_event -v` (missing command); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_resume_returns_job_to_saved_state_and_appends_event -v` (missing command); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_state_transition_graph_accepts_only_defined_edges -v` (missing validator); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_status_rebuilds_stale_snapshot_from_event_log -v` (stale state returned); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_job_lock_serializes_separate_processes -v` (missing lock); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_status_rejects_event_with_invalid_timestamp -v` (invalid timestamp accepted).
- **Regression:** `uv run --python 3.14 python -m unittest discover -s tests -v` — 18 tests passed.
- **Additional checks:** `uv run --python 3.14 python -m compileall -q factory tests` and `git -c core.whitespace=cr-at-eol diff --check` — passed.
- **Recovery coverage:** Stale/invalid snapshots are rebuilt; incomplete/corrupt journal tails fail closed; event-committed/snapshot-failed state changes recover from the journal.
- **Reviewed scope:** CLI, state/event handling, per-job locking, focused temporary-repository tests, and this progress record.
- **Checkpoint SHA:** `7aed742`.

## Checkpoint policy

After each slice, record the exact RED/GREEN/regression commands, results, reviewed file list, and verified commit SHA. Commit only after all required tests pass, using a focused Conventional Commit with the slice ID and verification summary in its body. A slice is not complete if a required test fails. Do not amend, push, or merge without explicit approval.
