# Chapters 3, 5, and 6 — Test Author and Feature Validation Contract

- **Status:** Draft for approval. High-level decisions are approved; implementation details below
  have not yet been approved.
- **Source:** [`design.md`](../../design.md), Chapters 3–6, and DD-25 through DD-31 in
  [`design-deviations.md`](./design-deviations.md).
- **Related M1 contract:** [`chapter-04.md`](./chapter-04.md) remains authoritative for the
  completed human-prepared workflow. This contract does not retroactively change it.

## 1. Goal and scope

Add an opt-in Test Author path before Builder work, preserve approved independent tests in the
target project, and expand Gate evidence so a human and reviewers can trace every required
Acceptance Criterion (AC) to executed tests.

The implementation must retain one Gate engine: per-slice Gate plus the existing final full Gate,
which serves as Feature/Job Validation. Builder commits before Slice Gate; a slice is recorded as
passed only after Gate PASS. The final Gate checks the complete project test/check suite before
review.

### In scope

- Stable AC IDs and strict validation of AC-to-test coverage.
- A Test Author session using a separate profile, with model family configurable.
- Post-run path-scope validation, G1 approval, baseline-red validation, and an orchestrator-owned
  test-only checkpoint.
- Distinct original job base, approved Gate base, per-attempt comparison base, and tested head.
- Gate report schema v2 and recovery for interrupted checkpoint/report creation.
- Focused unit, CLI, and temporary-Git integration tests.

### Out of scope

- Replacing or rewriting M1's manual test-registration workflow.
- OS-level sandboxing of the Test Author.
- Automatic retries, risk routing, or E2E selection; those belong to Chapter 9 and Chapter 7.
- Manual verification as a Gate PASS path.
- Automatic CI/test-infrastructure changes, Git push, PR creation, or merge.
- A second Gate engine, a new test framework, or a Factory-wide required test directory.

## 2. Compatibility and workflow selection

Existing/manual M1 jobs continue using the current registration, baseline-red, Gate, and recovery
behavior. They must not be reinterpreted as Test Author jobs.

New jobs opt into `test_source: "test_author"` explicitly. Until the Test Author feature is
implemented, the existing manual path remains the default. For report v2, a manual job has
`gate_base_sha == job_base_sha`; a Test Author job uses its verified test-only checkpoint as
`gate_base_sha`. Existing report files remain immutable and are not migrated.

The proposed CLI surface is:

```text
factory job new --project PATH --title TITLE --test-source manual|test_author
factory job author-tests JOB_ID --profile PROFILE
factory job prepare JOB_ID --stage tests --path CANDIDATE_MANIFEST --approve
```

`manual` remains the default for compatibility. `author-tests` accepts only a configured
Test Author profile and is valid after spec approval and worktree attachment, before test
registration. The Test Author's final response contains a strict JSON candidate manifest with
test IDs, project-relative paths, and AC IDs. The orchestrator validates it and writes the
candidate manifest under the factory-owned job directory; the agent cannot write job data directly.
The human reviews that exact manifest and worktree diff through G1 registration.

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

The role sequence is:

1. Orchestrator attaches the worktree and records `job_base_sha`.
2. Test Author runs in a separate session/profile and writes candidate tests only within the
   approved project test paths.
3. Orchestrator validates the complete diff and creates a candidate manifest.
4. Human G1 approves the exact manifest, paths, AC mapping, and test content.
5. Factory runs baseline-red against `job_base_sha`. Invalid setup, syntax, imports, or reports do
   not count as expected red.
6. Orchestrator commits only the approved test paths, verifies the commit, and records
   `gate_base_sha`.
7. Builder works in one session per approved slice. The existing per-slice and final Gate run.
8. Review uses the complete diff from `job_base_sha`; a human publishes after review.

Any manifest/content change after G1 invalidates that approval and requires a new G1 and
baseline-red result. A failed stage preserves work and evidence and stops before Builder starts.

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
discovery in `--fast`/`--full`. Builder test additions still obey the approved scope and hard
protected-path policy.

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

Every job and report distinguishes:

- `job_base_sha`: original attached branch base (`work_branch_start_sha` in existing M1 records).
  It anchors audit, Test Author validation, and the complete review/PR diff.
- `gate_base_sha`: approved Test Author checkpoint. For manual M1 jobs it equals
  `job_base_sha`.
- `comparison_base_sha`: the explicit base passed to one Gate attempt. For a slice, this is the
  commit immediately before that slice; for final validation, it is `gate_base_sha`.
- `head_sha`: exact committed HEAD evaluated by that Gate attempt.

The current Gate CLI remains `factory job gate JOB_ID --base SHA --mode fast|full [--slice ID]`.
This contract does not add a `baseline` CLI mode. The Test Author baseline-red check remains a
separate target-checks invocation. For each Gate attempt, the orchestrator supplies the proper
`comparison_base_sha` as `--base`; the report also retains the fixed `job_base_sha` and
`gate_base_sha`.

The full review diff starts at `job_base_sha`, so it includes the Test Author checkpoint. Gate
scope for implementation starts at `gate_base_sha`.

## 6. Gate report schema v2

Each attempt writes an immutable report and logs under
`jobs/<job-id>/gate/<attempt-id>/`. Required top-level shape:

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
  "risk": {"status": "DEFERRED"},
  "verdict": "PASS"
}
```

Allowed `gate_type` values are `slice` and `feature_validation`. Slice reports include the selected
slice's applicable ACs; final reports include all gating ACs and use `slice_id: null`. Check
statuses are `PASS`, `FAIL`, `SKIPPED`, `NOT_EXECUTED`, or `WARNING`; `WARNING` is valid only for
an explicitly optional check. AC statuses are `PASS`, `FAIL`, `MISSING_TEST`, `SKIPPED`, or
`NOT_EXECUTED`.

The overall verdict is PASS only if all required checks pass, every applicable required AC has
automated passing evidence, scope/protected-path checks pass, and the report is internally
consistent with the attempt's SHA values and input hashes. Missing or invalid required evidence
means FAIL. Do not derive PASS from an agent claim or from process exit code alone.

## 7. Journal, recovery, and failure behavior

- Keep state transitions under the existing orchestrator. Gate remains state-neutral.
- Record Test Author start/completion, G1 approval hash, baseline-red report reference, checkpoint
  SHA, and Gate report references in durable job events/snapshots. Do not store role approval only
  in an ephemeral session transcript.
- A Builder cannot start unless the job has a valid approved manifest, successful baseline-red
  evidence, and—when `test_source` is `test_author`—a verified `gate_base_sha`.
- Before each slice and final Gate, verify approved Test Author file hashes. Any mismatch fails
  closed before project checks execute.
- If the report is missing, malformed, or has inconsistent SHAs/hashes, the attempt fails and
  review is blocked.
- Preserve failed work, logs, manifests, and reports. Recovery may complete a provable interrupted
  journal/write operation, but must not rerun an agent, rewrite approvals, or discard a commit.
- Chapter 4/M1 remains retry-free. Failures transition to the existing human-escalation path.

## 8. Required validation

### Unit and contract tests

- Reject duplicate/malformed AC IDs, unknown AC references, missing Test Author coverage, duplicate
  test IDs, unsafe paths, symlink escapes, and hash mismatches.
- Reject G1 evidence bound to a different manifest or path set.
- Reject baseline-red success without an expected assertion/behavior failure for every test.
- Reject checkpoint commits containing any unapproved path or mismatched content.
- Validate report v2 required fields, enums, SHA/hash consistency, applicable AC coverage, and
  verdict derivation. Preserve v1 report compatibility for existing records without rewriting
  them.

### Temporary-Git integration tests

- Complete Test Author → G1 → baseline-red → exact test-only commit → slice Gate → final Gate.
- Confirm final review diff uses `job_base_sha`, while implementation Gate uses `gate_base_sha`.
- Simulate interruption before/after checkpoint commit and before/after journal persistence; ensure
  recovery neither duplicates nor drops the checkpoint.
- Mutate a locked Test Author test, add an out-of-scope file, forge a report, omit a required AC,
  and interrupt a check process; every case must block Builder progression or review as
  appropriate and preserve evidence.
- Confirm manual M1 jobs and their existing v1 records follow the unchanged path.

## 9. Implementation order

1. Add explicit job workflow selection and backward-compatible schema validation.
2. Define AC parsing, manifest v2, G1 evidence, and hash validation.
3. Add Test Author invocation and post-run scope enforcement.
4. Add checkpoint creation, journaling, and crash recovery.
5. Add Gate report v2 and the three SHA roles without changing the existing CLI.
6. Add focused unit and temporary-Git integration coverage.
7. Verify manual M1 behavior remains unchanged; do not add retries, risk routing, or CI changes.
