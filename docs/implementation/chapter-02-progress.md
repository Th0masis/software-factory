# Chapter 2 — Progress

- **Overall status:** CH2-01 and CH2-02 complete; CH2-03 through CH2-05 pending.
- **Current branch:** `feature/chapter-02`
- **Contract:** [`chapter-02.md`](./chapter-02.md)
- **Design deltas:** [`design-deviations.md`](./design-deviations.md)

## Checkpoint summary

- **Completed:** Contract approved; CH2-01 command construction and validation; CH2-02 process execution and artifact persistence.
- **Verified:** `config.json` defines the approved tool allow-list, mandatory `shell(git push)` denial, secret environment variable names, and two builder profiles. The installed Copilot CLI supports the required command flags and `--name`.
- **Remaining:** CH2-03 through CH2-05.
- **Blockers:** The environment cannot create native file symlinks (`WinError 1314`); the symlink guard passed a filesystem-boundary simulation instead.

## Slice status

| Slice | Status | Tests | Commit SHA | Notes |
|---|---|---|---|---|
| CH2-01 — Validating profiles and constructing a safe command | Complete | PASS (9 focused tests; 1 platform skip) | `d6aed01` | Exact command arguments, profile pinning, permission boundaries, path validation, cap threshold, and unique run directories verified. |
| CH2-02 — Executing a fake Copilot process and preserving artifacts | Complete | PASS (15 focused tests; 1 platform skip) | Pending | Fake-process success, nonzero exit with partial output, launch failure, and missing-artifact failure. |
| CH2-03 — Parsing usage and classifying outcomes | Pending | — | — | |
| CH2-04 — Preflight and Windows timeout recovery | Pending | — | — | |
| CH2-05 — Runner integration contract and regression | Pending | — | — | |

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
- **Checkpoint SHA:** Pending.

## Checkpoint policy

Record each slice's exact RED/GREEN/regression commands, results, reviewed files, and verified commit SHA. A slice is complete only after its acceptance checks pass and its diff is reviewed. Do not mark unverified work complete.
