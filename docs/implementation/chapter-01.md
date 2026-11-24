# Chapter 1 — Implementation Contract

- **Status:** Approved for implementation — 2026-11-20
- **Source:** [`design.md`](../../design.md), Chapter 1 and related pipeline, gate, builder, and recovery sections
- **Progress:** [`chapter-01-progress.md`](./chapter-01-progress.md)
- **Design delta register:** [`design-deviations.md`](./design-deviations.md)

This contract translates the reviewed Chapter 1 decisions into testable work. It does not amend `design.md`. Implementation must follow this contract; changes to its agreed requirements require explicit approval.

> **Approved v2 amendment — 2026-11-24:** New workflow-v2 jobs attach the prepared worktree in
> `DRAFT_SPEC` before G0 and record the workflow version at creation. Initial baseline and G0
> follow the [v2 contract](./chapter-05-06-test-author-validation.md). Existing M1 jobs retain
> the post-G0 attachment, state, journal, and recovery behavior below.

## 1. Scope

### Goals

- Create and inspect persistent jobs in the factory repository.
- Enforce a deterministic job state machine and persist every change.
- Support the initial `direct` operating mode: a person prepares a local branch after spec approval; the factory attaches a worktree to that existing branch.
- Recover job snapshots and interrupted worktree attachment without resetting or deleting branch work.
- Keep each vertical slice independently testable and record its verified commit in the progress file.

### Deliverables

- Python package/CLI entry point for `factory job`.
- Factory-root `config.json`; target-project `.factory/project.json`.
- Per-job `job.json` snapshot and append-only `events.jsonl`.
- `new`, `status`, `list`, administrative `transition`, and `resume` commands.
- Worktree attach, repair, and safe release operations for an existing local branch.
- Focused unit and Git integration tests.
- This contract, its progress record, and the cross-chapter design-delta register.

### Explicitly excluded

- Spec authoring, spec critic, or `approve-spec` validation (Chapter 5).
- TDD test generation, test-author routing, or the baseline red-check implementation (Chapter 6). The builder consumes/runs the prepared TDD tests; test authorship remains open for that chapter.
- Builder/runner, checks execution, gate verdicts, reviews, retries, budgets, routing, dashboard, or PR operations.
- Creating branches, branch-per-job mode, parallel jobs, and automatic branch/worktree cleanup.
- Pushing, rebasing, merging, resetting, or deleting a target branch.
- Changes to `design.md`.

### Dependencies and constraints

- Python >=3.12; verify locally with the available Python 3.14 runtime.
- Pydantic is the only planned runtime dependency. Use the standard library for process locking, Git invocation, and tests unless implementation evidence requires otherwise.
- Target repositories must be local Git repositories with a readable `.factory/project.json`.
- The initial execution model permits one active job globally. Chapter 1 provides per-job serialization; the runner/scheduler must enforce the global limit when introduced.
- Commands use argument arrays, never shell interpolation. Git/config paths and branch refs are validated before filesystem or Git operations.
- Runtime job data under `jobs/` and `worktrees/` is ignored by Git.

## 2. Architecture

### Responsibilities

- **CLI:** parse commands, return stable JSON success output, send diagnostics to stderr, and return a nonzero exit code on failure.
- **Job service:** create metadata-only jobs and expose validated state operations.
- **Event store:** append the authoritative per-job event stream, serialize writers with a cross-process per-job lock, and rebuild the snapshot.
- **Worktree service:** attach to an existing eligible local branch, inspect/recover an interrupted attachment, and release only a clean registered worktree.
- **Target project:** owns `.factory/project.json` and, in Chapter 3, its checks contract. The factory-side gate invokes that target contract; Chapter 1 does not run checks.

### CLI and paths

- `factory job new --project PATH --title TITLE` creates a metadata-only job in `DRAFT_SPEC`. It does not fetch, choose a branch, or create a worktree.
- `factory job status JOB_ID` shows the reconstructed job, last event, and worktree status.
- `factory job list` returns jobs in stable `job_id` order.
- `factory job transition JOB_ID NEEDS_HUMAN|FAILED --reason TEXT` is for administrative escalation only.
- `factory job resume JOB_ID --reason TEXT` returns only to the state saved when the job entered `NEEDS_HUMAN`.
- Worktree operations are exposed as `factory job worktree attach JOB_ID --branch LOCAL_BRANCH`, `repair JOB_ID`, and `release JOB_ID`. Attach requires `SPEC_APPROVED`; Chapter 5's `approve-spec` command records that state. Chapter 1 does not add a generic CLI path to bypass that precondition.
- Successful commands emit JSON on stdout. Errors go to stderr: exit `2` for usage/configuration errors and `1` for operational failures.
- The factory root `config.json` defines `jobs_dir` and `worktrees_dir`; relative values resolve from the factory repository root and must remain inside it. Defaults are `jobs` and `worktrees`.
- The CLI is invoked from the factory repository root. That current working directory is the factory root used to load `config.json` and resolve relative data paths.
- `--project PATH` is explicit and identifies the target repository root. Its `.factory/project.json` provides at least `project`, `remote`, and `base_branch`; known later fields such as `checks` and `pr_target` may be present but are not executed in this chapter. The chosen work branch is supplied to `attach`, not stored as a branch template in this project config.
- Job data lives at `jobs/<job_id>/`. Worktrees live at `worktrees/<project>/<job_id>/`.
- Proposed `job_id`: factory-generated UUID4. A project identifier must be a safe path component and must not map two different target repositories to the same worktree root.

### Workflow boundary

The approved sequence is:

`issue → job metadata (DRAFT_SPEC) → spec with agent → spec approval → person prepares a local branch → factory attaches worktree → TDD tests are ready/red → builder uses the tests → gate runs the checks contract → review`

Chapter 1 provides the job/state/worktree foundations only. `SPEC_APPROVED` is a precondition for branch attachment, but the command that validates and records spec approval is deferred to Chapter 5. Likewise, Chapter 1 stores later pipeline states but does not implement their owning workflows.

The builder uses and runs the TDD tests. Whether the builder or a separate test-author creates those tests is not decided by this contract; record and resolve it with Chapter 6.

### Job and event data

- `job.json` is a rebuildable snapshot, not an authority.
- `events.jsonl` is the authoritative per-job journal. Each event has a strictly increasing per-job `seq`, UTC timestamp, `job_id`, event type, actor, reason, and payload. State-change events also contain `from` and `to`.
- `JOB_CREATED` carries the complete initial job data. Every later mutation required to reconstruct the job is represented in an event payload.
- Initial job fields: `schema_version`, `job_id`, `title`, `state`, project identity/root/config path, `base_branch`, nullable `base_sha`, nullable `work_branch`, nullable `work_branch_start_sha`, nullable worktree path, worktree status, optional `resume_state`, and timestamps. Risk, budgets, spec/test hashes, sessions, features, and gate/review rounds are added by their owning chapters.
- On attachment, `base_sha` records the fetched configured remote base; `work_branch_start_sha` records the existing local work branch HEAD. They have distinct meanings.
- State changes append and flush/fsync the event before atomically replacing `job.json`. If the process stops after the event commit, readers rebuild the snapshot from the journal.
- A stale or invalid snapshot is regenerated from a valid event stream. A malformed or incomplete journal tail fails closed; it is not silently discarded or rewritten.
- Event append, snapshot rebuild, and state-changing operations run under a per-job cross-process lock. A global active-job scheduler is out of scope for this chapter.

### State machine

Initial state is `DRAFT_SPEC`. Normal forward transitions follow the pipeline in `design.md`; explicit retry edges are added where later chapters require them:

| From | To | Meaning |
|---|---|---|
| `DRAFT_SPEC` | `SPEC_APPROVED` | Human-approved spec; approval command is Chapter 5 |
| `SPEC_APPROVED` | `TESTS_WRITTEN` | TDD tests are available |
| `TESTS_WRITTEN` | `TESTS_RED_OK` | Baseline red check passed |
| `TESTS_RED_OK` | `BUILDING` | Builder may start |
| `BUILDING` | `GATE` | Builder step completed |
| `GATE` | `REVIEW` | Gate passed |
| `GATE` | `BUILDING` | Gate failed and work returns to builder |
| `REVIEW` | `READY_FOR_HUMAN` | Review accepted |
| `REVIEW` | `BUILDING` | Review requested changes |
| `READY_FOR_HUMAN` | `PR_OPEN` | PR opened (later chapter) |
| `PR_OPEN` | `MERGED` | Human merged PR |
| Any state except `NEEDS_HUMAN`, `MERGED`, or `FAILED` | `NEEDS_HUMAN` or `FAILED` | Explicit, reasoned escalation/failure |
| `NEEDS_HUMAN` | Saved prior state | Explicit `resume` |
| `NEEDS_HUMAN` | `FAILED` | Human closes job as failed |

`MERGED` and `FAILED` are terminal. `BUDGET_EXHAUSTED` is an event/reason that escalates the job to `NEEDS_HUMAN`, not a job state. Retry limits belong to Chapter 9. Normal forward transitions are invoked by their owning workflow commands; the generic CLI command cannot bypass them.

### Direct worktree lifecycle and errors

- A job starts without a branch/worktree. After spec approval, a person prepares an existing local `refs/heads/...` branch without checking it out elsewhere, then supplies it to `worktree attach`.
- Attach rejects invalid refs, missing local branches, branches already checked out in any worktree, occupied paths, and mismatched project identities. A remote-tracking-only branch must first be checked out by a person.
- Attach fetches the configured base remote, records `base_sha` and the selected branch's current `work_branch_start_sha`, then attaches the branch at the registered factory worktree path. It never creates or resets a branch.
- Persist an attach intent before Git side effects. When repairing an interrupted attach with no recorded `base_sha`, retry the configured base fetch and record its result before attaching; never treat a cached remote-tracking ref as proof that the fetch succeeded. If `base_sha` is already recorded, repair does not fetch again. Repair validates ownership and reattaches the same branch at its current HEAD without reset or deletion. Collisions fail closed.
- Persist a release intent before removing a worktree. If `repair` finds a job in `releasing`, it removes the still-registered worktree only if clean, or completes the journal if Git already removed it. A missing path that Git still registers fails closed.
- A failure or budget event preserves committed and uncommitted work and sends the job to `NEEDS_HUMAN`. No automatic rollback or branch reset.
- `release` is explicit and succeeds only for a clean registered worktree. It removes the worktree without force and keeps the branch. Dirty worktrees are refused. No automatic release.
- Git failures, lock failures, invalid config, missing jobs, corrupt event logs, illegal transitions, and failed snapshot recovery are surfaced explicitly. A failed cleanup is reported alongside the original error; it is never reported as successful.

## 3. Implementation slices

Each slice follows RED → GREEN → REFACTOR. Its progress entry is updated before staging; only slice-related files are staged. Do not create a RED-only commit. A slice is complete only after its acceptance checks pass, the diff is reviewed, and its focused Conventional Commit is verified. The commit body includes `Slice: CH1-XX`, `Tests: PASS`, and `Acceptance: VERIFIED`, plus `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`. Never amend or rewrite a completed slice commit.

### CH1-01 — Creating metadata-only jobs

- **Observable behavior:** `new` creates a persistent `DRAFT_SPEC` job with no branch/worktree; `status` and `list` return stable JSON.
- **Inputs/outputs:** `--project PATH --title TITLE`; stdout includes the generated job ID and initial state. Invalid/missing project config or blank title returns an error and creates no partial job.
- **Failing test first:** invoke `new` against a temporary factory root and target Git repository; assert job snapshot/event, `DRAFT_SPEC`, and no worktree/branch side effect.
- **Acceptance:** IDs are unique; jobs are isolated; project configuration is validated; `status`/`list` show created jobs deterministically; malformed input fails visibly.
- **Minimal scope:** packaging/CLI bootstrap, strict config/data models, initial event/snapshot creation, basic commands, focused tests.
- **Dependencies:** none.
- **Verification:** unit and CLI tests on temporary directories; no real project changes.
- **Completion:** all slice tests pass; progress lists commit SHA and exact verification command; worktree has no unintended changes.

### CH1-02 — Enforcing transitions and journal recovery

- **Observable behavior:** only legal state transitions are persisted; status recovers a stale snapshot and reports the last event.
- **Inputs/outputs:** job ID, target/action, actor, and required reason; successful event sequence and reconstructed snapshot.
- **Failing test first:** table-driven tests for every allowed and rejected edge, including resume to the saved state; add crash-boundary tests for event committed/snapshot stale and corrupt-tail rejection.
- **Acceptance:** unknown/illegal states fail closed; event sequence and state are deterministic; a stale snapshot is rebuilt; corrupt log is not silently repaired; concurrent writers serialize per job.
- **Minimal scope:** transition graph, event schema/replay, atomic snapshot writer, per-job lock, `transition`/`resume`, regression tests.
- **Dependencies:** CH1-01.
- **Verification:** unit tests, subprocess lock test where supported, injected snapshot-write failures.
- **Completion:** recovery and negative cases pass; progress records the commit SHA.

### CH1-03 — Attaching and recovering a direct worktree

- **Observable behavior:** after spec approval, the factory attaches a person-prepared local branch; repair is safe/idempotent; release removes only a clean worktree and retains its branch.
- **Inputs/outputs:** job ID and local branch; project remote/base config; recorded `base_sha`, `work_branch_start_sha`, path, status, and events.
- **Failing test first:** use a temporary Git repository with a local branch; assert attach succeeds only when eligible and does not create/change the branch ref.
- **Acceptance:** missing/invalid/already-checked-out branch and path collision fail without modifying user work; interrupted attach repairs from the existing branch; dirty release fails; clean release leaves the branch intact; interrupted release is safely completed before or after Git removal; no reset/delete/force operation is invoked.
- **Minimal scope:** Git argument-array helper, attach/repair/release operations, worktree status in job snapshot/events, temporary-repository integration tests.
- **Dependencies:** CH1-01 and CH1-02.
- **Verification:** Git integration tests in temporary repositories, including interrupted attach/release before and after side effects and negative cases; no network credentials or production repositories.
- **Completion:** all positive, negative, and recovery tests pass; progress records commit SHA and clean slice diff.

## 4. Verification strategy

- Use Python `unittest` from the standard library unless repository tooling added in CH1-01 requires a documented alternative.
- Run each focused test module during its slice; run the complete suite at the end:
  `uv run --python 3.14 python -m unittest discover -s tests -v`
- Exercise the CLI as a subprocess so exit status, stdout JSON, and stderr behavior are verified.
- Use temporary target repositories and local remotes only. Never fetch/push against a real project in automated tests.
- Cover positive paths: create, query/list, valid transitions, worktree attach, repair, clean release.
- Cover negative paths: malformed config/event, illegal transitions, absent branch, invalid branch ref, branch already checked out, path collision, dirty release.
- Cover recovery: stale snapshot reconstruction, partial event append fails closed, interrupted attach repair, snapshot replacement failure.
- Review `git diff` and `git status` after every slice; stage only its files. Do not commit with failing required tests.

## 5. Completion criteria

Chapter 1 is complete only when:

1. All three slices have verified commits and corresponding progress entries.
2. The state machine rejects every undefined edge and records every accepted change.
3. Job snapshots recover from the authoritative journal after an interrupted write.
4. Worktree attach/repair/release preserve user branches and changes; no reset, force, or branch deletion is performed.
5. The complete relevant test suite passes on the documented runtime.
6. The final diff contains only approved Chapter 1 scope and the progress/deviation records are current.

## 6. Deferred decisions

- **TDD test ownership:** this contract interprets “builder uses TDD tests” as the builder consuming/running tests prepared before it. Chapter 6 must decide whether a separate test-author creates them, as proposed in `design.md`. This does not block Chapter 1.
- **Worktree attach availability:** Chapter 1 implements and tests attach with the `SPEC_APPROVED` precondition. The normal workflow reaches that state when Chapter 5 adds `approve-spec`; no unguarded forward-transition escape hatch is added.
