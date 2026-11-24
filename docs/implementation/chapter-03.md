# Chapter 3 — Implementation Contract: Gate

- **Status:** Approved for implementation — 2026-11-22
- **Source:** [`design.md`](../../design.md), Chapter 3 and related Chapters 1, 2, 4, 7, 8, and 9
- **Dependencies:** [`chapter-01.md`](./chapter-01.md), [`chapter-02.md`](./chapter-02.md), [`design-deviations.md`](./design-deviations.md)
- **Specification review:** completed 2026-11-22
- **Approved Chapter 4 extension:** behavior approved 2026-11-23; its implementation contract is
  [`chapter-04.md`](./chapter-04.md).

This contract defines the factory-side Gate and its target-project checks interface. It does not
modify `design.md`. Implementation starts only after explicit approval of this contract.

> **Implementation status clarification — 2026-11-24:** The approved Chapter 4 text below specifies
> a `baseline` Gate mode, but the current `factory job gate` CLI accepts only `fast|full` with an
> optional slice ID. Baseline-red remains a separate `checks.py --red` input-readiness check.
> This clarification records current behavior; it does not silently expand the CLI or revise the
> approved M1 contract. Reconcile the specified baseline Gate mode under a separate approved change
> before relying on it.

## 1. Scope

### Goals

- Decide deterministically whether a requested target-project check mode passes.
- Reject changes outside the approved scope and changes to protected target-project paths.
- Include committed, staged, unstaged, and untracked non-ignored files in change checks.
- Preserve a machine-readable report and check output for each Gate attempt.
- Fail closed when configuration, check output, or Git change information is invalid.
- Ensure a failing Gate blocks review; the Gate itself does not start reviewers or change job state.

### Deliverables

- A factory-side Gate service and CLI entry point for an attached job worktree.
- A trusted scope-manifest input, safe path-pattern matching, and protected-path checks.
- A target-project `.factory/checks.py` contract with baseline, fast, and full checks, including
  optional slice selection for the Chapter 4 TDD extension.
- A bounded, shell-free checks process with known secret environment variables removed.
- Strict checks JSON validation and a persisted `gate-report.json` with diagnostics.
- The approved Chapter 4 extension for baseline and slice-selected TDD checks; see §2.
- Focused unit and temporary-Git integration tests, including failure and timeout cases.
- An implementation progress record once implementation begins.

### Explicitly excluded

- Runner/Copilot sessions, builder loops, reviewer invocation, review routing, PR creation, or
  publishing changes.
- Any push, branch creation, reset, merge, or worktree cleanup.
- Job-state transitions or journal writes. The owning orchestrator records the Gate result and
  decides whether to advance or return to building.
- Risk recomputation, numeric file/line diff budgets, secret scanning, and CI integration.
- Authoring or approving specs, generating scope manifests from natural-language specs, or
  generating TDD tests.
- Changes to `design.md`, Chapter 1 behavior, the existing Chapter 2 runner, or target project code.

### Dependencies and constraints

- Python >=3.12; verify locally with the available Python 3.14 runtime.
- Use the standard library and existing Pydantic dependency only; no new dependencies.
- Gate operates on a job's attached target worktree and receives an explicit base SHA and a
  factory-controlled scope manifest.
- Git and checks processes use argument arrays with `shell=False`. Do not invoke Bash or
  PowerShell scripts.
- The target repository is assumed trusted. This Gate is not an OS sandbox. The checks process
  inherits the normal environment except for configured secret variable names, and it has a
  wall-clock timeout.
- The existing Chapter 1 CLI convention remains: JSON success output, diagnostics on stderr,
  exit `2` for usage/configuration errors and `1` for operational failure or a failed Gate.

## 2. Confirmed decisions and proposed contract details

### Confirmed in the specification review

- This is **Chapter 3 — Gate** in `design.md`. The Copilot Runner is Chapter 2 and is already
  implemented under the approved Chapter 2 contract.
- Factory Gate invokes the target project's `.factory/checks.py` for baseline, selected-slice, or
  full checks. Exact arguments are specified by the approved Chapter 4 contract.
- The checks script must be unchanged from the supplied base SHA before it is executed. Remove
  configured secret environment variables and enforce a timeout. This limits accidental exposure;
  it does not sandbox trusted project code.
- The checks script emits a strict JSON report. Missing, malformed, or contradictory output fails
  closed.
- The Gate receives an explicit per-step `base_sha`. Its change set includes commits after that
  SHA plus staged, unstaged, and untracked non-ignored paths.
- Scope and protected-path patterns use normalized repository-relative paths. Unsafe or invalid
  patterns fail closed. Untracked files are included in scope/protected checks.
- Do not add numeric `max_files` or `max_lines` limits. Scope enforcement remains required.
- Target `.factory/protected.txt` and factory-owned job data are both protected, but by different
  boundaries: Gate checks target-worktree paths; the orchestrator is the sole writer of factory
  job data.
- Risk recomputation is deferred to Chapter 7. Chapter 3 reports it as deferred and does not
  infer an effective risk or block an otherwise passing requested check mode on that basis.
- Gate PASS means the explicitly requested mode passed. The future orchestrator must ensure that
  all risk-required checks have passed before it starts review.
- Review starts only after Gate PASS: code review always, UX/UI review for matching changes, and
  security review according to effective risk. Gate does not launch these reviewers.
- The builder may commit but may not push or open a PR. A human publishes after review; PR
  metadata and the `PR_OPEN` workflow are deferred to Chapter 8. `Closes #n` comes from a
  manually supplied issue reference.
- Every session task is a new snapshot derived from the current approved spec revision and feature.
  Review findings may add repair instructions but cannot silently change the spec or scope.
  Conflicts require a human decision and, when needed, an approved spec amend. Preserve task
  snapshots as job artifacts.
- The exact worktree path `.factory/task.md` is a factory-owned runtime input, not application
  output. Exclude it from change checks only while untracked; if it is staged or committed as a
  worktree change, Gate FAILs. Do not ignore other untracked paths.

### Approved Chapter 4 extension — 2026-11-23

- Gate supports a baseline check that excludes pending job TDD tests, a selected-slice check that
  runs only the locked tests mapped to that slice, and a final full check after all slices pass.
- M1 input registration uses a separate target-checks `--red --manifest PATH
  --baseline-sha SHA` invocation. It verifies TDD readiness and does not produce a Gate verdict.
- The red report binds test results to the locked manifest hash and attached branch's
  `work_branch_start_sha`; its schema is defined in the approved Chapter 4 contract.
- Slice selection does not bypass change-scope or protected-path checks.
- Gate remains state-neutral and does not mark a slice or feature as passed. The Chapter 4
  orchestrator records the result and owns all job-state transitions.
- A final Gate PASS is required before the orchestrator advances the job from `GATE` to `REVIEW`.
- Exact CLI spelling and checks-script arguments are specified in the approved
  [`chapter-04.md`](./chapter-04.md).

### Gate interface details

- Base CLI: `factory job gate JOB_ID --base SHA --mode fast|full`.
- Approved Chapter 4 extension CLI: `factory job gate JOB_ID --base SHA --mode baseline|fast|full
  [--slice SLICE_ID]`. The target checks interface receives the corresponding baseline, selected
  slice, or full-check request. `--slice` is valid only for a slice-selected fast check.
- M1 red-check CLI: `.factory/checks.py --red --manifest PATH --baseline-sha SHA`; this is an
  input-readiness check, separate from `factory job gate`.
- The scope file is JSON stored outside the target worktree and controlled by the factory:

  ```json
  {
    "schema_version": 1,
    "allowed_paths": ["src/**", "tests/**"]
  }
  ```

  Store it as `jobs/<job-id>/scope.json`, outside the builder's writable worktree. The file must
  be a regular non-symlink file under that job directory and contain at least one pattern.
  Chapter 3 consumes this validated input; Chapter 5 owns deriving and approving it from a spec.
- `.factory/protected.txt` contains one POSIX-style relative glob per line; blank lines and lines
  beginning with `#` are ignored. Its own path, `.factory/project.json`, and
  `.factory/checks.py` are protected by built-in rules. Load the protected list from
  `base_sha`, not the mutable worktree copy. The file must exist and be tracked at `base_sha`; a
  missing or invalid file is a configuration failure.
- Supported path pattern syntax is limited to `*`, `?`, and `**`. Absolute paths, drive prefixes,
  NUL/newline characters, and `..` path components are rejected. Normalize `\` to `/` before
  matching. Matching is case-sensitive and POSIX-style on every platform: `*` and `?` do not
  cross `/`, while `**` may match zero or more path components. For renames, validate both old and
  new paths.
- Treat exactly `.factory/task.md` as a reserved factory input: omit it from the changed-path set
  only when Git reports it as untracked. Any staged, committed, renamed, or otherwise tracked
  change to that path is protected and fails the Gate.
- Add `gate.timeout_seconds` to factory configuration, defaulting to 1800 seconds, and validate it
  as a positive integer.
- A valid checks report has this minimum shape:

  ```json
  {
    "schema_version": 1,
    "checks": [
      {"name": "unit", "status": "PASS", "exit_code": 0}
    ]
  }
  ```

  It must contain at least one uniquely named check. Each status is `PASS` or `FAIL`; `exit_code`
  is an integer, not a boolean. Gate PASS requires process exit `0`, valid JSON, and every check
  marked `PASS` with exit code `0`. Any other combination is FAIL.
- `gate-report.json` has this stable top-level shape:

  ```json
  {
    "schema_version": 1,
    "job_id": "job-id",
    "base_sha": "full-commit-sha",
    "mode": "fast",
    "slice_id": null,
    "checks": [{"name": "unit", "status": "PASS", "exit_code": 0}],
    "scope": {"changed_paths": [], "violations": []},
    "protected_paths": {"violations": []},
    "risk": {"status": "DEFERRED"},
    "verdict": "PASS"
  }
  ```

  If scope or protection fails before check execution, `checks` is empty and `checks_skipped`
  explains why. The validated project-checks JSON is preserved in the Gate report.
- Store each attempt under a unique `jobs/<job-id>/gate/<attempt-id>/` directory containing
  `gate-report.json`, `checks.stdout.txt`, and `checks.stderr.txt`. Do not overwrite an earlier
  attempt.
- Reuse the repository's Windows process-tree containment helper for timed-out checks on Windows.

These are implementation-contract proposals, not new requirements to `design.md`; approval of
this document will settle them.

## 3. Architecture

### Responsibilities

- **Gate service:** validate inputs, obtain the complete change set from Git, check scope and
  protected paths, verify the checks script has not changed from `base_sha`, run the requested
  checks mode, validate its JSON, write the report and return a verdict.
- **Target checks script:** run project-specific checks for the requested mode and emit one JSON
  object on stdout. Human-readable diagnostics go to stderr.
- **Artifact writer:** allocate an attempt-specific directory under the job and persist captured
  stdout, stderr, and the final report. It must not overwrite previous attempts.
- **CLI:** resolve the job and its attached worktree, pass the explicit base and scope manifest to
  the Gate service, emit JSON, and return a nonzero exit on failure.
- **Orchestrator:** supply the checkpoint and approved scope, record Gate lifecycle events through
  the Chapter 1 journal, and decide the next job state. It is not part of this chapter.

### Inputs and interface

The internal service should be callable without Copilot or network access:

```text
evaluate_gate(factory_root, job_id, base_sha, mode) -> GateResult
```

`base_sha` must identify a commit in the job's target repository and be an ancestor of the worktree
HEAD. The fixed `jobs/<job-id>/scope.json` input must be a regular non-symlink file outside the
target worktree. The job must have an attached worktree and a valid target project configuration.

`GateResult` contains `verdict: PASS|FAIL`, the report path, and the validated report. It does not
write `job.json` or `events.jsonl`.

### Change set and path checks

- Compare the supplied base commit to the current worktree, including branch commits and staged or
  unstaged changes; add untracked non-ignored paths from Git status.
- Do not use shell command strings or parse human-formatted Git output. Use NUL-delimited Git
  output and handle renames/deletions without losing either path endpoint.
- Every changed path must match at least one allowed scope pattern.
- Any changed path matching a protected pattern is a violation, including additions, modifications,
  deletions, and either endpoint of a rename.
- The scope manifest and protected-path list are trusted inputs outside the builder's writable
  worktree. The Gate must reject an invalid pattern rather than ignore it.
- The exact untracked `.factory/task.md` is the only exception to untracked-path scope checks;
  staged or committed changes to it remain a protected-path violation.
- The job's factory-side files are not included in the target Git diff. Their integrity relies on
  the orchestrator-only-writer boundary.

### Checks execution and report

- Require `.factory/checks.py` to exist as a tracked regular file and match the content at
  `base_sha`. Check this before executing any target code.
- Invoke it as `[sys.executable, ".factory/checks.py", "--fast"|"--full"]` with `cwd` set to the
  job worktree and `shell=False`.
- Remove every environment variable named in `config.json` `runner.secret_env_vars`; preserve
  ordinary environment required for Python and project tools.
- Enforce `gate.timeout_seconds`. On timeout, terminate the complete process tree and
  preserve partial stdout/stderr. A failure to terminate is reported explicitly and blocks review.
- Validate the JSON schema and the process exit code together; neither is authoritative by itself.
  Missing, malformed, or inconsistent output is FAIL. No successful fallback is permitted.
- Run scope and protected-path checks before target code. If either fails, do not execute the
  project checks script; persist a FAIL report explaining that checks were skipped.
- The overall verdict is PASS only if the requested checks mode passes and all scope/protected
  checks pass. Risk remains marked `DEFERRED` until Chapter 7.

### Error handling and state boundary

- Invalid job, path, SHA, mode, or scope/protected configuration is surfaced on stderr as a
  configuration error.
- A failed check, scope violation, protected-path violation, changed checks script, timeout, or
  malformed checks report produces a FAIL report and nonzero exit.
- Git/process/artifact I/O errors are surfaced explicitly; if a report directory exists, preserve
  diagnostics without replacing the original failure with a success-shaped result.
- Gate never transitions a job, modifies its journal, or starts review. A failed Gate must prevent
  the owning orchestrator from starting reviewers.

## 4. Implementation slices

Each slice follows RED → GREEN → REFACTOR. Implement and verify one slice before starting the next.
The contract and this document must be approved before any implementation begins.

### CH3-01 — Reading changes and enforcing scope/protection

- **Observable behavior:** a changed path outside the allowed scope or matching a protected path
  produces a deterministic Gate FAIL; valid changes pass these checks.
- **Inputs/outputs:** temporary Git worktree, explicit base SHA, scope JSON, protected-path list;
  normalized changed paths and scope/protection verdicts.
- **Failing test first:** modify a protected tracked file and add an untracked file outside scope;
  assert both are detected. Add cases for staged/unstaged changes, deletion, rename endpoints,
  invalid traversal patterns, and invalid/non-ancestor base SHA.
- **Acceptance criteria:** all committed and working-tree changes are included; untracked files are
  checked; path matching is root-relative and platform-independent; invalid inputs fail closed.
- **Minimal implementation scope:** Git change collector, scope/protected pattern validation and
  matcher, focused temporary-repository tests.
- **Dependencies:** Chapter 1 Git/job helpers.
- **Verification:** unit and Git integration tests using temporary repositories only.
- **Completion:** positive, negative, and rename/untracked cases pass; no target or factory files
  are mutated by the checks.

### CH3-02 — Running and validating project checks

- **Observable behavior:** an unchanged project checks script runs in the selected mode and returns
  PASS only for a valid all-passing report; failures and malformed output fail closed.
- **Inputs/outputs:** worktree, base SHA, `fast|full`, fake `.factory/checks.py`; captured process
  output and validated check results.
- **Failing test first:** fake script emits valid passing JSON and exits `0`; assert PASS. Then cover
  nonzero exit with apparent PASS, reported FAIL with exit `0`, malformed/missing JSON, changed
  checks script, secret environment removal, launch failure, and timeout with a child process.
- **Acceptance criteria:** `shell=False`; script integrity is checked before execution; configured
  secrets are absent; output schema and exit code both participate in verdict; timeout terminates
  the process tree and preserves diagnostics.
- **Minimal implementation scope:** checks process adapter, JSON report validation, timeout handling,
  and focused fake-script tests. Reuse existing Windows process containment rather than duplicating
  it.
- **Dependencies:** CH3-01 for validated worktree and base SHA.
- **Verification:** fake scripts run without Copilot, credentials, network access, or real project
  side effects.
- **Completion:** all process/result combinations and timeout recovery cases pass.

### CH3-03 — Producing a Gate report and exposing the CLI

- **Observable behavior:** a user can run Gate for an attached job, inspect a durable JSON report,
  and receive PASS/FAIL via stable CLI output and exit status; the job journal is unchanged.
- **Inputs/outputs:** job ID, mode, base SHA, factory-controlled scope file; JSON stdout, report and
  captured checks logs, exit status.
- **Failing test first:** run the CLI with a fake checks script and assert report schema, artifact
  persistence, correct exit code, and byte-for-byte unchanged `job.json`/`events.jsonl`.
- **Acceptance criteria:** every attempt has a unique report directory; failed checks never return
  PASS; Gate does not change job state or invoke a reviewer; existing Chapter 1 and Chapter 2 tests
  remain green.
- **Minimal implementation scope:** CLI integration, report/artifact writer, and end-to-end fake
  checks tests.
- **Dependencies:** CH3-01 and CH3-02.
- **Verification:** subprocess CLI tests against temporary repositories, then the complete test suite.
- **Completion:** report and CLI contracts are verified and the final diff is limited to Chapter 3.

## 5. Verification strategy

### Positive cases

- In-scope changes pass scope checks; unchanged protected paths pass protection checks.
- A valid checks script returns schema-valid JSON, exit `0`, and all checks `PASS`.
- Both `--fast` and `--full` are forwarded unchanged to the project script.
- The approved Chapter 4 extension excludes pending TDD tests from baseline checks and runs only
  the selected slice's locked tests when a slice is specified.
- Gate writes a report and captured logs without changing job state or journal.

### Negative cases

- Invalid/missing job, worktree, base SHA, scope file, protected manifest, checks script, mode, or
  report.
- Changes outside scope; protected path additions, modifications, deletions, or renames.
- Checks script differs from the base version.
- Check exits nonzero, reports a failed check, reports contradictory status/exit code, or prints
  malformed JSON.
- Secret environment variables are not visible to the checks process.

### Recovery cases

- Checks process times out after partial output; diagnostics are retained and no child process is
  left running.
- Report writing fails after checks complete; return an explicit operational error, never PASS.
- Gate is rerun; earlier report artifacts remain unchanged.
- Job journal is stale or already escalated; Gate reports the check result only and does not repair
  or alter job state.

### Commands

Run the focused Chapter 3 test module during each slice, then:

```text
uv run --python 3.14 python -m unittest discover -s tests -v
uv run --python 3.14 python -m compileall -q factory tests
git -c core.whitespace=cr-at-eol diff --check
```

All automated Gate tests use temporary repositories and fake checks scripts. They must not require
Copilot authentication, AI credits, network access, production projects, or Git remotes.

## 6. Completion criteria

Chapter 3 is complete only when:

1. The Gate service and CLI implement the approved inputs and deterministic PASS/FAIL contract.
2. The complete change set includes branch commits, staged/unstaged work, and untracked
   non-ignored paths relative to an explicit valid base SHA.
3. Scope and protected-path checks are platform-independent and fail closed.
4. The checks script is verified against the base before execution and is run with configured
   secrets removed, `shell=False`, and a timeout.
5. The checks JSON schema and process exit code are both validated.
6. Timeout handling terminates the complete process tree and retains partial diagnostics.
7. Gate reports and logs are durable, unique per attempt, and do not mutate the job journal/state.
8. A failed Gate cannot trigger reviewers through the Gate service or CLI.
9. Chapter 1 and Chapter 2 regression tests pass.
10. The full diff contains only approved Chapter 3 contract, implementation, tests, and progress
    records; `design.md` remains unchanged.

## 7. Deferred decisions

- **Risk recomputation and review routing:** Chapter 7 owns the effective-risk algorithm and the
  complete set of risk-required checks/reviewers. Until then, the report marks risk recomputation
  `DEFERRED`; a PASS applies only to the requested mode.
- **Human publication and PR state:** Chapter 8 must define how a human-created PR URL/issue
  reference is recorded and how `READY_FOR_HUMAN` advances to `PR_OPEN`. The builder and Gate do
  not push or open a PR.
- **Scope-manifest production:** Chapter 5 owns deriving and approving the machine-readable scope
  manifest from the approved specification. Chapter 3 only consumes a factory-controlled,
  validated manifest.
- **Task generation and repair loop:** Chapter 4/9 own generating an immutable task snapshot per
  session from the approved spec/feature and adding review findings to a new repair task. A spec
  conflict or scope expansion requires a human decision; it is never resolved by the builder.
- **End-to-end security isolation:** the trusted-target assumption and removal of configured
  secret environment variables do not sandbox repository code. A Windows/container sandbox is
  outside this chapter and must not be implied by the Gate report.

## 8. Reference examples reviewed

The external examples informed workflow observations only; their code and text are not copied:

- [Example job 001 — subtract function](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2/examples/jobs/001-add-a-subtract-function)
  illustrates a concrete scope and evidence-based acceptance list.
- [Example job 002 — UI history](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2/examples/jobs/002-show-a-history-of)
  demonstrates separate code, UX, and UI reviews after a passing Gate.
- [Example job 003 — conflicting protected path](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2/examples/jobs/003-add-multiply-divide-power)
  demonstrates that Gate PASS does not resolve a spec/protection conflict; human escalation is
  required.
- [Example job 005 — rejected request](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2/examples/jobs/005-add-a-crypto-miner)
  demonstrates rejecting an unsafe request before spec, build, and review.
