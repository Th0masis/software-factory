import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .job import ConfigurationError, FactoryError


@dataclass(frozen=True)
class ChangeAssessment:
    verdict: str
    changed_paths: tuple[str, ...]
    scope_violations: tuple[str, ...]
    protected_violations: tuple[str, ...]


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
            if line.strip() and not line.lstrip().startswith("#")
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
