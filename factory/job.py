import getpass
import json
import os
from datetime import datetime, timezone
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


def read_job(factory_root, job_id):
    try:
        job_id = str(UUID(job_id))
    except ValueError as error:
        raise ConfigurationError(f"invalid job ID: {job_id}") from error

    factory_root = factory_root.resolve()
    factory_config = _load_model(factory_root / "config.json", FactoryConfig)
    job_dir = factory_root / factory_config.jobs_dir / job_id
    if not job_dir.is_dir():
        raise FactoryError(f"job not found: {job_id}")
    try:
        job_data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        event_lines = (job_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise FactoryError(f"cannot read job {job_id}: {error}") from error
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"invalid job data for {job_id}: {error}") from error

    try:
        job = JobRecord.model_validate(job_data)
        events = [json.loads(line) for line in event_lines]
    except (ValidationError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"invalid job data for {job_id}: {error}") from error
    if not events:
        raise ConfigurationError(f"job has no events: {job_id}")
    return {"job": job.model_dump(mode="json"), "last_event": events[-1]}


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
        job = JobRecord.model_validate(_load_json(job_dir / "job.json"))
        if job.job_id != job_dir.name:
            raise ConfigurationError(f"job ID does not match directory: {job_dir}")
        jobs.append(job.model_dump(mode="json"))
    return jobs
