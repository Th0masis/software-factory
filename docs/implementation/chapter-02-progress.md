# Chapter 2 — Progress

- **Overall status:** CH2-04 follow-up verified; commit pending.
- **Current branch:** `feature/chapter-02`
- **Contract:** [`chapter-02.md`](./chapter-02.md)
- **Design deltas:** [`design-deviations.md`](./design-deviations.md)

## Checkpoint summary

- **Completed:** Contract approved; CH2-01 command construction and validation; CH2-02 process execution and artifact persistence; CH2-03 usage parsing and outcome classification; CH2-04 preflight and Windows timeout recovery; CH2-05 runner integration and regression.
- **Verified:** `config.json` defines the approved tool allow-list, mandatory `shell(git push)` denial, secret environment variable names, and two builder profiles. The installed Copilot CLI supports the required command flags and `--name`.
- **Remaining:** Commit the verified preflight and Windows timeout recovery follow-up.
- **Blockers:** The environment cannot create native file symlinks (`WinError 1314`); the symlink guard passed a filesystem-boundary simulation instead.

## Slice status

| Slice | Status | Tests | Commit SHA | Notes |
|---|---|---|---|---|
| CH2-01 — Validating profiles and constructing a safe command | Complete | PASS (9 focused tests; 1 platform skip) | `d6aed01` | Exact command arguments, profile pinning, permission boundaries, path validation, cap threshold, and unique run directories verified. |
| CH2-02 — Executing a fake Copilot process and preserving artifacts | Complete | PASS (15 focused tests; 1 platform skip) | `e4ebdbf` | Fake-process success, nonzero exit with partial output, launch failure, and missing-artifact failure. |
| CH2-03 — Parsing usage and classifying outcomes | Complete | PASS (25 focused tests; 1 platform skip) | `b8552c3` | Usage validation, model matching, budget signals, malformed output, fail-closed cases, and timeout precedence. |
| CH2-04 — Preflight and Windows timeout recovery | Follow-up verified; commit pending | Focused follow-up tests passed; 1 platform skip | `8551d08` | Separate authentication check, enforced preflight, and Windows Job Object containment implemented. |
| CH2-05 — Runner integration contract and regression | Follow-up verified | Full suite: 66 tests, 65 passed, 1 platform skip | `d9bc931` | Full suite passed after CH2-04 fixes. |

## CH2-01 checkpoint

- **RED:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -v` failed because `factory.runner` did not exist. The system Python launcher could not import the installed Pydantic dependency, so verification used the repository's existing virtual environment.
- **GREEN:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -v` — 9 tests passed; the native symlink test was skipped because creating links requires a privilege unavailable in this environment. `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k filesystem_reported -v` — filesystem-boundary symlink rejection test passed.
- **Regression:** `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 38 tests passed; 1 native symlink test skipped.
- **Additional checks:** `.venv\Scripts\python.exe -m compileall -q factory tests` and `git -c core.whitespace=cr-at-eol diff --check` — passed.
- **Implementation:** Added argument-array command construction with resolved executable, pinned configured profile, approved flags, required `git push` denial, cap omission below 30, task/worktree validation, and atomic unique per-job run-directory allocation. Job identity is checked from the authoritative journal without rebuilding or mutating `job.json`.
- **Reviewed scope:** `factory/runner.py`, `tests/test_runner.py`, and this progress record.
- **Checkpoint SHA:** `d6aed01`.

## CH2-02 checkpoint

- **RED:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k returns_success_with_persisted_process_artifacts -v` failed because `run_agent` did not exist. `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k non_path_task_file -v` failed with an uncaught `TypeError`.
- **GREEN:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -v` — 15 passed, 1 native symlink test skipped. `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k filesystem_reported -v` — passed. The invalid path test passed after path errors were converted to `ConfigurationError`.
- **Transient test issue:** The first focused-suite attempt hit `PermissionError` from Chapter 1's `os.replace` during temporary worktree setup; the isolated test and complete focused rerun passed.
- **Implementation:** Added `RunResult`, `run_agent`, argument-array `Popen(shell=False)`, stdout/stderr files, and explicit process/artifact failure results. Timeout currently terminates only the direct process; CH2-04 owns Windows process-tree termination.
- **Regression:** Full suite not rerun for CH2-02 yet.
- **Reviewed scope:** `factory/runner.py`, `tests/test_runner.py`, and this progress record.
- **Checkpoint SHA:** `e4ebdbf`.

## CH2-03 checkpoint

- **Technical check:** Local `copilot help billing` does not document the JSON usage schema. Microsoft VS Code source represents `modelMetrics` as a model-keyed object and uses a string `currentModel`; implementation accepts `currentModel` or exactly one model key and fails closed on ambiguity.
- **RED:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k parses_usage_and_preserves_unknown_fields -v` failed because usage was returned as an unvalidated dictionary. `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k usage_at_cap -v` failed because reaching the cap returned `OK`.
- **Implementation started:** Added strict nano-AIU/model parsing, preserved raw unknown usage fields, JSONL event inspection, model matching, and ordered TIMEOUT/BUDGET/OK/ERROR classification.
- **Test-harness correction:** The first expanded focused run had 10 errors because its fake `Popen` helper used positional parameter names that did not match the runner's keyword arguments. Updated the helper to accept `stdout`/`stderr`; no production-code failure was involved.
- **GREEN:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -v` — 24 tests passed; the native symlink test was skipped because Windows symlink creation requires an unavailable privilege. Added an explicit timeout-over-budget precedence case, then reran the same command — 25 tests passed, 1 platform skip.
- **Additional checks:** `.venv\Scripts\python.exe -m compileall -q factory tests` and `git -c core.whitespace=cr-at-eol diff --check` — passed.
- **Coverage:** Valid usage and unknown-field preservation, credit cap and explicit rate-limit budget signals, timeout precedence, model mismatch and single-key fallback, malformed/missing usage and JSONL, non-budget nonzero exit, and retained artifacts.
- **Reviewed scope:** `factory/runner.py`, `tests/test_runner.py`, and this progress record.
- **Checkpoint SHA:** `b8552c3`.

## CH2-04 checkpoint

- **Review follow-up:** Separate authentication smoke check and preflight enforcement are missing; `taskkill` failure only kills the parent and can leave child processes running. Fixes and verification are in progress.
- **Authentication check:** RED `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k authentication_check_precedes -v` failed because the first probe was model-specific. Added a separate smoke request using the CLI default model before the configured model checks; GREEN `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k preflight -v` — 5 passed. The extra request may consume AI credits and can also fail if the default model is unavailable.
- **Preflight gate:** RED `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k does_not_start_or_allocate -v` showed an agent run started despite a failed preflight. The runner now returns `ERROR` with no run directory on preflight failure; GREEN the same command — 1 passed, and `-k preflight_ -v` — 6 passed.
- **Windows recovery:** RED `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k timeout_terminates_child_process -v` failed with `ERROR` when `taskkill` was unavailable. The runner now assigns a waiting launcher to a kill-on-close Windows Job Object before starting the agent. GREEN `-k timeout -v` — 2 passed; `-k tree_termination_fails -v` — 1 passed, demonstrating that closing the job removes the child even if explicit termination fails.
- **Follow-up verification:** Full focused run while the timeout test was RED reported 34 tests, 1 failure, 1 skip. After the fix, the focused suite ran 36 tests with 1 skip and no failures; it reported an unclosed-pipe warning on job-assignment failure, which was fixed and rechecked with `-W error::ResourceWarning -k job_assignment_fails -v`. Real Windows tests also passed for job-assignment failure and successful contained process output. `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 66 tests: 65 passed, 1 native symlink test skipped.
- **Interface decision:** User approved returning `ERROR` with `paths={}` when preflight fails, because no run directory exists; recorded as DD-18. The live preflight was verified, but a live agent execution was not.
- **Final regression:** One full-suite attempt failed in the existing `test_worktree_release_removes_clean_worktree_and_keeps_branch` with `JSONDecodeError` when the worktree attachment returned empty stdout; its underlying failure was not captured. The isolated test then passed, and a repeated `.venv\Scripts\python.exe -m unittest discover -s tests -v` passed: 66 tests, 65 passed, 1 native symlink skip. `.venv\Scripts\python.exe -m compileall -q factory tests` and `git -c core.whitespace=cr-at-eol diff --check` passed.
- **Follow-up reviewed scope:** `factory/runner.py`, `factory/windows_job.py`, `tests/test_runner.py`, this progress record, and DD-18 in `design-deviations.md`.
- **Live preflight:** `.venv\Scripts\python.exe -c "from factory.runner import preflight; print(preflight())"` returned `PreflightResult(ok=True, model=None, error=None)` after the separate smoke check was added.
- **Decision:** User approved live model probes for preflight; each distinct configured model is checked with a short non-interactive request. Unit tests mock subprocesses and do not consume credits.
- **RED:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k terminates_child_process -v` failed because the child process survived direct-parent termination. `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k preflight_returns_authentication_failure -v` failed because `preflight` did not exist.
- **GREEN:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k timeout -v` — 2 passed, including a real Windows parent/child process test. `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k process_tree_termination_failure -v` — passed with an explicit `ERROR`. `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k preflight -v` — 4 passed for authentication failure, unavailable-model failure, successful checks across distinct models, and empty configuration.
- **Regression:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -v` — 31 passed; the native symlink test was skipped because Windows symlink creation requires an unavailable privilege.
- **Original implementation (superseded by follow-up above):** Windows timeouts used `taskkill /T /F` and returned an explicit error if tree termination failed. Preflight validated executable/help flags and probed each distinct configured model in a temporary working directory.
- **Live probe:** `.venv\Scripts\python.exe -c "from factory.runner import preflight; print(preflight())"` — `PreflightResult(ok=True, model=None, error=None)`; the approved live authentication/model checks succeeded.
- **Additional checks:** `.venv\Scripts\python.exe -m compileall -q factory tests` and `git -c core.whitespace=cr-at-eol diff --check` — passed.
- **Checkpoint SHA:** `8551d08`.
- **Reviewed scope:** `factory/runner.py`, `tests/test_runner.py`, and this progress record.

## CH2-05 checkpoint

- **RED/GREEN:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -k leaves_job_journal_unchanged -v` — passed against the current runner; no production change was needed.
- **Verified:** Called `factory.runner.run_agent` through the module interface with a fake process, confirmed `job.json` and `events.jsonl` were byte-for-byte unchanged, and confirmed the run directory contains only stdout, stderr, usage, and session artifacts.
- **Focused regression:** `.venv\Scripts\python.exe -m unittest discover -s tests -p test_runner.py -v` — 32 passed; the native symlink test was skipped because Windows symlink creation requires an unavailable privilege.
- **Full regression:** `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 61 passed; the same native symlink test was skipped due to the unavailable Windows symlink privilege.
- **Additional checks:** `.venv\Scripts\python.exe -m compileall -q factory tests` and `git -c core.whitespace=cr-at-eol diff --check` — passed.
- **Checkpoint SHA:** `d9bc931`.
- **Remaining:** None.

## Checkpoint policy

Record each slice's exact RED/GREEN/regression commands, results, reviewed files, and verified commit SHA. A slice is complete only after its acceptance checks pass and its diff is reviewed. Do not mark unverified work complete.
