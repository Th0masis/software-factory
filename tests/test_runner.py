import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from factory import job as job_service
from factory import runner as runner_module
from factory.job import ConfigurationError, RunnerProfile
from factory.runner import build_command, preflight, run_agent


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
        preflight_patch = patch(
            "factory.runner.preflight",
            return_value=runner_module.PreflightResult(True, None, None),
        )
        preflight_patch.start()
        self.addCleanup(preflight_patch.stop)

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

    def _run_fake_process(
        self,
        usage_data,
        stdout=None,
        stderr=b"",
        exit_code=0,
        create_session=True,
        process=None,
    ):
        output = stdout or b'{"type":"assistant.message","content":"done"}\n'
        error_output = stderr

        def fake_process(command, cwd, stdin, stdout, stderr, shell):
            stdout.write(output)
            stderr.write(error_output)
            if usage_data is not None:
                Path(command[command.index("--usage-output-file") + 1]).write_text(
                    json.dumps(usage_data), encoding="utf-8"
                )
            if create_session:
                Path(command[command.index("--share") + 1]).write_text(
                    "# Session output\n", encoding="utf-8"
                )
            return process or _CompletedProcess(returncode=exit_code)

        with patch("factory.runner.Path.cwd", return_value=self.factory_root):
            with patch(
                "factory.runner._spawn_process", side_effect=fake_process
            ):
                return run_agent(
                    "builder",
                    self.profile,
                    self.task_file,
                    self.cwd,
                    "repair-widget",
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

    def test_build_command_rejects_non_path_task_file(self):
        with self.assertRaises(ConfigurationError):
            self._build_command(task_file=17)

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

    def test_run_agent_returns_success_with_persisted_process_artifacts(self):
        def fake_process(command, cwd, stdin, stdout, stderr, shell):
            self.assertFalse(shell)
            stdout.write(b'{"type":"assistant.message","content":"done"}\n')
            usage_path = Path(command[command.index("--usage-output-file") + 1])
            usage_path.write_text(
                json.dumps(
                    {
                        "totalNanoAiu": 10,
                        "currentModel": "claude-sonnet-5.5",
                        "sessionId": "session-123",
                    }
                ),
                encoding="utf-8",
            )
            session_path = Path(command[command.index("--share") + 1])
            session_path.write_text("# Session output\n", encoding="utf-8")
            return _CompletedProcess()

        with patch("factory.runner.Path.cwd", return_value=self.factory_root):
            with patch("factory.runner._spawn_process", side_effect=fake_process):
                result = run_agent(
                    "builder",
                    self.profile,
                    self.task_file,
                    self.cwd,
                    "repair-widget",
                )

        self.assertEqual(result.outcome, "OK")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.usage.total_nano_aiu, Decimal("10"))
        self.assertEqual(result.session_id, "session-123")
        self.assertEqual(
            result.paths["stdout"].read_text(encoding="utf-8"),
            '{"type":"assistant.message","content":"done"}\n',
        )
        self.assertTrue(result.paths["stderr"].exists())
        self.assertEqual(
            result.paths["session"].read_text(encoding="utf-8"),
            "# Session output\n",
        )

    def test_run_agent_preserves_partial_artifacts_on_nonzero_exit(self):
        def fake_process(command, cwd, stdin, stdout, stderr, shell):
            stdout.write(b'{"type":"assistant.message","content":"partial"}\n')
            stderr.write(b"process failed\n")
            return _CompletedProcess(returncode=7)

        with patch("factory.runner.Path.cwd", return_value=self.factory_root):
            with patch("factory.runner._spawn_process", side_effect=fake_process):
                result = run_agent(
                    "builder",
                    self.profile,
                    self.task_file,
                    self.cwd,
                    "repair-widget",
                )

        self.assertEqual(result.outcome, "ERROR")
        self.assertEqual(result.exit_code, 7)
        self.assertIn("status 7", result.error)
        self.assertIn("partial", result.paths["stdout"].read_text(encoding="utf-8"))
        self.assertEqual(
            result.paths["stderr"].read_text(encoding="utf-8"), "process failed\n"
        )
        self.assertFalse(result.paths["usage"].exists())
        self.assertFalse(result.paths["session"].exists())

    def test_run_agent_reports_process_launch_failure(self):
        with patch("factory.runner.Path.cwd", return_value=self.factory_root):
            with patch(
                "factory.runner._spawn_process",
                side_effect=OSError("executable unavailable"),
            ):
                result = run_agent(
                    "builder",
                    self.profile,
                    self.task_file,
                    self.cwd,
                    "repair-widget",
                )

        self.assertEqual(result.outcome, "ERROR")
        self.assertIsNone(result.exit_code)
        self.assertIn("executable unavailable", result.error)
        self.assertTrue(result.paths["stdout"].is_file())
        self.assertTrue(result.paths["stderr"].is_file())

    def test_run_agent_fails_when_successful_process_omits_required_artifacts(self):
        def fake_process(command, cwd, stdin, stdout, stderr, shell):
            stdout.write(b'{"type":"assistant.message","content":"done"}\n')
            return _CompletedProcess()

        with patch("factory.runner.Path.cwd", return_value=self.factory_root):
            with patch("factory.runner._spawn_process", side_effect=fake_process):
                result = run_agent(
                    "builder",
                    self.profile,
                    self.task_file,
                    self.cwd,
                    "repair-widget",
                )

        self.assertEqual(result.outcome, "ERROR")
        self.assertIn("usage.json was not created", result.error)
        self.assertIn("session.md was not created", result.error)

    def test_run_agent_parses_usage_and_preserves_unknown_fields(self):
        usage_data = {
            "totalNanoAiu": 1_500_000_000,
            "currentModel": "claude-sonnet-5.5",
            "sessionId": "session-123",
            "futureField": {"preserved": True},
        }

        def fake_process(command, cwd, stdin, stdout, stderr, shell):
            stdout.write(b'{"type":"assistant.message","content":"done"}\n')
            Path(command[command.index("--usage-output-file") + 1]).write_text(
                json.dumps(usage_data), encoding="utf-8"
            )
            Path(command[command.index("--share") + 1]).write_text(
                "# Session output\n", encoding="utf-8"
            )
            return _CompletedProcess()

        with patch("factory.runner.Path.cwd", return_value=self.factory_root):
            with patch("factory.runner._spawn_process", side_effect=fake_process):
                result = run_agent(
                    "builder",
                    self.profile,
                    self.task_file,
                    self.cwd,
                    "repair-widget",
                )

        self.assertEqual(result.usage.total_nano_aiu, Decimal("1500000000"))
        self.assertEqual(result.usage.credits, Decimal("1.5"))
        self.assertEqual(result.usage.raw["futureField"], {"preserved": True})

    def test_run_agent_classifies_usage_at_cap_as_budget(self):
        usage_data = {
            "totalNanoAiu": 400_000_000_000,
            "currentModel": "claude-sonnet-5.5",
        }

        def fake_process(command, cwd, stdin, stdout, stderr, shell):
            Path(command[command.index("--usage-output-file") + 1]).write_text(
                json.dumps(usage_data), encoding="utf-8"
            )
            Path(command[command.index("--share") + 1]).write_text(
                "# Session output\n", encoding="utf-8"
            )
            return _CompletedProcess()

        with patch("factory.runner.Path.cwd", return_value=self.factory_root):
            with patch("factory.runner._spawn_process", side_effect=fake_process):
                result = run_agent(
                    "builder",
                    self.profile,
                    self.task_file,
                    self.cwd,
                    "repair-widget",
                )

        self.assertEqual(result.outcome, "BUDGET")
        self.assertEqual(result.exit_code, 0)

    def test_run_agent_gives_timeout_priority_over_budget(self):
        process = _TimedOutProcess()

        def fake_process(command, cwd, stdin, stdout, stderr, shell):
            stdout.write(b'{"type":"assistant.message","content":"partial"}\n')
            Path(command[command.index("--usage-output-file") + 1]).write_text(
                json.dumps(
                    {
                        "totalNanoAiu": 400_000_000_000,
                        "currentModel": "claude-sonnet-5.5",
                    }
                ),
                encoding="utf-8",
            )
            Path(command[command.index("--share") + 1]).write_text(
                "# Partial session\n", encoding="utf-8"
            )
            return process

        with patch("factory.runner.Path.cwd", return_value=self.factory_root):
            with patch("factory.runner._spawn_process", side_effect=fake_process):
                with patch(
                    "factory.runner._terminate_process_tree",
                    side_effect=lambda timed_process: timed_process.kill(),
                ):
                    result = run_agent(
                        "builder",
                        self.profile,
                        self.task_file,
                        self.cwd,
                        "repair-widget",
                    )

        self.assertEqual(result.outcome, "TIMEOUT")
        self.assertEqual(result.exit_code, -9)
        self.assertTrue(process.killed)
        self.assertTrue(result.paths["stdout"].is_file())
        self.assertTrue(result.paths["usage"].is_file())
        self.assertTrue(result.paths["session"].is_file())

    @unittest.skipUnless(os.name == "nt", "Windows process-tree verification")
    def test_run_agent_timeout_terminates_child_process(self):
        marker = Path(self.temp_dir.name) / "child-survived.txt"
        child_code = (
            "import time; from pathlib import Path; "
            f"time.sleep(1.5); Path({str(marker)!r}).write_text('survived')"
        )
        parent_code = (
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            "print('started', flush=True); "
            "time.sleep(60)"
        )
        run_dir = Path(self.temp_dir.name) / "run"
        run_dir.mkdir()

        original_which = shutil.which
        with patch(
            "factory.runner.shutil.which",
            side_effect=lambda name: None if name == "taskkill" else original_which(name),
        ):
            with patch(
                "factory.runner._prepare_command",
                return_value=(
                    [sys.executable, "-c", parent_code],
                    run_dir,
                    0.25,
                    self.cwd,
                ),
            ):
                result = run_agent(
                    "builder",
                    self.profile,
                    self.task_file,
                    self.cwd,
                    "repair-widget",
                )

        time.sleep(2)
        self.assertEqual(result.outcome, "TIMEOUT")
        self.assertFalse(marker.exists(), "child process survived the timeout")
        self.assertIn("started", result.paths["stdout"].read_text(encoding="utf-8"))
        self.assertTrue(result.paths["stderr"].is_file())

    @unittest.skipUnless(os.name == "nt", "Windows process-tree verification")
    def test_run_agent_closes_job_if_tree_termination_fails(self):
        marker = Path(self.temp_dir.name) / "child-survived.txt"
        child_code = (
            "import time; from pathlib import Path; "
            f"time.sleep(1.5); Path({str(marker)!r}).write_text('survived')"
        )
        parent_code = (
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            "print('started', flush=True); time.sleep(60)"
        )
        run_dir = Path(self.temp_dir.name) / "run"
        run_dir.mkdir()
        with patch(
            "factory.runner._prepare_command",
            return_value=([sys.executable, "-c", parent_code], run_dir, 0.25, self.cwd),
        ):
            with patch(
                "factory.runner._terminate_process_tree",
                side_effect=OSError("termination failed"),
            ):
                result = run_agent(
                    "builder", self.profile, self.task_file, self.cwd, "test"
                )
        time.sleep(2)
        self.assertEqual(result.outcome, "ERROR")
        self.assertIn("termination failed", result.error)
        self.assertIn("started", result.paths["stdout"].read_text(encoding="utf-8"))
        self.assertFalse(marker.exists(), "child process survived the timeout")

    @unittest.skipUnless(os.name == "nt", "Windows process-tree verification")
    def test_run_agent_does_not_start_if_job_assignment_fails(self):
        marker = Path(self.temp_dir.name) / "agent-started.txt"
        agent_code = f"from pathlib import Path; Path({str(marker)!r}).touch()"
        run_dir = Path(self.temp_dir.name) / "run"
        run_dir.mkdir()
        with patch(
            "factory.runner._prepare_command",
            return_value=([sys.executable, "-c", agent_code], run_dir, 5, self.cwd),
        ):
            with patch("factory.windows_job.WindowsJob.assign", side_effect=OSError("denied")):
                result = run_agent(
                    "builder", self.profile, self.task_file, self.cwd, "test"
                )
        self.assertEqual(result.outcome, "ERROR")
        self.assertIn("denied", result.error)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows process-tree verification")
    def test_run_agent_preserves_successful_contained_process_result(self):
        run_dir = Path(self.temp_dir.name) / "run"
        run_dir.mkdir()
        agent_code = (
            "import json, sys; from pathlib import Path; "
            "Path(sys.argv[1]).write_text(json.dumps("
            "{'totalNanoAiu': 10, 'currentModel': 'claude-sonnet-5.5'})); "
            "Path(sys.argv[2]).write_text('# Session'); "
            "print(json.dumps({'type': 'assistant.message', 'content': 'done'}))"
        )
        command = [
            sys.executable, "-c", agent_code,
            str(run_dir / "usage.json"), str(run_dir / "session.md"),
        ]
        with patch(
            "factory.runner._prepare_command", return_value=(command, run_dir, 5, self.cwd)
        ):
            result = run_agent("builder", self.profile, self.task_file, self.cwd, "test")

        self.assertEqual(result.outcome, "OK")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("done", result.paths["stdout"].read_text(encoding="utf-8"))
        self.assertEqual(result.paths["session"].read_text(encoding="utf-8"), "# Session")

    def test_preflight_returns_authentication_failure_without_running_agent(self):
        help_result = subprocess.CompletedProcess(
            ["copilot", "--help"],
            0,
            stdout=(
                "--model --prompt --no-ask-user --no-auto-update "
                "--disable-builtin-mcps --no-custom-instructions "
                "--available-tools --allow-tool"
            ),
            stderr="",
        )
        probe_result = subprocess.CompletedProcess(
            ["copilot", "-p"],
            1,
            stdout="",
            stderr="Authentication required",
        )
        event_log = self.factory_root / "jobs" / self.job_id / "events.jsonl"
        events_before = event_log.read_bytes()

        with patch(
            "factory.runner.subprocess.run",
            side_effect=[help_result, probe_result],
        ) as run:
            result = preflight(factory_root=self.factory_root)

        self.assertFalse(result.ok)
        self.assertIsNone(result.model)
        self.assertIn("Authentication required", result.error)
        self.assertEqual(run.call_count, 2)
        self.assertNotEqual(run.call_args_list[1].kwargs["cwd"], self.cwd)
        self.assertEqual(event_log.read_bytes(), events_before)

    def test_preflight_authentication_check_precedes_model_checks(self):
        help_result = subprocess.CompletedProcess(
            ["copilot", "--help"],
            0,
            stdout=(
                "--model --prompt --no-ask-user --no-auto-update "
                "--disable-builtin-mcps --no-custom-instructions "
                "--available-tools --allow-tool"
            ),
            stderr="",
        )
        auth_failure = subprocess.CompletedProcess(
            ["copilot", "-p"], 1, stdout="", stderr="Authentication required"
        )
        with patch(
            "factory.runner.subprocess.run", side_effect=[help_result, auth_failure]
        ) as run:
            result = preflight(factory_root=self.factory_root)

        self.assertFalse(result.ok)
        self.assertIsNone(result.model)
        self.assertIn("Authentication required", result.error)
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("--model", run.call_args_list[1].args[0])

    def test_preflight_checks_each_distinct_model_and_reports_unavailable_model(self):
        self.config["profiles"]["build-other"] = {
            "agent": "builder",
            "model": "gpt-4.1",
            "effort": "high",
            "context": "default",
            "max_ai_credits": 400,
        }
        self._write_config()
        help_result = subprocess.CompletedProcess(
            ["copilot", "--help"],
            0,
            stdout=(
                "--model --prompt --no-ask-user --no-auto-update "
                "--disable-builtin-mcps --no-custom-instructions "
                "--available-tools --allow-tool"
            ),
            stderr="",
        )
        successful_probe = subprocess.CompletedProcess(
            ["copilot", "-p"],
            0,
            stdout="READY",
            stderr="",
        )
        unavailable_probe = subprocess.CompletedProcess(
            ["copilot", "-p"],
            1,
            stdout="",
            stderr="Model not available",
        )

        with patch(
            "factory.runner.subprocess.run",
            side_effect=[
                help_result, successful_probe, successful_probe, unavailable_probe
            ],
        ) as run:
            result = preflight(factory_root=self.factory_root)

        self.assertFalse(result.ok)
        self.assertEqual(result.model, "gpt-4.1")
        self.assertIn("Model not available", result.error)
        self.assertEqual(run.call_count, 4)
        probed_models = [
            call.args[0][call.args[0].index("--model") + 1]
            for call in run.call_args_list[2:]
        ]
        self.assertEqual(probed_models, ["claude-sonnet-5.5", "gpt-4.1"])
        self.assertTrue(
            all(call.kwargs["shell"] is False for call in run.call_args_list)
        )

    def test_preflight_succeeds_after_probing_distinct_configured_models(self):
        self.config["profiles"]["build-other"] = {
            "agent": "builder",
            "model": "gpt-4.1",
            "effort": "high",
            "context": "long_context",
            "max_ai_credits": 400,
        }
        self._write_config()
        help_result = subprocess.CompletedProcess(
            ["copilot", "--help"],
            0,
            stdout=(
                "--model --prompt --no-ask-user --no-auto-update "
                "--disable-builtin-mcps --no-custom-instructions "
                "--available-tools --allow-tool"
            ),
            stderr="",
        )
        successful_probe = subprocess.CompletedProcess(
            ["copilot", "-p"],
            0,
            stdout="READY",
            stderr="",
        )

        with patch(
            "factory.runner.subprocess.run",
            side_effect=[
                help_result, successful_probe, successful_probe, successful_probe
            ],
        ) as run:
            result = preflight(factory_root=self.factory_root)

        self.assertTrue(result.ok)
        self.assertIsNone(result.model)
        self.assertIsNone(result.error)
        self.assertEqual(run.call_count, 4)
        self.assertIn("--available-tools", run.call_args_list[1].args[0])
        self.assertEqual(
            run.call_args_list[0].kwargs["cwd"],
            run.call_args_list[1].kwargs["cwd"],
        )
        self.assertEqual(
            [
                call.args[0][call.args[0].index("--model") + 1]
                for call in run.call_args_list[2:]
            ],
            ["claude-sonnet-5.5", "gpt-4.1"],
        )
        self.assertTrue(
            Path(run.call_args_list[1].kwargs["cwd"]).name.startswith(
                "factory-preflight-"
            )
        )

    def test_preflight_fails_closed_when_no_models_are_configured(self):
        self.config["profiles"] = {}
        self._write_config()

        with patch("factory.runner.subprocess.run") as run:
            result = preflight(factory_root=self.factory_root)

        self.assertFalse(result.ok)
        self.assertIsNone(result.model)
        self.assertIn("no configured runner models", result.error)
        run.assert_not_called()

    def test_run_agent_does_not_start_or_allocate_run_after_preflight_failure(self):
        failure = runner_module.PreflightResult(False, None, "authentication failed")
        runs_dir = self.factory_root / "jobs" / self.job_id / "runs"
        with patch("factory.runner.Path.cwd", return_value=self.factory_root):
            with patch("factory.runner.preflight", return_value=failure):
                with patch("factory.runner._spawn_process") as process:
                    result = run_agent(
                        "builder", self.profile, self.task_file, self.cwd, "test"
                    )

        self.assertEqual(result.outcome, "ERROR")
        self.assertIn("authentication failed", result.error)
        self.assertEqual(result.paths, {})
        self.assertFalse(runs_dir.exists())
        process.assert_not_called()

    def test_run_agent_leaves_job_journal_unchanged(self):
        job_dir = self.factory_root / "jobs" / self.job_id
        job_snapshot = (job_dir / "job.json").read_bytes()
        event_snapshot = (job_dir / "events.jsonl").read_bytes()

        def fake_process(command, cwd, stdin, stdout, stderr, shell):
            stdout.write(b'{"type":"assistant.message","content":"done"}\n')
            Path(command[command.index("--usage-output-file") + 1]).write_text(
                json.dumps(
                    {
                        "totalNanoAiu": 10,
                        "currentModel": "claude-sonnet-5.5",
                    }
                ),
                encoding="utf-8",
            )
            Path(command[command.index("--share") + 1]).write_text(
                "# Session output\n", encoding="utf-8"
            )
            return _CompletedProcess()

        with patch("factory.runner.Path.cwd", return_value=self.factory_root):
            with patch("factory.runner._spawn_process", side_effect=fake_process):
                result = runner_module.run_agent(
                    "builder",
                    self.profile,
                    self.task_file,
                    self.cwd,
                    "repair-widget",
                )

        self.assertEqual(result.outcome, "OK")
        self.assertEqual((job_dir / "job.json").read_bytes(), job_snapshot)
        self.assertEqual((job_dir / "events.jsonl").read_bytes(), event_snapshot)
        self.assertEqual(
            {path.name for path in result.paths["run_dir"].iterdir()},
            {"stdout.jsonl", "stderr.log", "usage.json", "session.md"},
        )

    def test_run_agent_reports_process_tree_termination_failure(self):
        with patch(
            "factory.runner._terminate_process_tree",
            side_effect=OSError("taskkill failed"),
        ):
            result = self._run_fake_process(
                {"totalNanoAiu": 10, "currentModel": "claude-sonnet-5.5"},
                process=_TimedOutProcess(),
            )

        self.assertEqual(result.outcome, "ERROR")
        self.assertIn("cannot terminate process tree: taskkill failed", result.error)

    def test_run_agent_classifies_explicit_rate_limit_event_as_budget(self):
        result = self._run_fake_process(
            None,
            stdout=(
                b'{"type":"session.error","data":{"errorType":"rate_limit",'
                b'"message":"Too many requests"}}\n'
            ),
            exit_code=1,
        )

        self.assertEqual(result.outcome, "BUDGET")

    def test_run_agent_rejects_usage_model_mismatch(self):
        result = self._run_fake_process(
            {
                "totalNanoAiu": 10,
                "currentModel": "claude-haiku-5.5",
            }
        )

        self.assertEqual(result.outcome, "ERROR")
        self.assertIn("does not match", result.error)

    def test_run_agent_uses_single_model_metrics_key_when_current_model_is_missing(
        self,
    ):
        result = self._run_fake_process(
            {
                "totalNanoAiu": 10,
                "modelMetrics": {"claude-sonnet-5.5": {"totalNanoAiu": 10}},
            }
        )

        self.assertEqual(result.outcome, "OK")
        self.assertEqual(result.usage.effective_model, "claude-sonnet-5.5")

    def test_run_agent_fails_closed_for_invalid_usage(self):
        invalid_usage = (
            {},
            {"totalNanoAiu": True, "currentModel": "claude-sonnet-5.5"},
            {"totalNanoAiu": -1, "currentModel": "claude-sonnet-5.5"},
            {"totalNanoAiu": "10", "currentModel": "claude-sonnet-5.5"},
            {"totalNanoAiu": 10, "modelMetrics": {"model-a": {}, "model-b": {}}},
        )
        for usage_data in invalid_usage:
            with self.subTest(usage_data=usage_data):
                result = self._run_fake_process(usage_data)
                self.assertEqual(result.outcome, "ERROR")
                self.assertIsNone(result.usage)

    def test_run_agent_does_not_infer_budget_from_nonzero_exit(self):
        result = self._run_fake_process(
            {"totalNanoAiu": 10, "currentModel": "claude-sonnet-5.5"},
            stderr=b"Authentication failed\n",
            exit_code=1,
        )

        self.assertEqual(result.outcome, "ERROR")
        self.assertIn("status 1", result.error)

    def test_run_agent_rejects_malformed_jsonl_on_successful_exit(self):
        result = self._run_fake_process(
            {"totalNanoAiu": 10, "currentModel": "claude-sonnet-5.5"},
            stdout=b"not-json\n",
        )

        self.assertEqual(result.outcome, "ERROR")
        self.assertIn("invalid stdout.jsonl", result.error)


class _CompletedProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode


class _TimedOutProcess:
    def __init__(self):
        self.wait_calls = 0
        self.killed = False

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired("copilot", timeout)
        return -9

    def kill(self):
        self.killed = True
