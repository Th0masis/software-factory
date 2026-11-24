import getpass
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


class ConfigurationError(Exception):
    pass


class FactoryError(Exception):
    pass


class RunnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    executable: str = "copilot"
    timeout_seconds: int = 1800
    artifacts_dir: str = "runs"
    allow_tools: list[str] = []
    deny_tools: list[str] = []
    secret_env_vars: list[str] = []

    @field_validator("executable", "artifacts_dir")
    @classmethod
    def validate_non_empty(cls, value):
        if not value.strip():
            raise ValueError("value must not be empty")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value):
        if value <= 0:
            raise ValueError("timeout_seconds must be positive")
        return value


class GateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    timeout_seconds: int = 1800

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value):
        if value <= 0:
            raise ValueError("timeout_seconds must be positive")
        return value


class RunnerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    agent: str
    model: str
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
    context: Literal["default", "long_context"]
    max_ai_credits: int

    @field_validator("agent", "model")
    @classmethod
    def validate_profile_name(cls, value):
        if not value.strip():
            raise ValueError("profile value must not be empty")
        return value

    @field_validator("max_ai_credits")
    @classmethod
    def validate_credit_cap(cls, value):
        if value < 0:
            raise ValueError("max_ai_credits must not be negative")
        return value


class FactoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jobs_dir: str = "jobs"
    worktrees_dir: str = "worktrees"
    runner: RunnerConfig = RunnerConfig()
    gate: GateConfig = GateConfig()
    profiles: dict[str, RunnerProfile] = {}

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


def _replace_with_retry(source, destination):
    for attempt in range(5):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 4:
                raise
            time.sleep(0.02 * 2**attempt)


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
        _replace_with_retry(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def _parse_test_manifest(content):
    try:
        manifest = json.loads(
            content,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise ConfigurationError(f"invalid test manifest: {error}") from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "tests"}
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or not isinstance(manifest["tests"], list)
        or not manifest["tests"]
    ):
        raise ConfigurationError(
            "test manifest must contain schema_version 1 and a non-empty tests list"
        )
    return manifest


def _test_file_hash(worktree, relative_path):
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\\" in relative_path
        or relative_path.startswith("/")
        or re.match(r"^[A-Za-z]:", relative_path)
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
        or not PurePosixPath(relative_path).parts
    ):
        raise ConfigurationError("test path must be a safe repository-relative POSIX path")
    parts = relative_path.split("/")
    candidate = worktree
    try:
        if stat.S_ISLNK(worktree.lstat().st_mode):
            raise ConfigurationError("worktree path must not be a symlink")
        for index, part in enumerate(parts):
            candidate = candidate / part
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ConfigurationError(
                    f"test path must not contain symlinks: {relative_path}"
                )
            if index < len(parts) - 1 and not stat.S_ISDIR(mode):
                raise ConfigurationError(
                    f"test path parent must be a directory: {relative_path}"
                )
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ConfigurationError(f"test file is missing or invalid: {relative_path}") from error
    if not stat.S_ISREG(mode):
        raise ConfigurationError(f"test path must be a regular non-symlink file: {relative_path}")
    try:
        resolved.relative_to(worktree)
    except ValueError as error:
        raise ConfigurationError(f"test path escapes the worktree: {relative_path}") from error
    try:
        content = candidate.read_bytes()
    except OSError as error:
        raise ConfigurationError(f"cannot read test file {relative_path}: {error}") from error
    return hashlib.sha256(content).hexdigest()


def _normalize_test_manifest(manifest, worktree):
    worktree = Path(worktree).resolve(strict=True)
    records = []
    identifiers = set()
    for test in manifest["tests"]:
        if not isinstance(test, dict) or set(test) != {"id", "path"}:
            raise ConfigurationError("each test must contain only an id and path")
        test_id = test["id"]
        relative_path = test["path"]
        if not isinstance(test_id, str) or not test_id.strip():
            raise ConfigurationError("test ID must be a non-empty string")
        if test_id in identifiers:
            raise ConfigurationError(f"duplicate test ID: {test_id}")
        identifiers.add(test_id)
        records.append(
            {
                "id": test_id,
                "path": relative_path,
                "sha256": _test_file_hash(worktree, relative_path),
            }
        )
    return {"schema_version": 1, "tests": records}


def _validate_registered_tests(job_dir, job, expected_sha256):
    tests_path = job_dir / "tests.json"
    if tests_path.is_symlink() or not tests_path.is_file():
        raise ConfigurationError("registered test manifest is missing or invalid")
    try:
        content = tests_path.read_bytes()
    except OSError as error:
        raise ConfigurationError(f"cannot read registered test manifest: {error}") from error
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ConfigurationError("registered test manifest hash mismatch")
    if not job.worktree_path or job.worktree_status != "attached":
        raise ConfigurationError("registered tests require an attached worktree")
    worktree_path = Path(job.worktree_path)
    if worktree_path.is_symlink() or not worktree_path.is_dir():
        raise ConfigurationError("registered test worktree is missing or invalid")
    manifest = _parse_test_manifest(content)
    if any(
        not isinstance(test, dict)
        or set(test) != {"id", "path", "sha256"}
        or not isinstance(test["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", test["sha256"]) is None
        for test in manifest["tests"]
    ):
        raise ConfigurationError("registered test manifest contains an invalid test")
    source_manifest = {
        "schema_version": 1,
        "tests": [
            {"id": test["id"], "path": test["path"]}
            for test in manifest["tests"]
        ],
    }
    normalized = _normalize_test_manifest(
        _parse_test_manifest(json.dumps(source_manifest)),
        Path(job.worktree_path),
    )
    if manifest != normalized:
        raise ConfigurationError("registered test file hash mismatch")
    return content, manifest


def _validate_red_report(job_dir, job, event):
    report_path = event.get("red_report_path")
    report_sha256 = event.get("red_report_sha256")
    if (
        not isinstance(report_path, str)
        or not isinstance(report_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", report_sha256) is None
    ):
        raise ConfigurationError("invalid baseline-red report metadata")
    parts = report_path.split("/")
    if (
        len(parts) != 3
        or parts[0] != "tests-red"
        or parts[1] in {"", ".", ".."}
        or parts[2] != "red-report.json"
    ):
        raise ConfigurationError("invalid baseline-red report path")
    try:
        if str(UUID(parts[1])) != parts[1]:
            raise ValueError
    except ValueError as error:
        raise ConfigurationError("invalid baseline-red report attempt ID") from error
    attempts_dir = job_dir / parts[0]
    attempt_dir = attempts_dir / parts[1]
    stored_path = job_dir.joinpath(*parts)
    if (
        attempts_dir.is_symlink()
        or not attempts_dir.is_dir()
        or attempt_dir.is_symlink()
        or not attempt_dir.is_dir()
        or stored_path.is_symlink()
        or not stored_path.is_file()
    ):
        raise ConfigurationError("baseline-red report is missing or invalid")
    try:
        content = stored_path.read_bytes()
    except OSError as error:
        raise ConfigurationError(f"cannot read baseline-red report: {error}") from error
    if hashlib.sha256(content).hexdigest() != report_sha256:
        raise ConfigurationError("baseline-red report hash mismatch")
    try:
        report = json.loads(
            content,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise ConfigurationError(f"invalid baseline-red report: {error}") from error
    tests_path = job_dir / "tests.json"
    try:
        manifest_hash = hashlib.sha256(tests_path.read_bytes()).hexdigest()
    except OSError as error:
        raise ConfigurationError(f"cannot read registered test manifest: {error}") from error
    _, manifest = _validate_registered_tests(job_dir, job, manifest_hash)
    results = report.get("tests") if isinstance(report, dict) else None
    result_ids = [
        result.get("id")
        for result in results
        if isinstance(result, dict)
    ] if isinstance(results, list) else []
    if (
        not isinstance(report, dict)
        or type(report.get("schema_version")) is not int
        or report["schema_version"] != 1
        or report.get("work_branch_start_sha") != job.work_branch_start_sha
        or report.get("manifest_sha256") != manifest_hash
        or len(result_ids) != len(manifest["tests"])
        or any(not isinstance(test_id, str) for test_id in result_ids)
        or set(result_ids) != {test["id"] for test in manifest["tests"]}
        or len(set(result_ids)) != len(result_ids)
        or any(
            not isinstance(result, dict)
            or result.get("outcome") != "ASSERTION_FAILURE"
            for result in results
        )
    ):
        raise ConfigurationError("baseline-red report does not match registered tests")


def _validate_initializer_attempt(job_dir, event):
    attempt_id = event.get("attempt_id")
    if (
        not isinstance(attempt_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", attempt_id) is None
        or not isinstance(event.get("profile"), str)
        or not event["profile"].strip()
        or not isinstance(event.get("scope_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", event["scope_sha256"]) is None
        or not isinstance(event.get("task_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", event["task_sha256"]) is None
    ):
        raise ConfigurationError("invalid initializer attempt ID")
    attempt_dir = job_dir / "initializer" / f"attempt-{attempt_id}"
    if (
        (job_dir / "initializer").is_symlink()
        or attempt_dir.is_symlink()
        or not attempt_dir.is_dir()
    ):
        raise ConfigurationError("initializer attempt artifacts are missing or invalid")
    scope_path = job_dir / "scope.json"
    if scope_path.is_symlink() or not scope_path.is_file():
        raise ConfigurationError("initializer scope is missing or invalid")
    try:
        scope_hash = hashlib.sha256(scope_path.read_bytes()).hexdigest()
    except OSError as error:
        raise ConfigurationError(f"cannot read initializer scope: {error}") from error
    if scope_hash != event.get("scope_sha256"):
        raise ConfigurationError("initializer scope hash mismatch")
    task_path = attempt_dir / "task.md"
    if task_path.is_symlink() or not task_path.is_file():
        raise ConfigurationError("initializer task snapshot is missing or invalid")
    try:
        task_hash = hashlib.sha256(task_path.read_bytes()).hexdigest()
    except OSError as error:
        raise ConfigurationError(f"cannot read initializer task snapshot: {error}") from error
    if task_hash != event["task_sha256"]:
        raise ConfigurationError("initializer task snapshot hash mismatch")


def _validate_initializer_artifacts(job_dir, event):
    _validate_initializer_attempt(job_dir, event)
    attempt_id = event["attempt_id"]

    artifact_fields = {
        "plan_path": f"initializer/attempt-{attempt_id}/slice-plan.json",
        "init_script_path": f"initializer/attempt-{attempt_id}/init.py",
    }
    for path_field, expected_path in artifact_fields.items():
        if event.get(path_field) != expected_path:
            raise ConfigurationError("invalid initializer artifact path")
        digest = event.get(path_field.replace("_path", "_sha256"))
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ConfigurationError("invalid initializer artifact hash")
        artifact = job_dir / expected_path
        if artifact.is_symlink() or not artifact.is_file():
            raise ConfigurationError("initializer artifact is missing or invalid")
        try:
            actual_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        except OSError as error:
            raise ConfigurationError(f"cannot read initializer artifact: {error}") from error
        if actual_digest != digest:
            raise ConfigurationError("initializer artifact hash mismatch")


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
                is_spec_approval = (
                    event["type"] == "JOB_STATE_CHANGED"
                    and job.state == "DRAFT_SPEC"
                    and event_job.state == "SPEC_APPROVED"
                )
                is_tests_written = (
                    event["type"] == "JOB_STATE_CHANGED"
                    and job.state == "SPEC_APPROVED"
                    and event_job.state == "TESTS_WRITTEN"
                )
                is_tests_red = (
                    event["type"] == "JOB_STATE_CHANGED"
                    and job.state == "TESTS_WRITTEN"
                    and event_job.state == "TESTS_RED_OK"
                )
                is_plan_approved = (
                    event["type"] == "JOB_STATE_CHANGED"
                    and job.state == "TESTS_RED_OK"
                    and event_job.state == "BUILDING"
                )
                is_gate_entry = (
                    event["type"] == "JOB_STATE_CHANGED"
                    and job.state == "BUILDING"
                    and event_job.state == "GATE"
                )
                is_final_review = (
                    event["type"] == "JOB_STATE_CHANGED"
                    and job.state == "GATE"
                    and event_job.state == "REVIEW"
                )
                is_initializer_failure = (
                    event["type"] == "JOB_STATE_CHANGED"
                    and job.state == "TESTS_RED_OK"
                    and event_job.state == "NEEDS_HUMAN"
                    and event.get("failure_stage") == "initializer"
                )
                is_slice_failure = (
                    event["type"] == "JOB_STATE_CHANGED"
                    and _validate_slice_failure_state(job, event, events[-1])
                )
                is_final_gate_failure = (
                    event["type"] == "JOB_STATE_CHANGED"
                    and job.state == "GATE"
                    and event_job.state == "NEEDS_HUMAN"
                    and event.get("failure_stage") == "final_gate"
                )
                has_input_metadata = any(
                    key in event for key in ("input_path", "input_sha256")
                )
                has_red_metadata = any(
                    key in event
                    for key in ("red_report_path", "red_report_sha256")
                )
                has_plan_metadata = any(
                    key in event
                    for key in (
                        "plan_path",
                        "plan_sha256",
                        "init_script_path",
                        "init_script_target",
                        "init_script_sha256",
                        "setup_commit",
                    )
                )
                has_initializer_failure = any(
                    key in event
                    for key in (
                        "failure_stage",
                        "attempt_id",
                        "failure_outcome",
                        "failure_reason",
                        "run_dir",
                        "session_id",
                    )
                )
                if is_spec_approval:
                    input_path = event.get("input_path")
                    input_sha256 = event.get("input_sha256")
                    if (
                        input_path != "spec.md"
                        or not isinstance(input_sha256, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", input_sha256)
                    ):
                        raise ConfigurationError(
                            f"invalid registered spec metadata for job {job_id}"
                        )
                    registered_spec = path.parent / "spec.md"
                    if registered_spec.is_symlink() or not registered_spec.is_file():
                        raise ConfigurationError(
                            f"registered spec is missing or invalid for job {job_id}"
                        )
                    try:
                        registered_hash = hashlib.sha256(
                            registered_spec.read_bytes()
                        ).hexdigest()
                    except OSError as error:
                        raise ConfigurationError(
                            f"cannot read registered spec for job {job_id}: {error}"
                        ) from error
                    if registered_hash != input_sha256:
                        raise ConfigurationError(
                            f"registered spec hash mismatch for job {job_id}"
                        )
                elif is_tests_written:
                    input_sha256 = event.get("input_sha256")
                    if (
                        event.get("input_path") != "tests.json"
                        or not isinstance(input_sha256, str)
                        or re.fullmatch(r"[0-9a-f]{64}", input_sha256) is None
                        or has_red_metadata
                    ):
                        raise ConfigurationError(
                            f"invalid registered test metadata for job {job_id}"
                        )
                    _validate_registered_tests(
                        path.parent, event_job, input_sha256
                    )
                elif is_tests_red:
                    if has_input_metadata or not has_red_metadata:
                        raise ConfigurationError(
                            f"invalid baseline-red event metadata for job {job_id}"
                        )
                    _validate_red_report(path.parent, event_job, event)
                elif is_plan_approved:
                    if (
                        has_input_metadata
                        or has_red_metadata
                        or not has_plan_metadata
                    ):
                        raise ConfigurationError(
                            f"invalid slice plan metadata for job {job_id}"
                        )
                    _validate_approved_plan_artifacts(path.parent, event)
                elif is_gate_entry:
                    if events[-1]["type"] != "SLICE_PASSED":
                        raise ConfigurationError(
                            f"final Gate started without a completed slice for job {job_id}"
                        )
                    _validate_all_slices_passed(path.parent, events)
                elif is_final_review:
                    final_gate = events[-1]
                    if (
                        final_gate["type"] != "FINAL_GATE_COMPLETED"
                        or final_gate.get("outcome") != "PASS"
                        or event.get("final_gate_report_path")
                        != final_gate.get("report_path")
                        or event.get("final_gate_report_sha256")
                        != final_gate.get("report_sha256")
                    ):
                        raise ConfigurationError(
                            f"review state requires a passing final Gate for job {job_id}"
                        )
                elif is_final_gate_failure:
                    started = next(
                        (
                            existing
                            for existing in reversed(events)
                            if existing["type"] == "FINAL_GATE_STARTED"
                        ),
                        None,
                    )
                    completed = next(
                        (
                            existing
                            for existing in reversed(events)
                            if existing["type"] == "FINAL_GATE_COMPLETED"
                        ),
                        None,
                    )
                    if (
                        started is None
                        or (
                            completed is not None
                            and completed["outcome"] == "PASS"
                        )
                        or event.get("final_gate_attempt_id")
                        != started["attempt_id"]
                        or not isinstance(event.get("failure_reason"), str)
                        or not event["failure_reason"].strip()
                    ):
                        raise ConfigurationError(
                            f"invalid final Gate failure event for job {job_id}"
                        )
                elif is_slice_failure:
                    if (
                        has_input_metadata
                        or has_red_metadata
                        or has_plan_metadata
                        or not has_initializer_failure
                    ):
                        raise ConfigurationError(
                            f"invalid slice failure event for job {job_id}"
                        )
                elif is_initializer_failure:
                    if (
                        has_input_metadata
                        or has_red_metadata
                        or has_plan_metadata
                        or not has_initializer_failure
                    ):
                        raise ConfigurationError(
                            f"invalid initializer failure event for job {job_id}"
                        )
                    _validate_initializer_failure_event(
                        path.parent, event, events[-1]
                    )
                elif (
                    has_input_metadata
                    or has_red_metadata
                    or has_plan_metadata
                    or has_initializer_failure
                ):
                    raise ConfigurationError(
                        f"unexpected registered input metadata for job {job_id}"
                    )
            elif event["type"] in {
                "RUN_STARTED",
                "RUN_FINISHED",
                "SLICE_GATE_COMPLETED",
                "SLICE_PASSED",
            }:
                _validate_run_event(path.parent, events, event, event_job)
            elif event["type"] in {"FINAL_GATE_STARTED", "FINAL_GATE_COMPLETED"}:
                _validate_final_gate_event(path.parent, events, event, event_job)
            elif event["type"] == "INITIALIZER_RUN_STARTED":
                if (
                    job.state != "TESTS_RED_OK"
                    or event_job != job
                    or events[-1]["type"]
                    in {"INITIALIZER_RUN_STARTED", "INITIALIZER_RUN_COMPLETED"}
                ):
                    raise ConfigurationError(
                        f"invalid initializer start event for job {job_id}"
                    )
                _validate_initializer_attempt(path.parent, event)
            elif event["type"] == "INITIALIZER_RUN_COMPLETED":
                if (
                    job.state != "TESTS_RED_OK"
                    or event_job != job
                    or event.get("outcome") != "OK"
                    or events[-1]["type"] != "INITIALIZER_RUN_STARTED"
                    or events[-1].get("attempt_id") != event.get("attempt_id")
                    or events[-1].get("profile") != event.get("profile")
                    or events[-1].get("scope_sha256") != event.get("scope_sha256")
                    or events[-1].get("task_sha256") != event.get("task_sha256")
                    or not isinstance(event.get("run_dir"), str)
                    or not re.fullmatch(r"runs/[1-9][0-9]*", event["run_dir"])
                    or (
                        event.get("session_id") is not None
                        and (
                            not isinstance(event["session_id"], str)
                            or not event["session_id"].strip()
                        )
                    )
                ):
                    raise ConfigurationError(
                        f"invalid initializer run event for job {job_id}"
                    )
                diagnostic_dir = path.parent / event["run_dir"]
                if diagnostic_dir.is_symlink() or not diagnostic_dir.is_dir():
                    raise ConfigurationError(
                        f"initializer diagnostics are missing or invalid for job {job_id}"
                    )
                _validate_initializer_artifacts(path.parent, event)
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
    _write_job_projections(job_dir, events)
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
    persisted_job, events = _read_event_log(job_dir / "events.jsonl", updated.job_id)
    if persisted_job != updated:
        raise ConfigurationError("persisted event job snapshot does not match")
    _write_snapshot(job_dir / "job.json", persisted_job)
    _write_job_projections(job_dir, events)
    return {"job": updated.model_dump(mode="json"), "last_event": event}


def _record_state_change(job_dir, current, target, reason, resuming=False, details=None):
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
    if details:
        event.update(details)

    return _persist_event(job_dir, event, updated)


def register_spec(factory_root, job_id, spec_path, approved):
    if not approved:
        raise ConfigurationError("spec registration requires explicit --approve")

    source_path = Path(spec_path)
    if source_path.is_symlink():
        raise ConfigurationError("spec path must not be a symlink")
    try:
        source_path = source_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ConfigurationError(f"invalid spec path: {spec_path}") from error
    if not source_path.is_file():
        raise ConfigurationError(f"spec path is not a regular file: {spec_path}")
    try:
        content = source_path.read_bytes()
    except OSError as error:
        raise ConfigurationError(f"cannot read spec {spec_path}: {error}") from error
    if not content.strip():
        raise ConfigurationError("spec file must not be empty")

    job_id, job_dir = _job_location(factory_root, job_id)
    spec_hash = hashlib.sha256(content).hexdigest()
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if job.state != "DRAFT_SPEC":
            raise ConfigurationError("spec registration requires DRAFT_SPEC")

        stored_spec = job_dir / "spec.md"
        if stored_spec.is_symlink() or (
            stored_spec.exists() and not stored_spec.is_file()
        ):
            raise ConfigurationError(f"invalid stored spec path: {stored_spec}")
        if stored_spec.exists():
            try:
                stored_content = stored_spec.read_bytes()
            except OSError as error:
                raise FactoryError(f"cannot read stored spec: {error}") from error
            if stored_content != content:
                raise FactoryError("an unregistered spec artifact already exists")
        else:
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=job_dir,
                    prefix=".spec-",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                _replace_with_retry(temporary_path, stored_spec)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

        return _record_state_change(
            job_dir,
            current,
            "SPEC_APPROVED",
            "Approved specification registered",
            details={
                "input_path": "spec.md",
                "input_sha256": spec_hash,
            },
        )


def _write_durable_artifact(path, content):
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _write_baseline_red_artifacts(job_dir, result):
    attempts_dir = job_dir / "tests-red"
    if attempts_dir.is_symlink() or (
        attempts_dir.exists() and not attempts_dir.is_dir()
    ):
        raise FactoryError("invalid baseline-red artifact directory")
    try:
        attempts_dir.mkdir(exist_ok=True)
        attempt_dir = attempts_dir / str(uuid4())
        attempt_dir.mkdir()
        report_path = attempt_dir / "red-report.json"
        _write_durable_artifact(report_path, result.stdout)
        _write_durable_artifact(
            attempt_dir / "checks.stdout.txt", result.stdout
        )
        _write_durable_artifact(
            attempt_dir / "checks.stderr.txt", result.stderr
        )
        result_summary = json.dumps(
            {
                "verdict": result.verdict,
                "exit_code": result.exit_code,
                "error": result.error,
            },
            indent=2,
            sort_keys=True,
        ) + "\n"
        _write_durable_artifact(
            attempt_dir / "result.json", result_summary.encode("utf-8")
        )
    except OSError as error:
        raise FactoryError(f"cannot write baseline-red artifacts: {error}") from error
    return report_path


def register_tests(factory_root, job_id, manifest_path, approved):
    if not approved:
        raise ConfigurationError("test registration requires explicit --approve")

    source_path = Path(manifest_path)
    if source_path.is_symlink():
        raise ConfigurationError("test manifest path must not be a symlink")
    try:
        source_path = source_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ConfigurationError(f"invalid test manifest path: {manifest_path}") from error
    if not source_path.is_file():
        raise ConfigurationError(f"test manifest path is not a regular file: {manifest_path}")
    try:
        source_content = source_path.read_bytes()
    except OSError as error:
        raise ConfigurationError(f"cannot read test manifest: {error}") from error

    job_id, job_dir = _job_location(factory_root, job_id)
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if job.state not in {"SPEC_APPROVED", "TESTS_WRITTEN"}:
            raise ConfigurationError(
                "test registration requires SPEC_APPROVED or TESTS_WRITTEN"
            )
        if (
            job.worktree_status != "attached"
            or not job.worktree_path
            or not job.work_branch_start_sha
        ):
            raise ConfigurationError("test registration requires an attached worktree")
        worktree_path = Path(job.worktree_path)
        if worktree_path.is_symlink() or not worktree_path.is_dir():
            raise ConfigurationError("test registration worktree is missing or invalid")
        manifest = _normalize_test_manifest(
            _parse_test_manifest(source_content), worktree_path
        )
        normalized_content = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        stored_tests = job_dir / "tests.json"
        if stored_tests.is_symlink() or (
            stored_tests.exists() and not stored_tests.is_file()
        ):
            raise ConfigurationError("invalid stored test manifest path")
        if job.state == "SPEC_APPROVED":
            if stored_tests.exists():
                if stored_tests.read_bytes() != normalized_content:
                    raise FactoryError("an unregistered test manifest already exists")
            else:
                temporary_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=job_dir,
                        prefix=".tests-",
                        suffix=".tmp",
                        delete=False,
                    ) as temporary:
                        temporary_path = Path(temporary.name)
                        temporary.write(normalized_content)
                        temporary.flush()
                        os.fsync(temporary.fileno())
                    _replace_with_retry(temporary_path, stored_tests)
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
            input_sha256 = hashlib.sha256(normalized_content).hexdigest()
            current = _record_state_change(
                job_dir,
                current,
                "TESTS_WRITTEN",
                "Approved TDD test manifest registered",
                details={
                    "input_path": "tests.json",
                    "input_sha256": input_sha256,
                },
            )
        else:
            registered_content = stored_tests.read_bytes()
            if registered_content != normalized_content:
                raise FactoryError("registered test manifest is locked")
            input_sha256 = hashlib.sha256(registered_content).hexdigest()

        from .gate import run_checks

        config = _load_model(Path(factory_root) / "config.json", FactoryConfig)
        result = run_checks(
            worktree_path,
            job.work_branch_start_sha,
            "red",
            config,
            manifest_path=stored_tests.resolve(strict=True),
            manifest_sha256=input_sha256,
            test_ids=[test["id"] for test in manifest["tests"]],
        )
        report_path = _write_baseline_red_artifacts(job_dir, result)
        if result.verdict != "PASS":
            raise FactoryError(
                f"baseline-red check failed: {result.error or 'invalid report'}; "
                f"diagnostics: {report_path.parent}"
            )
        try:
            _validate_registered_tests(
                job_dir,
                JobRecord.model_validate(current["job"]),
                input_sha256,
            )
        except ConfigurationError as error:
            raise FactoryError(
                f"registered tests changed during baseline-red check: {error}; "
                f"diagnostics: {report_path.parent}"
            ) from error

        report_relative_path = report_path.relative_to(job_dir).as_posix()
        completed = _record_state_change(
            job_dir,
            current,
            "TESTS_RED_OK",
            "Baseline-red TDD tests verified",
            details={
                "red_report_path": report_relative_path,
                "red_report_sha256": hashlib.sha256(
                    report_path.read_bytes()
                ).hexdigest(),
            },
        )
        return completed


def _initializer_task(spec_content, tests, scope):
    return (
        "You are the Software Factory initializer. Do not edit or create files in the "
        "target worktree. Read the locked specification, registered tests, and source "
        "files as needed, then return exactly one JSON object in your final assistant "
        "message with keys `plan` and `init_script`.\n\n"
        "The plan must use schema_version 1 and this shape: features is a non-empty "
        "ordered array of objects with id, title, and ordered slices. Each slice has "
        "id, goal, paths (exact repository-relative paths, not globs), acceptance "
        "(testable criteria), and test_ids (registered test IDs). Use each registered "
        "test ID exactly once across all slices. The init_script value must be the "
        "complete Python source for `.factory/init.py`.\n\n"
        "Approved specification:\n"
        f"{spec_content.decode('utf-8')}\n\n"
        "Locked test manifest:\n"
        f"{json.dumps(tests, indent=2, sort_keys=True)}\n\n"
        "Approved scope patterns:\n"
        f"{json.dumps(scope, indent=2)}\n"
    )


def _parse_initializer_response(stdout_path):
    from .gate import _reject_json_constant

    from .runner import _read_jsonl

    records, errors = _read_jsonl(stdout_path)
    if errors:
        raise ConfigurationError(
            "initializer output is invalid: " + "; ".join(errors)
        )
    messages = [
        record.get("content")
        for record in records
        if record.get("type") == "assistant.message"
    ]
    if not messages or not isinstance(messages[-1], str):
        raise ConfigurationError("initializer did not return a final assistant message")
    try:
        proposal = json.loads(
            messages[-1],
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, TypeError) as error:
        raise ConfigurationError(
            f"initializer final message must be one JSON object: {error}"
        ) from error
    if (
        not isinstance(proposal, dict)
        or set(proposal) != {"plan", "init_script"}
        or not isinstance(proposal["plan"], dict)
        or not isinstance(proposal["init_script"], str)
        or not proposal["init_script"].strip()
        or "\0" in proposal["init_script"]
    ):
        raise ConfigurationError(
            "initializer response must contain only a plan object and non-empty init_script"
        )
    return proposal


def _validate_slice_plan(plan, tests, scope):
    from .gate import _matches_path

    if (
        not isinstance(plan, dict)
        or set(plan) != {"schema_version", "features"}
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or not isinstance(plan["features"], list)
        or not plan["features"]
    ):
        raise ConfigurationError("slice plan must use schema_version 1 and contain features")

    registered_ids = {test["id"] for test in tests["tests"]}
    feature_ids = set()
    slice_ids = set()
    mapped_ids = []
    id_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
    for feature in plan["features"]:
        if (
            not isinstance(feature, dict)
            or set(feature) != {"id", "title", "slices"}
            or not isinstance(feature["id"], str)
            or id_pattern.fullmatch(feature["id"]) is None
            or feature["id"] in feature_ids
            or not isinstance(feature["title"], str)
            or not feature["title"].strip()
            or not isinstance(feature["slices"], list)
            or not feature["slices"]
        ):
            raise ConfigurationError("slice plan contains an invalid feature")
        feature_ids.add(feature["id"])
        for item in feature["slices"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"id", "goal", "paths", "acceptance", "test_ids"}
                or not isinstance(item["id"], str)
                or id_pattern.fullmatch(item["id"]) is None
                or item["id"] in slice_ids
                or not isinstance(item["goal"], str)
                or not item["goal"].strip()
                or not isinstance(item["acceptance"], list)
                or not item["acceptance"]
                or any(
                    not isinstance(criteria, str) or not criteria.strip()
                    for criteria in item["acceptance"]
                )
                or not isinstance(item["paths"], list)
                or not item["paths"]
                or not isinstance(item["test_ids"], list)
                or not item["test_ids"]
            ):
                raise ConfigurationError("slice plan contains an invalid slice")
            slice_ids.add(item["id"])
            for path in item["paths"]:
                if (
                    not isinstance(path, str)
                    or not path
                    or "\\" in path
                    or any(character in path for character in "*?[]\0\r\n")
                    or path.startswith("/")
                    or (len(path) > 1 and path[1] == ":")
                    or any(part in {"", ".", ".."} for part in path.split("/"))
                    or path == ".factory/init.py"
                ):
                    raise ConfigurationError("slice plan contains an unsafe path")
                if not any(_matches_path(pattern, path) for pattern in scope):
                    raise ConfigurationError("slice plan path is outside approved scope")
            if any(not isinstance(test_id, str) for test_id in item["test_ids"]):
                raise ConfigurationError("slice plan contains invalid test IDs")
            if len(set(item["test_ids"])) != len(item["test_ids"]):
                raise ConfigurationError("slice plan contains invalid test IDs")
            mapped_ids.extend(item["test_ids"])

    if len(mapped_ids) != len(registered_ids) or set(mapped_ids) != registered_ids:
        raise ConfigurationError("every registered test ID must appear exactly once")
    return plan


def _store_locked_artifact(path, content):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise FactoryError(f"invalid locked artifact path: {path}")
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as error:
            raise FactoryError(f"cannot read locked artifact {path}: {error}") from error
        if existing != content:
            raise FactoryError(f"locked artifact already exists with different content: {path}")
        return
    _write_durable_artifact(path, content)


def _validate_approved_plan_artifacts(job_dir, event):
    expected = {
        "plan_path": ("slice-plan.json", "plan_sha256"),
        "init_script_path": ("init.py", "init_script_sha256"),
    }
    for path_field, (expected_path, hash_field) in expected.items():
        digest = event.get(hash_field)
        if (
            event.get(path_field) != expected_path
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ConfigurationError("invalid approved slice plan metadata")
        artifact = job_dir / expected_path
        if artifact.is_symlink() or not artifact.is_file():
            raise ConfigurationError("approved slice plan artifact is missing or invalid")
        try:
            actual_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        except OSError as error:
            raise ConfigurationError(f"cannot read approved slice plan: {error}") from error
        if actual_digest != digest:
            raise ConfigurationError("approved slice plan artifact hash mismatch")

    scope_path = job_dir / "scope.json"
    if (
        event.get("init_script_target") != ".factory/init.py"
        or not isinstance(event.get("scope_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", event["scope_sha256"]) is None
        or not isinstance(event.get("setup_commit"), str)
        or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", event["setup_commit"])
        is None
        or scope_path.is_symlink()
        or not scope_path.is_file()
    ):
        raise ConfigurationError("invalid approved initializer metadata")
    try:
        scope_hash = hashlib.sha256(scope_path.read_bytes()).hexdigest()
    except OSError as error:
        raise ConfigurationError(f"cannot read approved scope: {error}") from error
    if scope_hash != event["scope_sha256"]:
        raise ConfigurationError("approved scope hash mismatch")


def _approved_plan(job_dir):
    path = job_dir / "slice-plan.json"
    if path.is_symlink() or not path.is_file():
        raise ConfigurationError("approved slice plan is missing or invalid")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise ConfigurationError(f"cannot read approved slice plan: {error}") from error


def _find_plan_slice(job_dir, feature_id, slice_id):
    matches = [
        (feature, item)
        for feature in _approved_plan(job_dir)["features"]
        for item in feature["slices"]
        if feature["id"] == feature_id and item["id"] == slice_id
    ]
    if len(matches) != 1:
        raise ConfigurationError("slice event does not match the approved plan")
    return matches[0]


def _write_projection(path, content):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ConfigurationError(f"invalid projection path: {path.name}")
    if path.is_file() and path.read_bytes() == content:
        return
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        _replace_with_retry(temporary_path, path)
    except OSError as error:
        raise FactoryError(f"cannot write {path.name} projection: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _write_job_projections(job_dir, events):
    approval = next(
        (
            event
            for event in events
            if event["type"] == "JOB_STATE_CHANGED"
            and event.get("from") == "TESTS_RED_OK"
            and event.get("to") == "BUILDING"
        ),
        None,
    )
    if approval is None:
        return
    plan = _approved_plan(job_dir)
    passed = {
        event["slice_id"]: event
        for event in events
        if event["type"] == "SLICE_PASSED"
    }
    features = []
    progress_lines = ["# Job progress", ""]
    next_slice = None
    for feature in plan["features"]:
        feature_slices = []
        feature_passes = True
        progress_lines.extend([f"## {feature['title']}", ""])
        for item in feature["slices"]:
            event = passed.get(item["id"])
            has_passed = event is not None
            feature_passes = feature_passes and has_passed
            if not has_passed and next_slice is None:
                next_slice = item
            projection_slice = dict(item)
            projection_slice["passes"] = has_passed
            if event is not None:
                projection_slice.update(
                    {
                        "commit_sha": event["commit_sha"],
                        "tests": event["test_ids"],
                    }
                )
            feature_slices.append(projection_slice)
            progress_lines.append(
                f"- **{item['id']}** — {'PASS' if has_passed else 'pending'}"
            )
            if event is not None:
                progress_lines.append(f"  - Commit: `{event['commit_sha']}`")
                progress_lines.append(
                    f"  - Tests: {', '.join(event['test_ids'])}"
                )
                progress_lines.append(
                    f"  - Handoff: {' '.join(event['handoff'].split())}"
                )
            progress_lines.append("")
        features.append(
            {
                "id": feature["id"],
                "title": feature["title"],
                "passes": feature_passes,
                "slices": feature_slices,
            }
        )
    if next_slice is None:
        progress_lines.extend(["All planned slices have passed.", ""])
    else:
        progress_lines.extend([f"Next slice: `{next_slice['id']}`", ""])
    final_gate_event = next(
        (
            event
            for event in reversed(events)
            if event["type"] in {"FINAL_GATE_STARTED", "FINAL_GATE_COMPLETED"}
        ),
        None,
    )
    if final_gate_event is not None:
        progress_lines.extend(
            [
                f"Final Gate: {final_gate_event['type'].removeprefix('FINAL_GATE_')}"
                + (
                    f" ({final_gate_event['outcome']})"
                    if "outcome" in final_gate_event
                    else ""
                ),
                "",
            ]
        )
    progress_lines.extend([f"Job state: {events[-1]['data']['state']}", ""])
    _write_projection(
        job_dir / "features.json",
        (json.dumps({"schema_version": 1, "features": features}, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    _write_projection(
        job_dir / "progress.md",
        "\n".join(progress_lines).encode("utf-8"),
    )


def _validate_run_event(job_dir, events, event, event_job):
    previous = events[-1]
    event_type = event["type"]
    if event_type == "RUN_STARTED":
        if (
            event_job.state != "BUILDING"
            or event_job != JobRecord.model_validate(previous["data"])
            or previous["type"] not in {"JOB_STATE_CHANGED", "JOB_RESUMED", "SLICE_PASSED"}
            or not isinstance(event.get("run_id"), str)
            or re.fullmatch(r"[0-9a-f]{32}", event["run_id"]) is None
            or not isinstance(event.get("feature_id"), str)
            or not isinstance(event.get("slice_id"), str)
            or not isinstance(event.get("profile"), str)
            or not event["profile"].strip()
            or not isinstance(event.get("head_before"), str)
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", event["head_before"]) is None
            or not isinstance(event.get("plan_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", event["plan_sha256"]) is None
        ):
            raise ConfigurationError("invalid slice run start event")
        feature, item = _find_plan_slice(
            job_dir, event["feature_id"], event["slice_id"]
        )
        if (
            hashlib.sha256((job_dir / "slice-plan.json").read_bytes()).hexdigest()
            != event["plan_sha256"]
            or item["id"] in {
                passed["slice_id"]
                for passed in events
                if passed["type"] == "SLICE_PASSED"
            }
        ):
            raise ConfigurationError("slice run start does not match the approved plan")
        task_path = event.get("task_path")
        task_sha256 = event.get("task_sha256")
        if (
            not isinstance(task_path, str)
            or task_path != f"slice-runs/{event['run_id']}/task.md"
            or not isinstance(task_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", task_sha256) is None
        ):
            raise ConfigurationError("invalid slice task snapshot metadata")
        task_file = job_dir / task_path
        if (
            task_file.is_symlink()
            or not task_file.is_file()
            or hashlib.sha256(task_file.read_bytes()).hexdigest() != task_sha256
        ):
            raise ConfigurationError("slice task snapshot is missing or changed")
        if any(
            existing["type"] == "RUN_STARTED"
            and not any(
                later["type"] == "RUN_FINISHED"
                and later.get("run_id") == existing.get("run_id")
                for later in events[index + 1 :]
            )
            for index, existing in enumerate(events)
        ):
            raise ConfigurationError("slice run started before prior run completed")
        return

    run_start = previous if previous["type"] == "RUN_STARTED" else None
    if event_type == "RUN_FINISHED":
        if (
            run_start is None
            or event_job.state != "BUILDING"
            or event_job != JobRecord.model_validate(run_start["data"])
            or event.get("run_id") != run_start.get("run_id")
            or event.get("feature_id") != run_start.get("feature_id")
            or event.get("slice_id") != run_start.get("slice_id")
            or event.get("outcome") not in {"OK", "BUDGET", "TIMEOUT", "ERROR"}
        ):
            raise ConfigurationError("invalid slice run completion event")
        if event["outcome"] == "OK":
            if (
                not isinstance(event.get("commit_sha"), str)
                or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", event["commit_sha"]) is None
                or not isinstance(event.get("handoff"), str)
                or not event["handoff"].strip()
                or not isinstance(event.get("test_ids"), list)
                or event["test_ids"] != _find_plan_slice(
                    job_dir, run_start["feature_id"], run_start["slice_id"]
                )[1]["test_ids"]
            ):
                raise ConfigurationError("invalid successful slice run metadata")
        run_dir = event.get("run_dir")
        if run_dir is not None:
            if (
                not isinstance(run_dir, str)
                or re.fullmatch(r"runs/[1-9][0-9]*", run_dir) is None
                or (job_dir / run_dir).is_symlink()
                or not (job_dir / run_dir).is_dir()
            ):
                raise ConfigurationError("invalid slice Runner diagnostic directory")
        return

    run_finished = previous if previous["type"] == "RUN_FINISHED" else None
    if event_type == "SLICE_GATE_COMPLETED":
        if (
            run_finished is None
            or run_finished.get("outcome") != "OK"
            or event_job.state != "BUILDING"
            or event_job != JobRecord.model_validate(run_finished["data"])
            or event.get("run_id") != run_finished.get("run_id")
            or event.get("outcome") not in {"PASS", "FAIL", "ERROR"}
            or event.get("feature_id") != run_finished.get("feature_id")
            or event.get("slice_id") != run_finished.get("slice_id")
        ):
            raise ConfigurationError("invalid slice Gate event")
        report_path = event.get("report_path")
        report_sha256 = event.get("report_sha256")
        if event["outcome"] == "ERROR":
            if (
                report_path is not None
                or report_sha256 is not None
                or not isinstance(event.get("error"), str)
                or not event["error"].strip()
            ):
                raise ConfigurationError("invalid slice Gate error metadata")
            return
        if (
            not isinstance(report_path, str)
            or re.fullmatch(r"gate/[0-9a-f-]{36}/gate-report\.json", report_path) is None
            or not isinstance(report_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", report_sha256) is None
        ):
            raise ConfigurationError("invalid slice Gate report metadata")
        report_file = job_dir / report_path
        if (
            report_file.is_symlink()
            or not report_file.is_file()
            or hashlib.sha256(report_file.read_bytes()).hexdigest() != report_sha256
        ):
            raise ConfigurationError("slice Gate report is missing or changed")
        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise ConfigurationError(f"invalid slice Gate report: {error}") from error
        run_start = events[-2]
        if (
            not isinstance(report, dict)
            or report.get("verdict") != event["outcome"]
            or report.get("slice_id") != run_start.get("slice_id")
            or report.get("base_sha") != run_start.get("head_before")
        ):
            raise ConfigurationError("slice Gate report does not match its event")
        return

    if event_type == "SLICE_PASSED":
        gate_event = previous
        if (
            gate_event["type"] != "SLICE_GATE_COMPLETED"
            or gate_event.get("outcome") != "PASS"
            or event_job.state != "BUILDING"
            or event_job != JobRecord.model_validate(gate_event["data"])
            or event.get("run_id") != gate_event.get("run_id")
            or event.get("feature_id") != gate_event.get("feature_id")
            or event.get("slice_id") != gate_event.get("slice_id")
            or event.get("commit_sha") != events[-2].get("commit_sha")
            or event.get("test_ids") != events[-2].get("test_ids")
            or not isinstance(event.get("handoff"), str)
            or not event["handoff"].strip()
        ):
            raise ConfigurationError("invalid slice pass event")
        if any(
            existing["type"] == "SLICE_PASSED"
            and existing.get("slice_id") == event.get("slice_id")
            for existing in events
        ):
            raise ConfigurationError("slice passed more than once")
        return
    raise ConfigurationError(f"invalid slice event type for job {event_job.job_id}")


def _validate_all_slices_passed(job_dir, events):
    plan = _approved_plan(job_dir)
    expected_ids = {
        item["id"]
        for feature in plan["features"]
        for item in feature["slices"]
    }
    passed_ids = [
        event["slice_id"]
        for event in events
        if event["type"] == "SLICE_PASSED"
    ]
    if len(passed_ids) != len(expected_ids) or set(passed_ids) != expected_ids:
        raise ConfigurationError("final Gate requires every approved slice to pass")


def _validate_final_gate_event(job_dir, events, event, event_job):
    previous = events[-1]
    if event["type"] == "FINAL_GATE_STARTED":
        if (
            event_job.state != "GATE"
            or event_job != JobRecord.model_validate(previous["data"])
            or previous["type"] != "JOB_STATE_CHANGED"
            or previous.get("from") != "BUILDING"
            or previous.get("to") != "GATE"
            or not isinstance(event.get("attempt_id"), str)
            or re.fullmatch(r"[0-9a-f]{32}", event["attempt_id"]) is None
            or not isinstance(event.get("base_sha"), str)
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", event["base_sha"]) is None
            or event.get("base_sha") != event_job.work_branch_start_sha
            or event.get("mode") != "full"
        ):
            raise ConfigurationError("invalid final Gate start event")
        _validate_all_slices_passed(job_dir, events)
        if any(
            existing["type"] in {"FINAL_GATE_STARTED", "FINAL_GATE_COMPLETED"}
            for existing in events
        ):
            raise ConfigurationError("final Gate was already started")
        return

    start = previous
    if (
        start["type"] != "FINAL_GATE_STARTED"
        or event_job.state != "GATE"
        or event_job != JobRecord.model_validate(start["data"])
        or event.get("attempt_id") != start.get("attempt_id")
        or event.get("base_sha") != start.get("base_sha")
        or not isinstance(event.get("outcome"), str)
        or event["outcome"] not in {"PASS", "FAIL", "ERROR"}
    ):
        raise ConfigurationError("invalid final Gate completion event")
    if event["outcome"] == "ERROR":
        if (
            event.get("report_path") is not None
            or event.get("report_sha256") is not None
            or not isinstance(event.get("error"), str)
            or not event["error"].strip()
        ):
            raise ConfigurationError("invalid final Gate error metadata")
        return
    report_path = event.get("report_path")
    report_sha256 = event.get("report_sha256")
    if (
        not isinstance(report_path, str)
        or re.fullmatch(r"gate/[0-9a-f-]{36}/gate-report\.json", report_path) is None
        or not isinstance(report_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", report_sha256) is None
    ):
        raise ConfigurationError("invalid final Gate report metadata")
    report_file = job_dir / report_path
    if (
        report_file.is_symlink()
        or not report_file.is_file()
        or hashlib.sha256(report_file.read_bytes()).hexdigest() != report_sha256
    ):
        raise ConfigurationError("final Gate report is missing or changed")
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise ConfigurationError(f"invalid final Gate report: {error}") from error
    if (
        not isinstance(report, dict)
        or report.get("job_id") != event_job.job_id
        or report.get("base_sha") != event.get("base_sha")
        or report.get("mode") != "full"
        or report.get("slice_id") is not None
        or report.get("verdict") != event["outcome"]
    ):
        raise ConfigurationError("final Gate report does not match its event")


def _validate_slice_failure_state(job, event, previous):
    if (
        job.state != "BUILDING"
        or event.get("to") != "NEEDS_HUMAN"
        or event.get("failure_stage") not in {"initializer_script", "runner", "slice_gate"}
        or not isinstance(event.get("failure_reason"), str)
        or not event["failure_reason"].strip()
        or not isinstance(event.get("slice_id"), str)
        or previous["type"] not in {"RUN_STARTED", "RUN_FINISHED", "SLICE_GATE_COMPLETED"}
        or event.get("slice_id") != previous.get("slice_id")
        or (
            previous.get("run_id") is not None
            and event.get("run_id") != previous.get("run_id")
        )
    ):
        return False
    if event["failure_stage"] == "slice_gate":
        return (
            previous["type"] == "SLICE_GATE_COMPLETED"
            and previous.get("outcome") in {"FAIL", "ERROR"}
        )
    if event["failure_stage"] == "runner":
        return previous["type"] == "RUN_FINISHED" and previous.get("outcome") != "OK"
    return previous["type"] == "RUN_FINISHED" and previous.get("failure_stage") == "initializer_script"


def _validate_initializer_failure_event(job_dir, event, previous):
    attempt_id = event.get("attempt_id")
    if (
        not isinstance(attempt_id, str)
        or re.fullmatch(r"[0-9a-f]{32}", attempt_id) is None
        or event.get("failure_stage") != "initializer"
        or event.get("failure_outcome") not in {"BUDGET", "TIMEOUT", "ERROR"}
        or not isinstance(event.get("failure_reason"), str)
        or not event["failure_reason"].strip()
        or (
            event.get("session_id") is not None
            and (
                not isinstance(event["session_id"], str)
                or not event["session_id"].strip()
            )
        )
        or previous.get("type") != "INITIALIZER_RUN_STARTED"
        or previous.get("attempt_id") != attempt_id
        or previous.get("task_sha256") != event.get("task_sha256")
        or previous.get("scope_sha256") != event.get("scope_sha256")
        or previous.get("profile") != event.get("profile")
    ):
        raise ConfigurationError("invalid initializer failure metadata")
    _validate_initializer_attempt(job_dir, event)
    run_dir = event.get("run_dir")
    if run_dir is not None:
        if not isinstance(run_dir, str) or re.fullmatch(r"runs/[1-9][0-9]*", run_dir) is None:
            raise ConfigurationError("invalid initializer diagnostic directory")
        diagnostic_dir = job_dir / run_dir
        if diagnostic_dir.is_symlink() or not diagnostic_dir.is_dir():
            raise ConfigurationError("initializer diagnostics are missing or invalid")


def register_slice_plan(factory_root, job_id, plan_path, init_script_path, approved):
    if not approved:
        raise ConfigurationError("slice plan registration requires explicit --approve")
    if init_script_path is None:
        raise ConfigurationError("slice plan registration requires --init-script")

    def read_source(path, label):
        source = Path(path)
        if source.is_symlink():
            raise ConfigurationError(f"{label} path must not be a symlink")
        try:
            source = source.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ConfigurationError(f"invalid {label} path: {path}") from error
        if not source.is_file():
            raise ConfigurationError(f"{label} path is not a regular file: {path}")
        try:
            return source.read_bytes()
        except OSError as error:
            raise ConfigurationError(f"cannot read {label}: {error}") from error

    plan_content = read_source(plan_path, "slice plan")
    script_content = read_source(init_script_path, "initializer script")
    try:
        plan = json.loads(
            plan_content,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        script_text = script_content.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise ConfigurationError(f"invalid slice plan or initializer script: {error}") from error
    try:
        compile(script_text, ".factory/init.py", "exec")
    except SyntaxError as error:
        raise ConfigurationError(f"initializer script is invalid Python: {error}") from error

    job_id, job_dir = _job_location(factory_root, job_id)
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if job.state != "TESTS_RED_OK":
            raise ConfigurationError("slice plan approval requires TESTS_RED_OK")
        if current["last_event"]["type"] != "INITIALIZER_RUN_COMPLETED":
            raise ConfigurationError("slice plan approval requires a completed initializer run")
        if job.worktree_status != "attached" or not job.worktree_path:
            raise ConfigurationError("slice plan approval requires an attached worktree")

        worktree = Path(job.worktree_path)
        if worktree.is_symlink() or not worktree.is_dir():
            raise ConfigurationError("slice plan worktree is missing or invalid")
        from .gate import _matches_path, _read_scope_manifest

        scope = _read_scope_manifest(job_dir, worktree)
        scope_path = job_dir / "scope.json"
        scope_content = scope_path.read_bytes()
        scope_sha256 = hashlib.sha256(scope_content).hexdigest()
        if scope_sha256 != current["last_event"].get("scope_sha256"):
            raise ConfigurationError("approved scope changed after initializer run")
        tests = json.loads(
            (job_dir / "tests.json").read_bytes(),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        _validate_slice_plan(plan, tests, scope)
        if not any(_matches_path(pattern, ".factory/init.py") for pattern in scope):
            raise ConfigurationError("initializer script path is outside approved scope")

        factory_script = job_dir / "init.py"
        stored_plan = job_dir / "slice-plan.json"
        _store_locked_artifact(stored_plan, (json.dumps(
            plan, indent=2, sort_keys=True
        ) + "\n").encode("utf-8"))
        _store_locked_artifact(factory_script, script_content)

        target_script = worktree / ".factory" / "init.py"
        if (
            (worktree / ".factory").is_symlink()
            or not (worktree / ".factory").is_dir()
            or target_script.is_symlink()
            or target_script.exists()
        ):
            raise ConfigurationError("initializer target path is missing or already exists")
        if _run_git(
            worktree,
            ["status", "--porcelain", "--untracked-files=all", "--ignored"],
            "check plan approval worktree",
        ):
            raise FactoryError("plan approval requires a clean worktree")
        _write_durable_artifact(target_script, script_content)
        _run_git(worktree, ["add", "--", ".factory/init.py"], "stage initializer script")
        _run_git(
            worktree,
            [
                "commit",
                "--only",
                "-m",
                "Add approved initializer setup",
                "--",
                ".factory/init.py",
            ],
            "commit initializer script",
        )
        changed_paths = _run_git(
            worktree,
            ["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            "verify initializer commit",
        ).splitlines()
        if changed_paths != [".factory/init.py"]:
            raise FactoryError("initializer setup commit contains unexpected files")
        setup_commit = _run_git(
            worktree, ["rev-parse", "HEAD"], "record initializer commit"
        )
        committed_script = subprocess.run(
            ["git", "show", f"{setup_commit}:.factory/init.py"],
            cwd=worktree,
            capture_output=True,
            check=False,
        )
        if committed_script.returncode:
            detail = committed_script.stderr.decode("utf-8", errors="replace").strip()
            raise FactoryError(f"verify committed initializer script failed: {detail}")
        if committed_script.stdout != script_content:
            raise FactoryError("initializer commit content does not match approved script")

        persisted = _record_state_change(
            job_dir,
            current,
            "BUILDING",
            "Approved slice plan registered",
            details={
                "plan_path": "slice-plan.json",
                "plan_sha256": hashlib.sha256(stored_plan.read_bytes()).hexdigest(),
                "init_script_path": "init.py",
                "init_script_target": ".factory/init.py",
                "init_script_sha256": hashlib.sha256(script_content).hexdigest(),
                "scope_sha256": scope_sha256,
                "setup_commit": setup_commit,
            },
        )
        return persisted


def _initializer_run_dir(job_dir, result, *, required):
    raw_path = result.paths.get("run_dir")
    if raw_path is None:
        if required:
            raise ConfigurationError("initializer run directory is missing")
        return None, None
    run_dir = Path(raw_path)
    if not run_dir.is_absolute() or run_dir.is_symlink():
        if required:
            raise ConfigurationError("initializer run directory is invalid")
        return None, None
    runs_root = (job_dir / "runs").resolve()
    try:
        run_dir = run_dir.resolve(strict=True)
        run_dir.relative_to(runs_root)
    except (OSError, RuntimeError, ValueError) as error:
        if required:
            raise ConfigurationError(
                "initializer run artifacts are outside the job"
            ) from error
        return None, None
    if run_dir.parent != runs_root or not re.fullmatch(r"[1-9][0-9]*", run_dir.name):
        if required:
            raise ConfigurationError("initializer run directory is invalid")
        return None, None
    return run_dir, run_dir.relative_to(job_dir).as_posix()


def _record_initializer_failure(
    job_dir,
    job_id,
    attempt_id,
    profile_name,
    scope_sha256,
    task_sha256,
    outcome,
    reason,
    run_dir,
    session_id,
):
    if outcome not in {"BUDGET", "TIMEOUT", "ERROR"}:
        outcome = "ERROR"
    reason = str(reason).strip() or "initializer failed without a diagnostic"
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if (
            job.state != "TESTS_RED_OK"
            or current["last_event"]["type"] != "INITIALIZER_RUN_STARTED"
            or current["last_event"].get("attempt_id") != attempt_id
        ):
            raise FactoryError("job changed during initializer failure handling")
        return _record_state_change(
            job_dir,
            current,
            "NEEDS_HUMAN",
            f"Initializer failed: {reason}",
            details={
                "failure_stage": "initializer",
                "attempt_id": attempt_id,
                "profile": profile_name,
                "scope_sha256": scope_sha256,
                "task_sha256": task_sha256,
                "failure_outcome": outcome,
                "failure_reason": reason,
                "run_dir": run_dir,
                "session_id": session_id,
            },
        )


def initialize_job(factory_root, job_id, profile_name):
    job_id, job_dir = _job_location(factory_root, job_id)
    factory_root = Path(factory_root).resolve()
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if job.state != "TESTS_RED_OK":
            raise ConfigurationError("initializer requires TESTS_RED_OK")
        if current["last_event"]["type"] in {
            "INITIALIZER_RUN_STARTED",
            "INITIALIZER_RUN_COMPLETED",
        }:
            raise ConfigurationError("initializer attempt already exists; resume for a retry")
        if job.worktree_status != "attached" or not job.worktree_path:
            raise ConfigurationError("initializer requires an attached worktree")

        config = _load_model(factory_root / "config.json", FactoryConfig)
        if profile_name not in config.profiles:
            raise ConfigurationError(f"unknown runner profile: {profile_name}")
        profile = config.profiles[profile_name]

        worktree = Path(job.worktree_path)
        if worktree.is_symlink() or not worktree.is_dir():
            raise ConfigurationError("initializer worktree is missing or invalid")
        from .gate import _read_scope_manifest

        scope = _read_scope_manifest(job_dir, worktree)
        if _run_git(
            worktree,
            ["status", "--porcelain", "--untracked-files=all", "--ignored"],
            "check initializer worktree",
        ):
            raise FactoryError("initializer requires a clean worktree")

        spec_content = (job_dir / "spec.md").read_bytes()
        tests_content = (job_dir / "tests.json").read_bytes()
        tests = json.loads(tests_content, object_pairs_hook=_strict_json_object)
        task_content = _initializer_task(spec_content, tests, scope).encode("utf-8")
        initializer_dir = job_dir / "initializer"
        if initializer_dir.is_symlink() or (
            initializer_dir.exists() and not initializer_dir.is_dir()
        ):
            raise FactoryError("invalid initializer artifact directory")
        initializer_dir.mkdir(exist_ok=True)
        attempt_id = uuid4().hex
        attempt_dir = initializer_dir / f"attempt-{attempt_id}"
        attempt_dir.mkdir()
        task_snapshot = attempt_dir / "task.md"
        task_file = worktree / ".factory" / "task.md"
        if (
            (worktree / ".factory").is_symlink()
            or not (worktree / ".factory").is_dir()
            or task_file.is_symlink()
            or task_file.exists()
        ):
            attempt_dir.rmdir()
            raise ConfigurationError("initializer task path is missing or already exists")
        _write_durable_artifact(task_snapshot, task_content)
        try:
            _write_durable_artifact(task_file, task_content)
        except OSError:
            task_snapshot.unlink(missing_ok=True)
            attempt_dir.rmdir()
            raise

        scope_sha256 = hashlib.sha256((job_dir / "scope.json").read_bytes()).hexdigest()
        task_sha256 = hashlib.sha256(task_content).hexdigest()
        start_event = {
            "seq": current["last_event"]["seq"] + 1,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "job_id": job_id,
            "type": "INITIALIZER_RUN_STARTED",
            "actor": getpass.getuser(),
            "reason": "Initializer session started",
            "data": job.model_dump(mode="json"),
            "attempt_id": attempt_id,
            "profile": profile_name,
            "scope_sha256": scope_sha256,
            "task_sha256": task_sha256,
        }
        _persist_event(job_dir, start_event, job)

    from .runner import run_agent

    task_unchanged = False
    result = None
    try:
        result = run_agent(
            profile.agent,
            profile,
            task_file,
            worktree,
            f"initializer-{job_id[:8]}",
        )
    except (ConfigurationError, FactoryError, OSError) as error:
        failure = _record_initializer_failure(
            job_dir,
            job_id,
            attempt_id,
            profile_name,
            scope_sha256,
            task_sha256,
            "ERROR",
            str(error),
            None,
            None,
        )
        raise FactoryError(
            f"initializer failed and requires human attention: {failure['job']['state']}: {error}"
        ) from error
    finally:
        if task_file.is_file() and not task_file.is_symlink():
            try:
                task_unchanged = task_file.read_bytes() == task_content
                if task_unchanged:
                    task_file.unlink()
            except OSError:
                task_unchanged = False

    try:
        run_dir, run_dir_relative = _initializer_run_dir(
            job_dir, result, required=result.outcome == "OK"
        )
    except ConfigurationError as error:
        _record_initializer_failure(
            job_dir,
            job_id,
            attempt_id,
            profile_name,
            scope_sha256,
            task_sha256,
            "ERROR",
            str(error),
            None,
            result.session_id,
        )
        raise FactoryError(
            f"initializer session failed and requires human attention: {error}"
        ) from error

    if result.outcome != "OK":
        _record_initializer_failure(
            job_dir,
            job_id,
            attempt_id,
            profile_name,
            scope_sha256,
            task_sha256,
            result.outcome,
            result.error or result.outcome,
            run_dir_relative,
            result.session_id,
        )
        raise FactoryError(
            f"initializer session failed and requires human attention: "
            f"{result.error or result.outcome}"
        )

    try:
        if not task_unchanged:
            raise FactoryError("initializer changed or removed its task file")
        if _run_git(
            worktree,
            ["status", "--porcelain", "--untracked-files=all", "--ignored"],
            "verify initializer worktree",
        ):
            raise FactoryError("initializer changed the target worktree")
        stdout_path = Path(result.paths.get("stdout", ""))
        if not stdout_path.is_absolute() or stdout_path.is_symlink() or not stdout_path.is_file():
            raise ConfigurationError("initializer output is missing or invalid")
        try:
            stdout_path = stdout_path.resolve(strict=True)
            stdout_path.relative_to(run_dir)
        except (OSError, RuntimeError, ValueError) as error:
            raise ConfigurationError(
                "initializer output is outside its run directory"
            ) from error
        proposal = _parse_initializer_response(stdout_path)
        plan_content = (
            json.dumps(proposal["plan"], indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        compile(proposal["init_script"], ".factory/init.py", "exec")
        script_content = proposal["init_script"].encode("utf-8")
        if hashlib.sha256((job_dir / "scope.json").read_bytes()).hexdigest() != scope_sha256:
            raise ConfigurationError("approved scope changed during initializer session")
        plan_path = attempt_dir / "slice-plan.json"
        script_path = attempt_dir / "init.py"
        _write_durable_artifact(plan_path, plan_content)
        _write_durable_artifact(script_path, script_content)
    except (ConfigurationError, FactoryError, OSError, SyntaxError, UnicodeError) as error:
        _record_initializer_failure(
            job_dir,
            job_id,
            attempt_id,
            profile_name,
            scope_sha256,
            task_sha256,
            "ERROR",
            str(error),
            run_dir_relative,
            result.session_id,
        )
        raise FactoryError(
            f"initializer output rejected and requires human attention: {error}"
        ) from error

    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if (
            job.state != "TESTS_RED_OK"
            or current["last_event"]["type"] != "INITIALIZER_RUN_STARTED"
            or current["last_event"].get("attempt_id") != attempt_id
        ):
            raise FactoryError("job changed while initializer was running")
        event = {
            "seq": current["last_event"]["seq"] + 1,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "job_id": job_id,
            "type": "INITIALIZER_RUN_COMPLETED",
            "actor": getpass.getuser(),
            "reason": "Initializer proposal recorded",
            "data": job.model_dump(mode="json"),
            "attempt_id": attempt_id,
            "profile": profile_name,
            "outcome": "OK",
            "run_dir": run_dir_relative,
            "session_id": result.session_id,
            "scope_sha256": scope_sha256,
            "task_sha256": task_sha256,
            "plan_path": f"initializer/attempt-{attempt_id}/slice-plan.json",
            "plan_sha256": hashlib.sha256(plan_content).hexdigest(),
            "init_script_path": f"initializer/attempt-{attempt_id}/init.py",
            "init_script_sha256": hashlib.sha256(script_content).hexdigest(),
        }
        persisted = _persist_event(job_dir, event, job)
        persisted["proposal"] = {
            "plan_path": event["plan_path"],
            "plan_sha256": event["plan_sha256"],
            "init_script_path": event["init_script_path"],
            "init_script_sha256": event["init_script_sha256"],
        }
        return persisted


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


def _fetch_base_branch(project_root, project_config):
    base_ref = (
        f"refs/heads/{project_config.base_branch}:"
        f"refs/remotes/{project_config.remote}/{project_config.base_branch}"
    )
    _run_git(
        project_root,
        ["fetch", "--no-tags", project_config.remote, base_ref],
        "fetch configured base branch",
    )
    return _run_git(
        project_root,
        [
            "rev-parse",
            "--verify",
            (
                f"refs/remotes/{project_config.remote}/"
                f"{project_config.base_branch}^{{commit}}"
            ),
        ],
        "resolve fetched base branch",
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

        base_sha = _fetch_base_branch(project_root, project_config)
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
            base_sha = _fetch_base_branch(project_root, project_config)
            current = _record_worktree_event(
                job_dir,
                current,
                "WORKTREE_BASE_FETCHED",
                "Fetched configured base branch during repair",
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


def _record_slice_event(job_dir, current, event_type, reason, details):
    job = JobRecord.model_validate(current["job"])
    event = {
        "seq": current["last_event"]["seq"] + 1,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "job_id": job.job_id,
        "type": event_type,
        "actor": getpass.getuser(),
        "reason": reason,
        "data": job.model_dump(mode="json"),
        **details,
    }
    return _persist_event(job_dir, event, job)


def _worktree_changes(worktree):
    return _run_git(
        worktree,
        ["status", "--porcelain", "--untracked-files=all"],
        "check slice worktree",
    )


def _run_init_script(job_dir, worktree, config, run_id):
    script = worktree / ".factory" / "init.py"
    stored_script = job_dir / "init.py"
    if (
        script.is_symlink()
        or not script.is_file()
        or stored_script.is_symlink()
        or not stored_script.is_file()
    ):
        raise FactoryError("approved initializer script is missing or invalid")
    expected_hash = hashlib.sha256(stored_script.read_bytes()).hexdigest()
    if hashlib.sha256(script.read_bytes()).hexdigest() != expected_hash:
        raise FactoryError("approved initializer script changed")

    runs_dir = job_dir / "setup-runs"
    if runs_dir.is_symlink() or (runs_dir.exists() and not runs_dir.is_dir()):
        raise FactoryError("invalid initializer diagnostics directory")
    runs_dir.mkdir(exist_ok=True)
    attempt_dir = runs_dir / run_id
    attempt_dir.mkdir()
    stdout_path = attempt_dir / "stdout.txt"
    stderr_path = attempt_dir / "stderr.txt"
    environment = os.environ.copy()
    from .gate import _validate_secret_env_vars
    from .runner import _close_process_tree, _spawn_process, _terminate_process_tree

    for name in _validate_secret_env_vars(config):
        environment.pop(name, None)
    process = None
    exit_code = None
    error = None
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            try:
                process = _spawn_process(
                    [sys.executable, ".factory/init.py"],
                    cwd=worktree,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    shell=False,
                    env=environment,
                )
            except OSError as launch_error:
                error = f"cannot start initializer script: {launch_error}"
            if process is not None:
                try:
                    exit_code = process.wait(timeout=config.gate.timeout_seconds)
                except subprocess.TimeoutExpired:
                    error = f"initializer script timed out after {config.gate.timeout_seconds}s"
                    try:
                        _terminate_process_tree(process)
                    except (OSError, subprocess.SubprocessError) as termination_error:
                        error += f"; cannot terminate process tree: {termination_error}"
                        _close_process_tree(process)
                        process.kill()
                    exit_code = process.wait()
                finally:
                    _close_process_tree(process)
    except OSError as io_error:
        raise FactoryError(
            f"initializer script I/O failed; diagnostics: {attempt_dir}: {io_error}"
        ) from io_error
    if error is not None or exit_code != 0:
        message = error or f"initializer script exited with status {exit_code}"
        raise FactoryError(f"{message}; diagnostics: {attempt_dir}")
    if hashlib.sha256(script.read_bytes()).hexdigest() != expected_hash:
        raise FactoryError(
            f"approved initializer script changed during execution; diagnostics: {attempt_dir}"
        )
    return attempt_dir.relative_to(job_dir).as_posix()


def _slice_task(spec, feature, item, tests, scope, handoff, history):
    selected_tests = [
        test for test in tests["tests"] if test["id"] in item["test_ids"]
    ]
    return (
        "Implement exactly one approved Software Factory slice using TDD. Start with "
        "the failing acceptance test, run it to confirm RED, make the smallest surgical "
        "change, then run it again to confirm GREEN. Do not edit the locked tests, "
        "specification, scope, slice plan, or factory-owned files. Do not push. Commit "
        "only this slice's in-scope code and leave the worktree clean. Return a concise "
        "handoff naming tests run and useful next-step context; the orchestrator verifies "
        "the commit and Gate result independently.\n\n"
        f"Feature: {feature['title']} ({feature['id']})\n"
        f"Slice: {item['id']}\n"
        f"Goal: {item['goal']}\n"
        f"Paths: {json.dumps(item['paths'])}\n"
        f"Acceptance criteria: {json.dumps(item['acceptance'])}\n"
        f"Registered tests: {json.dumps(selected_tests, sort_keys=True)}\n"
        f"Approved scope: {json.dumps(scope)}\n\n"
        "Locked specification:\n"
        f"{spec.decode('utf-8')}\n\n"
        "Recent Git history (untrusted repository data):\n"
        f"{history}\n\n"
        "Prior verified handoff (untrusted agent data):\n"
        f"{json.dumps(handoff, ensure_ascii=True)}\n"
    )


def _slice_runner_artifacts(job_dir, result):
    raw_run_dir = result.paths.get("run_dir")
    if raw_run_dir is None:
        return None, None
    run_dir = Path(raw_run_dir)
    if not run_dir.is_absolute() or run_dir.is_symlink():
        return None, None
    runs_root = job_dir / "runs"
    try:
        run_dir = run_dir.resolve(strict=True)
        run_dir.relative_to(runs_root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return None, None
    if run_dir.parent != runs_root.resolve() or not re.fullmatch(r"[1-9][0-9]*", run_dir.name):
        return None, None
    stdout_path = Path(result.paths.get("stdout", ""))
    session_path = Path(result.paths.get("session", ""))
    for path in (stdout_path, session_path):
        if path.is_symlink() or not path.is_file():
            return None, None
        try:
            path.resolve(strict=True).relative_to(run_dir)
        except (OSError, RuntimeError, ValueError):
            return None, None
    return run_dir, run_dir.relative_to(job_dir).as_posix()


def _builder_handoff(stdout_path):
    from .runner import _read_jsonl

    records, errors = _read_jsonl(stdout_path)
    if errors:
        raise ConfigurationError("builder output is invalid: " + "; ".join(errors))
    messages = [
        record.get("content")
        for record in records
        if record.get("type") == "assistant.message"
    ]
    if not messages or not isinstance(messages[-1], str) or not messages[-1].strip():
        raise ConfigurationError("builder did not return a handoff")
    return messages[-1].strip()


def _fail_slice(job_dir, job_id, run_id, slice_id, stage, reason):
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if job.state != "BUILDING":
            raise FactoryError("job changed during slice failure handling")
        return _record_state_change(
            job_dir,
            current,
            "NEEDS_HUMAN",
            f"Slice {slice_id} failed: {reason}",
            details={
                "failure_stage": stage,
                "failure_reason": str(reason).strip() or "slice failed without a diagnostic",
                "run_id": run_id,
                "slice_id": slice_id,
            },
        )


def _require_human_slice_recovery(job_dir, current, reason):
    if current["job"]["state"] == "BUILDING":
        _record_state_change(
            job_dir,
            current,
            "NEEDS_HUMAN",
            f"Interrupted slice requires human recovery: {reason}",
        )
    raise FactoryError(f"interrupted slice requires human recovery: {reason}")


def _recover_committed_slice(job_dir, worktree, run_started):
    head_before = run_started["head_before"]
    head_after = _run_git(
        worktree, ["rev-parse", "HEAD"], "resolve interrupted slice commit"
    )
    if head_after == head_before:
        raise FactoryError("interrupted slice has no verifiable commit")
    merge_base = _run_git(
        worktree,
        ["merge-base", head_before, head_after],
        "verify interrupted slice commit ancestry",
    )
    if merge_base != head_before:
        raise FactoryError("interrupted slice commit is not based on its recorded HEAD")
    commit_count = _run_git(
        worktree,
        ["rev-list", "--count", f"{head_before}..{head_after}"],
        "count interrupted slice commits",
    )
    if commit_count != "1":
        raise FactoryError("interrupted slice does not have exactly one new commit")
    if _worktree_changes(worktree):
        raise FactoryError("interrupted slice worktree is dirty")
    target_script = worktree / ".factory" / "init.py"
    stored_script = job_dir / "init.py"
    if (
        target_script.is_symlink()
        or not target_script.is_file()
        or stored_script.is_symlink()
        or not stored_script.is_file()
        or hashlib.sha256(target_script.read_bytes()).digest()
        != hashlib.sha256(stored_script.read_bytes()).digest()
    ):
        raise FactoryError("interrupted slice changed the approved initializer script")
    return head_after


def _complete_slice_gate(
    factory_root,
    job_dir,
    job_id,
    run_id,
    feature,
    item,
    head_before,
    commit_sha,
    builder_handoff,
):
    from .gate import evaluate_gate

    try:
        gate_result = evaluate_gate(
            factory_root,
            job_id,
            head_before,
            "fast",
            item["id"],
        )
    except (ConfigurationError, FactoryError, OSError) as error:
        with _job_lock(job_dir):
            current = _read_job_contents(job_dir, job_id)
            if (
                current["job"]["state"] != "BUILDING"
                or current["last_event"]["type"] != "RUN_FINISHED"
                or current["last_event"].get("run_id") != run_id
            ):
                raise FactoryError("job changed during slice Gate execution")
            _record_slice_event(
                job_dir,
                current,
                "SLICE_GATE_COMPLETED",
                "Slice Gate could not complete",
                {
                    "run_id": run_id,
                    "feature_id": feature["id"],
                    "slice_id": item["id"],
                    "outcome": "ERROR",
                    "error": str(error),
                },
            )
        _fail_slice(
            job_dir,
            job_id,
            run_id,
            item["id"],
            "slice_gate",
            str(error),
        )
        raise FactoryError(
            f"slice Gate failed and requires human attention: {error}"
        ) from error

    report_path = gate_result.report_path.relative_to(job_dir).as_posix()
    outcome = gate_result.verdict
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        if (
            current["job"]["state"] != "BUILDING"
            or current["last_event"]["type"] != "RUN_FINISHED"
            or current["last_event"].get("run_id") != run_id
        ):
            raise FactoryError("job changed during slice Gate execution")
        _record_slice_event(
            job_dir,
            current,
            "SLICE_GATE_COMPLETED",
            f"Slice Gate returned {outcome}",
            {
                "run_id": run_id,
                "feature_id": feature["id"],
                "slice_id": item["id"],
                "outcome": outcome,
                "report_path": report_path,
                "report_sha256": hashlib.sha256(
                    gate_result.report_path.read_bytes()
                ).hexdigest(),
            },
        )
    if outcome != "PASS":
        _fail_slice(
            job_dir,
            job_id,
            run_id,
            item["id"],
            "slice_gate",
            f"Gate report: {report_path}",
        )
        raise FactoryError(
            f"slice Gate failed; diagnostics: {gate_result.report_path.parent}"
        )
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        if (
            current["job"]["state"] != "BUILDING"
            or current["last_event"]["type"] != "SLICE_GATE_COMPLETED"
            or current["last_event"].get("run_id") != run_id
            or current["last_event"].get("outcome") != "PASS"
        ):
            raise FactoryError("job changed before recording slice pass")
        _record_slice_event(
            job_dir,
            current,
            "SLICE_PASSED",
            f"Slice {item['id']} passed",
            {
                "run_id": run_id,
                "feature_id": feature["id"],
                "slice_id": item["id"],
                "commit_sha": commit_sha,
                "test_ids": item["test_ids"],
                "handoff": builder_handoff,
                "report_path": report_path,
            },
        )


def _record_final_gate_event(job_dir, current, event_type, reason, details):
    job = JobRecord.model_validate(current["job"])
    event = {
        "seq": current["last_event"]["seq"] + 1,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "job_id": job.job_id,
        "type": event_type,
        "actor": getpass.getuser(),
        "reason": reason,
        "data": job.model_dump(mode="json"),
        **details,
    }
    return _persist_event(job_dir, event, job)


def _fail_final_gate(job_dir, job_id, reason):
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        if current["job"]["state"] != "GATE":
            raise FactoryError("job changed during final Gate failure handling")
        _, events = _read_event_log(job_dir / "events.jsonl", job_id)
        started = next(
            event for event in reversed(events)
            if event["type"] == "FINAL_GATE_STARTED"
        )
        completed = next(
            (
                event
                for event in reversed(events)
                if event["type"] == "FINAL_GATE_COMPLETED"
            ),
            None,
        )
        if completed is not None and completed["outcome"] == "PASS":
            raise FactoryError("passing final Gate cannot be marked as failed")
        _record_state_change(
            job_dir,
            current,
            "NEEDS_HUMAN",
            f"Final Gate requires human attention: {reason}",
            details={
                "failure_stage": "final_gate",
                "failure_reason": str(reason).strip() or "final Gate failed",
                "final_gate_attempt_id": started["attempt_id"],
            },
        )
    raise FactoryError(f"final Gate failed and requires human attention: {reason}")


def _complete_final_gate(factory_root, job_dir, job_id):
    from .gate import evaluate_gate

    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        job = JobRecord.model_validate(current["job"])
        if job.state == "REVIEW":
            return current
        if job.state != "GATE":
            raise ConfigurationError("final Gate requires GATE")
        _, events = _read_event_log(job_dir / "events.jsonl", job_id)
        completed = next(
            (
                event
                for event in reversed(events)
                if event["type"] == "FINAL_GATE_COMPLETED"
            ),
            None,
        )
        started = next(
            (
                event
                for event in reversed(events)
                if event["type"] == "FINAL_GATE_STARTED"
            ),
            None,
        )
        if completed is not None:
            if completed["outcome"] != "PASS":
                failure_reason = completed.get("error") or (
                    f"Gate report: {completed.get('report_path')}"
                )
            else:
                _record_state_change(
                    job_dir,
                    current,
                    "REVIEW",
                    "Final Gate passed",
                    details={
                        "final_gate_report_path": completed["report_path"],
                        "final_gate_report_sha256": completed["report_sha256"],
                    },
                )
                return _read_job_contents(job_dir, job_id)
        elif started is not None:
            failure_reason = "final Gate was interrupted; automatic retry is not allowed"
        else:
            _validate_all_slices_passed(job_dir, events)
            attempt_id = uuid4().hex
            base_sha = job.work_branch_start_sha
            _record_final_gate_event(
                job_dir,
                current,
                "FINAL_GATE_STARTED",
                "Started final full Gate",
                {
                    "attempt_id": attempt_id,
                    "base_sha": base_sha,
                    "mode": "full",
                },
            )
            failure_reason = None

    if failure_reason is not None:
        _fail_final_gate(job_dir, job_id, failure_reason)

    try:
        gate_result = evaluate_gate(
            factory_root,
            job_id,
            base_sha,
            "full",
        )
    except (ConfigurationError, FactoryError, OSError) as error:
        with _job_lock(job_dir):
            current = _read_job_contents(job_dir, job_id)
            if (
                current["job"]["state"] != "GATE"
                or current["last_event"]["type"] != "FINAL_GATE_STARTED"
                or current["last_event"].get("attempt_id") != attempt_id
            ):
                raise FactoryError("job changed during final Gate execution")
            _record_final_gate_event(
                job_dir,
                current,
                "FINAL_GATE_COMPLETED",
                "Final Gate could not complete",
                {
                    "attempt_id": attempt_id,
                    "base_sha": base_sha,
                    "outcome": "ERROR",
                    "error": str(error),
                },
            )
        _fail_final_gate(job_dir, job_id, str(error))

    report_path = gate_result.report_path.relative_to(job_dir).as_posix()
    report_sha256 = hashlib.sha256(gate_result.report_path.read_bytes()).hexdigest()
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        if (
            current["job"]["state"] != "GATE"
            or current["last_event"]["type"] != "FINAL_GATE_STARTED"
            or current["last_event"].get("attempt_id") != attempt_id
        ):
            raise FactoryError("job changed during final Gate execution")
        completed = _record_final_gate_event(
            job_dir,
            current,
            "FINAL_GATE_COMPLETED",
            f"Final Gate returned {gate_result.verdict}",
            {
                "attempt_id": attempt_id,
                "base_sha": base_sha,
                "outcome": gate_result.verdict,
                "report_path": report_path,
                "report_sha256": report_sha256,
            },
        )
        if gate_result.verdict == "PASS":
            return _record_state_change(
                job_dir,
                completed,
                "REVIEW",
                "Final Gate passed",
                details={
                    "final_gate_report_path": report_path,
                    "final_gate_report_sha256": report_sha256,
                },
            )

    _fail_final_gate(
        job_dir,
        job_id,
        f"Gate report: {gate_result.report_path.parent}",
    )


def run_job(factory_root, job_id, profile_name):
    factory_root = Path(factory_root).resolve()
    job_id, job_dir = _job_location(factory_root, job_id)
    config = _load_model(factory_root / "config.json", FactoryConfig)
    if profile_name not in config.profiles:
        raise ConfigurationError(f"unknown runner profile: {profile_name}")
    profile = config.profiles[profile_name]
    with _job_lock(job_dir):
        current = _read_job_contents(job_dir, job_id)
        state = current["job"]["state"]
    if state == "GATE":
        return _complete_final_gate(factory_root, job_dir, job_id)
    if state == "REVIEW":
        return current
    if state != "BUILDING":
        raise ConfigurationError("slice run requires BUILDING or GATE")

    while True:
        recovery_to_gate = None
        recovered_pass = False
        with _job_lock(job_dir):
            current = _read_job_contents(job_dir, job_id)
            job = JobRecord.model_validate(current["job"])
            if job.state == "REVIEW":
                return current
            if job.state != "BUILDING":
                raise ConfigurationError("slice run requires BUILDING")
            _, events = _read_event_log(job_dir / "events.jsonl", job_id)
            slice_events = [
                event
                for event in events
                if event["type"]
                in {"RUN_STARTED", "RUN_FINISHED", "SLICE_GATE_COMPLETED", "SLICE_PASSED"}
            ]
            latest_slice_event = slice_events[-1] if slice_events else None
            finished_run_ids = {
                event["run_id"]
                for event in events
                if event["type"] == "RUN_FINISHED"
            }
            unfinished_runs = [
                event
                for event in events
                if event["type"] == "RUN_STARTED"
                and event["run_id"] not in finished_run_ids
            ]
            if len(unfinished_runs) > 1:
                _require_human_slice_recovery(
                    job_dir, current, "multiple incomplete slice sessions exist"
                )
            plan = _approved_plan(job_dir)
            plan_event = next(
                event
                for event in events
                if event["type"] == "JOB_STATE_CHANGED"
                and event.get("from") == "TESTS_RED_OK"
                and event.get("to") == "BUILDING"
            )
            plan_hash = hashlib.sha256(
                (job_dir / "slice-plan.json").read_bytes()
            ).hexdigest()
            if plan_hash != plan_event.get("plan_sha256"):
                raise ConfigurationError("approved slice plan changed")
            scope_path = job_dir / "scope.json"
            scope_hash = hashlib.sha256(scope_path.read_bytes()).hexdigest()
            if scope_hash != plan_event.get("scope_sha256"):
                raise ConfigurationError("approved scope changed")
            from .gate import _read_scope_manifest

            worktree = Path(job.worktree_path)
            if worktree.is_symlink() or not worktree.is_dir():
                raise ConfigurationError("slice worktree is missing or invalid")
            scope = _read_scope_manifest(job_dir, worktree)
            tests_written = next(
                event
                for event in events
                if event["type"] == "JOB_STATE_CHANGED"
                and event.get("to") == "TESTS_WRITTEN"
            )
            _, tests = _validate_registered_tests(
                job_dir, job, tests_written["input_sha256"]
            )
            passed_ids = {
                event["slice_id"]
                for event in events
                if event["type"] == "SLICE_PASSED"
            }
            pending = next(
                (
                    (feature, item)
                    for feature in plan["features"]
                    for item in feature["slices"]
                    if item["id"] not in passed_ids
                ),
                None,
            )
            if pending is None:
                if unfinished_runs:
                    _require_human_slice_recovery(
                        job_dir, current, "incomplete run has no pending slice"
                    )
                _record_state_change(
                    job_dir,
                    current,
                    "GATE",
                    "All approved slices passed; starting final Gate",
                )
                break
            feature, item = pending
            if unfinished_runs:
                run_started = unfinished_runs[0]
                if (
                    current["last_event"]["type"] != "RUN_STARTED"
                    or current["last_event"].get("run_id") != run_started["run_id"]
                    or run_started["feature_id"] != feature["id"]
                    or run_started["slice_id"] != item["id"]
                ):
                    _require_human_slice_recovery(
                        job_dir,
                        current,
                        "incomplete run is not the latest journaled operation",
                    )
                try:
                    commit_sha = _recover_committed_slice(
                        job_dir, worktree, run_started
                    )
                except (ConfigurationError, FactoryError, OSError) as error:
                    _require_human_slice_recovery(job_dir, current, str(error))
                run_id = run_started["run_id"]
                head_before = run_started["head_before"]
                builder_handoff = (
                    "Recovered the committed slice after an interruption; "
                    "no builder handoff was recorded."
                )
                _record_slice_event(
                    job_dir,
                    current,
                    "RUN_FINISHED",
                    f"Recovered committed slice {item['id']}",
                    {
                        "run_id": run_id,
                        "feature_id": feature["id"],
                        "slice_id": item["id"],
                        "outcome": "OK",
                        "commit_sha": commit_sha,
                        "test_ids": item["test_ids"],
                        "handoff": builder_handoff,
                        "task_sha256": run_started["task_sha256"],
                        "recovered": True,
                    },
                )
                recovery_to_gate = (
                    run_id,
                    feature,
                    item,
                    head_before,
                    commit_sha,
                    builder_handoff,
                )
            elif latest_slice_event is not None and latest_slice_event["type"] in {
                "RUN_FINISHED",
                "SLICE_GATE_COMPLETED",
            }:
                if current["last_event"]["seq"] != latest_slice_event["seq"]:
                    _require_human_slice_recovery(
                        job_dir,
                        current,
                        "incomplete slice has intervening journal events",
                    )
                if (
                    latest_slice_event["feature_id"] != feature["id"]
                    or latest_slice_event["slice_id"] != item["id"]
                ):
                    _require_human_slice_recovery(
                        job_dir, current, "incomplete event does not match pending slice"
                    )
                if latest_slice_event["type"] == "RUN_FINISHED":
                    if latest_slice_event["outcome"] != "OK":
                        failure_stage = latest_slice_event.get("failure_stage", "runner")
                        failure_reason = latest_slice_event.get(
                            "failure_reason", "interrupted runner failed"
                        )
                        _record_state_change(
                            job_dir,
                            current,
                            "NEEDS_HUMAN",
                            f"Slice {item['id']} failed: {failure_reason}",
                            details={
                                "failure_stage": failure_stage,
                                "failure_reason": failure_reason,
                                "run_id": latest_slice_event["run_id"],
                                "slice_id": item["id"],
                            },
                        )
                        raise FactoryError(
                            f"interrupted slice failed and requires human attention: "
                            f"{failure_reason}"
                        )
                    run_started = next(
                        event
                        for event in events
                        if event["type"] == "RUN_STARTED"
                        and event["run_id"] == latest_slice_event["run_id"]
                    )
                    try:
                        commit_sha = _recover_committed_slice(
                            job_dir, worktree, run_started
                        )
                    except (ConfigurationError, FactoryError, OSError) as error:
                        _require_human_slice_recovery(job_dir, current, str(error))
                    if commit_sha != latest_slice_event["commit_sha"]:
                        _require_human_slice_recovery(
                            job_dir, current, "recorded slice commit no longer matches HEAD"
                        )
                    recovery_to_gate = (
                        latest_slice_event["run_id"],
                        feature,
                        item,
                        run_started["head_before"],
                        commit_sha,
                        latest_slice_event["handoff"],
                    )
                elif latest_slice_event["outcome"] == "PASS":
                    run_finished = events[-2]
                    run_started = events[-3]
                    try:
                        commit_sha = _recover_committed_slice(
                            job_dir, worktree, run_started
                        )
                    except (ConfigurationError, FactoryError, OSError) as error:
                        _require_human_slice_recovery(job_dir, current, str(error))
                    if commit_sha != run_finished.get("commit_sha"):
                        _require_human_slice_recovery(
                            job_dir, current, "recorded slice commit no longer matches HEAD"
                        )
                    _record_slice_event(
                        job_dir,
                        current,
                        "SLICE_PASSED",
                        f"Recovered Gate-passed slice {item['id']}",
                        {
                            "run_id": latest_slice_event["run_id"],
                            "feature_id": feature["id"],
                            "slice_id": item["id"],
                            "commit_sha": run_finished["commit_sha"],
                            "test_ids": run_finished["test_ids"],
                            "handoff": run_finished["handoff"],
                            "report_path": latest_slice_event["report_path"],
                        },
                    )
                    recovered_pass = True
                else:
                    failure_reason = (
                        latest_slice_event.get("error")
                        or f"Gate report: {latest_slice_event.get('report_path')}"
                    )
                    _record_state_change(
                        job_dir,
                        current,
                        "NEEDS_HUMAN",
                        f"Slice {item['id']} Gate failed: {failure_reason}",
                        details={
                            "failure_stage": "slice_gate",
                            "failure_reason": failure_reason,
                            "run_id": latest_slice_event["run_id"],
                            "slice_id": item["id"],
                        },
                    )
                    raise FactoryError(
                        f"interrupted slice Gate failed and requires human attention: "
                        f"{failure_reason}"
                    )
            else:
                head_before = _run_git(
                    worktree, ["rev-parse", "HEAD"], "resolve slice starting commit"
                )
                run_id = uuid4().hex
                handoff = next(
                    (
                        event["handoff"]
                        for event in reversed(events)
                        if event["type"] == "SLICE_PASSED"
                    ),
                    "",
                )
                history = _run_git(
                    worktree, ["log", "-5", "--oneline"], "read recent Git history"
                )
                task_content = _slice_task(
                    (job_dir / "spec.md").read_bytes(),
                    feature,
                    item,
                    tests,
                    scope,
                    handoff,
                    history,
                ).encode("utf-8")
                attempt_dir = job_dir / "slice-runs"
                if attempt_dir.is_symlink() or (
                    attempt_dir.exists() and not attempt_dir.is_dir()
                ):
                    raise FactoryError("invalid slice run artifact directory")
                attempt_dir.mkdir(exist_ok=True)
                attempt_dir = attempt_dir / run_id
                attempt_dir.mkdir()
                task_snapshot = attempt_dir / "task.md"
                _write_durable_artifact(task_snapshot, task_content)
                start = _record_slice_event(
                    job_dir,
                    current,
                    "RUN_STARTED",
                    f"Started slice {item['id']}",
                    {
                        "run_id": run_id,
                        "feature_id": feature["id"],
                        "slice_id": item["id"],
                        "profile": profile_name,
                        "head_before": head_before,
                        "plan_sha256": plan_hash,
                        "task_path": task_snapshot.relative_to(job_dir).as_posix(),
                        "task_sha256": hashlib.sha256(task_content).hexdigest(),
                    },
                )

        if recovered_pass:
            continue
        if recovery_to_gate is not None:
            _complete_slice_gate(factory_root, job_dir, job_id, *recovery_to_gate)
            continue

        task_file = worktree / ".factory" / "task.md"
        if (
            (worktree / ".factory").is_symlink()
            or not (worktree / ".factory").is_dir()
            or task_file.is_symlink()
            or task_file.exists()
        ):
            _record_slice_event(
                job_dir,
                start,
                "RUN_FINISHED",
                "Slice setup failed",
                {
                    "run_id": run_id,
                    "feature_id": feature["id"],
                    "slice_id": item["id"],
                    "outcome": "ERROR",
                    "failure_stage": "initializer_script",
                    "failure_reason": "builder task path is missing or already exists",
                },
            )
            _fail_slice(
                job_dir,
                job_id,
                run_id,
                item["id"],
                "initializer_script",
                "builder task path is missing or already exists",
            )
            raise FactoryError("builder task path is missing or already exists")

        setup_run = None
        try:
            if _worktree_changes(worktree):
                raise FactoryError("slice requires a clean worktree before initialization")
            setup_run = _run_init_script(job_dir, worktree, config, run_id)
            if _worktree_changes(worktree):
                raise FactoryError("initializer script changed the target worktree")
            _write_durable_artifact(task_file, task_content)
        except (ConfigurationError, FactoryError, OSError) as error:
            _record_slice_event(
                job_dir,
                start,
                "RUN_FINISHED",
                "Slice setup failed",
                {
                    "run_id": run_id,
                    "feature_id": feature["id"],
                    "slice_id": item["id"],
                    "outcome": "ERROR",
                    "failure_stage": "initializer_script",
                    "failure_reason": str(error),
                    "setup_run": setup_run,
                },
            )
            _fail_slice(
                job_dir,
                job_id,
                run_id,
                item["id"],
                "initializer_script",
                str(error),
            )
            raise FactoryError(
                f"slice initializer failed and requires human attention: {error}"
            ) from error

        from .runner import run_agent

        task_unchanged = False
        try:
            result = run_agent(
                profile.agent,
                profile,
                task_file,
                worktree,
                f"build-{job_id[:8]}-{item['id']}",
            )
        except (ConfigurationError, FactoryError, OSError) as error:
            result = None
            runner_error = str(error)
        finally:
            if task_file.is_file() and not task_file.is_symlink():
                try:
                    task_unchanged = task_file.read_bytes() == task_content
                    if task_unchanged:
                        task_file.unlink()
                except OSError:
                    task_unchanged = False

        if result is None:
            outcome = "ERROR"
            runner_error = runner_error or "builder Runner failed"
            run_dir_relative = None
            session_id = None
        else:
            outcome = result.outcome
            runner_error = result.error or result.outcome
            run_dir, run_dir_relative = _slice_runner_artifacts(job_dir, result)
            session_id = result.session_id
            if outcome == "OK" and run_dir is None:
                outcome = "ERROR"
                runner_error = "builder Runner artifacts are missing or invalid"

        commit_sha = None
        builder_handoff = None
        if outcome == "OK":
            try:
                if not task_unchanged:
                    raise FactoryError("builder changed or removed its task file")
                if _worktree_changes(worktree):
                    raise FactoryError("builder left uncommitted worktree changes")
                target_script = worktree / ".factory" / "init.py"
                expected_script_hash = hashlib.sha256(
                    (job_dir / "init.py").read_bytes()
                ).hexdigest()
                if (
                    target_script.is_symlink()
                    or not target_script.is_file()
                    or hashlib.sha256(target_script.read_bytes()).hexdigest()
                    != expected_script_hash
                ):
                    raise FactoryError("builder changed the approved initializer script")
                _, events = _read_event_log(job_dir / "events.jsonl", job_id)
                tests_written = next(
                    event
                    for event in events
                    if event["type"] == "JOB_STATE_CHANGED"
                    and event.get("to") == "TESTS_WRITTEN"
                )
                _validate_registered_tests(
                    job_dir,
                    JobRecord.model_validate(start["job"]),
                    tests_written["input_sha256"],
                )
                commit_sha = _run_git(
                    worktree, ["rev-parse", "HEAD"], "resolve slice commit"
                )
                if commit_sha == head_before:
                    raise FactoryError("builder did not create a slice commit")
                builder_handoff = _builder_handoff(Path(result.paths["stdout"]))
            except (ConfigurationError, FactoryError, OSError) as error:
                outcome = "ERROR"
                runner_error = str(error)

        run_details = {
            "run_id": run_id,
            "feature_id": feature["id"],
            "slice_id": item["id"],
            "outcome": outcome,
            "run_dir": run_dir_relative,
            "session_id": session_id,
        }
        if outcome == "OK":
            run_details.update(
                {
                    "commit_sha": commit_sha,
                    "test_ids": item["test_ids"],
                    "handoff": builder_handoff,
                    "task_sha256": hashlib.sha256(task_content).hexdigest(),
                }
            )
        else:
            failure_stage = "runner"
            run_details.update(
                {
                    "failure_stage": failure_stage,
                    "failure_reason": runner_error,
                }
            )
        with _job_lock(job_dir):
            current = _read_job_contents(job_dir, job_id)
            if (
                current["last_event"]["type"] != "RUN_STARTED"
                or current["last_event"].get("run_id") != run_id
                or current["job"]["state"] != "BUILDING"
            ):
                raise FactoryError("job changed during builder session")
            _record_slice_event(
                job_dir,
                current,
                "RUN_FINISHED",
                "Builder session completed" if outcome == "OK" else "Builder session failed",
                run_details,
            )

        if outcome != "OK":
            _fail_slice(
                job_dir,
                job_id,
                run_id,
                item["id"],
                "runner",
                runner_error,
            )
            raise FactoryError(
                f"builder session failed and requires human attention: {runner_error}"
            )

        _complete_slice_gate(
            factory_root,
            job_dir,
            job_id,
            run_id,
            feature,
            item,
            head_before,
            commit_sha,
            builder_handoff,
        )

    return _complete_final_gate(factory_root, job_dir, job_id)
