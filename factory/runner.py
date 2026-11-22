import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
_SESSION_LIMIT_MESSAGE = re.compile(
    r"\b(?:rate|usage|session|ai[\s-]+credit)\s+limit\b",
    re.IGNORECASE,
)
_SESSION_LIMIT_TYPES = {
    "ai_credit_limit",
    "rate_limit",
    "session_limit",
    "usage_limit",
}


@dataclass(frozen=True)
class RunResult:
    outcome: Literal["OK", "BUDGET", "TIMEOUT", "ERROR"]
    exit_code: int | None
    usage: "Usage | None"
    session_id: str | None
    paths: dict[str, Path]
    error: str | None


@dataclass(frozen=True)
class Usage:
    total_nano_aiu: Decimal
    effective_model: str
    raw: dict

    @property
    def credits(self):
        return self.total_nano_aiu / Decimal(1_000_000_000)


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    model: str | None
    error: str | None


class UsageError(ValueError):
    pass


def _parse_usage(data):
    if not isinstance(data, dict):
        raise UsageError("usage.json must contain a JSON object")

    total_nano_aiu = data.get("totalNanoAiu")
    if isinstance(total_nano_aiu, bool) or not isinstance(
        total_nano_aiu, (int, float)
    ):
        raise UsageError("usage.totalNanoAiu must be numeric")
    try:
        total_nano_aiu = Decimal(str(total_nano_aiu))
    except InvalidOperation as error:
        raise UsageError("usage.totalNanoAiu must be numeric") from error
    if not total_nano_aiu.is_finite() or total_nano_aiu < 0:
        raise UsageError("usage.totalNanoAiu must be finite and non-negative")

    if "currentModel" in data:
        effective_model = data["currentModel"]
        if (
            not isinstance(effective_model, str)
            or not effective_model
            or effective_model != effective_model.strip()
        ):
            raise UsageError("usage.currentModel must be a non-empty model name")
    else:
        model_metrics = data.get("modelMetrics")
        if not isinstance(model_metrics, dict) or len(model_metrics) != 1:
            raise UsageError("usage must identify exactly one effective model")
        effective_model = next(iter(model_metrics))
        if not isinstance(effective_model, str) or not effective_model.strip():
            raise UsageError("usage.modelMetrics contains an invalid model name")

    return Usage(total_nano_aiu, effective_model, data)


def _read_jsonl(path):
    records = []
    errors = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return records, [f"cannot read stdout.jsonl: {error}"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            errors.append(f"invalid stdout.jsonl line {line_number}: {error}")
            continue
        if not isinstance(record, dict):
            errors.append(f"stdout.jsonl line {line_number} must be a JSON object")
            continue
        records.append(record)
    return records, errors


def _has_session_limit(records):
    for record in records:
        event_type = record.get("type")
        if not isinstance(event_type, str):
            continue
        data = record.get("data")
        if not isinstance(data, dict):
            data = record
        if event_type in {"session.budget_exhausted", "session.limit"}:
            return True
        if event_type == "session.error":
            error_type = data.get("errorType")
            if isinstance(error_type, str) and error_type.lower() in _SESSION_LIMIT_TYPES:
                return True
        if event_type == "session.info":
            info_type = data.get("infoType")
            message = data.get("message")
            if isinstance(info_type, str) and info_type.lower() in _SESSION_LIMIT_TYPES:
                return True
            if isinstance(message, str) and _SESSION_LIMIT_MESSAGE.search(message):
                return True
    return False


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


def _terminate_process_tree(process):
    if os.name != "nt":
        process.kill()
        return

    taskkill = shutil.which("taskkill")
    if taskkill is None:
        raise OSError("taskkill.exe was not found")
    result = subprocess.run(
        [taskkill, "/F", "/T", "/PID", str(process.pid)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise OSError(detail or f"taskkill exited with status {result.returncode}")


def _resolve_executable(executable):
    resolved = shutil.which(executable)
    if resolved is None:
        raise ConfigurationError(f"Copilot executable was not found: {executable}")
    return str(Path(resolved).resolve())


def _run_preflight_command(command, timeout_seconds, cwd):
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return None, str(error)
    if result.returncode:
        diagnostic = "\n".join(
            output.strip()
            for output in (result.stderr, result.stdout)
            if output.strip()
        )
        return None, diagnostic or f"Copilot exited with status {result.returncode}"
    return result.stdout, None


def preflight(*, factory_root=None):
    factory_root = Path(factory_root or Path.cwd()).resolve()
    config = _load_model(factory_root / "config.json", FactoryConfig)
    try:
        executable = _resolve_executable(config.runner.executable)
    except ConfigurationError as error:
        return PreflightResult(False, None, str(error))

    timeout_seconds = config.runner.timeout_seconds
    models = sorted({profile.model for profile in config.profiles.values()})
    if not models:
        return PreflightResult(False, None, "no configured runner models")
    with tempfile.TemporaryDirectory(prefix="factory-preflight-") as probe_cwd:
        help_output, error = _run_preflight_command(
            [executable, "--help"], timeout_seconds, probe_cwd
        )
        if error:
            return PreflightResult(
                False, None, f"Copilot CLI validation failed: {error}"
            )
        required_flags = (
            "--model",
            "--prompt",
            "--no-ask-user",
            "--no-auto-update",
            "--disable-builtin-mcps",
            "--no-custom-instructions",
            "--available-tools",
            "--allow-tool",
        )
        missing_flags = [flag for flag in required_flags if flag not in help_output]
        if missing_flags:
            return PreflightResult(
                False,
                None,
                f"Copilot CLI is missing required flags: {', '.join(missing_flags)}",
            )

        for model in models:
            _, error = _run_preflight_command(
                [
                    executable,
                    "-p",
                    "Reply with exactly READY. Do not use tools.",
                    "--model",
                    model,
                    "--no-ask-user",
                    "--no-auto-update",
                    "--disable-builtin-mcps",
                    "--no-custom-instructions",
                    "--output-format",
                    "text",
                    "--available-tools",
                    "read",
                    "--allow-tool",
                    "read",
                    "--deny-tool",
                    _REQUIRED_DENY_TOOL,
                ],
                timeout_seconds,
                probe_cwd,
            )
            if error:
                return PreflightResult(False, model, error)
    return PreflightResult(True, None, None)


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
    termination_error = None
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
                    try:
                        _terminate_process_tree(process)
                    except (OSError, subprocess.SubprocessError) as error:
                        termination_error = f"cannot terminate process tree: {error}"
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
        usage = _parse_usage(json.loads(paths["usage"].read_text(encoding="utf-8")))
    except FileNotFoundError:
        usage = None
        artifact_errors.append("usage.json was not created")
    except (OSError, json.JSONDecodeError, UsageError) as error:
        usage = None
        artifact_errors.append(f"cannot read usage.json: {error}")
    if not paths["session"].is_file():
        artifact_errors.append("session.md was not created")
    jsonl_records, jsonl_errors = _read_jsonl(paths["stdout"])
    artifact_errors.extend(jsonl_errors)
    try:
        stderr_text = paths["stderr"].read_text(
            encoding="utf-8", errors="replace"
        ).strip()
    except OSError as error:
        stderr_text = ""
        artifact_errors.append(f"cannot read stderr.log: {error}")

    session_id = usage.raw.get("sessionId") if usage else None
    if not isinstance(session_id, str):
        session_id = None
    budget_reached = _has_session_limit(jsonl_records) or (
        usage is not None
        and usage.total_nano_aiu
        >= Decimal(profile.max_ai_credits) * Decimal(1_000_000_000)
    )
    if termination_error:
        outcome = "ERROR"
        error = termination_error
    elif timed_out:
        outcome = "TIMEOUT"
        error = "Copilot process exceeded the configured timeout"
    elif budget_reached:
        outcome = "BUDGET"
        error = "Copilot session budget limit reached"
    elif exit_code == 0 and not artifact_errors:
        if usage.effective_model == profile.model:
            outcome = "OK"
            error = None
        else:
            outcome = "ERROR"
            error = (
                f"effective model {usage.effective_model!r} does not match "
                f"configured model {profile.model!r}"
            )
    else:
        outcome = "ERROR"
        errors = artifact_errors
        if exit_code != 0:
            errors = [f"Copilot exited with status {exit_code}", *errors]
        if stderr_text:
            errors.append(f"see stderr log: {paths['stderr']}")
        error = "; ".join(errors) or "Copilot process failed"

    return RunResult(
        outcome=outcome,
        exit_code=exit_code,
        usage=usage,
        session_id=session_id,
        paths=paths,
        error=error,
    )
