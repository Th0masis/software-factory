import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .job import (
    ConfigurationError,
    FactoryConfig,
    FactoryError,
    _job_location,
    _load_model,
    _read_event_log,
    _storage_path,
    _validate_job_project,
    _worktree_path,
)
from .runner import _close_process_tree, _spawn_process, _terminate_process_tree


@dataclass(frozen=True)
class ChangeAssessment:
    verdict: str
    changed_paths: tuple[str, ...]
    scope_violations: tuple[str, ...]
    protected_violations: tuple[str, ...]


@dataclass(frozen=True)
class ChecksResult:
    verdict: Literal["PASS", "FAIL"]
    report: dict[str, object] | None
    checks: tuple[dict[str, object], ...]
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    error: str | None


@dataclass(frozen=True)
class GateResult:
    verdict: Literal["PASS", "FAIL"]
    report_path: Path
    report: dict[str, object]


def _normalize_patterns(patterns, name, *, required):
    if not isinstance(patterns, (list, tuple)) or (required and not patterns):
        raise ConfigurationError(f"{name} must be a non-empty list of path patterns")

    normalized = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern or any(
            character in pattern for character in "\0\r\n"
        ):
            raise ConfigurationError(f"{name} contains an invalid path pattern")
        pattern = pattern.replace("\\", "/")
        components = pattern.split("/")
        if (
            pattern.startswith("/")
            or (len(pattern) > 1 and pattern[1] == ":")
            or any(component in {"", ".", ".."} for component in components)
            or any("**" in component and component != "**" for component in components)
            or "[" in pattern
            or "]" in pattern
        ):
            raise ConfigurationError(f"{name} contains an unsafe path pattern")
        normalized.append(pattern)
    return tuple(normalized)


def _matches_path(pattern, path):
    expression = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*" and pattern[index:index + 2] == "**":
            if pattern[index + 2:index + 3] == "/":
                expression.append("(?:.*/)?")
                index += 3
            else:
                expression.append(".*")
                index += 2
        elif character == "*":
            expression.append("[^/]*")
            index += 1
        elif character == "?":
            expression.append("[^/]")
            index += 1
        else:
            expression.append(re.escape(character))
            index += 1
    return re.fullmatch("".join(expression), path) is not None


def _git(worktree, *arguments):
    result = subprocess.run(
        ["git", *arguments],
        cwd=worktree,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise FactoryError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout


def _validate_base_sha(worktree, base_sha):
    if not isinstance(base_sha, str) or re.fullmatch(
        r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base_sha, re.IGNORECASE
    ) is None:
        raise ConfigurationError("base_sha must be a full commit SHA")
    try:
        resolved_sha = _git(
            worktree, "rev-parse", "--verify", f"{base_sha}^{{commit}}"
        ).decode("ascii").strip()
    except (FactoryError, UnicodeDecodeError) as error:
        raise ConfigurationError("base_sha must identify a commit") from error
    if resolved_sha.lower() != base_sha.lower():
        raise ConfigurationError("base_sha must identify a full commit SHA")

    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, "HEAD"],
        cwd=worktree,
        capture_output=True,
        check=False,
    )
    if result.returncode == 1:
        raise ConfigurationError("base_sha must be an ancestor of worktree HEAD")
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise FactoryError(f"git merge-base --is-ancestor failed: {detail}")


def assess_changes(worktree, base_sha, scope_manifest):
    allowed_patterns = _normalize_patterns(
        scope_manifest, "scope manifest", required=True
    )
    try:
        worktree = Path(worktree).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ConfigurationError(f"invalid worktree path: {error}") from error
    if not worktree.is_dir():
        raise ConfigurationError(f"worktree is not a directory: {worktree}")

    root = _git(worktree, "rev-parse", "--show-toplevel").decode("utf-8").strip()
    if Path(root).resolve() != worktree:
        raise ConfigurationError("worktree must be a Git repository root")
    _validate_base_sha(worktree, base_sha)

    tracked = _git(
        worktree,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        base_sha,
        "--",
    )
    untracked = _git(
        worktree,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    tracked_paths = {path.decode("utf-8") for path in tracked.split(b"\0") if path}
    untracked_paths = {
        path.decode("utf-8") for path in untracked.split(b"\0") if path
    }
    untracked_paths.discard(".factory/task.md")
    changed_paths = tracked_paths | untracked_paths
    try:
        protected_data = _git(
            worktree,
            "show",
            f"{base_sha}:.factory/protected.txt",
        )
        protected_text = protected_data.decode("utf-8")
    except FactoryError as error:
        raise ConfigurationError(
            "base commit must contain .factory/protected.txt"
        ) from error
    except UnicodeDecodeError as error:
        raise ConfigurationError(
            ".factory/protected.txt must be valid UTF-8"
        ) from error

    protected_patterns = _normalize_patterns(
        [
            line.strip()
            for line in protected_text.splitlines()
            if line.strip() and not line.startswith("#")
        ],
        ".factory/protected.txt",
        required=False,
    )
    protected_patterns = protected_patterns + (
        ".factory/checks.py",
        ".factory/project.json",
        ".factory/protected.txt",
        ".factory/task.md",
    )
    scope_violations = tuple(
        sorted(
            path
            for path in changed_paths
            if not any(_matches_path(pattern, path) for pattern in allowed_patterns)
        )
    )
    protected_violations = tuple(
        sorted(
            path
            for path in changed_paths
            if any(_matches_path(pattern, path) for pattern in protected_patterns)
        )
    )
    changed_paths = tuple(sorted(changed_paths))
    return ChangeAssessment(
        verdict="FAIL" if scope_violations or protected_violations else "PASS",
        changed_paths=changed_paths,
        scope_violations=scope_violations,
        protected_violations=protected_violations,
    )


def _validate_checks_script(worktree, base_sha):
    script = Path(worktree) / ".factory" / "checks.py"
    try:
        entries = _git(
            worktree,
            "ls-tree",
            "-z",
            base_sha,
            "--",
            ".factory/checks.py",
        ).split(b"\0")
    except FactoryError as error:
        raise ConfigurationError("cannot inspect checks script in base commit") from error
    if len(entries) != 2 or not entries[0]:
        raise ConfigurationError(".factory/checks.py must be tracked at base_sha")
    metadata, separator, path = entries[0].partition(b"\t")
    fields = metadata.split()
    if (
        not separator
        or path != b".factory/checks.py"
        or len(fields) != 3
        or fields[0] not in {b"100644", b"100755"}
        or fields[1] != b"blob"
    ):
        raise ConfigurationError(".factory/checks.py must be a regular tracked file")
    try:
        if not stat.S_ISDIR((script.parent).lstat().st_mode):
            raise ConfigurationError(".factory must be a regular directory")
        if not stat.S_ISREG(script.lstat().st_mode):
            raise ConfigurationError(".factory/checks.py must be a regular file")
        current_data = script.read_bytes()
        base_data = _git(worktree, "show", f"{base_sha}:.factory/checks.py")
    except OSError as error:
        raise ConfigurationError(f"cannot read .factory/checks.py: {error}") from error
    if current_data != base_data:
        raise ConfigurationError(".factory/checks.py differs from base_sha")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def _parse_checks_report(stdout):
    try:
        report = json.loads(
            stdout,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"checks stdout is not valid JSON: {error}") from error
    if (
        not isinstance(report, dict)
        or type(report.get("schema_version")) is not int
        or report["schema_version"] != 1
    ):
        raise ValueError("checks report must use schema_version 1")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("checks report must contain at least one check")

    names = set()
    validated = []
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("each check must be an object")
        name = check.get("name")
        status = check.get("status")
        exit_code = check.get("exit_code")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("check name must be a non-empty string")
        if name in names:
            raise ValueError(f"duplicate check name: {name}")
        if not isinstance(status, str) or status not in {"PASS", "FAIL"}:
            raise ValueError(f"check {name!r} has an invalid status")
        if type(exit_code) is not int:
            raise ValueError(f"check {name!r} exit_code must be an integer")
        names.add(name)
        validated.append(
            {"name": name, "status": status, "exit_code": exit_code}
        )
    return report, tuple(validated)


def _validate_secret_env_vars(config):
    secret_names = config.runner.secret_env_vars
    if any(
        not isinstance(name, str)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
        for name in secret_names
    ):
        raise ConfigurationError("runner.secret_env_vars contains an invalid name")
    return secret_names


def _parse_baseline_red_report(stdout, baseline_sha, manifest_sha256, test_ids):
    try:
        report = json.loads(
            stdout,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"baseline-red stdout is not valid JSON: {error}") from error
    if (
        not isinstance(report, dict)
        or type(report.get("schema_version")) is not int
        or report["schema_version"] != 1
        or report.get("work_branch_start_sha") != baseline_sha
        or report.get("manifest_sha256") != manifest_sha256
    ):
        raise ValueError("baseline-red report metadata does not match registered inputs")
    results = report.get("tests")
    if not isinstance(results, list) or len(results) != len(test_ids):
        raise ValueError("baseline-red report must contain each registered test once")
    result_ids = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("each baseline-red result must be an object")
        test_id = result.get("id")
        outcome = result.get("outcome")
        if (
            not isinstance(test_id, str)
            or not test_id
            or not isinstance(outcome, str)
            or outcome not in {"ASSERTION_FAILURE", "PASS", "ERROR"}
        ):
            raise ValueError("baseline-red result has an invalid ID or outcome")
        result_ids.append(test_id)
    if len(set(result_ids)) != len(result_ids) or set(result_ids) != set(test_ids):
        raise ValueError("baseline-red report test IDs do not match the manifest")
    return report


def run_checks(
    worktree,
    base_sha,
    mode,
    config,
    *,
    manifest_path=None,
    manifest_sha256=None,
    test_ids=None,
):
    if not isinstance(mode, str) or mode not in {"fast", "full", "red"}:
        raise ConfigurationError("mode must be 'fast', 'full', or 'red'")
    if mode == "red":
        if (
            not isinstance(manifest_path, Path)
            or not manifest_path.is_absolute()
            or manifest_path.is_symlink()
            or not isinstance(manifest_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
            or not isinstance(test_ids, (list, tuple))
            or not test_ids
            or any(not isinstance(test_id, str) or not test_id for test_id in test_ids)
            or len(set(test_ids)) != len(test_ids)
        ):
            raise ConfigurationError("invalid baseline-red check inputs")
        try:
            if (
                not manifest_path.is_file()
                or hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                != manifest_sha256
            ):
                raise ConfigurationError("registered test manifest is missing or changed")
        except OSError as error:
            raise ConfigurationError(
                f"cannot read registered test manifest: {error}"
            ) from error
    if not isinstance(config, FactoryConfig):
        raise ConfigurationError("config must be a validated factory config")
    secret_names = _validate_secret_env_vars(config)
    try:
        worktree = Path(worktree).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ConfigurationError(f"invalid worktree path: {error}") from error
    if not worktree.is_dir():
        raise ConfigurationError(f"worktree is not a directory: {worktree}")
    try:
        root = _git(worktree, "rev-parse", "--show-toplevel").decode("utf-8").strip()
    except FactoryError as error:
        raise ConfigurationError("worktree must be a Git repository root") from error
    if Path(root).resolve() != worktree:
        raise ConfigurationError("worktree must be a Git repository root")
    _validate_base_sha(worktree, base_sha)
    try:
        _validate_checks_script(worktree, base_sha)
    except ConfigurationError as error:
        return ChecksResult(
            verdict="FAIL",
            report=None,
            checks=(),
            exit_code=None,
            stdout=b"",
            stderr=b"",
            error=str(error),
        )

    environment = os.environ.copy()
    for name in secret_names:
        environment.pop(name, None)

    stdout = tempfile.TemporaryFile()
    stderr = tempfile.TemporaryFile()
    process = None
    error = None
    exit_code = None
    try:
        try:
            arguments = [sys.executable, ".factory/checks.py"]
            if mode == "red":
                arguments.extend(
                    [
                        "--red",
                        "--manifest",
                        str(manifest_path),
                        "--baseline-sha",
                        base_sha,
                    ]
                )
            else:
                arguments.append(f"--{mode}")
            process = _spawn_process(
                arguments,
                cwd=worktree,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                env=environment,
            )
        except OSError as launch_error:
            error = f"cannot start checks script: {launch_error}"
        if process is not None:
            try:
                exit_code = process.wait(timeout=config.gate.timeout_seconds)
            except subprocess.TimeoutExpired:
                error = f"checks script timed out after {config.gate.timeout_seconds}s"
                try:
                    _terminate_process_tree(process)
                except (OSError, subprocess.SubprocessError) as termination_error:
                    error += f"; cannot terminate process tree: {termination_error}"
                    _close_process_tree(process)
                    process.kill()
                exit_code = process.wait()
            finally:
                _close_process_tree(process)
        stdout.seek(0)
        stderr.seek(0)
        stdout_data = stdout.read()
        stderr_data = stderr.read()
    finally:
        stdout.close()
        stderr.close()

    checks = ()
    report = None
    if error is None:
        try:
            if mode == "red":
                if manifest_path.is_symlink() or (
                    hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                    != manifest_sha256
                ):
                    raise ValueError("registered test manifest changed during baseline-red check")
                report = _parse_baseline_red_report(
                    stdout_data, base_sha, manifest_sha256, test_ids
                )
            else:
                report, checks = _parse_checks_report(stdout_data)
        except ValueError as report_error:
            error = str(report_error)
        except OSError as report_error:
            error = f"cannot read registered test manifest: {report_error}"
    if mode == "red":
        all_checks_passed = (
            isinstance(report, dict)
            and all(
                result["outcome"] == "ASSERTION_FAILURE"
                for result in report["tests"]
            )
        )
        passed = error is None and exit_code == 0 and all_checks_passed
        if error is None and exit_code:
            error = f"baseline-red check exited with status {exit_code}"
        elif error is None and not all_checks_passed:
            error = "baseline-red check did not report assertion failures for every test"
        return ChecksResult(
            verdict="PASS" if passed else "FAIL",
            report=report,
            checks=checks,
            exit_code=exit_code,
            stdout=stdout_data,
            stderr=stderr_data,
            error=error,
        )
    all_checks_passed = bool(checks) and all(
        check["status"] == "PASS" and check["exit_code"] == 0
        for check in checks
    )
    passed = error is None and exit_code == 0 and all_checks_passed
    if error is None and exit_code:
        error = f"checks script exited with status {exit_code}"
    return ChecksResult(
        verdict="PASS" if passed else "FAIL",
        report=report,
        checks=checks,
        exit_code=exit_code,
        stdout=stdout_data,
        stderr=stderr_data,
        error=error,
    )


def _read_scope_manifest(job_dir, worktree):
    scope_path = job_dir / "scope.json"
    try:
        if not stat.S_ISREG(scope_path.lstat().st_mode):
            raise ConfigurationError("scope.json must be a regular file")
        scope_path = scope_path.resolve(strict=True)
    except OSError as error:
        raise ConfigurationError(f"cannot read scope.json: {error}") from error
    if scope_path.parent != job_dir.resolve():
        raise ConfigurationError("scope.json must be directly under the job directory")
    if scope_path.is_relative_to(worktree):
        raise ConfigurationError("scope.json must be outside the target worktree")
    try:
        manifest = json.loads(
            scope_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise ConfigurationError(f"invalid scope.json: {error}") from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "allowed_paths"}
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or not isinstance(manifest["allowed_paths"], list)
    ):
        raise ConfigurationError("scope.json must contain schema_version 1 and allowed_paths")
    return _normalize_patterns(
        manifest["allowed_paths"], "scope manifest", required=True
    )


def _gate_job_context(factory_root, job_id):
    try:
        factory_root = Path(factory_root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ConfigurationError(f"invalid factory root: {error}") from error
    if not factory_root.is_dir():
        raise ConfigurationError(f"factory root is not a directory: {factory_root}")
    config = _load_model(factory_root / "config.json", FactoryConfig)
    try:
        job_id, job_dir = _job_location(factory_root, job_id)
    except FactoryError as error:
        if str(error).startswith("job not found:"):
            raise ConfigurationError(str(error)) from error
        raise
    jobs_root = _storage_path(factory_root, config.jobs_dir)
    if job_dir.is_symlink() or job_dir.resolve().parent != jobs_root:
        raise ConfigurationError("job directory must be a regular factory-owned directory")
    job, _ = _read_event_log(job_dir / "events.jsonl", job_id)
    _validate_job_project(job)
    if job.worktree_status != "attached" or not job.worktree_path:
        raise ConfigurationError("job must have an attached worktree")
    expected_worktree = _worktree_path(factory_root, job)
    stored_worktree = Path(job.worktree_path)
    if not stored_worktree.is_absolute() or stored_worktree.is_symlink():
        raise ConfigurationError("job worktree path must be an absolute non-symlink path")
    try:
        worktree = stored_worktree.resolve(strict=True)
        expected_worktree = expected_worktree.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ConfigurationError(f"invalid job worktree: {error}") from error
    if worktree != expected_worktree:
        raise ConfigurationError("job worktree path does not match factory configuration")
    return config, job_id, job_dir, job, worktree


def _create_gate_attempt(job_dir):
    gate_dir = job_dir / "gate"
    if gate_dir.is_symlink():
        raise ConfigurationError("Gate artifact directory must not be a symlink")
    try:
        gate_dir.mkdir(exist_ok=True)
        if not gate_dir.is_dir():
            raise ConfigurationError("Gate artifact path is not a directory")
        while True:
            attempt_dir = gate_dir / str(uuid4())
            try:
                attempt_dir.mkdir()
                return attempt_dir
            except FileExistsError:
                continue
    except OSError as error:
        raise FactoryError(f"cannot create Gate attempt directory: {error}") from error


def _write_gate_artifacts(attempt_dir, report, checks_result):
    stdout = checks_result.stdout if checks_result is not None else b""
    stderr = checks_result.stderr if checks_result is not None else b""
    try:
        (attempt_dir / "checks.stdout.txt").write_bytes(stdout)
        (attempt_dir / "checks.stderr.txt").write_bytes(stderr)
        report_path = attempt_dir / "gate-report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise FactoryError(f"cannot write Gate artifacts: {error}") from error
    return report_path


def evaluate_gate(factory_root, job_id, base_sha, mode):
    if not isinstance(mode, str) or mode not in {"fast", "full"}:
        raise ConfigurationError("mode must be 'fast' or 'full'")
    config, job_id, job_dir, job, worktree = _gate_job_context(
        factory_root, job_id
    )
    _validate_secret_env_vars(config)
    scope_manifest = _read_scope_manifest(job_dir, worktree)
    assessment = assess_changes(worktree, base_sha, scope_manifest)

    attempt_dir = _create_gate_attempt(job_dir)
    checks_result = None
    report = {
        "schema_version": 1,
        "job_id": job_id,
        "base_sha": base_sha,
        "mode": mode,
        "checks": [],
        "checks_report": None,
        "checks_error": None,
        "checks_skipped": None,
        "scope": {
            "changed_paths": list(assessment.changed_paths),
            "violations": list(assessment.scope_violations),
        },
        "protected_paths": {
            "violations": list(assessment.protected_violations),
        },
        "risk": {"status": "DEFERRED"},
        "verdict": "FAIL",
    }
    if assessment.verdict == "FAIL":
        reasons = []
        if assessment.scope_violations:
            reasons.append("scope violations")
        if assessment.protected_violations:
            reasons.append("protected-path violations")
        report["checks_skipped"] = ", ".join(reasons)
    else:
        checks_result = run_checks(worktree, base_sha, mode, config)
        report["checks"] = list(checks_result.checks)
        report["checks_report"] = checks_result.report
        report["checks_error"] = checks_result.error
        if checks_result.verdict == "PASS":
            report["verdict"] = "PASS"

    report_path = _write_gate_artifacts(attempt_dir, report, checks_result)
    return GateResult(report["verdict"], report_path, report)
