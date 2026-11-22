import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from factory import job as job_service
from factory.job import ConfigurationError, RunnerProfile
from factory.runner import build_command


class RunnerCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        temp_root = Path(self.temp_dir.name)
        from test_job_create import JobCreateCliTests

        (
            self.factory_root,
            _,
            _,
            self.job_id,
            self.branch,
            _,
        ) = JobCreateCliTests()._prepare_approved_worktree_job(temp_root)
        fake_executable = self.factory_root / "copilot.exe"
        fake_executable.write_text("", encoding="utf-8")
        self.config = {
            "jobs_dir": "jobs",
            "worktrees_dir": "worktrees",
            "runner": {
                "executable": str(fake_executable),
                "timeout_seconds": 1800,
                "artifacts_dir": "runs",
                "allow_tools": ["read", "write", "shell(python:*)", "shell(git:*)"],
                "deny_tools": ["shell(git push)"],
                "secret_env_vars": ["COPILOT_GITHUB_TOKEN", "GH_TOKEN"],
            },
            "profiles": {
                "build-standard": {
                    "agent": "builder",
                    "model": "claude-sonnet-5.5",
                    "effort": "high",
                    "context": "default",
                    "max_ai_credits": 400,
                }
            },
        }
        self._write_config()
        job_service.attach_worktree(self.factory_root, self.job_id, self.branch)
        job = job_service.read_job(self.factory_root, self.job_id)["job"]
        self.cwd = Path(job["worktree_path"])
        self.task_file = self.cwd / ".factory" / "task.md"
        self.task_file.parent.mkdir(exist_ok=True)
        self.task_file.write_text("Repair the widget.", encoding="utf-8")
        self.profile = RunnerProfile.model_validate(
            self.config["profiles"]["build-standard"]
        )

    def _write_config(self):
        (self.factory_root / "config.json").write_text(
            json.dumps(self.config), encoding="utf-8"
        )

    def _build_command(
        self,
        role="builder",
        profile=None,
        task_file=None,
        cwd=None,
        session_name="repair-widget",
    ):
        return build_command(
            role,
            profile or self.profile,
            task_file or self.task_file,
            cwd or self.cwd,
            session_name,
            factory_root=self.factory_root,
        )

    def test_build_command_pins_profile_permissions_and_artifact_paths(self):
        command = self._build_command()

        run_dir = self.factory_root / "jobs" / self.job_id / "runs" / "1"
        self.assertEqual(
            command,
            [
                str((self.factory_root / "copilot.exe").resolve()),
                "-C",
                str(self.cwd),
                "-p",
                "Follow .factory/task.md",
                "--agent",
                "builder",
                "--model",
                "claude-sonnet-5.5",
                "--reasoning-effort",
                "high",
                "--context",
                "default",
                "--max-ai-credits",
                "400",
                "--name",
                "repair-widget",
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
                "read",
                "write",
                "shell(python:*)",
                "shell(git:*)",
                "--deny-tool",
                "shell(git push)",
                "--secret-env-vars",
                "COPILOT_GITHUB_TOKEN",
                "GH_TOKEN",
            ],
        )
        self.assertTrue(run_dir.is_dir())

    def test_build_command_rejects_task_file_symlink_alias(self):
        task_alias = self.task_file.parent / "task-alias.md"
        try:
            task_alias.symlink_to(self.task_file.name)
        except OSError as error:
            self.skipTest(f"file symlinks are unavailable: {error}")

        with self.assertRaises(ConfigurationError):
            self._build_command(task_file=task_alias)

    def test_build_command_rejects_filesystem_reported_task_symlink(self):
        task_alias = self.task_file.parent / "task-alias.md"
        with patch.object(
            Path,
            "is_symlink",
            autospec=True,
            side_effect=lambda path: path == task_alias,
        ):
            with self.assertRaises(ConfigurationError):
                self._build_command(task_file=task_alias)

    def test_build_command_omits_credit_cap_below_cli_minimum(self):
        self.config["profiles"]["build-standard"]["max_ai_credits"] = 29
        self._write_config()
        profile = RunnerProfile.model_validate(
            self.config["profiles"]["build-standard"]
        )

        command = self._build_command(profile=profile)

        self.assertNotIn("--max-ai-credits", command)

    def test_build_command_rejects_role_not_in_configured_profile(self):
        with self.assertRaises(ConfigurationError):
            self._build_command(role="reviewer")
        self.assertFalse((self.factory_root / "jobs" / self.job_id / "runs").exists())

    def test_build_command_rejects_unconfigured_profile(self):
        profile_data = self.config["profiles"]["build-standard"].copy()
        profile_data["model"] = "unconfigured-model"
        profile = RunnerProfile.model_validate(profile_data)

        with self.assertRaises(ConfigurationError):
            self._build_command(profile=profile)
        self.assertFalse((self.factory_root / "jobs" / self.job_id / "runs").exists())

    def test_build_command_rejects_permission_expansion(self):
        self.config["runner"]["allow_tools"].append("--allow-all")
        self._write_config()

        with self.assertRaises(ConfigurationError):
            self._build_command()
        self.assertFalse((self.factory_root / "jobs" / self.job_id / "runs").exists())

    def test_build_command_requires_git_push_deny_rule(self):
        self.config["runner"]["deny_tools"] = []
        self._write_config()

        with self.assertRaises(ConfigurationError):
            self._build_command()

    def test_build_command_rejects_task_file_outside_fixed_path(self):
        task_file = self.factory_root / "task.md"
        task_file.write_text("not the worktree task", encoding="utf-8")

        with self.assertRaises(ConfigurationError):
            self._build_command(task_file=task_file)

    def test_build_command_rejects_cwd_outside_worktrees_root(self):
        cwd = self.factory_root / "not-a-worktree"
        cwd.mkdir()

        with self.assertRaises(ConfigurationError):
            self._build_command(cwd=cwd)

    def test_build_command_allocates_distinct_run_directories(self):
        first = self._build_command()
        second = self._build_command()

        self.assertEqual(
            Path(first[first.index("--usage-output-file") + 1]).parent.name, "1"
        )
        self.assertEqual(
            Path(second[second.index("--usage-output-file") + 1]).parent.name, "2"
        )
