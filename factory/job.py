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
        "not_attached", "attaching", "attached", "released"
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
            if job is None or event["type"] not in {
                "JOB_STATE_CHANGED",
                "JOB_RESUMED",
            }:
                raise ConfigurationError(f"invalid event type for job {job_id}")
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
                    raise ConfigurationError(f"invalid resume event for job {job_id}")
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
                raise ConfigurationError(f"invalid state event data for job {job_id}")
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

    with (job_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _write_snapshot(job_dir / "job.json", updated)
    return {"job": updated.model_dump(mode="json"), "last_event": event}


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
