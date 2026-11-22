# Chapter 2 — Implementation Contract

- **Status:** Approved for implementation — 2026-11-22
- **Source:** [`design.md`](../../design.md), Chapter 2 and related pipeline, budget, and recovery sections
- **Dependencies:** [`chapter-01.md`](./chapter-01.md), [`design-deviations.md`](./design-deviations.md)

This contract translates the reviewed Chapter 2 decisions into testable work. It does not amend
`design.md`. Implementation must follow this contract; changes to its agreed requirements require
explicit approval.

## 1. Scope

### Goals

- Provide the single internal Python layer that starts Copilot CLI agents.
- Pin the complete agent profile for every run and pass arguments without shell interpolation.
- Prevent accidental access to unrelated user configuration and unsafe tool permissions.
- Classify runs deterministically from timeout, process result, stderr, JSONL output, and usage data.
- Preserve run diagnostics and usage artifacts for successful, failed, budget-limited, and timed-out runs.
- Make the runner testable without Copilot authentication, network access, or AI credits.

### Deliverables

- Internal `run_agent(role, profile, task_file, cwd, session_name) -> RunResult` API.
- Profile and runner configuration models with strict validation.
- Copilot command construction using an argument array and the approved security flags.
- Preflight checks for authentication and availability of every model used by configured profiles.
- Usage parsing and model-consistency validation.
- Deterministic `OK`, `BUDGET`, `TIMEOUT`, and `ERROR` classification.
- Per-run artifact directory containing stdout, stderr, usage, and shared session output.
- Windows-safe timeout handling that terminates the complete child-process tree.
- Focused unit tests with a fake Copilot executable or injected process runner, plus regression tests.

### Explicitly excluded

- A public `factory job run` command. Chapter 4 owns the builder workflow and its job-state
  preconditions.
- Builder sessions, feature selection, retries, gate execution, reviews, routing, escalation,
  or dashboard behavior.
- Job state transitions. The runner reports a result; the owning workflow records any event through
  the Chapter 1 event store.
- Automatic branch creation, reset, release, push, pull request creation, or merge.
- Isolated `COPILOT_HOME` or another unverified configuration-directory redirection mechanism.
- Secret values in command arguments, logs, test fixtures, or committed configuration.
- Changes to `design.md`, Chapter 1 behavior, or the target project's checks contract.

### Dependencies and constraints

- Python >=3.12; verify locally with the available Python 3.14 runtime.
- Use the standard library for process execution, validation, JSONL parsing, filesystem handling,
  and timeout control. Reuse the repository's existing Pydantic dependency for strict models where
  that is consistent with Chapter 1.
- The runner is invoked from the factory orchestrator and receives a prepared, attached worktree.
- Process arguments are passed as a list with `shell=False`; no prompt, path, profile value, or
  permission pattern is interpolated into a shell command.
- The configured Copilot executable must be resolved explicitly and its version must not update
  during a run (`--no-auto-update`).
- The runner does not write `job.json` or `events.jsonl` directly. Run lifecycle events belong to
  the owning orchestration workflow and must use the authoritative Chapter 1 journal.
- The configured global concurrency remains one active job. Enforcement by a scheduler is outside
  this chapter; the runner must not introduce parallel execution.

## 2. Architecture

### Responsibilities

- **Profile/configuration:** load and validate named profiles from factory configuration. A profile
  pins `agent`, `model`, reasoning effort, context tier, and the requested AI-credit cap.
- **Runner:** validate inputs, create a run directory, construct the command, execute Copilot,
  collect output, enforce timeout, parse usage, and return a `RunResult`.
- **Preflight:** perform a separate authentication smoke check and verify model availability
  before an agent run. A failed preflight prevents the agent run and does not change job state.
- **Process adapter:** provide the real subprocess implementation and a fake-stub seam for tests.
- **Artifact store:** create an atomic per-run directory and retain all diagnostics, including
  partial output after timeout or process failure.
- **Orchestrator:** calculate the remaining budget, invoke the runner, persist `run_started` and
  `run_finished` events, and decide the next job workflow action. These decisions are not delegated
  to Copilot.

### Internal interface

```text
run_agent(
    role,
    profile,
    task_file,
    cwd,
    session_name,
) -> RunResult
```

`role` identifies the configured agent role and must resolve to a known profile. `profile` is the
validated profile value used for this invocation; arbitrary model or permission values supplied by
callers are rejected. `task_file` is an absolute regular file controlled by the factory. `cwd` is
the expected existing job worktree. `session_name` is a diagnostic CLI name, not a substitute for
the Copilot session ID.

```text
RunResult {
    outcome: OK | BUDGET | TIMEOUT | ERROR,
    exit_code: integer | null,
    usage: Usage | null,
    session_id: string | null,
    paths: {
        run_dir: path,
        stdout: path,
        stderr: path,
        usage: path,
        session: path,
    },
    error: string | null,
}
```

The result is returned only after artifacts have been flushed. A missing or invalid required
artifact is reported explicitly; it is never replaced with a success-shaped default.

### Command contract

The real process uses an argument array equivalent to:

```text
copilot
  -C <cwd>
  -p "Follow .factory/task.md"
  --agent <agent>
  --model <model>
  --reasoning-effort <effort>
  --context <tier>
  [--max-ai-credits <cap>]       # only when cap >= 30
  --no-ask-user
  --no-auto-update
  --disable-builtin-mcps
  --no-custom-instructions
  --output-format json
  --usage-output-file <run>/usage.json
  --share <run>/session.md
  --allow-tool <approved pattern>...
  --deny-tool "shell(git push)"
  --secret-env-vars <approved names>...
```

The task file is referenced by the fixed prompt. The runner validates that the task file exists
under the job/run data boundary and is not a symlink before launching the process. The exact
approved allow-list is configuration, but it must not contain `--allow-all`, `--allow-all-tools`,
`--allow-all-paths`, `--allow-all-urls`, `--allow-all`, or `--yolo`. The deny rule for `git push`
is mandatory and denial precedence is relied upon.

The runner does not use `--trust-folder`, `--add-dir`, user plugins, or user MCP configuration to
expand the execution scope. Configuration isolation is intentionally limited to the verified flags
above until a separate technical decision verifies a safe `COPILOT_HOME` strategy.

### Preflight

Preflight runs before an agent run:

1. Validate the Copilot executable and expected CLI invocation.
2. Run an authentication smoke test without modifying the target worktree.
3. Check every distinct model referenced by the profiles that may be used for the job.
4. Return a structured failure with the affected model or authentication diagnostic.

Preflight failures are fail-closed. They do not consume an agent run, do not create a successful
`RunResult`, and do not mutate job state. Preflight itself may write a diagnostic artifact only
when the owning workflow supplies a run directory for that purpose.

### Usage and model contract

The runner requires a valid JSON usage file for a completed agent run. The minimum consumed fields
are:

- `totalNanoAiu` — non-negative numeric total used to calculate credits:
  `credits = totalNanoAiu / 1_000_000_000`;
- `modelMetrics` and/or `currentModel` — effective model identity;
- optional session identity and code-change fields when supplied by the CLI.

Unknown fields are preserved in the parsed usage object but are not required for classification.
Missing, malformed, negative, or non-numeric required values produce `ERROR`.

The effective model must match the model pinned by the profile. A frontmatter `model:` in a custom
agent that takes precedence over the CLI profile is therefore detected after the run and classified
as `ERROR`; the runner must not silently accept the mismatch.

### Outcome classification

Classification is deterministic and ordered:

1. `TIMEOUT` — the wall-clock deadline expired and the process tree was terminated.
2. `BUDGET` — usage is valid and credits are at or above the requested CLI cap, or the JSONL
   stream contains an explicit session-limit event.
3. `OK` — the process exited with code `0`, usage is valid, the effective model matches, and no
   higher-priority condition applies.
4. `ERROR` — all other cases, including authentication, invalid arguments, unavailable model or
   agent, missing usage, malformed output, model mismatch, and nonzero exit without a budget signal.

Exit code alone never determines `BUDGET` or `ERROR`.

The orchestrator owns the actual job and feature budgets. The runner receives a calculated cap.
Because Copilot CLI rejects caps below 30, the runner omits `--max-ai-credits` for a calculated cap
below 30 and relies on post-run usage validation for that invocation. A cap of at least 30 is
passed unchanged; the CLI cap remains a soft limit.

### Artifact and process lifecycle

- Allocate a unique `runs/<n>` directory without reusing an existing run.
- Write stdout as JSONL and stderr as a text log while the process runs.
- Pass absolute paths for `usage.json` and `session.md`.
- Flush and close output handles before parsing or returning.
- Preserve partial stdout, stderr, usage, and session artifacts on every failure.
- Use `Popen` with `shell=False` and a Windows process-group/job-object strategy that terminates
  the complete child tree on timeout.
- Never reset the worktree or delete uncommitted changes as part of runner cleanup.

## 3. Implementation slices

Each slice follows RED -> GREEN -> REFACTOR. Do not create a RED-only commit. A slice is complete
only after its acceptance checks pass, the diff is reviewed, and the Chapter 2 progress record is
updated on the same chapter branch.

### CH2-01 — Validating profiles and constructing a safe command

- **Observable behavior:** a valid profile produces a deterministic argument array; invalid roles,
  models, paths, permissions, symlinked task files, and outside-worktree directories are rejected.
- **Inputs/outputs:** validated profile, task file, worktree, and session name; command arguments and
  run paths, with no shell string.
- **Failing test first:** assert the command array contains the pinned profile, required security
  flags, the mandatory `git push` deny rule, and omits the CLI cap below 30; assert forbidden
  all-permission flags and invalid paths fail.
- **Acceptance criteria:** no shell interpolation; no unsafe all-permission flag; task file and cwd
  trust-boundary checks are explicit; run paths are unique and inside the configured factory data.
- **Minimal scope:** runner models, configuration loading, path validation, command builder, and
  focused unit tests.
- **Dependencies:** Chapter 1 configuration and path conventions.
- **Verification:** unit tests inspect exact argument lists and rejected inputs.
- **Completion:** all focused tests pass and no real Copilot process is launched.

### CH2-02 — Executing a fake Copilot process and preserving artifacts

- **Observable behavior:** the runner executes a fake Copilot executable with the exact arguments,
  captures JSONL/stdout and stderr, and returns a result with stable artifact paths.
- **Inputs/outputs:** command, cwd, timeout, and fake process behavior; `RunResult` plus complete
  or partial artifacts.
- **Failing test first:** inject a fake process that writes valid JSONL, usage, and session output;
  assert `OK` and persisted files. Add a failure case with nonzero exit and partial output.
- **Acceptance criteria:** `shell=False`; output handles close before parsing; artifacts remain after
  success and failure; process launch errors are explicit `ERROR`.
- **Minimal scope:** process adapter seam, artifact directory handling, stdout/stderr capture, and
  tests using a local fake executable or injected adapter.
- **Dependencies:** CH2-01.
- **Verification:** tests run without authentication, network access, or AI credits.
- **Completion:** fake process tests pass and the real CLI is not required.

### CH2-03 — Parsing usage and classifying outcomes

- **Observable behavior:** the same captured artifacts always produce the same outcome according to
  the ordered classification contract.
- **Inputs/outputs:** exit code, timeout flag, stderr, JSONL events, usage JSON, profile; `RunResult`.
- **Failing test first:** table-test timeout, budget by credits, explicit budget event, valid OK,
  authentication error, invalid argument, missing usage, malformed usage, and model mismatch.
- **Acceptance criteria:** timeout has priority; budget is not inferred from exit code; OK requires
  valid usage and model match; invalid data fails closed as ERROR.
- **Minimal scope:** usage model/parser, JSONL event inspection, model comparison, classifier, and
  focused tests.
- **Dependencies:** CH2-02.
- **Verification:** deterministic unit tests with synthetic artifacts.
- **Completion:** every outcome path and precedence rule is covered.

### CH2-04 — Preflight and Windows timeout recovery

- **Observable behavior:** preflight blocks agent execution when authentication or a configured model
  is unavailable; a timeout terminates the complete process tree and retains diagnostics.
- **Inputs/outputs:** executable, model set, timeout, fake process tree; preflight result or
  `TIMEOUT` `RunResult`.
- **Failing test first:** fake preflight failures for authentication and model availability; fake
  parent/child process that exceeds the deadline and assert the child is not left running.
- **Acceptance criteria:** preflight is fail-closed and state-neutral; timeout leaves artifacts;
  no orphan child process remains; no worktree reset or deletion occurs.
- **Minimal scope:** preflight adapter, Windows process-tree termination, timeout handling, and
  focused platform-aware tests.
- **Dependencies:** CH2-03.
- **Verification:** Windows tests with a controlled fake executable; skip only tests requiring
  unavailable platform primitives and document the limitation.
- **Completion:** positive, negative, and timeout recovery tests pass on the supported Windows
  runtime.

### CH2-05 — Runner integration contract and regression

- **Observable behavior:** the internal runner can be called by a future orchestrator with a
  validated profile and returns artifacts/results without writing job state.
- **Inputs/outputs:** fake profile/task/worktree/run root; stable `RunResult` and unchanged Chapter 1
  job journal.
- **Failing test first:** invoke the runner through its public internal module interface and assert
  no `job.json` or `events.jsonl` mutation.
- **Acceptance criteria:** only the runner's declared files change; Chapter 1 tests remain green;
  no public `factory job run` command is added; fake-stub tests cover the complete vertical path.
- **Minimal scope:** integration fixture, exports, documentation references, and regression tests.
- **Dependencies:** CH2-01 through CH2-04.
- **Verification:** focused Chapter 2 suite followed by the complete repository suite.
- **Completion:** contract behavior is verified and the final diff contains only Chapter 2 scope.

## 4. Verification strategy

### Positive scenarios

- Valid profile, task file, worktree, usage, and matching model return `OK`.
- Valid run at or above a CLI cap returns `BUDGET`, even with exit code `0`.
- A cap below 30 omits `--max-ai-credits` and still records post-run usage.
- Preflight succeeds for authenticated access and every configured model.
- All stdout, stderr, usage, and session artifacts are present after a successful run.

### Negative scenarios

- Missing or invalid profile, task file, cwd, executable, usage, model, or permission pattern.
- Symlinked task file, task file outside the factory data boundary, or cwd outside the expected
  worktree.
- Authentication failure, unavailable model, unavailable agent, invalid CLI argument, and nonzero
  process exit.
- Model mismatch caused by agent frontmatter or another CLI precedence rule.
- Forbidden `--allow-all*`, `--allow-all`, `--yolo`, shell interpolation, or missing `git push` deny.

### Recovery scenarios

- Process exits after partial stdout/stderr output.
- Usage write is absent, incomplete, malformed, or unreadable.
- Process exceeds the wall-clock deadline and has a child process.
- Timeout or launch failure occurs after the run directory is created.
- Runner returns an error while the worktree contains uncommitted changes; those changes remain.

### Commands

Use the repository's Python launcher on Windows:

```text
py -3.14 -m unittest discover -s tests -v
py -3.14 -m compileall -q factory tests
git -c core.whitespace=cr-at-eol diff --check
```

The focused Chapter 2 tests must run without `copilot` authentication or network access. The
complete suite must run after all slices. If dependency restoration is required, use the existing
project environment and report environment failures separately from test failures.

## 5. Completion criteria

Chapter 2 is complete only when:

1. The internal `run_agent` API and `RunResult` contract are implemented and documented.
2. Every invocation pins the validated profile and uses argument-array execution without shell
   interpolation.
3. Preflight fails closed for authentication or unavailable profile models.
4. The approved security flags are present; all-permission flags and automatic user interaction
   are not enabled; `git push` is denied.
5. Usage is validated, model precedence is detected, and outcome precedence is covered by tests.
6. Timeout handling terminates the complete process tree on Windows and preserves diagnostics.
7. Orchestrator budget handling and the CLI minimum of 30 are tested explicitly.
8. Fake-stub tests verify the runner without credentials, network access, or AI credits.
9. Chapter 1 tests remain green and the runner does not mutate the authoritative job journal.
10. The final chapter branch contains the contract, implementation, focused tests, and a progress
    record with exact verification commands and results.

## 6. Deferred decisions

- Public `factory job run` and its job-state preconditions belong to Chapter 4.
- Job event schemas for `run_started` and `run_finished` are owned by the orchestrator and must
  extend the Chapter 1 event contract without making the runner a second state writer.
- Isolated `COPILOT_HOME` remains deferred until the CLI's configuration redirection behavior is
  verified in a separate technical test.
- Exact per-role allow-list contents and allowed secret environment variable names must be fixed
  in the factory configuration before implementation, without committing secret values.
