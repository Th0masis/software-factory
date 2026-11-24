# Design Review Outcome: Test Lifecycle and Feature Validation

- **Status:** Design decisions approved by the user on 2026-11-24; canonical design reconciled;
  follow-on implementation contract drafted for approval; not yet implemented.
- **Purpose:** Record the decisions from the Feature Validation Gate review and link the concrete
  follow-on contract without silently changing the Chapter 4 M1 contract.
- **Related design:** [`design.md`](../../design.md), especially Chapters 3-9.
- **Decision register:** [`design-deviations.md`](./design-deviations.md), DD-25 through DD-31.
- **Follow-on contract:** [`chapter-05-06-test-author-validation.md`](./chapter-05-06-test-author-validation.md).

## 1. Review conclusion

The proposal is directionally suitable for Software Factory, but it extends an existing
implementation rather than introducing a new Gate architecture. Slice execution, slice Gate,
final full Gate, baseline-red validation, locked test manifests, scope/protected-path checks,
event journaling, and fail-closed recovery already exist.

The major implementation gap is the future Test Author workflow. Test Author-created tests must
be persistent project tests, but a test commit created after the current `work_branch_start_sha`
can conflict with the Gate's protected-path and diff checks. The workflow therefore needs an
explicit test-only checkpoint and distinct branch and Gate baselines before Test Author can be
added safely.

## 2. Current implementation evidence

- [`factory/job.py`](../../factory/job.py) registers approved test manifests, records test-file
  hashes, validates baseline-red results, and runs a final Gate against `work_branch_start_sha`.
- [`factory/gate.py`](../../factory/gate.py) enforces scope and protected paths, validates check
  output, and creates per-attempt reports. Slice checks use selected registered test IDs; risk is
  reported as deferred.
- [`factory/cli.py`](../../factory/cli.py) exposes Gate modes `fast` and `full`, with an optional
  slice ID. Baseline-red is performed during test registration, not through a Gate CLI mode.
- [`docs/implementation/chapter-04.md`](./chapter-04.md) describes the M1 workflow: human-prepared
  inputs, immutable registered tests and plan, one Builder session per slice, commit before Gate,
  and no automatic retry.
- The `agents/` directory currently contains no role definitions, and the configured profiles
  currently cover Builder only. Test Author is a future workflow, not an implemented role.
- No GitHub Actions workflow is currently present in `.github/workflows/`.

These are observations of the current repository, not claims that the approved changes have already
been implemented.

## 3. Approved decisions

### 3.1 Test lifecycle and role ownership

1. Tests that protect lasting project behavior are project tests. They follow the target project's
   existing test layout, are committed to the project, discovered by its standard test process, and
   remain available to future development and CI.
2. Factory job artifacts may contain manifests, hashes, logs, and reports, but must not be the only
   copy of a lasting test. Job-local tests are limited to explicitly temporary experiments and
   diagnostics; they are not evidence of lasting regression protection.
3. Test Author creates independent acceptance tests from the approved specification before
   Builder implementation. A human performs G1 approval of those tests before Builder starts. G1
   approves the test design; it does not require the human to run automated tests manually.
4. Test Author runs in a separate session and profile. A different model family is configurable,
   not a hard requirement.
5. Builder may add lasting project tests for implementation, technical, and regression cases.
   Builder must not modify or remove the approved Test Author test set without a new human
   approval, a new hash, and a new baseline-red check. Builder-owned test changes remain visible
   in the diff and are subject to review and Gate execution.
6. Test Author's allowed paths are restricted to test-related paths by the job scope. The
   orchestrator checks the complete diff after the session and fails closed on out-of-scope
   changes, preserving them for human review. This is post-run enforcement, not an OS-level
   filesystem sandbox.

### 3.2 Acceptance Criteria and Gate semantics

1. Every Acceptance Criterion has a stable ID, such as `AC-001`, in the approved specification.
2. Every gating AC maps to one or more automated test IDs. A test may cover more than one related
   AC; tests without an AC mapping may still provide technical or regression coverage.
3. Manual execution is not a passing path through Feature Validation Gate. A required AC that
   cannot be automated must be made automatable, moved to a separately approved human-review
   checkpoint, or excluded from the automated gating scope before implementation. Gate must not
   claim a manual check passed without execution.
4. At the AC level, `PASS`, `FAIL`, `MISSING_TEST`, `NOT_EXECUTED`, and `SKIPPED` are distinct
   outcomes. Any missing, failed, skipped, or unexecuted required verification makes the overall
   Gate verdict `FAIL`.
5. `WARNING` is reserved for explicitly optional checks. It cannot mask an incomplete required
   check.

### 3.3 Gate workflow, commits, and retries

1. Keep one deterministic Gate implementation. The existing Slice Gate remains the per-slice
   check; the existing final full Gate serves as Feature/Job Validation after all slices. Do not
   add a second Gate engine or repeat the same test suite under a duplicate gate.
2. Builder commits the slice before Slice Gate. Gate checks the exact committed head. The slice is
   recorded as passed only after Gate PASS. A failed Gate preserves the commit and work and
   escalates; it does not reset or discard changes.
3. Chapter 4/M1 remains without automatic retry loops. Retry policy belongs in Chapter 9.
4. Slice checks run the selected slice tests and applicable fast checks. Final validation runs the
   complete project full checks. Risk-based E2E selection belongs in Chapter 7; until implemented,
   the Factory must not imply that risk routing occurred.

### 3.4 Test Author checkpoint and SHA ownership

After G1, the approved Test Author tests become a test-only checkpoint in the target project. The
orchestrator records and validates this checkpoint before Builder work begins.

The workflow must distinguish:

- `job_base_sha`: the original branch base, used to identify the complete job/PR diff and for audit;
- `gate_base_sha`: the approved Test Author checkpoint, used by implementation scope and
  protected-path checks;
- `head_sha`: the exact commit evaluated by a Gate attempt.

Test Author changes are validated separately against the original branch base: only approved test
paths may change, their hashes must match, and baseline-red must prove they fail against the
pre-feature implementation for assertion/behavior reasons rather than setup or syntax errors.
After G1 and successful baseline-red validation, the test-only checkpoint is recorded. Builder
slices and final Gate then use the approved Gate baseline. Review still sees the complete diff from
`job_base_sha`, including the Test Author checkpoint.

The checkpoint commit's exact creator and Git command belong in the follow-on implementation
contract. The approved invariant is that the checkpoint is durable, test-only, validated, and
journaled before Builder starts.

### 3.5 Gate report v2

Keep the current Gate CLI and engine. Expand the report to schema version 2 with at least:

- `schema_version`, `gate_type` (`slice` or `feature_validation`), `job_id`, `feature_ids`, and
  nullable `slice_id`;
- `job_base_sha`, `gate_base_sha`, and `head_sha`;
- start/end timestamps, duration, and overall `PASS` or `FAIL` verdict;
- each check's ID, required flag, status, exit code when available, and relative log artifact path;
- each AC's stable ID, status, and mapped test IDs;
- hashes for the approved test manifest and slice plan, plus a summary of tests actually executed;
- risk marked `DEFERRED` until Chapter 7 implements risk computation.

The report is deterministic and auditable. A missing, malformed, or internally inconsistent report
is a failure. The overall verdict is derived from required checks, not from Builder or another
agent's claim. Detailed counts are optional when the project's test runner cannot provide them
reliably.

### 3.6 CI and missing project test infrastructure

1. Factory may detect the project's test command and whether existing CI runs it. It must not
   modify CI workflow files without explicit human approval.
2. If the project has no usable test infrastructure, propose the minimum project-specific setup as
   a separately approved scope before implementing a feature. Do not silently add a framework,
   test layout, or CI workflow.

## 4. Recommended tests for Software Factory itself

The Factory test suite should prioritize observable deterministic guarantees:

1. **Unit tests:** state transitions, manifest and report validation, AC-to-test mapping,
   scope/protected-path matching, and Gate verdict derivation.
2. **CLI and contract tests:** JSON schema, exit codes, missing/skipped required checks, immutable
   artifacts, and useful failure diagnostics.
3. **Integration tests:** temporary Git repositories and worktrees with fake Runner/checks
   processes. Cover baseline-red, test checkpoint, slice Gate, final Gate, journal replay, and
   interruption recovery.
4. **Security and invariant tests:** path traversal, symlinks, out-of-scope writes, protected test
   mutation, malformed or forged reports, secret environment removal, and no push.
5. **Golden tasks:** a small representative set using a real agent to measure end-to-end feature
   success, false PASS, regression detection, review findings, and cost. Keep these separate from
   fast deterministic CI because they are slower, costlier, and less reproducible.

Acceptance tests should assert user-visible behavior. Builder tests should add technical and edge
case coverage; the Factory should not require redundant duplicate tests for every AC.

## 5. Remaining implementation mismatches

1. The approved Chapter 3/4 contracts specify a `baseline` Gate CLI mode, but the current CLI
   accepts only `fast|full`; baseline-red is part of test registration. Both contracts now record
   this mismatch. The follow-on contract does not silently expand the CLI; reconcile the baseline
   Gate mode separately before relying on it.
2. The canonical design now assigns publishing to a human, as required by DD-20. Chapter 3/4
   contracts retain their approved M1 wording and now explicitly note the CLI mismatch; the
   Test Author contract does not silently expand that CLI.
3. The current report schema is v1 and does not contain AC outcomes, the Test Author checkpoint, or
   the three SHA roles above.
4. The current Chapter 4 contract is complete for M1 and remains the source of truth for that
   implemented workflow. The decisions here govern follow-on work; they do not retroactively claim
   that Test Author or schema v2 already exists.

## 6. Recommended implementation order

1. **Approve the follow-on contract.** Review
   [`chapter-05-06-test-author-validation.md`](./chapter-05-06-test-author-validation.md), especially
   its workflow-selection, AC format, manifest v2, G1, checkpoint, and report v2 details. Reconcile
   the separately documented M1 baseline Gate-mode mismatch before implementing that mode.
2. **Define schemas and trust boundaries.** The draft contract specifies stable AC IDs, test
   ownership, Test Author scope, G1 evidence, baseline-red result, checkpoint event, and SHA roles.
3. **Implement Test Author preparation.** Run it in a separate session/profile; enforce test-only
   scope after execution; validate baseline-red; require G1; create and journal the test checkpoint.
4. **Implement Gate report v2.** Use the approved test checkpoint for slice and final checks while
   retaining the original base for audit and review. Resolve the M1 baseline-mode mismatch
   separately; this contract does not add that CLI mode.
5. **Extend Builder test handling.** Permit Builder-owned persistent tests without allowing
   mutation of the approved Test Author set; verify discovery and execution.
6. **Add project test/CI detection.** Recommend project-specific setup and require human approval
   for new infrastructure or CI changes.
7. **Validate with temporary Git integration tests and a small set of golden tasks.** Add
   risk-based E2E routing in Chapter 7 and retry loops in Chapter 9 only when those chapters are
   designed and approved.

## 7. Non-goals of this record

This record and the follow-on contract do not implement Test Author, change the M1 state machine,
introduce retries, add risk routing, create CI workflows, or authorize push/PR automation.
