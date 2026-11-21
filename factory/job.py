import getpass
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


class ConfigurationError(Exception):
    pass


class FactoryError(Exception):
    pass


class FactoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobs_dir: str = "jobs"
    worktrees_dir: str = "worktrees"

    @field_validator("jobs_dir", "worktrees_dir")
    @classmethod
    def validate_storage_path(cls, value):
        if not value.strip():
            raise ValueError("storage path must not be empty")
        return value


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project: str
    remote: str
    base_branch: str
    pr_target: str | None = None
    checks: str | None = None
    mode: Literal["direct"] = "direct"
    push: Literal["orchestrator"] = "orchestrator"

    @field_validator("project")
    @classmethod
    def validate_project_id(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
            raise ValueError("project must be a safe path component")
        return value

    @field_validator("remote", "base_branch")
    @classmethod
    def validate_required_string(cls, value):
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    job_id: str
    title: str
    state: Literal[
        "DRAFT_SPEC",
        "SPEC_APPROVED",
        "TESTS_WRITTEN",
        "TESTS_RED_OK",
        "BUILDING",
        "GATE",
        "REVIEW",
        "READY_FOR_HUMAN",
        "PR_OPEN",
        "MERGED",
        "NEEDS_HUMAN",
        "FAILED",
    ]
    project: str
    project_root: str
    project_config_path: str
    remote: str
    base_branch: str
    pr_target: str
    base_sha: str | None = None
    work_branch: str | None = None
    work_branch_start_sha: str | None = None
    worktree_path: str | None = None
    worktree_status: Literal[
        "not_attached", "attaching", "attached", "releasing", "released"
    ] = "not_attached"
    resume_state: str | None = None
    created_at: str
    updated_at: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("title must not be empty")
        return value


STATE_TRANSITIONS = {
    "DRAFT_SPEC": {"SPEC_APPROVED", "NEEDS_HUMAN", "FAILED"},
    "SPEC_APPROVED": {"TESTS_WRITTEN", "NEEDS_HUMAN", "FAILED"},
    "TESTS_WRITTEN": {"TESTS_RED_OK", "NEEDS_HUMAN", "FAILED"},
    "TESTS_RED_OK": {"BUILDING", "NEEDS_HUMAN", "FAILED"},
    "BUILDING": {"GATE", "NEEDS_HUMAN", "FAILED"},
    "GATE": {"REVIEW", "BUILDING", "NEEDS_HUMAN", "FAILED"},
    "REVIEW": {"READY_FOR_HUMAN", "BUILDING", "NEEDS_HUMAN", "FAILED"},
    "READY_FOR_HUMAN": {"PR_OPEN", "NEEDS_HUMAN", "FAILED"},
    "PR_OPEN": {"MERGED", "NEEDS_HUMAN", "FAILED"},
    "NEEDS_HUMAN": {"FAILED"},
    "MERGED": set(),
    "FAILED": set(),
}


def validate_state_transition(source, target):
    allowed_targets = STATE_TRANSITIONS.get(source)
    if allowed_targets is None or target not in allowed_targets:
        raise FactoryError(f"illegal state transition: {source} -> {target}")


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigurationError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"invalid JSON in {path}: {error}") from error


def _load_model(path, model):
    try:
        return model.model_validate(_load_json(path))
    except ValidationError as error:
        raise ConfigurationError(f"invalid configuration in {path}: {error}") from error


def _storage_path(factory_root, configured_path):
    root = factory_root.resolve()
    path = (root / configured_path).resolve()
    if path == root or root not in path.parents:
        raise ConfigurationError(
            f"storage path must be a child of the factory root: {configured_path}"
        )
    return path


@contextmanager
def _job_lock(job_dir):
    with (job_dir / ".lock").open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)

        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _git_root(path):
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ConfigurationError(f"not a Git repository: {path}")
    root = Path(result.stdout.strip()).resolve()
    if root != path:
        raise ConfigurationError(f"project path must be the repository root: {root}")
    return root


def _validate_base_branch(project_root, branch):
    result = subprocess.run(
        ["git", "check-ref-format", f"refs/heads/{branch}"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ConfigurationError(f"invalid base branch: {branch}")


def _run_git(project_root, arguments, operation):
    result = subprocess.run(
        ["git", *arguments],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip()
        raise FactoryError(f"{operation} failed: {detail}")
    return result.stdout.strip()


def _validate_job_project(job):
    try:
        project_root = Path(job.project_root).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ConfigurationError(f"invalid job project path: {job.project_root}") from error
    project_root = _git_root(project_root)
    expected_config_path = project_root / ".factory" / "project.json"
    try:
        stored_config_path = Path(job.project_config_path).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ConfigurationError(
            f"invalid project config path: {job.project_config_path}"
        ) from error
    if stored_config_path != expected_config_path:
        raise ConfigurationError("job project configuration path has changed")
    project_config = _load_model(expected_config_path, ProjectConfig)
    if (
        project_config.project != job.project
        or project_config.remote != job.remote
        or project_config.base_branch != job.base_branch
    ):
        raise ConfigurationError("job project identity has changed")
    _validate_base_branch(project_root, project_config.base_branch)
    return project_root, project_config


def _worktree_path(factory_root, job):
    factory_config = _load_model(factory_root / "config.json", FactoryConfig)
    worktrees_root = _storage_path(factory_root, factory_config.worktrees_dir)
    project_worktrees = worktrees_root / job.project
    if project_worktrees.is_symlink() or (
        project_worktrees.exists() and not project_worktrees.is_dir()
    ):
        raise ConfigurationError(f"invalid worktree directory: {project_worktrees}")
    return project_worktrees / job.job_id


def _write_snapshot(path, job):
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=".job-",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        json.dump(job.model_dump(mode="json"), temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_event_log(path, job_id):
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as error:
        raise FactoryError(f"cannot read job events {job_id}: {error}") from error
    if not contents or not contents.endswith("\n"):
        raise ConfigurationError(f"incomplete event log for job {job_id}")

    events = []
    job = None
    for sequence, line in enumerate(contents.splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"invalid event log for job {job_id}: {error}"
            ) from error
        if not isinstance(event, dict):
            raise ConfigurationError(f"invalid event in job log: {job_id}")
        if type(event.get("seq")) is not int or event["seq"] != sequence:
            raise ConfigurationError(f"invalid event sequence for job {job_id}")
        if event.get("job_id") != job_id:
            raise ConfigurationError(f"event job ID mismatch for job {job_id}")
        if not all(
            isinstance(event.get(field), str)
            for field in ("ts", "type", "actor", "reason")
        ):
            raise ConfigurationError(f"invalid event metadata for job {job_id}")
        timestamp = event["ts"]
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp)
        except ValueError as error:
            raise ConfigurationError(
                f"invalid event timestamp for job {job_id}"
            ) from error
        if (
            parsed_timestamp.utcoffset() != timedelta(0)
            or not event["actor"].strip()
            or not event["reason"].strip()
        ):
            raise ConfigurationError(f"invalid event metadata for job {job_id}")

        try:
            event_job = JobRecord.model_validate(event.get("data"))
        except ValidationError as error:
            raise ConfigurationError(
                f"invalid event data for job {job_id}: {error}"
            ) from error
        if event_job.job_id != job_id:
            raise ConfigurationError(f"event data job ID mismatch for {job_id}")

        if sequence == 1:
            if (
                event["type"] != "JOB_CREATED"
                or event_job.state != "DRAFT_SPEC"
                or event_job.resume_state is not None
            ):
                raise ConfigurationError(f"invalid initial event for job {job_id}")
        else:
            if job is None:
                raise ConfigurationError(f"invalid event type for job {job_id}")
            if event["type"] in {"JOB_STATE_CHANGED", "JOB_RESUMED"}:
                if (
                    event.get("from") != job.state
                    or event.get("to") != event_job.state
                ):
                    raise ConfigurationError(f"event state mismatch for job {job_id}")

                if event["type"] == "JOB_RESUMED":
                    if (
                        job.state != "NEEDS_HUMAN"
                        or job.resume_state != event_job.state
                        or event_job.resume_state is not None
                    ):
                        raise ConfigurationError(
                            f"invalid resume event for job {job_id}"
                        )
                    expected_resume_state = None
                else:
                    try:
                        validate_state_transition(job.state, event_job.state)
                    except FactoryError as error:
                        raise ConfigurationError(
                            f"invalid state transition in job log {job_id}: {error}"
                        ) from error
                    expected_resume_state = (
                        job.state if event_job.state == "NEEDS_HUMAN" else None
                    )

                expected_job = job.model_copy(
                    update={
                        "state": event_job.state,
                        "resume_state": expected_resume_state,
                        "updated_at": event_job.updated_at,
                    }
                )
                if expected_job != event_job:
                    raise ConfigurationError(
                        f"invalid state event data for job {job_id}"
                    )
            elif event["type"] == "WORKTREE_ATTACH_STARTED":
                if (
                    job.state != "SPEC_APPROVED"
                    or job.worktree_status != "not_attached"
                    or event_job.worktree_status != "attaching"
                    or not event_job.work_branch
                    or not event_job.work_branch_start_sha
                    or not event_job.worktree_path
                ):
                    raise ConfigurationError(
                        f"invalid worktree attach intent for job {job_id}"
                    )
                changes = {
                    "work_branch": event_job.work_branch,
                    "work_branch_start_sha": event_job.work_branch_start_sha,
                    "worktree_path": event_job.worktree_path,
                    "worktree_status": "attaching",
                }
                expected_job = job.model_copy(
                    update={**changes, "updated_at": event_job.updated_at}
                )
                if expected_job != event_job:
                    raise ConfigurationError(
                        f"invalid worktree attach intent for job {job_id}"
                    )
            elif event["type"] == "WORKTREE_BASE_FETCHED":
                if (
                    job.worktree_status != "attaching"
                    or not event_job.base_sha
                ):
                    raise ConfigurationError(
                        f"invalid fetched base event for job {job_id}"
                    )
                expected_job = job.model_copy(
                    update={
                        "base_sha": event_job.base_sha,
                        "updated_at": event_job.updated_at,
                    }
                )
                if expected_job != event_job:
                    raise ConfigurationError(
                        f"invalid fetched base event for job {job_id}"
                    )
            elif event["type"] in {"WORKTREE_ATTACH_COMPLETED", "WORKTREE_REPAIRED"}:
                if (
                    job.worktree_status not in {"attaching", "attached"}
                    or not job.base_sha
                    or event_job.worktree_status != "attached"
                ):
                    raise ConfigurationError(
                        f"invalid worktree completion event for job {job_id}"
                    )
                expected_job = job.model_copy(
                    update={
                        "worktree_status": "attached",
                        "updated_at": event_job.updated_at,
                    }
                )
                if expected_job != event_job:
                    raise ConfigurationError(
                        f"invalid worktree completion event for job {job_id}"
                    )
            elif event["type"] == "WORKTREE_RELEASE_STARTED":
                if (
                    job.worktree_status != "attached"
                    or event_job.worktree_status != "releasing"
                ):
                    raise ConfigurationError(
                        f"invalid worktree release intent for job {job_id}"
                    )
                expected_job = job.model_copy(
                    update={
                        "worktree_status": "releasing",
                        "updated_at": event_job.updated_at,
                    }
                )
                if expected_job != event_job:
                    raise ConfigurationError(
                        f"invalid worktree release intent for job {job_id}"
                    )
            elif event["type"] == "WORKTREE_RELEASED":
                if (
                    job.worktree_status != "releasing"
                    or event_job.worktree_status != "released"
                ):
                    raise ConfigurationError(
                        f"invalid worktree release event for job {job_id}"
                    )
                expected_job = job.model_copy(
                    update={
                        "worktree_status": "released",
                        "updated_at": event_job.updated_at,
                    }
                )
                if expected_job != event_job:
                    raise ConfigurationError(
                        f"invalid worktree release event for job {job_id}"
                    )
            else:
                raise ConfigurationError(f"invalid event type for job {job_id}")
        job = event_job
        events.append(event)

    return job, events


def create_job(factory_root, project_path, title):
    factory_root = factory_root.resolve()
    try:
        project_root = Path(project_path).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ConfigurationError(f"invalid project path: {project_path}") from error
    project_root = _git_root(project_root)
    factory_config = _load_model(factory_root / "config.json", FactoryConfig)
    project_config_path = project_root / ".factory" / "project.json"
    project_config = _load_model(project_config_path, ProjectConfig)
    _validate_base_branch(project_root, project_config.base_branch)

    jobs_root = _storage_path(factory_root, factory_config.jobs_dir)
    _storage_path(factory_root, factory_config.worktrees_dir)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    job_id = str(uuid4())
    job = JobRecord(
        job_id=job_id,
        title=title,
        state="DRAFT_SPEC",
        project=project_config.project,
        project_root=str(project_root),
        project_config_path=str(project_config_path),
        remote=project_config.remote,
        base_branch=project_config.base_branch,
        pr_target=project_config.pr_target or project_config.base_branch,
        created_at=timestamp,
        updated_at=timestamp,
    )
    jobs_root.mkdir(parents=True, exist_ok=True)
    job_dir = jobs_root / job_id
    job_dir.mkdir()

    event = {
        "seq": 1,
        "ts": timestamp,
        "job_id": job_id,
        "type": "JOB_CREATED",
        "actor": getpass.getuser(),
        "reason": "Job created",
        "data": job.model_dump(mode="json"),
    }

    with (job_dir / "events.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _write_snapshot(job_dir / "job.json", job)

    return {"job_id": job_id, "state": job.state}


def _job_location(factory_root, job_id):
    try:
        job_id = str(UUID(job_id))
    except ValueError as error:
        raise ConfigurationError(f"invalid job ID: {job_id}") from error

    factory_root = factory_root.resolve()
    factory_config = _load_model(factory_root / "config.json", FactoryConfig)
    jobs_root = _storage_path(factory_root, factory_config.jobs_dir)
    job_dir = jobs_root / job_id
    if not job_dir.is_dir():
        raise FactoryError(f"job not found: {job_id}")
    return job_id, job_dir


def _read_job_contents(job_dir, job_id):
    job, events = _read_event_log(job_dir / "events.jsonl", job_id)

    snapshot_path = job_dir / "job.json"
    try:
        snapshot_data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot = JobRecord.model_validate(snapshot_data)
    except FileNotFoundError:
        snapshot = None
    except (json.JSONDecodeError, ValidationError):
        snapshot = None
    except OSError as error:
        raise FactoryError(f"cannot read job snapshot {job_id}: {error}") from error
    if snapshot != job:
        _write_snapshot(snapshot_path, job)
    return {"job": job.model_dump(mode="json"), "last_event": events[-1]}


def read_job(factory_root, job_id):
    job_id, job_dir = _job_location(factory_root, job_id)
    with _job_lock(job_dir):
        return _read_job_contents(job_dir, job_id)


def _persist_event(job_dir, event, updated):
    with (job_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _write_snapshot(job_dir / "job.json", updated)
    return {"job": updated.model_dump(mode="json"), "last_event": event}


def _record_state_change(job_dir, current, target, reason, resuming=False):
    job = JobRecord.model_validate(current["job"])
    is_resume = (
        resuming
        and job.state == "NEEDS_HUMAN"
        and target == job.resume_state
    )
    if not is_resume:
        validate_state_transition(job.state, target)
    if not reason.strip():
        raise ConfigurationError("reason must not be empty")

    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    updated = job.model_copy(
        update={
            "state": target,
            "resume_state": job.state if target == "NEEDS_HUMAN" else None,
            "updated_at": timestamp,
        }
    )
    event = {
        "seq": current["last_event"]["seq"] + 1,
        "ts": timestamp,
        "job_id": job.job_id,
        "type": "JOB_RESUMED" if is_resume else "JOB_STATE_CHANGED",
        "actor": getpass.getuser(),
        "reason": reason.strip(),
        "from": job.state,
        "to": target,
        "data": updated.model_dump(mode="json"),
    }

    return _persist_event(job_dir, event, updated)


def _record_worktree_event(job_dir, current, event_type, reason, changes):
    job = JobRecord.model_validate(current["job"])
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    updated = job.model_copy(update={**changes, "updated_at": timestamp})
    event = {
        "seq": current["last_event"]["seq"] + 1,
        "ts": timestamp,
        "job_id": job.job_id,
        "type": event_type,
        "actor": getpass.getuser(),
        "reason": reason,
        "data": updated.model_dump(mode="json"),
    }

    return _persist_event(job_dir, event, updated)


def _worktree_entries(project_root):
    output = _run_git(
        project_root, ["worktree", "list", "--porcelain"], "list Git worktrees"
    )
    entries = []
    entry = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            if entry is not None:
                entries.append(entry)
            entry = {"path": line[len("worktree ") :], "branch": None}
        elif line.startswith("branch ") and entry is not None:
            entry["branch"] = line[len("branch ") :]
    if entry is not None:
        entries.append(entry)
    return entries


def _git_common_dir(path):
    common_dir = Path(
        _run_git(path, ["rev-parse", "--git-common-dir"], "resolve Git directory")
    )
    if not common_dir.is_absolute():
        common_dir = path / common_dir
    return common_dir.resolve()


def _validate_registered_worktree(project_root, worktree_path, branch, entries):
    expected_path = worktree_path.resolve()
    entry = next(
        (
            entry
            for entry in entries
            if Path(entry["path"]).resolve() == expected_path
        ),
        None,
    )
    if entry is None or entry["branch"] != f"refs/heads/{branch}":
        raise FactoryError(f"worktree ownership mismatch: {worktree_path}")
    if _git_root(worktree_path) != expected_path:
        raise FactoryError(f"worktree repository mismatch: {worktree_path}")
    if _git_common_dir(project_root) != _git_common_dir(worktree_path):
        raise FactoryError(f"worktree belongs to another repository: {worktree_path}")
    checked_out_branch = _run_git(
        worktree_path, ["branch", "--show-current"], "inspect worktree branch"
    )
    if checked_out_branch != branch:
        raise FactoryError(f"worktree branch mismatch: {worktree_path}")


def _ensure_worktree_clean(worktree_path):
    changes = _run_git(
        worktree_path,
        ["status", "--porcelain", "--untracked-files=all", "--ignored"],
        "check worktree cleanliness",
    )
    if changes:
        raise FactoryError("cannot release worktree with uncommitted changes")


def _finish_worktree_release(project_root, worktree_path, branch, entries):
    expected_path = worktree_path.resolve()
    registered = any(
        Path(entry["path"]).resolve() == expected_path for entry in entries
    )
    if not worktree_path.exists():
        if registered:
            raise FactoryError(
                f"worktree path is missing but remains registered: {worktree_path}"
            )
        return

    _validate_registered_worktree(project_root, worktree_path, branch, entries)
    _ensure_worktree_clean(worktree_path)
    _run_git(
        project_root,
        ["worktree", "remove", "--", str(worktree_path)],
        "release worktree",
    )


def attach_worktree(factory_root, job_id, branch):
    job_id, job_dir = _job_location(factory_root, job_id)
    factory_root = factory_root.resolve()
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if job.state != "SPEC_APPROVED":
            raise FactoryError("worktree attach requires SPEC_APPROVED")
        if job.worktree_status != "not_attached":
            raise FactoryError(
                f"worktree cannot be attached from status: {job.worktree_status}"
            )

        project_root, project_config = _validate_job_project(job)
        ref_check = subprocess.run(
            ["git", "check-ref-format", f"refs/heads/{branch}"],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        if ref_check.returncode:
            raise ConfigurationError(f"invalid local branch: {branch}")
        branch_ref = f"refs/heads/{branch}"
        branch_check = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", branch_ref],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        if branch_check.returncode:
            raise FactoryError(f"local branch does not exist: {branch}")

        remotes = _run_git(project_root, ["remote"], "list Git remotes").splitlines()
        if project_config.remote not in remotes:
            raise ConfigurationError(
                f"configured remote does not exist: {project_config.remote}"
            )
        worktrees = _worktree_entries(project_root)
        if any(entry["branch"] == branch_ref for entry in worktrees):
            raise FactoryError(f"local branch is already checked out: {branch}")

        branch_sha = _run_git(
            project_root,
            ["rev-parse", "--verify", f"{branch_ref}^{{commit}}"],
            "resolve local branch",
        )
        worktree_path = _worktree_path(factory_root, job)
        if worktree_path.exists() or worktree_path.is_symlink():
            raise FactoryError(f"worktree path already exists: {worktree_path}")

        current = _record_worktree_event(
            job_dir,
            current,
            "WORKTREE_ATTACH_STARTED",
            "Worktree attach started",
            {
                "work_branch": branch,
                "work_branch_start_sha": branch_sha,
                "worktree_path": str(worktree_path),
                "worktree_status": "attaching",
            },
        )

        base_ref = (
            f"refs/heads/{project_config.base_branch}:"
            f"refs/remotes/{project_config.remote}/{project_config.base_branch}"
        )
        _run_git(
            project_root,
            ["fetch", "--no-tags", project_config.remote, base_ref],
            "fetch configured base branch",
        )
        base_sha = _run_git(
            project_root,
            [
                "rev-parse",
                "--verify",
                f"refs/remotes/{project_config.remote}/{project_config.base_branch}^{{commit}}",
            ],
            "resolve fetched base branch",
        )
        current = _record_worktree_event(
            job_dir,
            current,
            "WORKTREE_BASE_FETCHED",
            "Fetched configured base branch",
            {"base_sha": base_sha},
        )

        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        _run_git(
            project_root,
            ["worktree", "add", "--", str(worktree_path), branch],
            "attach local branch",
        )
        return _record_worktree_event(
            job_dir,
            current,
            "WORKTREE_ATTACH_COMPLETED",
            "Worktree attached",
            {"worktree_status": "attached"},
        )


def repair_worktree(factory_root, job_id):
    job_id, job_dir = _job_location(factory_root, job_id)
    factory_root = factory_root.resolve()
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if job.worktree_status not in {"attaching", "attached", "releasing"}:
            raise FactoryError(
                f"worktree cannot be repaired from status: {job.worktree_status}"
            )
        if not job.work_branch or not job.worktree_path:
            raise ConfigurationError("job has incomplete worktree registration")

        project_root, project_config = _validate_job_project(job)
        worktree_path = _worktree_path(factory_root, job)
        if Path(job.worktree_path).resolve() != worktree_path.resolve():
            raise ConfigurationError("registered worktree path has changed")
        if worktree_path.is_symlink():
            raise ConfigurationError(
                f"worktree path must not be a symlink: {worktree_path}"
            )

        worktrees = _worktree_entries(project_root)
        if job.worktree_status == "releasing":
            _finish_worktree_release(
                project_root, worktree_path, job.work_branch, worktrees
            )
            return _record_worktree_event(
                job_dir,
                current,
                "WORKTREE_RELEASED",
                "Completed interrupted worktree release",
                {"worktree_status": "released"},
            )

        if worktree_path.exists():
            _validate_registered_worktree(
                project_root, worktree_path, job.work_branch, worktrees
            )
            if job.worktree_status == "attached":
                return current
            if not job.base_sha:
                raise ConfigurationError("attached worktree has no recorded base SHA")
            return _record_worktree_event(
                job_dir,
                current,
                "WORKTREE_REPAIRED",
                "Recovered interrupted worktree attach",
                {"worktree_status": "attached"},
            )

        branch_ref = f"refs/heads/{job.work_branch}"
        branch_check = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", branch_ref],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        if branch_check.returncode:
            raise FactoryError(f"registered local branch is missing: {job.work_branch}")
        if any(entry["branch"] == branch_ref for entry in worktrees):
            raise FactoryError(
                f"registered local branch is checked out elsewhere: {job.work_branch}"
            )

        if not job.base_sha:
            base_sha = _run_git(
                project_root,
                [
                    "rev-parse",
                    "--verify",
                    (
                        f"refs/remotes/{project_config.remote}/"
                        f"{project_config.base_branch}^{{commit}}"
                    ),
                ],
                "resolve previously fetched base branch",
            )
            current = _record_worktree_event(
                job_dir,
                current,
                "WORKTREE_BASE_FETCHED",
                "Recovered previously fetched base branch",
                {"base_sha": base_sha},
            )

        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        _run_git(
            project_root,
            ["worktree", "add", "--", str(worktree_path), job.work_branch],
            "reattach registered branch",
        )
        _validate_registered_worktree(
            project_root,
            worktree_path,
            job.work_branch,
            _worktree_entries(project_root),
        )
        return _record_worktree_event(
            job_dir,
            current,
            "WORKTREE_REPAIRED",
            "Reattached registered worktree",
            {"worktree_status": "attached"},
        )


def release_worktree(factory_root, job_id):
    job_id, job_dir = _job_location(factory_root, job_id)
    factory_root = factory_root.resolve()
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if job.worktree_status != "attached":
            raise FactoryError(
                f"worktree cannot be released from status: {job.worktree_status}"
            )
        if not job.work_branch or not job.worktree_path:
            raise ConfigurationError("job has incomplete worktree registration")

        project_root, _ = _validate_job_project(job)
        worktree_path = _worktree_path(factory_root, job)
        if Path(job.worktree_path).resolve() != worktree_path.resolve():
            raise ConfigurationError("registered worktree path has changed")
        if worktree_path.is_symlink():
            raise ConfigurationError(
                f"worktree path must not be a symlink: {worktree_path}"
            )

        worktrees = _worktree_entries(project_root)
        _validate_registered_worktree(
            project_root, worktree_path, job.work_branch, worktrees
        )
        _ensure_worktree_clean(worktree_path)
        current = _record_worktree_event(
            job_dir,
            current,
            "WORKTREE_RELEASE_STARTED",
            "Worktree release started",
            {"worktree_status": "releasing"},
        )
        _finish_worktree_release(
            project_root,
            worktree_path,
            job.work_branch,
            _worktree_entries(project_root),
        )
        return _record_worktree_event(
            job_dir,
            current,
            "WORKTREE_RELEASED",
            "Worktree released",
            {"worktree_status": "released"},
        )


def _change_job_state(factory_root, job_id, target, reason, resuming=False):
    if not resuming and target not in {"NEEDS_HUMAN", "FAILED"}:
        raise ConfigurationError(f"unsupported administrative transition: {target}")
    job_id, job_dir = _job_location(factory_root, job_id)
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if resuming:
            if job.state != "NEEDS_HUMAN" or job.resume_state is None:
                raise FactoryError(f"job cannot be resumed from state: {job.state}")
            if job.resume_state not in STATE_TRANSITIONS or job.resume_state in {
                "NEEDS_HUMAN",
                "MERGED",
                "FAILED",
            }:
                raise ConfigurationError(
                    f"invalid saved resume state: {job.resume_state}"
                )
            target = job.resume_state
        return _record_state_change(
            job_dir, current, target, reason, resuming=resuming
        )


def transition_job(factory_root, job_id, target, reason):
    return _change_job_state(factory_root, job_id, target, reason)


def resume_job(factory_root, job_id, reason):
    return _change_job_state(factory_root, job_id, None, reason, resuming=True)


def list_jobs(factory_root):
    factory_root = factory_root.resolve()
    factory_config = _load_model(factory_root / "config.json", FactoryConfig)
    jobs_root = _storage_path(factory_root, factory_config.jobs_dir)
    if not jobs_root.exists():
        return []
    if not jobs_root.is_dir():
        raise ConfigurationError(f"jobs path is not a directory: {jobs_root}")

    jobs = []
    for job_dir in sorted(jobs_root.iterdir(), key=lambda path: path.name):
        if not job_dir.is_dir():
            continue
        job = read_job(factory_root, job_dir.name)["job"]
        if job["job_id"] != job_dir.name:
            raise ConfigurationError(f"job ID does not match directory: {job_dir}")
        jobs.append(job)
    return jobs
