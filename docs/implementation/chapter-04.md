# Chapter 4 — Implementation Contract: TDD Slice Builder

- **Status:** Approved for implementation — 2026-11-23
- **Source:** [`design.md`](../../design.md), Chapter 4 and related Chapters 1–3, 5, and 9
- **Dependencies:** [`chapter-01.md`](./chapter-01.md), [`chapter-02.md`](./chapter-02.md),
  [`chapter-03.md`](./chapter-03.md), [`design-deviations.md`](./design-deviations.md)
- **Approved deviations:** DD-23 and DD-24
- **Progress:** [`chapter-04-progress.md`](./chapter-04-progress.md), created when implementation starts

This contract translates the Chapter 4 review into a testable execution contract. It does not amend
`design.md`. Implementation starts only after this contract is explicitly approved.

## 1. Scope

### Goals

- Provide the M1 manual path for registering human-approved specifications and TDD tests without
  implementing the Chapter 5 spec-authoring or Chapter 6 test-authoring workflows.
- Use an initializer session to propose small implementation slices from the locked inputs, then
  require human approval before the slice plan or initializer script is used.
- Run one coding session per slice. The builder implements the locked TDD tests and cannot edit
  tests, the approved slice plan, scope, or job progress.
- Run the target project's initialization script before each slice session.
- Gate each slice, run the complete local unit-test suite after all slices, and run a final Gate
  before advancing the job to `REVIEW`.
- Preserve run, slice, Gate, and state history in one authoritative per-job event journal and
  recover projections deterministically after interruption.

### Deliverables

- A bounded manual input-registration command for the M1 approved spec and tests.
- An initializer workflow that proposes an immutable slice plan and a reviewed
  `.factory/init.py`.
- Slice-level TDD verification through the Chapter 3 Gate extension.
- An orchestrator-driven `factory job run` workflow with one Copilot session per slice and an
  explicit configured profile.
- Typed run/slice/Gate events in the Chapter 1 event journal; rebuildable feature and progress
  projections.
- Final unit-test and Gate execution, with a successful final Gate advancing the job to `REVIEW`.
- Focused CLI, unit, fake-process, and temporary-Git integration tests.
- This contract and a Chapter 4 progress record.

### Explicitly excluded

- AI authoring or approval of the specification and locked TDD tests. A person creates and approves
  both; AI may assist the person.
- Risk calculation or automatic profile routing (Chapter 7).
- Automatic repair/retry loops, review-finding repair, or budget policy beyond fail-closed
  escalation (Chapter 9).
- Reviewer invocation, PR creation, push, merge, or branch/worktree cleanup.
- Editable subagents, parallel slices, or concurrent jobs.
- Changes to `design.md`, unrelated Chapter 1/2 behavior, or unrelated target-project files.

### Dependencies and constraints

- Python >=3.12; verify locally with the available Python 3.14 runtime.
- Reuse the standard library, existing Pydantic dependency, Chapter 1 job/event helpers, Chapter 2
  Runner, and Chapter 3 Gate. Add no dependency.
- The target is an explicitly selected, trusted local Git repository. This workflow is not an OS
  sandbox; existing Chapter 2/3 security limitations remain.
- The profile is selected explicitly from configured profiles. Chapter 4 does not infer risk.
- Commands use argument arrays and `shell=False`. No Bash or PowerShell scripts are introduced.
- The initializer-created `.factory/init.py` is human-reviewed, included in the approved scope, and
  committed as the only target-worktree setup file. Its approved content is pinned before it is
  executed for slice sessions.
- The builder may commit scoped code but may not push. No failure path resets, deletes, or discards
  user work.
- The factory repository remains the sole writer of job metadata, feature status, and canonical
  progress.

## 2. Approved decisions and proposed contract details

### Approved user decisions

- A person creates and approves the spec and basic TDD tests; AI may assist. The approved contents
  are locked before the builder runs, and the builder cannot modify them.
- An initializer session proposes slices and their TDD acceptance mapping from those locked inputs.
  A person approves and locks the resulting plan before coding.
- Coding uses one new session per slice. The initializer's `.factory/init.py` is reviewed and run
  before each slice session.
- Gate checks each slice. After every slice is verified, the job enters `GATE`; the final Gate runs
  the complete local unit-test suite once as part of its full check.
- The job stays in `BUILDING` through slice checks. Final Gate PASS advances `GATE` to `REVIEW`.
- Gate failure, runner `ERROR`/`TIMEOUT`/`BUDGET`, initialization failure, or final unit-test
  failure stops the workflow, preserves work, and escalates to `NEEDS_HUMAN`. Chapter 4 adds no
  automatic retry.
- `events.jsonl` is the single authoritative journal. `features.json`, `job.json`, and
  `progress.md` are rebuildable projections. The orchestrator records verified progress; the agent
  supplies only a handoff summary.
- Subagents are read-only. The builder profile is explicit. The Chapter 4 progress file is
  `chapter-04-progress.md`.

### Approved M1 deviations

The following are explicitly approved, but do not amend `design.md`:

1. **Manual M1 registration (DD-23):** Chapter 4 may register already human-approved and locked
   spec/test artifacts and record the required readiness transitions. It does not author either
   artifact. The approved CLI and transitions are specified below, including the agreed
   test-manifest and baseline-red report schemas. Registration must preserve Chapter 1's guarded
   transition rules.
2. **Slice-aware Gate (DD-24):** Chapter 3 is extended to run a baseline that excludes pending
   locked TDD tests, to run only the selected slice's TDD tests between slices, and to run the full
   local checks after all slices. Gate remains state-neutral; only the Chapter 4 orchestrator
   records progress and advances job state.

### Responsibilities

- **Manual input registration:** records human approval and immutable hashes for the supplied spec
  and tests; verifies the inputs and legal state transitions; never authors them.
- **Initializer:** reads the registered inputs and proposes the slice plan, TDD-to-slice mapping,
  and `.factory/init.py`. Its output is pending until human approval. It does not set slice status
  or job state.
- **Builder:** receives one approved slice per session, reads the prior verified handoff, performs
  the TDD work, commits only scoped application changes, and returns a concise handoff. It cannot
  change tests, the approved plan, `passes`, or canonical progress.
- **Runner:** executes the selected configured profile and preserves per-run artifacts. It does not
  mutate the job journal.
- **Gate:** checks scope/protection and executes baseline, selected-slice, or final checks. It
  produces a report and does not mutate job state or feature status.
- **Orchestrator:** serializes the workflow under the per-job lock, invokes the Runner and Gate,
  appends authoritative events, rebuilds projections, and makes all deterministic state decisions.
- **Human:** approves the spec/tests, initializer outputs, and any resume after an escalated failure.

The approved specification is copied to `jobs/<job-id>/spec.md`; its relative path and SHA-256 are
recorded in the state-change event. The agreed test-manifest and baseline-red report formats are
defined in the next section.

### Workflow and state transitions

The user-visible sequence is:

```text
DRAFT_SPEC
  -> human registers approved spec
SPEC_APPROVED
  -> person attaches the prepared worktree
  -> human registers locked TDD tests; baseline-red validation succeeds
TESTS_RED_OK
  -> initializer proposes plan and init.py; human reviews and locks both
  -> BUILDING
  -> [one builder session -> slice Gate -> verified commit/handoff] for each pending slice
GATE
  -> final Gate runs the complete local unit suite and full checks once
  -> PASS
REVIEW
```

The job remains `BUILDING` while individual slices are checked. After all slices pass, it enters
`GATE`; the final Gate's full check is the single execution of the complete local unit suite, so
the orchestrator does not run those tests separately. Slice pass/fail is a workflow event, not a
job-state transition. A failure moves the job to `NEEDS_HUMAN`, saving the prior job state. After
explicit `resume`, the orchestrator may continue only from the first unverified slice and only when
the worktree is safe to use. It never silently cleans a dirty worktree.

**Approved CLI shape:**

```text
factory job prepare JOB_ID --stage spec|tests --path PATH --approve
factory job initialize JOB_ID --profile PROFILE
factory job prepare JOB_ID --stage plan --path PLAN_PATH --init-script PATH --approve
factory job run JOB_ID --profile PROFILE
factory job gate JOB_ID --base SHA --mode baseline|fast|full [--slice SLICE_ID]
```

- `prepare --stage spec` is allowed only from `DRAFT_SPEC`; successful human confirmation records
  the immutable spec hash and the `SPEC_APPROVED` transition.
- `prepare --stage tests` is allowed only after worktree attachment; it records the locked test
  manifest and test-file hashes, then invokes the target checks script's red-check mode against
  `work_branch_start_sha`. It advances through `TESTS_WRITTEN` to `TESTS_RED_OK` only when the
  report and hashes validate.
- `initialize` runs the specialized first session and stores its proposed plan and init script as
  pending job artifacts. It does not start a coding session or execute the proposed script.
- `prepare --stage plan` records human approval of the initializer output, stores an immutable
  `slice-plan.json`, and installs/commits only the approved `.factory/init.py` in the target
  worktree.
- `run` drives the approved job's pending slices sequentially. Every slice gets a distinct Runner
  session. The `--profile` value must match a configured profile; arbitrary model or permission
  values are rejected.
- `gate --mode fast --slice SLICE_ID` checks the selected slice. `baseline` excludes locked,
  not-yet-implemented job tests; `full` runs the complete local checks after all slices. `--slice`
  is invalid with `baseline` and `full`.
- Successful CLI commands emit JSON on stdout. Errors go to stderr with Chapter 1 exit-code
  conventions. A failed Gate returns nonzero.

### Test manifest and baseline-red report

The approved input manifest has this shape:

```json
{
  "schema_version": 1,
  "tests": [
    {"id": "tests.test_widget.WidgetTests.test_acceptance", "path": "tests/test_widget.py"}
  ]
}
```

`id` is a non-empty, unique selector interpreted by the target checks script. `path` is a
POSIX-style repository-relative path to a regular, non-symlink test file in the attached worktree.
Reject absolute paths, drive prefixes, `..` components, missing files, duplicate IDs, and an empty
test list. The factory stores an immutable normalized `jobs/<job-id>/tests.json` with the computed
SHA-256 for each path. The manifest intentionally has no `slice_id`; the approved slice plan maps
every test ID to exactly one slice.

For baseline-red validation, the factory invokes the target `.factory/checks.py` with
`--red --manifest PATH --baseline-sha <work_branch_start_sha>`. Its JSON report has this shape:

```json
{
  "schema_version": 1,
  "work_branch_start_sha": "full-commit-sha",
  "manifest_sha256": "sha256-of-stored-tests.json",
  "tests": [
    {"id": "tests.test_widget.WidgetTests.test_acceptance", "outcome": "ASSERTION_FAILURE"}
  ]
}
```

The result IDs must match the manifest IDs exactly once. Outcomes are `ASSERTION_FAILURE`,
`PASS`, or `ERROR`; registration succeeds only when the process exits `0`, the report matches the
attached branch's `work_branch_start_sha` and stored manifest hash, and every result is
`ASSERTION_FAILURE`. The project script is responsible for distinguishing assertion failures from
test/setup errors. This red check is input-readiness validation, not a Gate verdict.

The factory stores each baseline-red attempt under
`jobs/<job-id>/tests-red/<attempt-id>/`, preserving the raw report, stdout, stderr, and a result
summary. The first attempt records the immutable manifest and advances to `TESTS_WRITTEN` before
running checks. A failed or interrupted check stays in `TESTS_WRITTEN`; it may be retried only with
the identical normalized manifest. A successful report is referenced by hash from the
`TESTS_RED_OK` event.

### Slice plan and TDD contract

The approved plan is stored as `jobs/<job-id>/slice-plan.json` and is immutable after human
approval. It maps each feature to ordered slices. Each slice has a stable ID, a short goal,
in-scope paths, acceptance criteria, and test IDs from the locked manifest. During CH4-02 plan
validation, every registered test ID must map to exactly one slice before the plan is approved. A
feature is complete only when all its slices pass. A job is complete only when all planned features
pass.

The plan uses schema version 1: `features` is an ordered array of `{id, title, slices}` objects;
each slice is `{id, goal, paths, acceptance, test_ids}`. Slice paths are exact repository-relative
paths, not globs, and must match the approved scope. Feature and slice IDs are unique, acceptance
criteria and path lists are non-empty, and the test-ID mapping is complete and duplicate-free.

The initializer may propose a plan but cannot approve it, alter the spec/tests, or mark any
acceptance criterion passed. The orchestrator validates the plan against the approved spec and
scope, records its hash, and requires explicit human approval before starting a builder session.
The builder receives a new immutable task snapshot per slice with the relevant spec excerpt,
acceptance criteria, test references, scope, prior verified handoff, and recent Git history.

For each slice:

1. Verify the worktree and approved plan are unchanged and run the pinned `.factory/init.py`.
2. Start a fresh builder session with the selected profile and one slice only.
3. The builder runs the slice's TDD tests and makes the smallest change that satisfies them. It
   does not edit tests, scope, plan, or factory-owned job data.
4. The builder commits only the scoped code change, leaves no uncommitted changes, and returns a
   short handoff with tests run, commit SHA, and next-step context.
5. The orchestrator runs the slice Gate. Only a valid PASS allows it to append the slice-passed
   event and update projections.
6. A failed or incomplete slice is not marked passed and does not start the next slice.

The initializer commit contains only `.factory/init.py`. The locked slice plan and canonical
progress remain in the factory job directory; they are not duplicated as builder-editable target
files.

### Gate integration

Chapter 3 retains its existing scope and protected-path checks and remains state-neutral. Its
approved extension is:

- **Baseline:** run existing baseline checks without pending job TDD tests.
- **Slice:** the factory selects test IDs from the approved plan, supplies a filtered manifest, and
  runs only those TDD tests plus the target project's applicable fast checks.
- **Final:** run the complete local unit suite and target full checks after all slices pass.

Every attempt remains immutable and gets its own report directory. Reports identify the mode and,
for slice runs, the selected slice ID. The orchestrator—not Gate—records slice status and job-state
transitions. The final Gate must pass before `GATE -> REVIEW`.

### Persistence, atomicity, and recovery

- `events.jsonl` remains the only authority for job state and workflow outcomes. Every event retains
  the Chapter 1 metadata and a reconstructible job snapshot; run/slice/Gate events add validated,
  typed details such as run ID, feature/slice ID, outcome, report path, input hashes, and commit SHA.
  The immutable spec, test manifest, and slice plan are input artifacts whose hashes are recorded
  in the journal.
- Extend the event reader to validate the new event types without rejecting existing Chapter 1
  state/worktree events. Unknown event types, inconsistent state snapshots, duplicate IDs, or
  malformed journal tails fail closed.
- Append and fsync each event under the existing per-job lock before rebuilding `job.json`,
  `features.json`, or `progress.md`. `features.json` is rebuilt from the locked slice plan plus the
  journal; `job.json` and `progress.md` are rebuilt from the journal and verified handoff data.
  Projections are never used to infer a missing success event.
- `RUN_STARTED` without a matching `RUN_FINISHED` is interrupted, not successful. On recovery, do
  not launch another AI session automatically. If the worktree has uncommitted changes or the last
  verified commit cannot be established, preserve it and escalate to `NEEDS_HUMAN`.
- A commit may succeed before the corresponding Gate/event write. Recovery may rerun Gate against
  that exact commit and record a verified result; it must not repeat the coding session or create a
  success-shaped fallback.
- `progress.md` is an orchestrator-written projection. It includes verified slice, commit, test,
  and next-slice context plus the builder's handoff summary. A builder-authored statement alone
  never changes `passes`.
- No failure path resets, deletes, or discards worktree changes.

### Initializer and security boundary

- The initializer is a distinct first session with a specialized task; later sessions are coding
  sessions. It may propose only the approved plan and `.factory/init.py`.
- Its final `assistant.message` must be one JSON object with exactly `plan` and `init_script`.
  The orchestrator extracts that object from Runner JSONL output and stores attempt-scoped pending
  artifacts under the job directory; the initializer does not write proposal files into the target
  worktree.
- A human reviews and approves the script before any invocation. Its approved hash and exact path
  are recorded. The orchestrator rejects a missing, changed, symlinked, or out-of-scope script.
- Run the script with the configured Python executable, argument-array invocation, `shell=False`,
  a bounded timeout, and configured secret environment variables removed. Preserve stdout/stderr
  diagnostics. A script failure prevents the builder session.
- The approved target repository is trusted code, not sandboxed. Do not claim that script hashing
  or Gate validation provides OS-level isolation.
- Read-only subagents only. If the installed Copilot CLI cannot enforce read-only delegation, do
  not enable subagent/fleet execution; verify the exact CLI capability before implementation.

### Failure and recovery policy

- No automatic L1/L2 or retry loop is implemented in Chapter 4.
- Baseline, init, Runner, slice Gate, unit-test, final Gate, journal, or projection failure stops
  forward progress and escalates to `NEEDS_HUMAN` with a reason and durable diagnostics.
- Runner `BUDGET`, `TIMEOUT`, and `ERROR` preserve all committed and uncommitted work. Budget is an
  event/reason, not a new job state.
- `resume` restores the saved job state. The next `run` starts at the first unverified slice only
  after journal and worktree validation; ambiguous or dirty work requires human intervention.
- A Gate FAIL never starts review. A final Gate PASS is the only Chapter 4 path to `REVIEW`.

## 3. Implementation slices

Each slice follows RED → GREEN → REFACTOR. Do not create a RED-only commit. A slice is complete
only after its acceptance checks pass, the diff is reviewed, and the Chapter 4 progress record is
updated. Stage only slice-related files. Do not add a Copilot co-author trailer.

### CH4-01 — Registering human-approved M1 inputs

- **Observable behavior:** a person can register an already approved spec and locked tests without
  an AI authoring workflow; invalid or unapproved inputs do not advance the job.
- **Inputs/outputs:** job ID, registration stage, spec/test paths, explicit human confirmation;
  JSON containing recorded hashes, current state, and last event.
- **Failing test first:** assert that missing approval, invalid paths, mismatched hashes, and
  out-of-order registration fail without state or journal changes.
- **Acceptance criteria:** spec approval can reach `SPEC_APPROVED`; tests can be registered only
  after worktree attachment; baseline-red validation is explicit and fail-closed; test contents and
  paths are locked before the builder can run; manifest test IDs are unique. Slice mapping is
  validated during CH4-02, before the plan is approved.
- **Minimal implementation scope:** prepare CLI/service path, artifact hashing/validation, target
  red-check invocation, legal Chapter 1 transitions, journal events, and focused CLI tests.
- **Dependencies:** Chapter 1 job/event/worktree services, Chapter 3 checks execution, and the
  approved DD-23 deviation.
- **Verification:** temporary factory and target repositories; no Copilot credentials or network.
- **Completion:** valid human-prepared inputs reach `TESTS_RED_OK`; invalid cases leave no partial
  registration or illegal state.

### CH4-02 — Initializing and locking the slice plan

- **Observable behavior:** the first specialized session proposes the initialization script and
  slice/TDD map; no coding slice starts until a person approves and locks them.
- **Inputs/outputs:** locked spec/tests, scope, selected configured profile; initializer artifacts,
  hashes, proposed ordered slices, and approval state.
- **Failing test first:** use a fake Runner result to show that an unapproved or malformed plan
  cannot start a builder session or mark any slice passed.
- **Acceptance criteria:** one initializer run is durably recorded; `.factory/init.py` is
  human-reviewed and its path is in approved scope; its setup commit contains no other target
  files; the locked plan maps every registered test ID to exactly one slice; plan and script hashes
  are stable.
- **Minimal implementation scope:** initializer prompt/task, plan validation and approval,
  init-script verification/execution seam, and focused fake-runner tests.
- **Dependencies:** CH4-01, Chapter 2 Runner, and Chapter 3 baseline Gate extension.
- **Verification:** fake Copilot process and temporary Git worktree; no real AI session.
- **Completion:** only approved initializer outputs are persisted or executed; all rejected
  outputs fail closed.

### CH4-03 — Building and gating one TDD slice

- **Observable behavior:** one session implements one slice; only an independent slice Gate PASS
  advances it and allows the next slice to start.
- **Inputs/outputs:** approved feature/slice, explicit profile, prior handoff, init script, and
  locked tests; run artifacts, commit SHA, Gate report, and slice event.
- **Failing test first:** fake a passing runner with a failing selected-slice check; assert no pass
  event, no next-slice session, and no `passes=true`. Then fake a passing check and assert a
  verified commit is recorded once.
- **Acceptance criteria:** task scope is one slice; init runs before that session; tests/manifest
  remain unchanged; only the selected tests run; out-of-scope/protected changes fail; no push or
  reset occurs.
- **Minimal implementation scope:** sequential slice orchestrator, task snapshot, event/projection
  writes, Gate selector call, and focused fake-runner/Git tests.
- **Dependencies:** CH4-02, Chapter 2 Runner, and Chapter 3 slice Gate extension.
- **Verification:** fake process results and temporary Git repositories; assert event order,
  worktree preservation, and exact Gate selection.
- **Completion:** positive, negative, and interrupted slice cases pass; next work begins only from
  a journaled Gate PASS.

### CH4-04 — Recovering interrupted slices safely

- **Observable behavior:** a restarted workflow resumes from the first unverified slice without
  repeating an uncertain coding session or losing user work.
- **Inputs/outputs:** authoritative event log, run artifacts, current Git status/HEAD, and saved job
  state; recovered projections or explicit `NEEDS_HUMAN`.
- **Failing test first:** inject termination after `RUN_STARTED`, after builder commit, and after
  Gate PASS but before projection replacement; assert deterministic recovery at each boundary.
- **Acceptance criteria:** incomplete runs never count as success; a committed but unrecorded slice
  is re-gated rather than re-coded; dirty or ambiguous work is preserved and escalated; corrupt
  journals fail closed.
- **Minimal implementation scope:** typed event replay, projection rebuild, safe continuation
  checks, and crash-boundary tests.
- **Dependencies:** CH4-01 through CH4-03 and Chapter 1 locking/recovery.
- **Verification:** injected I/O failures and temporary repositories; verify no auto-clean or
  duplicate Runner invocation.
- **Completion:** every documented crash boundary either reconstructs the last verified state or
  reports an explicit human-recovery blocker.

### CH4-05 — Completing the job and entering review

- **Observable behavior:** after all slices pass, the job enters `GATE` and the final Gate runs
  full local unit tests once; only a final PASS advances `GATE -> REVIEW`.
- **Inputs/outputs:** all verified slices, full-check result, final Gate report; final job state
  and durable event/progress projection.
- **Failing test first:** assert a failed unit test or final Gate leaves review unopened and moves
  the job to `NEEDS_HUMAN`; then assert a passing final Gate records `REVIEW`.
- **Acceptance criteria:** no skipped or pending slice; the final Gate runs full local tests once
  after all slices; a final Gate report is present; Gate itself remains state-neutral; no reviewer
  or PR is launched.
- **Minimal implementation scope:** final verification orchestration, terminal workflow events,
  state transition, and CLI integration tests.
- **Dependencies:** CH4-03, CH4-04, and Chapter 3 final-check contract.
- **Verification:** fake target checks and temporary jobs, followed by the complete repository
  regression suite.
- **Completion:** only a fully verified job reaches `REVIEW`; all failure paths preserve work and
  have durable diagnostics.

## 4. Verification strategy

### Positive scenarios

- Human-approved spec/test registration reaches the legal ready state and records hashes.
- Initializer output is reviewed, locked, and committed within its approved path.
- Each slice gets a distinct run/session and exactly its selected tests.
- Slice Gate PASS records the verified commit and permits the next slice.
- All slices pass, the final Gate runs and passes the complete local unit suite, and the
  orchestrator records `REVIEW`.
- `features.json` and `progress.md` can be rebuilt from the locked plan, event journal, and verified
  handoff data.

### Negative scenarios

- Missing human confirmation, invalid approval stage, modified spec/test/plan, invalid profile, or
  out-of-scope init script.
- Init script timeout, launch error, nonzero result, changed hash, symlink, or missing file.
- Builder modifies locked tests, plan, scope, or factory job data.
- Slice Gate selects the wrong tests, reports malformed output, or detects scope/protected-path
  violations.
- Pending slices are skipped, a feature is marked passed by the agent, or Gate failure starts
  review.
- Invalid/unknown event types, duplicate event IDs, or malformed journal tails.

### Recovery scenarios

- Process stops after each durable event boundary and after a target-worktree commit.
- Runner times out, exhausts budget, or returns an error with partial artifacts.
- Gate fails after the builder has committed; the commit and diagnostics remain untouched.
- Event append succeeds but a projection write fails; projections rebuild from the journal.
- Event append fails after a commit; recovery re-runs Gate against that commit without starting a
  second builder session.
- Dirty worktree or ambiguous HEAD on resume escalates without automatic cleanup.

### Commands

Run focused tests during each slice, then:

```text
py -3.14 -m unittest discover -s tests -v
py -3.14 -m compileall -q factory tests
git -c core.whitespace=cr-at-eol diff --check
```

All automated tests use fake Copilot processes and temporary repositories. They must not require
Copilot authentication, AI credits, network access, production projects, or Git remotes.

## 5. Completion criteria

Chapter 4 is complete only when:

1. The M1 manual registration path requires human approval, locks spec/tests, and follows legal
   Chapter 1 transitions.
2. The initializer proposes slices and `.factory/init.py`; human approval and scope checks precede
   execution.
3. Every coding session handles exactly one slice, uses the explicit profile and TDD tests, and
   cannot modify locked tests or factory-owned status.
4. The Chapter 3 Gate supports baseline, selected-slice, and final checks while remaining
   state-neutral.
5. Slice pass status is recorded only after Gate PASS; full unit tests and a final Gate precede
   `REVIEW`.
6. `events.jsonl` remains authoritative and all projections recover from it after interrupted
   writes.
7. Failures preserve committed and uncommitted work, escalate to `NEEDS_HUMAN`, and do not trigger
   automatic retries or reviewers.
8. The complete test suite passes on the documented runtime; the full diff contains only approved
   Chapter 4/Chapter 3 extension, progress, and deviation-register changes.
9. `design.md` remains unchanged.

## 6. Decisions deferred beyond Chapter 4

- Risk calculation and automatic profile routing remain Chapter 7 work; Chapter 4 requires an
  explicit configured profile.
- Automatic L1/L2 repair and retry budgets remain Chapter 9 work.
- Reviewer selection, review verdicts, and PR workflow remain later-chapter work.
- The baseline-red report is an input-readiness check, not a Gate verdict; its target-script
  invocation and exact schema are fixed in §2.
- Read-only subagent enforcement must be technically verified against the installed Copilot CLI.
  If it cannot be enforced, subagent execution stays disabled.
