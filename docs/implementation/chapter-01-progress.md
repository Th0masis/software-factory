# Chapter 1 — Progress

- **Overall status:** CH1-01, CH1-02, and CH1-03 complete.
- **Current branch:** `docs/chapter-01-contract`
- **Contract:** [`chapter-01.md`](./chapter-01.md)
- **Design deltas:** [`design-deviations.md`](./design-deviations.md)

## Checkpoint summary

- **Completed:** Chapter 1 specification review and contract approval; CH1-01 metadata-only job creation, CH1-02 state/recovery, and CH1-03 worktree lifecycle.
- **Verified:** All 29 tests pass; Git integration tests use temporary repositories.
- **Remaining:** TDD test authorship remains deferred to Chapter 6.
- **Blockers:** None.

## Slice status

| Slice | Status | Tests | Commit SHA | Notes |
|---|---|---|---|---|
| CH1-01 — Creating metadata-only jobs | Complete | PASS (8 tests) | `74fa133` | Metadata-only creation and public CLI queries |
| CH1-02 — Enforcing transitions and journal recovery | Complete | PASS (18 tests) | `fd539df` | Escalation/resume, validated journal replay, recovery, and process lock |
| CH1-03 — Attaching and recovering a direct worktree | Complete | PASS (28 tests) | `50421e0` | Attach, repair and clean-only release with interrupted-release recovery |

## CH1-01 verification

- **RED:** Invalid base branch was accepted; missing project path returned an operational error; invalid project config was not handled as a configuration error.
- **GREEN/regression:** `uv run --python 3.14 python -m unittest discover -s tests -v` — 8 tests passed.
- **Additional check:** `uv run --python 3.14 python -m compileall -q factory tests` — passed.
- **Reviewed scope:** CLI/package setup, strict config and job creation, temporary-repository CLI tests, factory config, ignore rule, and this contract/progress/deviation documentation.
- **Checkpoint SHA:** `74fa133`.

## CH1-02 verification

- **RED commands:** `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_transition_to_needs_human_persists_resume_state_and_event -v` (missing command); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_resume_returns_job_to_saved_state_and_appends_event -v` (missing command); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_state_transition_graph_accepts_only_defined_edges -v` (missing validator); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_status_rebuilds_stale_snapshot_from_event_log -v` (stale state returned); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_job_lock_serializes_separate_processes -v` (missing lock); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_status_rejects_event_with_invalid_timestamp -v` (invalid timestamp accepted).
- **Regression:** `uv run --python 3.14 python -m unittest discover -s tests -v` — 18 tests passed.
- **Additional checks:** `uv run --python 3.14 python -m compileall -q factory tests` and `git -c core.whitespace=cr-at-eol diff --check` — passed.
- **Recovery coverage:** Stale/invalid snapshots are rebuilt; incomplete/corrupt journal tails fail closed; event-committed/snapshot-failed state changes recover from the journal.
- **Reviewed scope:** CLI, state/event handling, per-job locking, focused temporary-repository tests, and this progress record.
- **Checkpoint SHA:** `fd539df`.

## CH1-03 verification

- **RED commands:** `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_worktree_attach_uses_existing_approved_local_branch -v` (missing command); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_worktree_repair_reattaches_missing_worktree_at_current_branch_head -v` (missing command); `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_worktree_release_refuses_dirty_worktree_without_changing_it -v` (release unavailable); and `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_worktree_repair_completes_interrupted_release tests.test_job_create.JobCreateCliTests.test_worktree_repair_finalizes_release_after_git_removal -v` (unsupported `releasing` journal state).
- **GREEN:** Added `attach`, `repair`, and `release`; release now journals its intent and repair completes a still-registered clean worktree or finalizes the journal after Git removal.
- **Acceptance coverage:** Attach requires `SPEC_APPROVED`, uses an existing local branch without changing its ref, and rejects invalid/missing/already-checked-out branches and occupied paths. Repair reattaches the existing branch at its current HEAD without reset, is idempotent, and preserves unregistered path collisions. When `base_sha` is already recorded, repair does not fetch again; see the follow-up below for interrupted fetch recovery. Release refuses dirty worktrees without changing their files/state; clean release removes the registered worktree and retains its branch. Interrupted release recovery is tested before/after Git removal, and a missing path still registered by Git fails closed.
- **Targeted GREEN commands:** `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_worktree_repair_completes_interrupted_release tests.test_job_create.JobCreateCliTests.test_worktree_repair_finalizes_release_after_git_removal tests.test_job_create.JobCreateCliTests.test_worktree_release_refuses_dirty_worktree_without_changing_it tests.test_job_create.JobCreateCliTests.test_worktree_release_removes_clean_worktree_and_keeps_branch -v` and `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_worktree_repair_rejects_missing_but_registered_release_path -v` — passed.
- **Regression:** `uv run --python 3.14 python -m unittest discover -s tests -v` — 28 tests passed.
- **Additional checks:** `uv run --python 3.14 python -m compileall -q factory tests` and `git -c core.whitespace=cr-at-eol diff --check` — passed.
- **Reviewed scope:** `factory/job.py`, `factory/cli.py`, `tests/test_job_create.py`, `chapter-01.md`, `design-deviations.md`, and this progress record; integration tests use temporary repositories and local bare remotes.
- **Checkpoint SHA:** `50421e0`.

## CH1-03 follow-up — interrupted base fetch

- **Decision:** if repair finds no recorded `base_sha`, retry the configured fetch and record its result before attaching; never infer fetch success from a cached remote-tracking ref. Repair does not fetch again when `base_sha` is already recorded.
- **TDD RED:** `uv run --python 3.14 python -m unittest tests.test_job_create.JobCreateCliTests.test_worktree_repair_retries_base_fetch_interrupted_during_attach -v` — failed because repair recorded the stale cached SHA.
- **GREEN/regression:** the same focused test passed; `uv run --python 3.14 python -m unittest discover -s tests -v` — 29 tests passed; `uv run --python 3.14 python -m compileall -q factory tests` and `git -c core.whitespace=cr-at-eol diff --check` — passed.
- **Commits:** `d74b914` (behavior-preserving journal persistence refactor), `ba016cc` (fetch retry and regression test).

## Checkpoint policy

After each slice, record the exact RED/GREEN/regression commands, results, reviewed file list, and verified commit SHA. Commit only after all required tests pass, using a focused Conventional Commit with the slice ID and verification summary in its body. A slice is not complete if a required test fails. Do not amend, push, or merge without explicit approval.
