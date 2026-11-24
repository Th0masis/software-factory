# Chapter 4 — Progress

- **Overall status:** In progress; CH4-01 through CH4-03 are complete, and CH4-04 through CH4-05
  remain pending.
- **Current branch:** `feat/chapter-04-runner-security`
- **Contract:** [`chapter-04.md`](./chapter-04.md)
- **Design deltas:** [`design-deviations.md`](./design-deviations.md)

## Checkpoint summary

- **Completed:** Chapter 4 contract approved on 2026-11-23; DD-23 and DD-24 recorded as approved.
- **Verified:** Fetched `origin/main` is already contained in the feature branch; documentation
  diff check passes.
- **Current slice:** CH4-04 — recovering interrupted slices safely.
- **Remaining:** CH4-04 and CH4-05.
- **Blockers:** None.

## Slice status

| Slice | Status | Tests | Commit SHA | Notes |
|---|---|---|---|---|
| CH4-01 — Registering human-approved M1 inputs | Complete | 40 job CLI, 30 Gate service, 8 Gate CLI tests passed; 7 new registration cases | `8c2d84f` | Contract mapping prerequisite moved to CH4-02 |
| CH4-02 — Initializing and locking the slice plan | Complete | 50 job CLI tests passed; 75 Gate/Runner regressions passed (1 skipped) | — | Depends on CH4-01 |
| CH4-03 — Building and gating one TDD slice | Complete | 127 passed, 1 skipped; 2 focused tests passed | — | Depends on CH4-02 |
| CH4-04 — Recovering interrupted slices safely | Pending | — | — | Depends on CH4-01 through CH4-03 |
| CH4-05 — Completing the job and entering review | Pending | — | — | Depends on CH4-03 and CH4-04 |

## CH4-01 — Registering human-approved M1 inputs

- **Test seam:** `factory job prepare JOB_ID --stage spec` and `--stage tests`, exercised as
  subprocesses; verify resulting state and last event through `factory job status`.
- **RED:** `py -3.14 -m unittest tests.test_job_create.JobCreateCliTests.test_prepare_approved_spec_records_hash_and_advances_state -v`
  first could not import Pydantic from the global interpreter; rerun with the repository's
  `.venv\Lib\site-packages` on `PYTHONPATH` and it failed as expected because `job prepare` is
  unregistered.
- **GREEN:** The same focused test passed after adding `job prepare --stage spec`. Its hash
  expectation was made byte-deterministic after Windows translated fixture newlines.
- **Integrity RED/GREEN:** Status initially passed despite a modified registered spec; event replay
  now validates the stored spec hash. Tampering is rejected without rewriting the event journal.
- **Approval RED/GREEN:** Registration initially succeeded without `--approve`; it now fails with
  no state, journal, or artifact change. Re-registration after approval is also rejected.
- **Reviewed scope:** `factory/job.py`, `factory/cli.py`, `tests/test_job_create.py`, and the Chapter 4
  contract/progress plus Chapter 3 contract.
- **Decision confirmed:** `tests.json` contains stable IDs and repository-relative paths; the
  approved slice plan maps test IDs to slices. The target checks script runs baseline-red and binds
  its report to the manifest hash and `work_branch_start_sha`.
- **Test-registration RED:** `test_prepare_approved_tests_records_manifest_after_baseline_red`
  fails as expected because `prepare --stage tests` is not registered by the CLI.
- **Test-registration GREEN:** Seven cases pass: successful registration, baseline-red failure/retry
  with the manifest locked, mismatched report hash, missing approval, unsafe test paths, mutation
  during checks, and post-registration tampering.
- **Regression:** `py -3.14 -m unittest tests.test_job_create -v` — 40 passed;
  `tests.test_gate` — 30 passed; `tests.test_gate_cli` — 8 passed.
- **Additional checks:** `py -3.14 -m compileall -q factory tests` and
  `git -c core.whitespace=cr-at-eol diff --check` — passed.
- **Contract clarification:** Exact test-to-slice mapping validation moved to CH4-02, before plan
  approval, to avoid requiring the plan before CH4-01 can complete.
- **CH4-01 result:** Human-approved spec and test-manifest registration is durable, validated, and
  hash-locked. Baseline-red failures preserve diagnostics and allow only an identical manifest retry.

## CH4-02 — Initializing and locking the slice plan

- **Test seam:** `factory job initialize JOB_ID --profile PROFILE` and
  `factory job prepare JOB_ID --stage plan --path PATH --init-script PATH --approve`, exercised
  through the CLI with a fake Runner result.
- **Output protocol decision:** The initializer's final `assistant.message` is one JSON object with
  exactly `plan` and `init_script`. The response is extracted from Runner JSONL; the initializer
  cannot write proposal files into the target worktree.
- **Plan contract:** Schema version 1 with ordered features and slices; slice paths are exact
  repository-relative paths. Every registered test ID must appear once, and all slice paths and
  `.factory/init.py` must be covered by the approved scope.
- **RED/GREEN:** The first initializer CLI test failed because the command was not registered.
  Initialization now stores attempt-scoped pending plan/script artifacts, pins their hashes and the
  scope/task snapshot in the journal, and leaves the job at `TESTS_RED_OK` pending human approval.
- **Failure/recovery:** A Runner timeout initially left the job in `TESTS_RED_OK`; it now records
  diagnostics and moves to `NEEDS_HUMAN`. A retry requires explicit resume and uses a new attempt.
  Malformed JSON, invalid run directories, and target-worktree changes also fail closed; target
  changes are preserved.
- **Plan validation:** The unmapped-test case failed before `prepare --stage plan` was implemented.
  Approval now rejects unknown or missing test IDs and out-of-scope paths, requires explicit
  `--approve`, locks plan/script hashes, and commits only `.factory/init.py`.
- **Projection boundary:** CH4-02 stores the locked plan in the factory job directory and commits
  only `.factory/init.py`; `features.json` and `progress.md` projection/recovery work remains in
  CH4-03/CH4-04 as scoped by their contract sections.
- **Verification:** `py -3.14 -m unittest tests.test_job_create -v` — 50 passed;
  `py -3.14 -m unittest tests.test_gate tests.test_gate_cli tests.test_runner -v` — 75 passed,
  1 skipped because Windows symlink creation requires a privilege; `py -3.14 -m compileall -q
  factory tests` and `git -c core.whitespace=cr-at-eol diff --check` passed.
- **CH4-02 result:** Initializer output is attempt-pinned, human approval locks the validated plan
  and script, and invalid results escalate durably without discarding target changes. Diff review
  confirmed only the approved contract/progress and CH4-02 implementation/test files changed.

## CH4-03 — Building and gating one TDD slice

- **Test seam:** The user approved exercising `factory job run JOB_ID --profile PROFILE` through
  the public CLI with a fake Runner and a temporary Git project using the real Gate/checks path.
  Verify state and journal through `factory job status`, plus the immutable Gate report.
- **Verified before implementation:** The topic branch is clean and contains fetched `origin/main`.
  The current CLI has no `job run` command, and Gate has no selected-slice mode yet.

- **RED/GREEN:** The first `job run` test failed because the command was not registered. Added
  sequential slice execution, per-slice Gate selection, pass journaling/projections, and failure
  escalation. The selected-slice failure, two-slice pass sequencing, dirty-worktree, and interrupted
  session cases now pass. A multiline handoff projection test failed before whitespace flattening
  and passed after the handoff was reduced to one Markdown line.
- **Gate evidence:** Temporary-project checks confirmed the Gate report contains only the selected
  slice's registered test ID. A failed Gate leaves the slice unpassed, starts no next session, and
  escalates to `NEEDS_HUMAN`; a passing Gate records each slice once and proceeds sequentially.
- **Verification:** `py -3.14 -m unittest tests.test_job_create tests.test_gate tests.test_gate_cli
  tests.test_runner -v` — 127 passed, 1 skipped (Windows symlink privilege). The focused
  `test_run_does_not_restart_an_interrupted_slice_session` and updated two-slice projection test
  also passed after their latest changes.
  `py -3.14 -m compileall -q factory tests` passed.
- **Unverified and to verify:** No real Copilot builder session or target-project `checks.py` has
  been run; the runner and checks protocol were exercised with fakes and a temporary project.
  Initializer execution succeeded in the temporary project, but timeout behavior and compatibility
  with the deployment's Python executable/environment remain unverified. `sys.executable` is still
  the implementation assumption. Recovery after interruption is not implemented in CH4-03 and
  remains CH4-04 work; current behavior refuses to restart an uncertain session.
- **CH4-03 result:** Each slice is initialized, run once, independently gated against its registered
  tests, and journaled as passed only after Gate PASS. The worktree cleanliness, selected test IDs,
  failure stop, successful sequencing, no-restart interruption behavior, and single-line handoff
  projection are verified.
