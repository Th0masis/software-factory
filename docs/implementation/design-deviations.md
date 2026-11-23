# Design Decisions and Deviations

This register records decisions that differ from or resolve open/proposed items in [`design.md`](../../design.md). Keep the source design unchanged unless explicitly approved. Update this register as later chapters settle deferred items.

Statuses:

- **Resolved:** an explicit decision exists; implementation must follow it.
- **Deferred:** intentionally postponed to a later chapter.
- **Open:** needs an explicit decision before the owning chapter is implemented.

| ID | Design reference | Original design / open point | Recorded decision | Status / follow-up |
|---|---|---|---|---|
| DD-01 | §2.2, D1 | Factory repository may be standalone or vendored. | This repository is the standalone factory repository. Target projects are selected explicitly with `--project`; their project configuration remains in `.factory/project.json`. | Resolved |
| DD-02 | D7; §2.1 | Default concurrency is one until the dashboard; exact enforcement point is open. | No parallel branch/job execution initially. Chapter 1 serializes writes per job; the runner/scheduler must enforce one active job globally when introduced. | Resolved; enforcement deferred to runner |
| DD-03 | §1.2, §2.1, §2.2, D14 | `branch-per-job` proposed as default; `direct` may reset to `base_sha` on failure. | Start with direct mode. A job begins as metadata only. After spec approval, a person prepares an existing, valid, unoccupied local branch and the factory attaches a worktree. No Git tag, automatic branch creation, reset, force, or branch deletion. Branch-per-job and parallel execution are later work. | Resolved; exact operational contract in Chapter 1 |
| DD-04 | §2.2; Chapter 1 proposal | `job.json` is described as the sole source of truth, with a separate append-only event log. | `events.jsonl` is authoritative; `job.json` is a rebuildable atomic snapshot. A stale snapshot is reconstructed from valid events; a corrupt/incomplete log fails closed. | Resolved |
| DD-05 | §2.1; Chapter 9 | Diagram lists `BUDGET_EXHAUSTED` as a state, while Chapter 9 sends budget exhaustion to `NEEDS_HUMAN`. | `BUDGET_EXHAUSTED` is an event/reason; the job state becomes `NEEDS_HUMAN`. | Resolved |
| DD-06 | Chapter 1 proposed `job.json` fields | Risk, budget, hash, feature, session, and gate/review round fields are listed before their owning workflows exist. | Chapter 1 stores only the versioned job/state/project/branch/worktree core. Add later fields with their owning chapters. | Resolved |
| DD-07 | Chapter 1; D11 | Pydantic and Python 3.12 are proposed; implementation setup is absent. | Python >=3.12, verify with available 3.14; Pydantic is the only planned runtime dependency. | Resolved |
| DD-08 | §2.1; Chapter 1 lifecycle | Original branch setup occurs at job start; the user workflow places branch selection after spec approval. | `job new` creates metadata without branch/worktree. After spec approval, a person prepares the local branch and the factory explicitly attaches it, before TDD tests. | Resolved |
| DD-09 | §2.2; Chapter 3 | Target `.factory/checks.py` is the per-project checks contract; gate implementation lives in the factory. | Factory-side gate/dispatcher invokes the target project's checks contract. Checks execution remains outside Chapter 1. | Resolved |
| DD-10 | Chapters 5–6 | Chapter 5 proposes interactive spec approval; Chapter 6 proposes a separate test-author and locked tests. | Builder uses/runs TDD tests prepared before implementation. The user has not decided who authors them; do not infer acceptance or reject the separate test-author proposal yet. | Open; resolve in Chapter 6 |
| DD-11 | §2.1; Chapter 1 transition proposal | Generic `transition` exists, but its interaction with guarded normal transitions was not fully settled. | Generic CLI transition is limited to escalation/resume; normal forward transitions require their owning commands and precondition checks. `SPEC_APPROVED` is required before branch attachment; Chapter 5's `approve-spec` reaches that state. | Resolved; approval workflow is Chapter 5 |
| DD-12 | Chapter 1 CLI proposal | Output/error shapes and numeric exit-code categories are unspecified. | Success is JSON on stdout; errors go to stderr; exit `2` means usage/configuration error and `1` means operational failure. | Resolved in the Chapter 1 contract |
| DD-13 | Chapter 1 factory config | The config location is known, but factory-root discovery is unspecified. | The CLI runs from the factory repository root; the current working directory is the factory root for `config.json` and relative data paths. | Resolved |
| DD-14 | D14 `.factory/project.json` | The project config proposes a `work_branch` template. | The person prepares the exact local branch after spec approval and passes it to `worktree attach`; no branch template or automatic branch creation is used initially. | Resolved; branch-per-job remains deferred |
| DD-15 | Chapter 1 worktree release | Interrupted release recovery is not specified. | CH1-03 journals a release intent before Git removal; `repair` safely completes removal or finalizes the journal if Git already removed the worktree. A missing path that Git still registers fails closed. | Resolved; approved during CH1-03 implementation |
| DD-16 | Chapter 1 worktree attach recovery | Repair was specified not to fetch, but an interrupted fetch before `WORKTREE_BASE_FETCHED` leaves no journal evidence that a cached remote-tracking ref is fresh. | If attach recovery has no recorded `base_sha`, repair retries the configured base fetch and records its result before attaching. If `base_sha` is already recorded, repair does not fetch again. Never infer fetch success from a cached ref. | Resolved; approved during CH1-03 follow-up |
| DD-17 | Chapter 2, D13 | Headless CLI configuration isolation was open; verified flags might not prevent user MCP configuration from loading. | Accept user MCP configuration loading as a known Chapter 2 security limitation. The runner must still use `--disable-builtin-mcps`, `--no-custom-instructions`, `--no-auto-update`, and explicit tool permissions. Full `COPILOT_HOME` isolation is deferred and must not be implied by these flags. | Resolved; accepted 2026-11-22, follow-up remains deferred |
| DD-18 | Chapter 2 runner interface and preflight | `RunResult` specifies artifact paths, but a failed preflight must not allocate a run directory. | On preflight failure, `run_agent` returns an `ERROR` result with `paths={}` and a diagnostic; no run directory is created. | Resolved; explicitly approved by user |

## Review workflow

1. Record each approved decision with a concise identifier and source section.
2. Mark unapproved proposals as **Open**, not as requirements.
3. When a later chapter resolves a deferred item, add the resolving chapter/contract and update the status; do not rewrite the original design silently.
