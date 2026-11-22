import json
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Literal

from .job import (
    ConfigurationError,
    FactoryConfig,
    RunnerProfile,
    _job_location,
    _job_lock,
    _load_model,
    _read_event_log,
    _storage_path,
)


_REQUIRED_DENY_TOOL = "shell(git push)"
_FORBIDDEN_ALLOW_TOOLS = {
    "--allow-all",
    "--allow-all-tools",
    "--allow-all-paths",
    "--allow-all-urls",
    "--yolo",
}


@dataclass(frozen=True)
class RunResult:
    outcome: Literal["OK", "BUDGET", "TIMEOUT", "ERROR"]
    exit_code: int | None
    usage: dict | None
    session_id: str | None
    paths: dict[str, Path]
    error: str | None


def _validate_profile(role, profile, config):
    if not isinstance(profile, RunnerProfile):
        raise ConfigurationError("profile must be a validated runner profile")
    if role != profile.agent or profile not in config.profiles.values():
        raise ConfigurationError("role and profile must match a configured profile")


def _validate_runner_config(config):
    runner = config.runner
    if not runner.allow_tools:
        raise ConfigurationError("runner.allow_tools must not be empty")
    if _REQUIRED_DENY_TOOL not in runner.deny_tools:
        raise ConfigurationError(
            f"runner.deny_tools must include {_REQUIRED_DENY_TOOL!r}"
        )
    if any(
        tool in _FORBIDDEN_ALLOW_TOOLS or tool.startswith("--")
        for tool in runner.allow_tools
    ):
        raise ConfigurationError("runner.allow_tools contains a forbidden permission")
    if any(
        not tool or any(character in tool for character in "\0\r\n")
        for tool in runner.allow_tools + runner.deny_tools
    ):
        raise ConfigurationError("tool permissions must be non-empty single-line values")
    if any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
        for name in runner.secret_env_vars
    ):
        raise ConfigurationError("runner.secret_env_vars contains an invalid name")


def _validate_run_inputs(factory_root, config, role, profile, task_file, cwd):
    _validate_profile(role, profile, config)
    _validate_runner_config(config)

    try:
        worktrees_root = _storage_path(factory_root, config.worktrees_dir)
        cwd_path = Path(cwd)
        task_file_path = Path(task_file)
        if not cwd_path.is_absolute() or not task_file_path.is_absolute():
            raise ConfigurationError("task_file and cwd must be absolute paths")
        if task_file_path.is_symlink() or task_file_path.parent.is_symlink():
            raise ConfigurationError("task_file must not be a symlink")
        cwd = cwd_path.resolve(strict=True)
        task_file = task_file_path.resolve(strict=True)
    except (OSError, TypeError, ValueError) as error:
        raise ConfigurationError(f"invalid runner path: {error}") from error

    if not cwd.is_dir():
        raise ConfigurationError(f"cwd is not a directory: {cwd}")
    try:
        worktree_parts = cwd.relative_to(worktrees_root).parts
    except ValueError as error:
        raise ConfigurationError("cwd must be a configured job worktree") from error
    if len(worktree_parts) != 2:
        raise ConfigurationError("cwd must be a configured job worktree")

    job_id, job_dir = _job_location(factory_root, worktree_parts[-1])
    jobs_root = _storage_path(factory_root, config.jobs_dir)
    if job_dir.is_symlink() or job_dir.resolve().parent != jobs_root:
        raise ConfigurationError("job data directory must stay inside jobs_dir")
    with _job_lock(job_dir):
        job, _ = _read_event_log(job_dir / "events.jsonl", job_id)
    if (
        job.worktree_status != "attached"
        or not job.worktree_path
        or Path(job.worktree_path).resolve() != cwd
    ):
        raise ConfigurationError("cwd is not the attached worktree for this job")

    expected_task_file = cwd / ".factory" / "task.md"
    if task_file != expected_task_file:
        raise ConfigurationError("task_file must be <cwd>/.factory/task.md")
    if (cwd / ".factory").is_symlink() or expected_task_file.is_symlink():
        raise ConfigurationError("task_file must not be a symlink")
    try:
        task_mode = expected_task_file.lstat().st_mode
    except OSError as error:
        raise ConfigurationError(f"cannot inspect task_file: {error}") from error
    if not stat.S_ISREG(task_mode):
        raise ConfigurationError("task_file must be a regular file")

    artifacts_root = _storage_path(job_dir, config.runner.artifacts_dir)
    return cwd, job_dir, artifacts_root


def _create_run_dir(artifacts_root):
    for run_number in count(1):
        run_dir = artifacts_root / str(run_number)
        try:
            run_dir.mkdir(parents=True)
            return run_dir
        except FileExistsError:
            continue


def _resolve_executable(executable):
    resolved = shutil.which(executable)
    if resolved is None:
        raise ConfigurationError(f"Copilot executable was not found: {executable}")
    return str(Path(resolved).resolve())


def _prepare_command(
    role,
    profile,
    task_file,
    cwd,
    session_name,
    *,
    factory_root=None,
):
    factory_root = Path(factory_root or Path.cwd()).resolve()
    config = _load_model(factory_root / "config.json", FactoryConfig)
    cwd, _, artifacts_root = _validate_run_inputs(
        factory_root, config, role, profile, task_file, cwd
    )
    if (
        not isinstance(session_name, str)
        or not session_name.strip()
        or any(character in session_name for character in "\0\r\n")
    ):
        raise ConfigurationError("session_name must be a non-empty single-line value")

    executable = _resolve_executable(config.runner.executable)
    run_dir = _create_run_dir(artifacts_root)
    command = [
        executable,
        "-C",
        str(cwd),
        "-p",
        "Follow .factory/task.md",
        "--agent",
        profile.agent,
        "--model",
        profile.model,
        "--reasoning-effort",
        profile.effort,
        "--context",
        profile.context,
    ]
    if profile.max_ai_credits >= 30:
        command.extend(["--max-ai-credits", str(profile.max_ai_credits)])
    command.extend(
        [
            "--name",
            session_name,
            "--no-ask-user",
            "--no-auto-update",
            "--disable-builtin-mcps",
            "--no-custom-instructions",
            "--output-format",
            "json",
            "--usage-output-file",
            str(run_dir / "usage.json"),
            "--share",
            str(run_dir / "session.md"),
            "--allow-tool",
            *config.runner.allow_tools,
            "--deny-tool",
            *config.runner.deny_tools,
        ]
    )
    if config.runner.secret_env_vars:
        command.extend(["--secret-env-vars", *config.runner.secret_env_vars])
    return command, run_dir, config.runner.timeout_seconds, cwd


def build_command(
    role,
    profile,
    task_file,
    cwd,
    session_name,
    *,
    factory_root=None,
):
    command, _, _, _ = _prepare_command(
        role,
        profile,
        task_file,
        cwd,
        session_name,
        factory_root=factory_root,
    )
    return command


def run_agent(role, profile, task_file, cwd, session_name):
    command, run_dir, timeout_seconds, cwd = _prepare_command(
        role, profile, task_file, cwd, session_name
    )
    paths = {
        "run_dir": run_dir,
        "stdout": run_dir / "stdout.jsonl",
        "stderr": run_dir / "stderr.log",
        "usage": run_dir / "usage.json",
        "session": run_dir / "session.md",
    }

    launch_error = None
    try:
        with paths["stdout"].open("wb") as stdout, paths["stderr"].open(
            "wb"
        ) as stderr:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    shell=False,
                )
            except OSError as error:
                launch_error = error
            else:
                try:
                    exit_code = process.wait(timeout=timeout_seconds)
                    timed_out = False
                except subprocess.TimeoutExpired:
                    timed_out = True
                    process.kill()
                    exit_code = process.wait()
    except OSError as error:
        return RunResult(
            outcome="ERROR",
            exit_code=None,
            usage=None,
            session_id=None,
            paths=paths,
            error=f"process or artifact I/O failed: {error}",
        )
    if launch_error is not None:
        return RunResult(
            outcome="ERROR",
            exit_code=None,
            usage=None,
            session_id=None,
            paths=paths,
            error=f"cannot execute Copilot: {launch_error}",
        )

    artifact_errors = []
    try:
        usage = json.loads(paths["usage"].read_text(encoding="utf-8"))
        if not isinstance(usage, dict):
            artifact_errors.append("usage.json must contain a JSON object")
            usage = None
    except FileNotFoundError:
        usage = None
        artifact_errors.append("usage.json was not created")
    except (OSError, json.JSONDecodeError) as error:
        usage = None
        artifact_errors.append(f"cannot read usage.json: {error}")
    if not paths["session"].is_file():
        artifact_errors.append("session.md was not created")

    session_id = usage.get("sessionId") if usage else None
    if not isinstance(session_id, str):
        session_id = None
    if timed_out:
        outcome = "TIMEOUT"
        error = "Copilot process exceeded the configured timeout"
    elif exit_code == 0 and not artifact_errors:
        outcome = "OK"
        error = None
    else:
        outcome = "ERROR"
        errors = artifact_errors
        if exit_code != 0:
            errors = [f"Copilot exited with status {exit_code}", *errors]
        error = "; ".join(errors) or "Copilot process failed"

    return RunResult(
        outcome=outcome,
        exit_code=exit_code,
        usage=usage,
        session_id=session_id,
        paths=paths,
        error=error,
    )
