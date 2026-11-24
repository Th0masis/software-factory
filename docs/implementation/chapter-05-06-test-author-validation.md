# Chapters 3, 5, and 6 — Baseline, Test Author, and Feature Validation Contract

- **Status:** Approved for implementation by the user on 2026-11-24.
- **Scope:** New jobs only. Existing M1 jobs and records retain their current behavior.
- **Inputs:** [`design.md`](../../design.md), [`chapter-01.md`](./chapter-01.md),
  [`chapter-03.md`](./chapter-03.md), [`chapter-04.md`](./chapter-04.md), and
  [`design-deviations.md`](./design-deviations.md).
- **Decision provenance:** The workflow direction, choices in section 10, and the interfaces,
  schemas, and slices below were approved by the user on 2026-11-24.
- **Earlier contracts:** The user separately approved the v2-specific amendments identified in
  section 10. Existing M1 requirements and records remain unchanged.

## 1. Goal and boundaries

For each new job, run a full build and the complete existing project test suite before G0. Preserve
the results as the job's initial baseline. A job may contain multiple features; it has one accepted
initial baseline and one final full Gate, not one of each per feature.

After G0, automatically start a separate Test Author session. A human reviews its exact new test
files and mappings at G1. The Factory then verifies baseline-red and creates a test-only checkpoint
before the initializer and Builder. Builder commits each slice before its Slice Gate. After all
slices, the final Gate runs the complete build and tests and evaluates every feature's ACs.

The Factory keeps one Gate engine. The initial baseline, baseline-red, Slice Gate, and final Gate
are different operations:

| Operation | Purpose | Test scope |
|---|---|---|
| Initial baseline | Record the project's starting state | Full build and all existing tests, once per accepted job baseline |
| Baseline-red | Verify new acceptance tests fail before implementation | All approved Test Author tests against `job_base_sha` |
| Slice Gate | Verify one committed implementation step | Selected slice tests and applicable fast regressions, excluding pending job tests |
| Final Gate / Feature Validation | Validate the entire job | Full build, all project tests, and all ACs for each feature |

Baseline-red is input readiness, not a Gate mode. There is no separate per-slice baseline Gate
execution. The final Gate reports each feature separately but is run once after all features, not
once per feature. Risk routing, automatic retries, CI changes, and push/PR/merge automation remain
out of scope. A human publishes the PR.

## 2. Compatibility and trust

### 2.1 Workflow selection

`JOB_CREATED` for a new job records `workflow_version: 2` durably. A legacy `JOB_CREATED` without
that field selects M1. Replay must reject an unknown version and must not infer the workflow from
later config or missing report fields. M1's existing state transitions, target-owned
`.factory/checks.py`, v1 manifests and reports, CLI behavior, event replay, and recovery remain
unchanged. Do not rewrite or migrate old events, snapshots, manifests, or reports.

New-workflow validation is version-specific. In particular, qualified verdicts and pre-G0
attachment must not become M1 prerequisites or be interpreted as M1 `PASS`. The old report reader
continues to read existing v1 records. New v2 reports are not written over old reports.

### 2.2 Trust boundary

The target repository, its approved build/test commands, and its test runner are trusted code. The
Factory-owned adapter is also trusted only after explicit human approval. Hashes prove that approved
bytes were not changed; they do not prove that a command actually tested the claimed behavior or
that the runner discovered every test it should have discovered.

Agent permissions, Git validation, and post-run checks are not an OS sandbox. They cannot prove
that ignored files, external services, another process, or temporary writes were untouched. A
matching failure fingerprint cannot prove an unchanged root cause. The user selected automatic
matching with that residual risk; the Factory must disclose it, never claim that a match proves
there is no new error, and block on changed or ambiguous evidence.

## 3. Project checks setup and environment

### 3.1 Factory-owned adapter

New jobs do not require the target project to contain `.factory/checks.py`. A project has a
Factory-owned adapter and configuration stored outside its target worktree. The project key must
identify one canonical project root without colliding with another project. The Factory retains
immutable approved adapter revisions and binds each job attempt to the exact revision.

If no approved adapter exists, the Factory may inspect the project's test layout, build
configuration, and CI conventions and propose an adapter and commands. Discovery is informational:
it must not run guessed commands, install packages, edit target configuration or CI, or silently
approve a proposal. A human reviews and explicitly approves the exact adapter bytes, command
argument arrays, required checks, test-discovery mechanism, environment identity inputs, and
project identity before the adapter or its commands run. Any change requires a new approval.

An adapter runs in the target worktree. Commands use argument arrays with `shell=False`, bounded
timeouts and process-tree termination, and the existing configured secret-environment removal.
There is no automatic project setup or CI modification. If the project already provides a complete
test check, use it and the required build; do not run the same build twice if the approved check
already performs it. Otherwise, use the project's approved test-discovery commands. Do not hard-code
only the tests present during setup: newly added project tests must be discovered by `full`.

The adapter must use the runner's reliable machine-readable output, or an equivalently unambiguous
supported output, to produce the v2 report in section 4. If it cannot establish unique test IDs,
complete discovery, outcomes, and the requested selection, it fails closed and proposes a
separately approved reporting or infrastructure change. It must not infer individual outcomes
from aggregate counts. A successful discovery of zero existing tests is valid if the runner itself
is usable and the build succeeds; record zero explicitly. New acceptance tests are still required.

### 3.2 Environment identity

Each approved adapter config identifies the build/test executables and interpreter, and the
repository-relative dependency and configuration files whose identity matters. Before each
attempt, the Factory records:

- OS/platform and the interpreter and declared tools' executable identities and exact versions;
- SHA-256 of the approved adapter and of its approved configuration, including commands and
  selection rules;
- for each declared dependency/configuration path, its relative path and SHA-256 of its bytes.

Missing, unreadable, unsafe, or ambiguous identity inputs invalidate the attempt. Do not persist
secret values in the identity or command configuration. The identity hash is SHA-256 over UTF-8
JSON of this identity object with keys sorted and separators `,` and `:`; arrays retain their
declared order. Record the object as well as its hash. Record each attempt's exact argument-array
invocation and working directory separately; do not include mode-specific arguments or attempt
paths in the environment hash, or `full` and `slice` could never match. This does not inventory
all installed dependencies, ignored files, or external services; those limitations remain visible.

If the recorded relevant environment or HEAD changes before Test Author begins, a new full
baseline is required. If G0 was already approved, a person must approve G0 again against the new
accepted baseline, even if only the environment changed. For a later exception match, a different
or unavailable environment identity blocks automatic reuse and requires human review.

## 4. Checks adapter v2

### 4.1 Invocation and request

The Factory invokes the approved Python adapter as an argument array:

```text
python <factory-owned-adapter> --mode full|slice|red --request <absolute-factory-owned-json>
```

The request is an immutable attempt artifact. Its exact fields are `schema_version: 2`, `job_id`,
`attempt_id`, `mode`, `head_sha`, `adapter_sha256`, `selected_ids`, `excluded_ids`,
`manifest_path`, `manifest_sha256`, and `request_sha256`. All IDs are strings, hashes are
lowercase SHA-256, and SHAs identify full commits. Mode-specific values are:

- `full`: `selected_ids: []`, `excluded_ids: []`, `manifest_sha256: null`.
- `slice`: exact `selected_ids` from the approved slice, exact `excluded_ids` for still-pending
  Test Author tests, and the approved full manifest hash in `manifest_sha256`.
- `red`: all approved Test Author test IDs in `selected_ids`, `excluded_ids: []`, and the approved
  manifest hash. `head_sha` is `job_base_sha`; the new test files are present as the only approved
  uncommitted additions on top of that commit.

`request_sha256` is computed by the Factory over the same canonical JSON encoding as section 3.2,
omitting `request_sha256` itself. IDs in each list are unique and the selected and excluded sets
are disjoint. `manifest_path` is null for `full`; otherwise it is the absolute path of the approved
manifest. Request and manifest hashes are checked before and after the process. Each is a regular
non-symlink Factory artifact.

### 4.2 Report and process result

The adapter writes exactly one JSON object to stdout; diagnostics go to stderr. Its report has this
shape:

```json
{
  "schema_version": 2,
  "job_id": "job-id",
  "attempt_id": "attempt-id",
  "mode": "full",
  "head_sha": "full-commit-sha",
  "request_sha256": "64-lowercase-hex",
  "manifest_sha256": null,
  "discovery_complete": true,
  "selected_ids": [],
  "excluded_ids": [],
  "discovered_ids": ["tests/test_login.py::test_login"],
  "checks": [
    {"id": "build", "kind": "build", "status": "PASS", "exit_code": 0},
    {"id": "unit", "kind": "test", "status": "PASS", "exit_code": 0}
  ],
  "tests": [
    {
      "id": "tests/test_login.py::test_login",
      "check_id": "unit",
      "outcome": "PASS",
      "failure": null
    }
  ]
}
```

The example omits additional project checks, not required fields. `checks` contains each check ID
configured for the requested mode exactly once, with `kind` `build`, `test`, or `other`; its
`status` is `PASS`, `FAIL`,
`SKIPPED`, or `NOT_EXECUTED`. `exit_code` is an integer or null if no process completed. Optional
checks are explicitly identified in the approved configuration, not inferred from their result.
Test `outcome` is `PASS`, `ASSERTION_FAILURE`, `ERROR`, `SKIPPED`, or `NOT_EXECUTED`.
`failure` is null for PASS, SKIPPED, or NOT_EXECUTED; for a failure it is
`{"category": "assertion|error", "evidence": "non-empty text"}`. The category must agree with
the outcome. `check_id` identifies exactly one declared test-kind check. Test IDs are unique
across the attempt, even if a runner could otherwise execute the same test twice. The adapter
records native process exit codes and forwards captured native stdout/stderr to its stderr log.
The approved configuration defines the native runner exit codes that mean completed tests versus
infrastructure failure. A failed test check is eligible for exception evaluation only if the
runner completed and every failure in that check is represented by its individual test results;
an unattributed process failure is never eligible.

All IDs are non-empty, stable, unique runner selectors. Duplicate JSON keys, unknown enum values,
malformed IDs, duplicate IDs, missing fields, inconsistent metadata, or unrecognized fields fail
closed. Every discovered ID has exactly one test result; selected IDs must be discovered and
executed. In `full`, discovery covers the complete project test process and no ID is excluded.
In `slice`, the adapter reports the actual discovered and executed set, including the selected
tests and applicable approved fast regressions, and proves no excluded ID executed. If an excluded
ID is discovered, its result is `NOT_EXECUTED` and it is not a required test for that attempt.
In `red`, the discovery and result sets are exactly the selected IDs. An adapter that cannot
establish these properties must not claim success.

The adapter process returns `0` only when it completed and emitted a valid report, even when a
test failed. A nonzero adapter exit, timeout, interruption, launch failure, incomplete discovery,
or missing/malformed report is infrastructure failure. The project runner may itself use a
nonzero code for a normal test failure; the adapter must distinguish that from an incomplete or
failed runner and record the actual code. The Factory validates both the adapter exit and report.
It never converts a process error, a skipped check, or incomplete execution into a qualified pass.

### 4.3 Slice selection proof

The approved adapter configuration defines the project's fast regression check IDs and their
selection mechanism. Before a slice, Factory constructs the selected IDs from the approved plan
and excludes Test Author IDs belonging to future, unpassed slices. The adapter runs:

1. every selected test for the current slice;
2. all applicable configured fast regression checks and their discovered tests;
3. no excluded pending Test Author test.

Previously completed acceptance tests run again only if they belong to the configured fast
regressions. Other completed acceptance tests are required at final Gate, not every slice. The
adapter must produce the actual ID set and results for both selected tests and regressions; it
must not merely echo the request. Missing a required regression check, inability to exclude pending
tests, or inability to demonstrate actual execution fails the Gate. The Factory compares the
report with the request and configuration. This proof depends on the trusted adapter and runner;
it is not independent verification of their honesty.

## 5. Initial baseline and exceptions

### 5.1 Initial attempt and acceptance

A person prepares an existing local branch. After pre-G0 attachment, the Factory checks the
worktree is clean of tracked and untracked non-ignored changes, records HEAD as `candidate_sha`,
verifies the approved adapter and environment, and automatically invokes `full`. If adapter setup
is pending, the job stops before any guessed command is run. After setup or repair, a person uses
the explicit baseline command in section 9.

The Factory checks HEAD, tracked files, and untracked non-ignored files both before and after the
attempt. A dirty worktree or changed HEAD invalidates the attempt even if the checks claim PASS.
The before/after checks do not prove that transient writes did not occur. Raw stdout/stderr,
request, environment identity, actual results, timestamps, and immutable report are retained.

Every attempt has its own `candidate_sha`. Before acceptance `job_base_sha` is null. A valid
all-passing attempt gets an accepted baseline disposition of `PASS`. If tests fail, the immutable
attempt stays `FAIL`; G0 is blocked until repair plus a new full baseline or a disposition accepting
every eligible failing test by an explicit human exception. That separate disposition is
`PASS_WITH_EXCEPTION` and refers to the failed report and all approvals. Only then does
`job_base_sha` become the accepted `candidate_sha`. A failed build, infrastructure error, missing
or skipped required check, incomplete test, or invalid report is not waivable.

### 5.2 Exception evidence and approval

An exception covers one existing project test ID that was discovered and executed as an
`ASSERTION_FAILURE` in the accepted initial attempt. It cannot cover a suite, build failure, test
setup/import/syntax error, interrupted check, skipped or unexecuted test, a new acceptance test,
scope or integrity violation, or any missing report. A required AC always needs passing automated
evidence. Every failing baseline test needs its own approval; any non-eligible failure blocks G0.

For a failure, the Factory stores the adapter's category and non-empty evidence text and computes
a version-1 fingerprint as follows:

1. Replace CRLF and lone CR with LF.
2. Replace exact occurrences of the absolute worktree root in native path spelling with
   `<WORKTREE>`. Do not normalize other paths, whitespace, values, messages, stack frames, or text.
3. Encode the resulting text as UTF-8 and calculate SHA-256. Store the original evidence, normalized
   evidence, version, and hash. IDs and categories are compared separately, not folded into the
   text hash.

The human approval records a unique approval ID, job and baseline attempt IDs, baseline report
hash, candidate SHA, environment identity hash, test ID, category, fingerprint version/hash,
reason, timestamp, and local actor. The actor is a declared local identity, not strong
authentication. Missing identity or reason blocks approval.

For a later Slice or final Gate, an approval applies only if that test was actually executed in
that attempt, all binding hashes and the environment identity match, and its ID, category, and
versioned fingerprint match. Any other failure, absent evidence, ambiguous ID, changed fingerprint,
or changed environment blocks automatic acceptance. A changed failure fingerprint cannot be
approved as an exception in this job: fix the failure and explicitly run Gate again. An excepted
test outside the selected slice checks is not represented as executed or as an active exception in
that slice.

An observed PASS invalidates the approval for later recurrence; persist that invalidation when the
result is consumed. If the test later fails again with the original fingerprint, it needs a fresh
human approval referencing both the original baseline and the new failed attempt. The failed
attempt remains FAIL; a person must explicitly request another Gate. A changed fingerprint remains
ineligible. An approval never rewrites the baseline or current test result. Equal fingerprints
are a conservative matching rule, not proof of equal cause: an unnoticed new cause with identical
evidence remains an explicitly accepted risk.

### 5.3 Comparing baseline and final discovery

The baseline preserves every discovered test ID and its actual outcome. The final full attempt
compares its discovery with the accepted baseline inventory and reports additions, retained IDs,
and missing IDs. A missing original ID blocks the attempt; it is not automatically considered
passed or covered by an exception.

A person may separately review a rename or intentional retirement of a non-required baseline
test. The decision identifies the old ID, optional replacement ID, reason, actor, baseline binding,
and scope/spec revision. It must not claim the old test executed or waive a required check or AC.
Changing a required verification is outside this contract and requires a separately approved scope
or spec amendment before proceeding. Record the old ID as not executed and separately retired;
any replacement must actually execute. Preserve the failed attempt and explicitly request a new
final Gate after the decision. An unreviewed disappearance always blocks progress.

## 6. Specification, Test Author, G1, and checkpoint

### 6.1 Spec and feature ownership

The new-workflow spec has one `## Acceptance Criteria` section. It contains feature headings
`### <feature-id>` and single-line bullets `- AC-001: observable behavior`. Feature IDs use
`[A-Za-z0-9][A-Za-z0-9._-]*`; AC IDs use `AC-[0-9]{3,}` and are unique within the job. Each AC
belongs to exactly one feature and is automatable. Reject empty, duplicated, malformed, or
unmapped criteria. A non-automatable requirement must be separately approved outside automated
Gate; manual verification is never an automated AC PASS.

G0 explicitly approves the exact spec, feature/AC ownership, scope, and test-path scope against the
accepted baseline disposition and environment identity. Scope input is a factory-owned JSON
artifact with exactly `schema_version: 2`, non-empty `allowed_paths`, and non-empty
`test_paths` arrays using the existing safe glob rules. Every concrete candidate test path must
match both arrays; hard protected Factory/config paths cannot be overridden. G0 stores and hashes
the exact scope input and spec. Persist their hashes and actor. If the accepted
baseline changes after G0, preserve the old approval but mark it invalid and require a new G0.
G0 is durable even if the automatically launched Test Author subsequently fails; the command
reports that failure and the job awaits human action.

### 6.2 Candidate and G1

After G0, the orchestrator starts one automatic Test Author session using a configured dedicated
profile. The agent receives the approved spec, conventions, and test-path scope. A different model
family is optional. Do not grant the profile direct commit or push permission. Post-run checks
must detect HEAD changes and out-of-scope writes, but cannot undo a push or provide OS isolation;
do not claim that tool-denial settings alone guarantee prevention of indirect commands.

For this first version Test Author may create only new test files, not modify or delete existing
files. Candidate paths must be regular non-symlink files under approved test paths, absent from
`job_base_sha`, safe repository-relative POSIX paths, and distinct from protected Factory/config
paths. A broad protected test glob may be overridden only for the exact new paths approved at G1.
The complete tracked and non-ignored untracked diff, excluding only the Factory-owned untracked
`.factory/task.md` runtime input, must consist exactly of candidate test paths. HEAD must remain
`job_base_sha`. Any unexpected commit, path, staged change, or file type stops and preserves work.
The task file must be absent before the session and removed only if it is still the exact
Factory-created content; otherwise stop for human review.

The final agent response is strict JSON:

```json
{
  "schema_version": 2,
  "tests": [
    {
      "id": "tests/test_profile.py::test_save",
      "path": "tests/test_profile.py",
      "acceptance_criteria": ["AC-001"]
    }
  ]
}
```

IDs are unique stable runner selectors. Each test maps to one or more ACs of the same feature;
every gating AC maps to at least one test. Multiple test IDs may share one new file. Reject
unknown ACs, unsafe paths, duplicates, empty lists, extra fields, and files absent from the complete
candidate diff. The orchestrator computes each file's SHA-256 and stores the normalized manifest as
an immutable Factory-owned artifact. The human reviews the exact diff, manifest, mappings, and
hashes and approves them at G1. The stored manifest has the same schema with an additional
`sha256` field on each test, computed from the exact file bytes; IDs sharing a path must carry the
same file hash. G1 is bound to the manifest hash, paths, content hashes, G0 revision, and baseline.
A changed candidate invalidates G1.

### 6.3 Baseline-red and checkpoint

After G1, run the approved adapter in `red` mode. Check HEAD is `job_base_sha` and the complete
worktree diff contains only the exact approved new test files with matching hashes, before and
after the run. The report binds the request, manifest, and SHA and has exactly one result per
registered test, all `ASSERTION_FAILURE`. PASS, ERROR, missing/duplicate test, setup/import/syntax
failure, invalid report, or changed inputs blocks the checkpoint. The trusted adapter is
responsible for classifying failures; a SHA in its report alone does not prove execution.

After successful red, the orchestrator writes checkpoint intent, stages and commits only the
approved paths, verifies that the commit has exactly `job_base_sha` as its sole parent and exactly
those changed paths and approved bytes, and records the full commit SHA as `gate_base_sha`.
Unexpected staged or unstaged changes, paths, parents, content, or HEAD fail closed. No reset,
force, or automatic deletion of commits is allowed. Persist the verified checkpoint event and
artifact hashes before initializer or Builder starts. Review uses the complete diff from
`job_base_sha`; implementation Gate uses `gate_base_sha`.

The existing initializer and human plan approval still follow checkpoint and precede Builder.
Its setup commit remains separate from the test-only checkpoint. Scope and protected-path
requirements apply, and its pinned script must be verified before execution. New-workflow
initializer and Builder cannot start without the durable verified checkpoint.

### 6.4 Feature, slice, and AC mapping

The plan contains ordered features and slices. Each feature references its own AC IDs. Each slice
references the ACs it contributes to and the registered Test Author test IDs that verify that
step; a slice need not complete an AC. Each registered test belongs to exactly one slice of its
feature. Each AC must be referenced by at least one slice and covered by at least one registered
test. Reject cross-feature, unknown, missing, or duplicate mappings.

The v2 plan has exactly `schema_version: 2` and a non-empty `features` array. Each feature has
`id`, `title`, `acceptance_criteria` (its AC IDs), and non-empty `slices`. Each slice has unique
`id`, `goal`, non-empty exact `paths`, non-empty `acceptance_criteria`, and non-empty `test_ids`.
Slice paths must be within job scope and follow the existing safe path rules. Feature and AC sets
must match G0 exactly. A slice's tests must map only to ACs referenced by that slice. The human
approves the plan and initializer script, and their hashes are journaled before Builder starts.

Slice Gate reports the current slice and its test results, not an overall AC PASS. After all
slices in all features pass, one final full Gate evaluates all mapped tests of every AC, grouped
by feature. An AC is PASS only if all its mapped required tests ran and passed; otherwise use
`FAIL`, `MISSING_TEST`, `SKIPPED`, or `NOT_EXECUTED`. The final Gate runs all project tests,
including Builder-added tests through normal discovery.

## 7. Gate report v2 and verdicts

Each initial baseline, slice, and final attempt has a unique immutable directory
`jobs/<job-id>/gate/<attempt-id>/` containing the request, adapter stdout/stderr, environment
identity, and Factory `gate-report.json`. The report has:

```json
{
  "schema_version": 2,
  "gate_type": "slice",
  "job_id": "job-id",
  "attempt_id": "attempt-id",
  "feature_ids": ["feature-1"],
  "slice_id": "slice-1",
  "candidate_sha": null,
  "job_base_sha": "full-commit-sha",
  "gate_base_sha": "full-commit-sha",
  "comparison_base_sha": "full-commit-sha",
  "head_sha": "full-commit-sha",
  "started_at": "RFC3339 UTC timestamp",
  "completed_at": "RFC3339 UTC timestamp",
  "duration_ms": 1234,
  "inputs": {
    "spec_sha256": "64-lowercase-hex",
    "tests_sha256": "64-lowercase-hex",
    "slice_plan_sha256": "64-lowercase-hex",
    "scope_sha256": "64-lowercase-hex",
    "adapter_sha256": "64-lowercase-hex",
    "environment_sha256": "64-lowercase-hex",
    "request_sha256": "64-lowercase-hex"
  },
  "checks": [{"id": "fast", "kind": "test", "required": true, "status": "PASS", "exit_code": 0}],
  "tests": [{"id": "tests/test_profile.py::test_save", "check_id": "fast", "outcome": "PASS", "failure": null}],
  "acceptance_criteria": [],
  "exceptions": [],
  "baseline_comparison": null,
  "scope": {"changed_paths": [], "violations": []},
  "protected_paths": {"violations": []},
  "logs": {"stdout": "checks.stdout.txt", "stderr": "checks.stderr.txt"},
  "risk": {"status": "DEFERRED"},
  "verdict": "PASS"
}
```

The example shows one passing slice; actual `checks` and `tests` include all applicable results.
`gate_type` is `initial_baseline`, `slice`, or `feature_validation`. For an initial baseline,
`candidate_sha`, `comparison_base_sha`, and `head_sha` are equal; `job_base_sha`, `gate_base_sha`,
`slice_id`, and not-yet-approved input hashes are null, and feature and AC lists are empty. The
separate disposition records the accepted `job_base_sha`. For a slice, `comparison_base_sha` is
the verified commit immediately before it, and `head_sha` is the committed slice head. For final
Gate, the comparison base is `gate_base_sha` and `head_sha` is the tested committed head.

The report includes the validated adapter outcomes, actual check exit codes, hashes, log references,
and scope/protected-path assessment. `baseline_comparison` is null except for final Gate, where it
lists added, retained, missing, and explicitly retired IDs and approval references. Each final AC
entry has `feature_id`, `id`, `status`, and mapped `test_ids`. The `exceptions` list contains each
active approval ID, test ID, initial attempt/report hash, both evidence fingerprints, and log
references. Entries are unique. Test outcomes remain their actual values.

The final `baseline_comparison` object has `added_ids`, `retained_ids`, `missing_ids`, and
`retired` arrays. Each retired entry has `id`, nullable `replacement_id`, and `approval_id`.
Missing IDs remain listed even if retired. Each exception entry has `approval_id`, `test_id`,
`baseline_attempt_id`, `baseline_report_sha256`, `baseline_fingerprint_sha256`,
`current_fingerprint_sha256`, `baseline_log_path`, and `current_log_path`. Logs are safe relative
artifact paths. Scope objects list normalized changed paths and violations; for baseline their
arrays are empty only after the clean worktree checks. A failed preflight records an explicit
diagnostic and never presents missing checks as executed.

Overall verdict:

- `PASS`: every required check and applicable final AC passed, all required tests executed, and no
  exception is active.
- `PASS_WITH_EXCEPTION`: all scope, integrity, execution, build, and applicable AC requirements
  pass; every remaining actual test failure is an executed, approved and matching pre-existing
  failure, and every active exception is disclosed. There must be at least one active exception.
- `FAIL`: any other required failure, missing/skipped/unexecuted check or test, missing AC,
  changed/ambiguous evidence, invalid report, timeout, or inconsistent input.

An aggregate project test check may remain visibly `FAIL` in a qualified report only when the
adapter has completed normally and its complete per-test evidence proves that every failure in
that check is covered. Build and other required checks cannot be waived. Optional `WARNING` may
appear only for an explicitly optional check and never replaces an incomplete required check.

For new-workflow CLI Gate results, exit `0` for `PASS` and `PASS_WITH_EXCEPTION`, and `1` for
`FAIL`; malformed invocation/configuration retains exit `2`. JSON always discloses the precise
verdict and exceptions. Gate stays state-neutral. The orchestrator records the report path and
hash and accepts either passing verdict for slice progression or final review; it must never
rewrite `PASS_WITH_EXCEPTION` as `PASS`. Before and after each Gate, verify the expected committed
HEAD and locked Test Author hashes. A changed HEAD or worktree invalidates the attempt.

## 8. Journal, states, and recovery

`events.jsonl` remains authoritative; snapshots and progress are projections. Each new event has
the existing sequence, UTC timestamp, job ID, actor, reason, and reconstructible job data, plus
the following event-specific fields. Paths are job-relative regular non-symlink artifacts, and
each referenced immutable artifact is bound by SHA-256:

| Event | Required event-specific fields |
|---|---|
| `BASELINE_STARTED` | attempt ID, candidate SHA, adapter/config hashes, environment hash, request hash |
| `BASELINE_COMPLETED` | attempt ID, candidate SHA, report path/hash, actual verdict |
| `EXCEPTION_APPROVED` | approval ID and all baseline/test/evidence/actor bindings in section 5.2 |
| `EXCEPTION_INVALIDATED` | approval ID, observed PASS attempt/report hash, test ID |
| `BASELINE_ACCEPTED` | attempt ID, report hash, candidate SHA, `PASS` or `PASS_WITH_EXCEPTION`, approval IDs |
| `G0_APPROVED` | spec/scope hashes, baseline disposition reference, SHA, environment hash, actor |
| `TEST_AUTHOR_STARTED` | run ID, G0 reference, base SHA, profile, immutable task hash |
| `TEST_AUTHOR_COMPLETED` | run ID, outcome, run artifact references/hashes, candidate manifest hash if valid |
| `G1_APPROVED` | G0 reference, candidate manifest/diff hashes, approved path/content hashes, actor |
| `RED_COMPLETED` | attempt ID, G1 reference, request/report hashes, base SHA, actual outcome |
| `CHECKPOINT_STARTED` | G1/red references, parent SHA, exact paths and content hashes |
| `CHECKPOINT_COMPLETED` | intent reference, verified checkpoint SHA, parent SHA, path/content hashes |
| `TEST_RETIREMENT_APPROVED` | baseline report hash, old ID, optional replacement ID, reason, actor, scope/spec revision |
| `GATE_REVIEW_APPROVED` | gate stage, nullable slice ID, exhausted batch and attempt IDs, reason, actor |
| Slice/final Gate events | attempt ID, report path/hash, exact bases/head, actual verdict and active approval IDs |

All IDs are unique in their scope; events are appended and fsynced under the existing job lock
before projections or dependent work. Replay validates workflow version, event order, references,
hashes, legal state, and verdicts. Unknown, contradictory, incomplete, or altered evidence fails
closed. A partial journal tail is not silently discarded.

For workflow v2:

| State/condition | Allowed forward operation |
|---|---|
| `DRAFT_SPEC`, no worktree | Attach existing branch; start automatic baseline when adapter is approved |
| `DRAFT_SPEC`, accepted baseline | Human G0; persist approval and enter `SPEC_APPROVED`; start Test Author automatically |
| `SPEC_APPROVED`, invalidated G0 before Test Author | Explicit new baseline, then fresh G0; start Test Author only after approval |
| `SPEC_APPROVED`, valid completed candidate | Human G1; persist manifest and enter `TESTS_WRITTEN` |
| `TESTS_WRITTEN`, approved G1 | Run red; on success enter `TESTS_RED_OK`, then create and verify checkpoint |
| `TESTS_RED_OK`, verified checkpoint | Initialize and approve plan/init script; enter `BUILDING` |
| `BUILDING` | Commit and Gate each slice; record a slice passed only on a valid passing verdict |
| `BUILDING`, all slices passed | Enter `GATE`; run one final full Gate |
| `GATE`, passing final Gate | Enter `REVIEW`, retaining the exact verdict and evidence |

Failures preserve evidence and stop or escalate to `NEEDS_HUMAN`; resume restores the prior state
only after its prerequisites are revalidated. A new accepted baseline after G0 invalidates that
G0 and requires a fresh G0 approval event before Test Author; earlier events are not overwritten.
An interrupted baseline is not successful and requires an explicit new attempt. A valid report
without its event may be finalized only if all attempt inputs and outputs are provably identical;
otherwise stop, never infer success from a partial artifact.

An interrupted or failed Test Author does not trigger an automatic retry. Preserve its logs and
failed run record. For an explicit human-requested restart, the person reviews the worktree and
confirms exactly which new Test Author files to discard. Factory may remove only listed, regular,
new files under approved test scope whose paths and hashes still match the reviewed snapshot; it
must not remove pre-existing, changed, ambiguous, or out-of-scope files. A clean initial snapshot
and matching hashes do not independently prove who wrote a file; if ownership is uncertain, stop
for manual resolution. Never reset Git or silently remove commits. Once the worktree is clean and
the same baseline, G0, and environment remain valid, start a new session only on explicit request.
The original automatic invocation occurs once per G0; later sessions require explicit action.

For checkpoint recovery, a durable intent and unchanged parent with no commit permit explicit
continuation. If a single existing commit can be proven to have the intended sole parent, exact
paths, and approved content, recovery may append the missing completion event without another
commit. Otherwise stop for human repair. A durable completion must be revalidated before use.
Slice and final Gate recovery must preserve existing M1 behavior on the M1 path; for v2 it must
never replay an agent automatically or infer a passing Gate from missing or inconsistent evidence.

For v2, each slice and the final Gate have separate batches of at most three attempted Gate runs,
including the initial attempt. A failed, errored, or interrupted started attempt consumes one
slot; replaying its durable result does not consume another. `PASS` and `PASS_WITH_EXCEPTION`
complete that stage. After three non-passing attempts, the orchestrator blocks further attempts
for that stage until a person reviews the evidence and explicitly records `GATE_REVIEW_APPROVED`
with a reason. That approval opens one new batch of up to three explicitly requested attempts.
The cap applies to workflow progression attempts, not state-neutral diagnostic Gate invocations,
which cannot advance a slice or job. Neither approval nor a failure starts an agent, repair, or
Gate automatically. A generic resume cannot bypass an exhausted batch.

## 9. Proposed human-facing operations

The following spellings are the approved interfaces. Successful operations emit JSON; errors go to
stderr using the existing exit-code convention. They do not push, open a PR, or merge.

```text
factory project checks propose --project PATH
factory project checks approve --project PATH --proposal PATH --approve
factory job new --project PATH --title TITLE
factory job worktree attach JOB_ID --branch LOCAL_BRANCH
factory job baseline run JOB_ID
factory job baseline approve-exception JOB_ID --attempt BASELINE_ATTEMPT_ID --test TEST_ID [--observed-attempt GATE_ATTEMPT_ID] --reason TEXT --approve
factory job prepare JOB_ID --stage spec --path SPEC_PATH --scope SCOPE_PATH --approve
factory job test-author restart JOB_ID --discard-manifest PATH --approve
factory job prepare JOB_ID --stage tests --path CANDIDATE_MANIFEST --approve
factory job initialize JOB_ID --profile PROFILE
factory job prepare JOB_ID --stage plan --path PLAN_PATH --init-script PATH --approve
factory job run JOB_ID --profile PROFILE
factory job gate JOB_ID --base SHA --mode fast|full [--slice SLICE_ID]
factory job gate-review approve JOB_ID --stage slice|final [--slice SLICE_ID] --reason TEXT --approve
factory job test-retirement approve JOB_ID --test TEST_ID [--replacement TEST_ID] --reason TEXT --approve
```

Attachment automatically starts the baseline when an adapter is already approved. `baseline run`
is an explicit new attempt after a failed/interrupted run, repair, environment change, or initial
adapter approval; it is not an automatic retry. A person approves the spec only when an accepted
baseline is valid. Its approval automatically starts Test Author. G1 accepts the exact
Factory-generated candidate; an arbitrary externally authored manifest cannot substitute for it.
The restart discard manifest lists reviewed paths and hashes and is subject to section 8.
`--observed-attempt` is required only when reapproving the original fingerprint after an observed
PASS. It binds that approval to the subsequent failed Gate evidence; it does not change that
attempt's FAIL or bypass an exhausted Gate batch.

Test retirement approval alone does not run Gate. Explicit continuation must revalidate all
inputs. CLI names and recovery commands must not be silently changed during implementation.
For v2, `prepare --stage spec` also accepts a fresh approval from `SPEC_APPROVED`
when its previous G0 has been invalidated; it must not start Test Author before the new baseline
and G0 are durable. Gate review approval is allowed only after the stage's batch is exhausted;
`--slice` is required for slice review and forbidden for final review. A local actor and reason
are mandatory for exception, gate-review, and retirement decisions.

## 10. Decision status and proposed amendments

### 10.1 User choices to carry into this draft

- A job may contain multiple features; initial full build/tests run once per accepted job baseline.
- One final full Gate evaluates all features and their ACs; Slice Gate evaluates only the step.
- New jobs use a human-approved Factory-owned project adapter and structured per-test v2 results.
  An adapter that cannot report individual IDs/outcomes reliably stops rather than guessing.
- A usable runner reporting zero existing tests can establish baseline if the build passes.
- Qualified exceptions can automatically match later failures using minimally normalized evidence;
  the residual risk of an identical symptom with a new cause is acknowledged.
- Adapter exit `0` means valid completed report, even if tests fail; nonzero is adapter failure.
- A change in the recorded relevant environment before Test Author requires a new baseline and,
  after G0, a new G0. Local actor identity is audit data, not strong authentication.
- Test Author initially creates only new test files. On failure it can restart from scratch after
  human review; valid G0 and baseline need not be repeated.
- Slice fast includes selected tests and applicable fast regressions, excluding pending tests.
  Previously completed acceptance tests need not rerun unless included in fast regressions.
- Missing baseline test IDs require human review. Retirement never claims execution and cannot
  silently waive a required check.
- A changed exception fingerprint requires repair. A slice or final Gate gets at most three
  explicitly requested attempts per batch; a human review may approve a further batch of three.

### 10.2 Approved amendments to earlier records

This draft proposes pre-G0 attachment for v2, unlike Chapter 1 and DD-03/DD-08/DD-11/DD-14.
It proposes a Factory-owned adapter for v2, unlike the target-owned adapter in Chapter 3 and
DD-09/DD-21/DD-23. It replaces the separately specified per-slice `baseline` mode for v2 with
Slice `fast`, unlike Chapter 3/4 and DD-24. It proposes qualified passing verdicts for narrowly
approved failures, unlike the unqualified PASS-only wording in Chapter 3/4 and DD-26/DD-27/DD-29.
It proposes new-workflow preflight and approved project adapter setup while retaining DD-30's
prohibition on unapproved infrastructure or CI changes.

`design.md` also described post-G0 attachment, a target-owned checks script, and PASS/FAIL-only
Gate semantics. The user separately approved targeted updates to that file, Chapters 1, 3 and 4,
and the decision register on 2026-11-24. M1-specific approved behavior remains unchanged.

### 10.3 Implementation boundary

The adapter protocol, report fields, event names, commands, recovery rules, environment identity,
and fingerprint normalization above are approved for implementation. If implementation reveals
that a runner cannot supply the evidence, or an invariant is not achievable without broadening
scope, stop and consult the user rather than weakening it.

## 11. Implementation slices and acceptance

Each slice is a small vertical RED → GREEN cycle: first demonstrate a failing observable test,
make the minimal implementation, rerun it and related regressions, inspect the diff, and record
progress. Public seams are the CLI, persisted journal/artifact contracts, and temporary-Git
workflow; these seams were approved with the contract. No new dependency is assumed.

| Slice | Observable acceptance criteria | Focused verification |
|---|---|---|
| 1. Version and attach | New `JOB_CREATED` selects v2 and can attach before G0; G0 stays blocked until baseline exists; old unversioned M1 replays unchanged; unknown version fails. | CLI plus temporary Git; replay existing v1 fixtures and compare old artifacts. |
| 2. Approved adapter | Proposal never executes guessed commands; approval pins exact project/config/adapter; changed or unapproved bytes block execution. | CLI with fake adapter and marker; project identity collision and hash negatives. |
| 3. Baseline | Attach runs full build and complete discovery automatically with an approved adapter; zero tests is explicit; dirty/changed HEAD or incomplete results fail. | Temporary Git fake runner; PASS, build FAIL, empty discovery, timeout and worktree mutation. |
| 4. Exceptions | One individually approved baseline failure yields a separate qualified disposition; mixed or non-waivable failure blocks it. | CLI and report tests for evidence, actor, report/SHA binding, recurrence, mismatch and PASS invalidation. |
| 5. G0 and ACs | Valid baseline permits G0; spec and feature/AC mappings are pinned; changed SHA/environment invalidates G0 and requires reapproval. | CLI/replay tests for duplicate/unknown ACs, stale approval and post-G0 launch failure. |
| 6. Test Author and G1 | One automatic session produces only new in-scope files and a complete manifest; G1 pins exact bytes; unsafe diff fails. | Fake Runner and temporary Git; out-of-scope writes, existing-file edits, changed HEAD and interrupted restart. |
| 7. Red and checkpoint | Each approved test has assertion-red at the original base; one exact test-only commit is journaled before initializer/Builder. | Temporary Git positive and malformed red; crashes before/after commit/event; reject extra staged path or wrong parent. |
| 8. Slice and final v2 | Slice executes selected and fast regressions without pending tests; final full build/tests evaluates all feature ACs and baseline inventory. | Fake adapter with individual IDs; missing/excluded tests, cross-slice AC, missing baseline IDs, and added Builder tests. |
| 9. Qualified progression | Matching exceptions remain actual FAILs and are disclosed; valid qualified slice advances and final qualified Gate reaches review; no other failure can be waived. | CLI, report and replay tests, including exit codes, mixed failures, changed evidence and unexecuted checks. |
| 10. Attempt limit | Three non-passing slice/final attempts block progression; a recorded human review opens at most three more without automatic execution. | CLI/replay tests for separate counters, interruption, resume bypass and new batches. |
| 11. End-to-end recovery and M1 | Complete multi-feature workflow preserves SHA roles and review diff; interrupted operations never duplicate sessions/commits; M1 records and behavior remain unchanged. | Temporary Git integration and targeted existing M1 regression suite; no network, push, or real agent required. |

Keep real-agent golden tasks separate from deterministic tests; do not claim they passed unless
they were actually run.
