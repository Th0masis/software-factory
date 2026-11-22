# Chapter 3 — Progress

- **Overall status:** Implementation in progress; Chapter 3 regression acceptance is blocked.
- **Current branch:** `feat/chapter-03-gate`
- **Contract:** [`chapter-03.md`](./chapter-03.md)
- **Design deltas:** [`design-deviations.md`](./design-deviations.md)

## Checkpoint summary

- **Completed:** Chapter 3 specification review; implementation contract approved; task snapshots
  and review-repair task lifecycle clarified.
- **Verified:** Documentation whitespace checks passed; `design.md` is unchanged. The topic branch
  is local and based on fetched `origin/main`.
- **Current slice:** CH3-03 implementation is complete; its targeted tests pass.
- **Remaining:** Resolve or establish the baseline for the Runner timeout-test failures before
  declaring Chapter 3 complete.
- **Blockers:** `test_runner.py` has two failing 0.25-second timeout tests; baseline against
  pre-CH3-02 Runner code is unverified.

## Slice status

| Slice | Status | Tests | Commit SHA | Notes |
|---|---|---|---|---|
| CH3-01 — Reading changes and enforcing scope/protection | Complete | PASS (13 focused tests) | `42256b8` | Scope, base SHA, protected paths, reserved task input |
| CH3-02 — Running and validating project checks | Complete | PASS (29 focused Gate tests) | `ba560ae` | Depends on CH3-01 |
| CH3-03 — Producing a Gate report and exposing the CLI | Targeted tests pass; chapter acceptance blocked | PASS (8 CLI integration tests) | — | Existing Runner regression tests fail |

## Baseline and contract checkpoint

- **Contract approval:** User approved `docs/implementation/chapter-03.md` on 2026-11-22.
- **CH3-01 RED:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -v`
  failed with `ModuleNotFoundError: No module named 'factory.gate'`, as expected. The test creates
  a temporary Git repository, modifies an in-scope tracked file, and adds an out-of-scope untracked
  file.
- **CH3-01 GREEN:** The same focused command passed after adding `assess_changes` and Git
  collection for tracked and untracked paths — 1 test passed.
- **Protected-path RED:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -k change_to_protected_path -v`
  failed because the assessment returned PASS for a protected new test file.
- **Protected-path GREEN:** The same focused command passed after loading protected patterns from
  `.factory/protected.txt` at `base_sha` and matching the new file.
- **Task-input RED:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -k untracked_task_file -v`
  failed because the untracked factory task input was incorrectly reported outside scope.
- **Task-input GREEN:** The same focused command passed after excluding only the exact
  untracked `.factory/task.md` path.
- **Staged-task RED:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -k staged_task_file -v`
  failed because the task path was not yet included in built-in protected paths.
- **Staged-task GREEN:** The same focused command passed after adding `.factory/task.md` to the
  built-in protected patterns.
- **Built-in-path RED:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -k project_configuration_and_checks_script -v`
  failed because `.factory/project.json` was not identified as protected.
- **Built-in-path GREEN:** The same command passed after adding `.factory/project.json`,
  `.factory/checks.py`, and `.factory/protected.txt` to the built-in protected paths.
- **Base-SHA RED:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -k invalid_base_sha -v`
  reached Git with an invalid revision and raised `FactoryError` instead of rejecting invalid
  input as `ConfigurationError`.
- **Base-SHA GREEN:** The same command passed after validating full commit SHA syntax and
  resolving it before collecting changes. `-k base_sha_must_be_an_ancestor` also passed after
  enforcing the ancestor requirement.
- **Pattern validation RED/GREEN:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -k unsafe_scope_patterns -v`
  first failed for repeated/trailing separators and a partial-component `**`; it passed after
  rejecting malformed path components and requiring `**` to occupy a whole component.
- **Path behavior verified:** `-k double_star_matches`, `-k single_star_does_not`,
  `-k protected_patterns_are_read_from_base`, and `-k rename_checks_both` passed. These verify
  normalized glob semantics, trusted baseline protection rules, and both rename endpoints.
- **Change-set behavior verified:** `-k change_set_includes` passed with committed, staged,
  unstaged, and untracked paths present together.
- **Focused regression:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -v`
  — 13 tests passed.
- **Reviewed scope so far:** `factory/gate.py`, `tests/test_gate.py`, and this progress file.
- **CH3-01 commit:** `42256b8` (`feat: add Gate change assessment`); progress checkpoint:
  `a2654a4` (`docs: record CH3-01 checkpoint`).
- **CH3-02 RED:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -k valid_passing_checks_report -v`
  failed because `factory.gate.run_checks` is not implemented. The first check-runner test uses a
  temporary Git worktree and a valid passing JSON report.
- **CH3-02 configuration:** Added `gate.timeout_seconds` to factory configuration with a
  1800-second default and positive-value validation.
- **CH3-02 GREEN:** The valid-report test passed after adding the checks process seam, script
  integrity validation, JSON report parsing, and timeout-bounded execution. The shared runner
  launcher now accepts an explicit environment so Gate can remove configured secret variables.
- **CH3-02 report validation:** Tests passed for nonzero process exit, reported check failure,
  contradictory status/exit-code pairs, malformed/missing JSON, duplicate JSON keys and check
  names, non-standard JSON constants, and non-integer exit codes. The malformed report test first
  exposed an uncaught `TypeError` for a list-valued status; validation now rejects non-string
  statuses explicitly.
- **CH3-02 report preservation:** A RED test showed valid extra JSON fields were discarded from
  `ChecksResult`. The result now preserves the complete validated report object for the later
  durable Gate report.
- **CH3-02 input validation:** Tests passed for repository-root enforcement and rejection of a
  missing checks script.
- **CH3-02 execution behavior:** Tests passed for rejecting a changed checks script before launch,
  invalid modes and secret names, removing a configured secret, forwarding `--full`, handling
  launch failure, and terminating a child process on timeout while preserving partial output. The
  Windows timeout-child test passed.
- **CH3-02 fail result:** Contract review showed that a missing or modified checks script must
  become a Gate FAIL result (for the future report), not escape as a configuration exception.
  RED tests reproduced both exceptions; `run_checks` now returns FAIL and does not execute changed
  script content.
- **Platform output detail:** Assertions treat captured line endings platform-independently; the
  initial byte-for-byte LF expectation was incorrect for Windows and was corrected.
- **Regression runs:** `test_gate.py` — 29 tests passed. `test_job_create.py` — 29 tests passed.
  The existing Runner successful-contained-process test also passed after the launcher update.
  `test_runner.py` completed with 2 failures and 1 skipped; both failures were in 0.25-second
  timeout tests that did not capture their startup marker. Rerunning each test in isolation
  reproduced the failures. The Runner launcher preserves its previous arguments when no explicit
  environment is supplied; the failures appear tied to the tests' short startup timeout, but the
  baseline status remains unverified.
- **Latest focused check:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -v`
  — all 29 tests passed after the changed/missing-script FAIL behavior and strict JSON validation.
- **CH3-03 RED:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate_cli.py -v`
  failed because `factory job gate` is not registered. Its integration fixture creates an attached
  job with a passing target checks script and asserts persisted report output plus unchanged job
  snapshot/event log.
- **CH3-03 GREEN:** The same focused integration test passed after adding `evaluate_gate`, unique
  attempt artifacts, and the `factory job gate JOB_ID --base SHA --mode fast|full` CLI path.
  The report/log files were persisted and `job.json`/`events.jsonl` remained byte-for-byte unchanged.
- **CH3-03 failure paths:** Focused CLI tests passed for out-of-scope changes (checks skipped),
  failing project checks (nonzero exit and persisted report), and distinct immutable attempt
  directories on rerun.
- **CH3-03 validation:** Protected-path failures are reported without running checks; invalid
  scope input returns usage error without creating an attempt. Job snapshot/event bytes remain
  unchanged on passing and failing attempts.
- **CH3-03 missing-job RED:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate_cli.py -k missing_job_is_a_usage_error -v`
  returned exit `1` for an unknown job. The contract classifies an invalid job as a usage/configuration
  error (exit `2`).
- **CH3-03 missing-job GREEN:** The same focused command passed after mapping `job not found` to a
  configuration error.
- **CH3-03 scope-file validation:** A focused CLI test passed for rejecting a non-regular
  `scope.json` before creating an attempt directory.
- **CH3-03 regression:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate_cli.py -v`
  — all 8 CLI integration tests passed.
- **CH3-03 Gate regression:** `uv run --python 3.14 python -m unittest discover -s tests -p test_gate.py -v`
  — all 29 unit and process tests passed.
- **CH3-03 scope reviewed:** `factory/cli.py`, `factory/gate.py`, `tests/test_gate_cli.py`, and this
  progress record. `design.md` remains unchanged.
- **Reviewed scope:** `factory/job.py`, `factory/runner.py`, `factory/gate.py`,
  `tests/test_gate.py`, and this progress file.
- **Documentation checks:** `git -c core.whitespace=cr-at-eol diff --check` passed.
  `design.md` remained unchanged.
- **Chapter 1/2 regression gate:** `uv run --python 3.14 python -m unittest discover -s tests -p test_runner.py -v`
  completed with 2 failures and 1 skip. Both failures are 0.25-second timeout tests that did not
  capture the child startup marker; each also failed when rerun alone. The prior
  `test_job_create.py` run passed all 29 tests. The CH3-02 Runner change preserves the original
  process arguments when no environment is supplied, but a pre-CH3-02 baseline was not run;
  Chapter 3 regression acceptance therefore remains unverified.
- **Reviewed scope:** `docs/implementation/chapter-03.md`, this progress file,
  `docs/implementation/design-deviations.md`, and `docs/README.md`.
- **CH3-03 commit:** Not created yet.

## Checkpoint policy

After each slice, record exact RED/GREEN/regression commands, results, reviewed files, and commit
SHA. A slice is complete only after its acceptance checks pass and its diff is reviewed. Do not
push the branch. Follow repository instructions: do not add a Copilot co-author trailer.
