# Chapters 3, 5, and 6 — Baseline, Test Author, and Feature Validation Contract

- **Status:** Revised draft for approval. The workflow and exception direction were confirmed in
  discussion; the implementation details below require approval before coding.
- **Source:** [`design.md`](../../design.md), Chapters 3–6, and DD-25 through DD-31 in
  [`design-deviations.md`](./design-deviations.md).
- **Related M1 contract:** [`chapter-04.md`](./chapter-04.md) remains authoritative for existing
  human-prepared jobs. The new-job workflow proposed here changes their future defaults without
  reinterpreting existing jobs.
- **Proposed amendments:** Before implementation, approval of this draft must explicitly resolve
  the conflicting worktree-ordering and Gate requirements in Chapters 1, 3, and 4 and DD-03,
  DD-08, DD-26, DD-29, and DD-30. This draft does not silently revise those approved records.

## 1. Goal and scope

For each new job, establish the target project's initial test state before specification approval
or project changes, then automatically run Test Author after spec approval. Preserve approved
independent tests in the target project and provide traceable Gate evidence for every required
Acceptance Criterion (AC).

One job may contain multiple features and ordered slices. Run one initial baseline for the job,
Slice Gate after each slice, and one final full Gate for the complete job. Keep one deterministic
Gate implementation. Builder commits before Slice Gate; only PASS or an explicitly qualified
PASS_WITH_EXCEPTION records a slice as passed. Final validation runs the complete project
test/check suite before review.

### In scope

- Stable AC IDs and strict validation of AC-to-test coverage.
- Pre-specification worktree attachment, test-environment discovery, and initial baseline.
- Explicit human-approved exceptions for identifiable pre-existing test failures.
- Automatic Test Author invocation using a separate configured profile, with model family configurable.
- Post-run path-scope validation, G1 approval, baseline-red validation, and an orchestrator-owned
  test-only checkpoint.
- Distinct original job base, approved Gate base, per-attempt comparison base, and tested head.
- Gate report schema v2 and recovery for interrupted checkpoint/report creation.
- Focused unit, CLI, and temporary-Git integration tests.

### Out of scope

- Reinterpreting or rewriting existing M1 jobs, their events, or their artifacts.
- OS-level sandboxing of the Test Author.
- Automatic retries, risk routing, or E2E selection; those belong to Chapter 9 and Chapter 7.
- Treating manual verification as passing automated AC evidence.
- Automatic CI/test-infrastructure changes, Git push, PR creation, or merge.
- A second Gate engine, a new test framework, or a Factory-wide required test directory.

## 2. Compatibility and job workflow

Existing M1 jobs continue using their current registration, baseline-red, Gate, and recovery
behavior, including existing states, transitions, report schemas, and event replay. They must not
be reinterpreted as new-workflow jobs. Existing report files remain immutable and are not migrated.

Once this workflow is implemented, new jobs default to an initial baseline and Test Author; neither
requires an opt-in. The workflow version must be recorded durably at job creation so replay does
not infer it from missing fields or later configuration. Legacy records without that version
continue on the M1 path. New workflow stages must not become prerequisites for existing M1 jobs.

The initial worktree is attached while the job is in `DRAFT_SPEC`, before G0. As in M1, a person
prepares an existing local branch; Factory does not create, reset, push, or delete it. The attached
branch HEAD is the first baseline's candidate SHA. Once a baseline or its exception disposition
is accepted, its SHA becomes `job_base_sha`. The baseline is run once for the job, not once per
feature or slice. If the branch HEAD or relevant environment changes before Test Author begins,
the original baseline cannot silently be reused: preserve the evidence and require a new baseline
for the actual starting SHA and environment. If the SHA changes after G0, require fresh human
specification approval against the new baseline before Test Author starts.

The proposed user-facing operations are:

```text
factory job new --project PATH --title TITLE
factory job worktree attach JOB_ID --branch LOCAL_BRANCH
factory job prepare JOB_ID --stage spec --path SPEC_PATH --approve
factory job prepare JOB_ID --stage tests --path CANDIDATE_MANIFEST --approve
```

For a new-workflow job, orchestration automatically performs preflight, discovery, and baseline
after attachment, and automatically starts Test Author after successful G0. It uses a configured
Test Author profile, not an arbitrary model or permission supplied by an agent. The exact commands
for baseline failure repair, exception approval, and explicit continuation after interruption
remain open for approval; none may silently approve a failure or automatically retry an agent.

The Test Author's final response contains a strict JSON candidate manifest with test IDs,
project-relative paths, and AC IDs. The orchestrator validates it and writes the candidate manifest
under the factory-owned job directory; the agent cannot write job data directly. The human reviews
that exact manifest and worktree diff through G1 registration.

Candidate shape:

```json
{
  "schema_version": 2,
  "tests": [
    {
      "id": "profile-save-roundtrip",
      "path": "tests/profile/test_save.py",
      "acceptance_criteria": ["AC-001"]
    }
  ]
}
```

The orchestrator computes file hashes and adds them to the approved `tests.json`; it does not trust
hashes supplied by the agent.

The job sequence is:

1. A person prepares the branch. Factory attaches a clean worktree before G0, checks the repository,
   worktree, dependencies and environment, records `job_base_sha`, and discovers test conventions.
2. Factory runs initial baseline against the unchanged project. A failure blocks the workflow until
   it is fixed and a new baseline passes, or a person approves an eligible exception.
3. A person approves the specification, gating ACs, and scope at G0.
4. Factory automatically starts Test Author in a separate session/profile. It writes candidate
   tests only within the approved project test paths.
5. Orchestrator validates the complete diff and creates a candidate manifest.
6. Human G1 approves the exact manifest, paths, AC mapping, and test content.
7. Factory runs baseline-red against `job_base_sha`. Invalid setup, syntax, imports, or reports do
   not count as expected red.
8. Orchestrator commits only the approved test paths, verifies the commit, and records
   `gate_base_sha`.
9. Builder works in one session per approved slice. Slice Gate runs after each; final Gate runs once.
10. Review uses the complete diff from `job_base_sha`; a human publishes after review.

Any manifest/content change after G1 invalidates that approval and requires a new G1 and
baseline-red result. A failed stage preserves work and evidence and stops before Builder starts.

## 2A. Initial baseline and exceptions

### Preflight and discovery

Preflight verifies that the attached branch and worktree are usable and that the required
environment and configured checks are available. Discovery records the project's test structure,
test commands and CI conventions as available. Discovery is informational: Factory must not invent
or execute guessed commands, install dependencies, or modify project configuration or CI. The
initial baseline executes the trusted project checks contract; if no usable checks contract or
test infrastructure exists, stop and propose a separately approved setup.

Run the existing full project checks at the exact recorded candidate SHA, before Test Author or
other feature changes. Check for tracked and untracked non-ignored changes before and after the
run; a dirty or changed worktree or changed HEAD invalidates the attempt and preserves evidence.
This is not an OS sandbox and does not prove that ignored files or external services were untouched.
Record the precise invocation, SHA, relevant environment/tool identities, check results, timestamps,
and stdout/stderr artifacts. A valid passing report establishes the initial baseline; it does not
prove that later failures have the same or a different cause.

If a baseline fails, preserve the attempt and block G0 and subsequent work. A person may repair
the project outside Factory's feature changes, after which Factory must record the actual new
starting SHA and run a new baseline. Alternatively, a person may approve specific eligible
pre-existing test failures as exceptions. The failed baseline is never rewritten as PASS.

### Exception eligibility and approval

An exception may cover only an individually identified existing project test observed failing in
the initial baseline. It cannot cover a whole suite, a new acceptance test, a missing or skipped
test, a setup/import/syntax failure, an invalid or missing report, an interrupted check, or a
scope, protected-path, integrity, or security failure. Required ACs still need passing automated
evidence; an exception cannot make an AC pass.

The checks adapter must report executed test IDs and individual outcomes with sufficient
failure evidence to compare the approved baseline failure to each subsequent occurrence. A
check-level aggregate alone is insufficient for an exception. If the adapter cannot provide
reliable per-test evidence, Factory must block and ask for human repair or improved reporting;
it must not infer the result from a process exit code or blanket-waive the suite. A v1 target
report can still support an unqualified baseline PASS where its existing contract is satisfied.
The exact versioned target-checks per-test report schema and failure-comparison algorithm are
open design items; they require approval before implementing exception handling. In particular,
matching a test ID or failure text is evidence, not proof of an unchanged root cause.

Each G1-independent exception approval is an explicit human action, persisted in the job journal
and bound to the baseline attempt/report hash, initial SHA, exact test ID, recorded failure
evidence, reason, and approval identity or equivalent auditable actor. It authorizes only the
documented failure for this job; it does not change the recorded test result or the approved spec.
Each later Gate that executes the excepted test must validate the approval, check the actual
execution outcome and compare its failure evidence. Changed or ambiguous failure evidence,
missing required execution, or additional failures block progress pending human review. If the
previously failing test passes, report it as PASS rather than as an active exception; a later
recurrence requires fresh human review.

Approved exceptions apply to the initial baseline and to a Slice Gate or final Gate only when that
specific existing test is actually executed and its failure is eligible. Tests outside the
selected slice's applicable checks are not treated as missing exception results. A valid slice or
final attempt with at least one such failure and no other failure yields `PASS_WITH_EXCEPTION`,
not PASS. A slice with that verdict may advance, and a final Gate with that verdict may enter
review, with the exception prominently disclosed. No exception may conceal an unexecuted required
check or new failure.

The initial baseline report is written before any exception can be approved and remains immutable
with its actual FAIL. Once all eligible failures are explicitly approved, Factory records a
separate durable baseline disposition of `PASS_WITH_EXCEPTION` referencing that report and the
approvals. G0 may proceed only after this disposition is validated; Factory does not rewrite the
report or rerun checks merely to manufacture a qualified result.

## 3. AC and test manifest contracts

### 3.1 Acceptance Criteria

The approved `spec.md` has one `## Acceptance Criteria` section. Each gating criterion is a
single-line bullet in this form:

```markdown
- AC-001: A user can save a valid profile and retrieve the same values.
```

IDs match `AC-[0-9]{3,}`, are unique within the approved spec, and remain stable across amend
revisions where the behavior is unchanged. Every gating AC must be automatable. Human-only checks
are outside this automated Gate contract and cannot satisfy an AC PASS.

### 3.2 Registered Test Author manifest

The factory-owned `jobs/<job-id>/tests.json` uses schema version 2:

```json
{
  "schema_version": 2,
  "tests": [
    {
      "id": "profile-save-roundtrip",
      "path": "tests/profile/test_save.py",
      "acceptance_criteria": ["AC-001"],
      "sha256": "64-lowercase-hex-characters"
    }
  ]
}
```

Validation rules:

- Test IDs are unique, non-empty stable identifiers.
- Paths are normalized repository-relative paths, stay within the approved test scope, and do
  not traverse symlinks or escape the worktree.
- Each Test Author test maps to one or more known AC IDs. Every gating AC maps to at least one
  Test Author test.
- Candidate hashes are computed by the orchestrator from exact worktree bytes before G1 so the
  human approves the precise content. Recompute and compare them after G1 and before checkpoint.
  The manifest is stored outside the Builder-writable worktree.
- The manifest contains only the approved independent Test Author set. Builder-authored tests are
  ordinary project tests; they do not replace or rewrite this manifest.
- M1 schema-v1 manifests remain readable by the existing M1 path. Do not silently upgrade or
  reinterpret them.

The existing slice plan assigns each registered Test Author test ID to exactly one slice. Slice
Gate reports only ACs exercised by that slice; final Feature Validation reports every gating AC.
Project checks must include ordinary Builder-authored tests through the project's standard
discovery in `--fast`/`--full`. Slice `--fast` must also run the applicable existing regression
checks while excluding pending job tests; the selected slice tests must still run. Factory must
not claim those regression checks ran when the target adapter cannot establish that they did.
Builder test additions still obey the approved scope and hard protected-path policy.

## 4. G1, baseline-red, and checkpoint

### G1 and Test Author scope

The Test Author receives the approved spec, project conventions, and approved test-path scope. A
separate session/profile is mandatory; a different model family is optional. The Test Author has
no commit or push authority.

After the session, the orchestrator compares the complete worktree against `job_base_sha`.
Only declared test-path changes are candidates. Any other change fails closed and is preserved for
human review. This is post-run enforcement, not an OS sandbox.

G1 approves the exact candidate diff and AC mapping. The approval is bound to the manifest content
hash and test paths. It may authorize additions or updates only at those exact test paths; deleting
an existing test file is rejected. A broad protected glob may be overridden only for these exact
G1-approved paths during this phase; G1 does not authorize changes to any other protected path or
a general bypass of `.factory/protected.txt`. After checkpoint, the Test Author file hashes are
immutable for the Builder.

### Baseline-red

Use the existing target-checks red invocation:

```text
.factory/checks.py --red --manifest PATH --baseline-sha <job_base_sha>
```

The report must bind to the exact manifest hash and `job_base_sha`, contain exactly one result per
registered test, and classify each expected failure as an assertion/behavior failure. A passing
test, syntax/import/setup failure, missing result, malformed report, or unexpected test ID blocks
the checkpoint. Baseline-red is input readiness, not a Gate mode or verdict.

### Test-only checkpoint

After G1 and successful baseline-red, the orchestrator—not the Test Author—creates one commit
containing only the approved Test Author test paths. It must:

1. stage and commit the exact approved path list, never the whole worktree;
2. verify the commit's changed paths and content hashes against G1;
3. record the resulting full commit SHA as `gate_base_sha`;
4. persist the manifest, G1 evidence, baseline-red report, checkpoint SHA, and journal event before
   starting Builder.

A crash before the checkpoint event is durable must be recoverable by inspecting Git and the
journal. If the commit cannot be proven to contain exactly the approved test content, fail closed
and require human repair; never reset or delete the commit automatically.

The implementation may reuse the orchestrator-only, exact-path commit precedent in
`factory/job.py`. The Test Author itself must never create the checkpoint.

## 5. SHA roles and Gate invocation

New-workflow jobs and their reports distinguish:

- `job_base_sha`: the accepted starting SHA after baseline or approved exception disposition
  (`work_branch_start_sha` in existing M1 records). It anchors audit, Test Author validation, and
  the complete review/PR diff; earlier rejected baseline attempts retain their own SHA.
- `gate_base_sha`: approved Test Author checkpoint; unavailable until that checkpoint is verified.
  When represented for manual M1 jobs it equals `job_base_sha`.
- `comparison_base_sha`: the explicit base passed to one Gate attempt. For a slice, this is the
  commit immediately before that slice; for final validation, it is `gate_base_sha`.
- `head_sha`: exact committed HEAD evaluated by that Gate attempt. For the initial baseline it
  equals `job_base_sha`.

This draft proposes that the orchestrator invoke initial baseline automatically, with no new
user-facing `baseline` Gate CLI mode. The existing Gate CLI remains
`factory job gate JOB_ID --base SHA --mode fast|full [--slice ID]`. For the earlier Chapter 3/4
proposal of a separate baseline check excluding pending tests, this draft instead requires slice
`--fast` to execute applicable regression checks without pending tests, as specified above. The
initial baseline and this slice requirement serve different purposes; neither is baseline-red.
This replacement needs explicit approval because it changes the approved Chapter 3/4 contract.

The Test Author baseline-red check remains a separate target-checks invocation. For each slice or
final Gate attempt, the orchestrator supplies the proper `comparison_base_sha` as `--base`; the
report also retains the fixed `job_base_sha` and `gate_base_sha`.

The full review diff starts at `job_base_sha`, so it includes the Test Author checkpoint. Gate
scope for implementation starts at `gate_base_sha`.

## 6. Gate report schema v2

Each initial baseline, slice, or final attempt writes an immutable report and logs under
`jobs/<job-id>/gate/<attempt-id>/`. The example shows a slice after all inputs exist:

```json
{
  "schema_version": 2,
  "gate_type": "slice",
  "job_id": "job-id",
  "feature_ids": ["feature-1"],
  "slice_id": "slice-1",
  "job_base_sha": "full-sha",
  "gate_base_sha": "full-sha",
  "comparison_base_sha": "full-sha",
  "head_sha": "full-sha",
  "started_at": "RFC3339 timestamp",
  "completed_at": "RFC3339 timestamp",
  "duration_ms": 1234,
  "checks": [
    {
      "id": "slice-tests",
      "required": true,
      "status": "PASS",
      "exit_code": 0,
      "log_path": "checks.stdout.txt"
    }
  ],
  "acceptance_criteria": [
    {
      "id": "AC-001",
      "status": "PASS",
      "test_ids": ["profile-save-roundtrip"]
    }
  ],
  "inputs": {
    "spec_sha256": "64-lowercase-hex-characters",
    "tests_sha256": "64-lowercase-hex-characters",
    "slice_plan_sha256": "64-lowercase-hex-characters",
    "scope_sha256": "64-lowercase-hex-characters"
  },
  "tests": {"executed_ids": ["profile-save-roundtrip"]},
  "exceptions": [],
  "risk": {"status": "DEFERRED"},
  "verdict": "PASS"
}
```

Allowed `gate_type` values are `initial_baseline`, `slice`, and `feature_validation`. Initial
baseline precedes G0, so its `feature_ids` and `acceptance_criteria` are empty, `slice_id` and
`gate_base_sha` are null, and the four not-yet-approved input hashes in `inputs` are null. Its
`comparison_base_sha` and `head_sha` equal `job_base_sha`. It records the baseline invocation and
environment identities as additional evidence; their exact schema is an open item. Slice reports
include the selected slice's applicable ACs; final reports include all gating ACs and use
`slice_id: null`. The initial failed attempt keeps `verdict: "FAIL"`; the separate, later
baseline disposition records `PASS_WITH_EXCEPTION` after approval.

Check statuses are `PASS`, `FAIL`, `SKIPPED`, `NOT_EXECUTED`, or `WARNING`; `WARNING` is valid only
for an explicitly optional check. AC statuses are `PASS`, `FAIL`, `MISSING_TEST`, `SKIPPED`, or
`NOT_EXECUTED`. The `exceptions` array is empty for an unqualified PASS. Otherwise, each entry
must identify the human approval, original baseline attempt and report hash, test ID, baseline and
current failure evidence, and links to the relevant logs. The corresponding check/test result
remains visibly FAIL; an exception is never represented as a passing test.

The overall verdict is:

- `PASS`: all required checks and applicable ACs pass, and no active exception is needed.
- `PASS_WITH_EXCEPTION`: all otherwise-required evidence is valid, every applicable AC passes,
  scope/protected-path and integrity checks pass, and every remaining failure is an executed,
  individually approved and matching pre-existing test failure listed in `exceptions`.
- `FAIL`: any other required check fails or is missing, skipped, unexecuted, malformed,
  inconsistent, or not demonstrably covered by a valid exception.

Project checks must provide enough structured evidence to distinguish expected failed test
results from infrastructure or process errors. An exit code is recorded and validated against
that report; a nonzero exit alone is neither a PASS nor sufficient evidence for an exception.
The exact target adapter and process-exit contract for qualified exceptions is an open item. Do
not derive a verdict from an agent claim or from process exit code alone.

## 7. Journal, recovery, and failure behavior

- Keep state transitions under the existing orchestrator. Gate remains state-neutral.
- Record workflow version, initial baseline attempts and environment, human exception approvals,
  Test Author start/completion, G1 approval hash, baseline-red report reference, checkpoint SHA,
  and Gate report references in durable job events/snapshots. Do not store approval only in an
  ephemeral session transcript.
- A new-workflow Builder cannot start unless the job has a valid baseline or approved exceptions,
  valid approved manifest, successful baseline-red evidence, and verified `gate_base_sha`.
- Automatically invoke Test Author only once after G0; a start without a durable completion
  is interrupted, not successful. Preserve its work and require human intervention rather than
  starting another session automatically.
- An interrupted initial baseline is not a successful attempt. Preserve its artifacts and require
  explicit continuation; do not infer a verdict from a partial report.
- Before each slice and final Gate, verify approved Test Author file hashes. Any mismatch fails
  closed before project checks execute.
- If the report is missing, malformed, or has inconsistent SHAs/hashes, the attempt fails and
  review is blocked.
- Preserve failed work, logs, manifests, and reports. Recovery may complete a provable interrupted
  journal/write operation, but must not rerun an agent, rewrite approvals, or discard a commit.
- Chapter 4/M1 remains retry-free. New-workflow failures stop and require human action; an
  explicitly requested new baseline after repair is not an automatic retry loop.

## 8. Required validation

### Unit and contract tests

- Verify pre-spec worktree attachment and automatic baseline/Test Author apply only to new-workflow
  jobs; existing M1 event replay and transitions remain unchanged.
- Reject dirty or changed worktrees, changed SHA/environment, missing checks, interrupted baseline,
  malformed reports, and guesses about test commands.
- Reject blanket exceptions, unknown test IDs, changed failure evidence, wrong baseline/report
  binding, unapproved or stale exceptions, new test failures, and non-waivable errors.
- Verify actual test FAIL remains visible, `PASS_WITH_EXCEPTION` lists every active exception, and
  unexecuted required checks or ACs cannot be waived.
- Reject duplicate/malformed AC IDs, unknown AC references, missing Test Author coverage, duplicate
  test IDs, unsafe paths, symlink escapes, and hash mismatches.
- Reject G1 evidence bound to a different manifest or path set.
- Reject baseline-red success without an expected assertion/behavior failure for every test.
- Reject checkpoint commits containing any unapproved path or mismatched content.
- Validate all report v2 gate types, early nullable inputs, enums, SHA/hash consistency, applicable
  AC coverage, and verdict derivation. Preserve v1 report compatibility without rewriting records.

### Temporary-Git integration tests

- Complete clean initial baseline → G0 → automatic Test Author → G1 → baseline-red → exact
  test-only commit → slice Gate → final Gate, including a job with multiple features.
- Verify failing initial baseline blocks G0; after human-approved eligible exception, verify the
  matching failure is disclosed in applicable slice and final reports. Verify an additional or
  changed failure blocks advancement.
- Verify repair and a new baseline bind evidence to the actual new `job_base_sha`.
- Confirm final review diff uses `job_base_sha`, while implementation Gate uses `gate_base_sha`.
- Simulate interruption before/after checkpoint commit and before/after journal persistence; ensure
  recovery neither duplicates nor drops the checkpoint.
- Mutate a locked Test Author test, add an out-of-scope file, forge a report, omit a required AC,
  and interrupt a check process; every case must block Builder progression or review as
  appropriate and preserve evidence.
- Confirm manual M1 jobs and their existing v1 records follow the unchanged path.

## 9. Implementation order

1. Define and approve the open items in section 10; reconcile the approved earlier contracts and
   decision register before coding.
2. Add durable new-job workflow selection, backward-compatible replay, and pre-G0 attachment.
3. Add preflight/discovery, initial baseline evidence and recovery, then explicit exception
   approval and fail-closed matching.
4. Define AC parsing, manifest v2, G1 evidence, and hash validation.
5. Add automatic Test Author invocation and post-run scope enforcement.
6. Add checkpoint creation, journaling, and crash recovery.
7. Add Gate report v2, exception-aware slice/final verdicts, and SHA roles; ensure slice `--fast`
   executes applicable regression checks without pending tests.
8. Add focused unit and temporary-Git integration coverage at each step.
9. Verify existing M1 behavior remains unchanged; do not add automatic retries, risk routing,
   automatic CI changes, or push.

## 10. Open items before implementation

The workflow and outcome direction above is not sufficient to implement the exception safely.
Resolve and approve these details, together with the proposed earlier-contract amendments, before
starting code:

1. The versioned target-checks report schema for per-test execution and failure evidence; how
   process exit codes and mixed approved/unapproved failures are interpreted.
2. The deterministic comparison and re-approval rules for a recurring failure, including what
   evidence is sufficiently stable and what environmental changes invalidate it.
3. The exact CLI/interaction for a person to approve an exception, request a new baseline after
   repair, and continue after an interruption; how approval identity is recorded.
4. The baseline invocation/environment artifact schema, and how pre-G0 discovery identifies a
   trusted checks contract without changing or guessing project commands.
5. The precise proof that slice `--fast` executed applicable regressions and excluded pending job
   tests, including the target adapter's contract and failure behavior when it cannot prove this.
