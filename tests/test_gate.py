import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.gate import assess_changes, run_checks
from factory.job import ConfigurationError, FactoryConfig
from pydantic import ValidationError


class GateChangeAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.worktree = Path(self.temp_dir.name) / "target"
        self.worktree.mkdir()
        self._git("init", "--quiet", "--initial-branch=main")
        self._git("config", "user.name", "Factory Test")
        self._git("config", "user.email", "factory-test@example.invalid")

        (self.worktree / ".factory").mkdir()
        (self.worktree / ".factory" / "protected.txt").write_text(
            "tests/**\n", encoding="utf-8"
        )
        (self.worktree / ".factory" / "project.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (self.worktree / ".factory" / "checks.py").write_text(
            "print('{}')\n", encoding="utf-8"
        )
        (self.worktree / "app.py").write_text("value = 1\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "--quiet", "-m", "baseline")
        self.base_sha = self._git("rev-parse", "HEAD")

    def _git(self, *arguments):
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.worktree,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def test_untracked_file_outside_scope_fails_assessment(self):
        (self.worktree / "app.py").write_text("value = 2\n", encoding="utf-8")
        outside_scope = self.worktree / "notes" / "unapproved.txt"
        outside_scope.parent.mkdir()
        outside_scope.write_text("new file\n", encoding="utf-8")

        result = assess_changes(self.worktree, self.base_sha, ["app.py"])

        self.assertEqual(result.verdict, "FAIL")
        self.assertIn("notes/unapproved.txt", result.scope_violations)

    def test_change_set_includes_committed_staged_unstaged_and_untracked_paths(self):
        committed_file = self.worktree / "committed.py"
        committed_file.write_text("value = 1\n", encoding="utf-8")
        self._git("add", "committed.py")
        self._git("commit", "--quiet", "-m", "committed change")

        staged_file = self.worktree / "staged.py"
        staged_file.write_text("value = 2\n", encoding="utf-8")
        self._git("add", "staged.py")
        (self.worktree / "app.py").write_text("value = 3\n", encoding="utf-8")
        (self.worktree / "untracked.py").write_text(
            "value = 4\n", encoding="utf-8"
        )

        result = assess_changes(self.worktree, self.base_sha, ["**"])

        self.assertEqual(
            result.changed_paths,
            ("app.py", "committed.py", "staged.py", "untracked.py"),
        )

    def test_change_to_protected_path_fails_assessment(self):
        protected_file = self.worktree / "tests" / "test_app.py"
        protected_file.parent.mkdir()
        protected_file.write_text("assert True\n", encoding="utf-8")

        result = assess_changes(
            self.worktree, self.base_sha, ["tests/test_app.py"]
        )

        self.assertEqual(result.verdict, "FAIL")
        self.assertIn("tests/test_app.py", result.protected_violations)

    def test_protected_patterns_are_read_from_base_commit(self):
        protected_file = self.worktree / ".factory" / "protected.txt"
        protected_file.write_text("", encoding="utf-8")
        test_file = self.worktree / "tests" / "test_app.py"
        test_file.parent.mkdir()
        test_file.write_text("assert True\n", encoding="utf-8")

        result = assess_changes(
            self.worktree, self.base_sha, [".factory/**", "tests/**"]
        )

        self.assertIn("tests/test_app.py", result.protected_violations)

    def test_rename_checks_both_old_and_new_paths(self):
        protected_file = self.worktree / "tests" / "protected.py"
        protected_file.parent.mkdir()
        protected_file.write_text("value = 1\n", encoding="utf-8")
        self._git("add", "tests/protected.py")
        self._git("commit", "--quiet", "-m", "add protected file")
        self.base_sha = self._git("rev-parse", "HEAD")

        destination = self.worktree / "src" / "protected.py"
        destination.parent.mkdir()
        self._git("mv", "tests/protected.py", "src/protected.py")

        result = assess_changes(
            self.worktree, self.base_sha, ["src/**", "tests/**"]
        )

        self.assertIn("tests/protected.py", result.changed_paths)
        self.assertIn("src/protected.py", result.changed_paths)
        self.assertIn("tests/protected.py", result.protected_violations)

    def test_untracked_task_file_is_excluded_from_application_changes(self):
        task_file = self.worktree / ".factory" / "task.md"
        task_file.write_text("Implement the approved feature.\n", encoding="utf-8")

        result = assess_changes(self.worktree, self.base_sha, ["app.py"])

        self.assertEqual(result.verdict, "PASS")
        self.assertNotIn(".factory/task.md", result.changed_paths)

    def test_staged_task_file_is_a_protected_change(self):
        task_file = self.worktree / ".factory" / "task.md"
        task_file.write_text("Implement the approved feature.\n", encoding="utf-8")
        self._git("add", ".factory/task.md")

        result = assess_changes(self.worktree, self.base_sha, ["app.py"])

        self.assertIn(".factory/task.md", result.protected_violations)

    def test_project_configuration_and_checks_script_are_protected(self):
        project_config = self.worktree / ".factory" / "project.json"
        project_config.write_text('{"project": "changed"}\n', encoding="utf-8")
        checks_script = self.worktree / ".factory" / "checks.py"
        checks_script.write_text("print('changed')\n", encoding="utf-8")

        result = assess_changes(
            self.worktree,
            self.base_sha,
            [".factory/project.json", ".factory/checks.py"],
        )

        self.assertIn(".factory/project.json", result.protected_violations)
        self.assertIn(".factory/checks.py", result.protected_violations)

    def test_invalid_base_sha_is_a_configuration_error(self):
        with self.assertRaises(ConfigurationError):
            assess_changes(self.worktree, "not-a-full-commit-sha", ["app.py"])

    def test_base_sha_must_be_an_ancestor_of_worktree_head(self):
        tree_sha = self._git("rev-parse", "HEAD^{tree}")
        unrelated_sha = self._git("commit-tree", tree_sha, "-m", "unrelated")

        with self.assertRaises(ConfigurationError):
            assess_changes(self.worktree, unrelated_sha, ["app.py"])

    def test_double_star_matches_zero_or_more_directories(self):
        source = self.worktree / "src"
        nested_source = source / "nested"
        nested_source.mkdir(parents=True)
        (source / "app.py").write_text("value = 1\n", encoding="utf-8")
        (nested_source / "app.py").write_text("value = 2\n", encoding="utf-8")

        result = assess_changes(
            self.worktree, self.base_sha, [r"src\**\*.py"]
        )

        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(
            result.changed_paths, ("src/app.py", "src/nested/app.py")
        )

    def test_single_star_does_not_match_across_directories(self):
        nested_source = self.worktree / "src" / "nested"
        nested_source.mkdir(parents=True)
        (nested_source / "app.py").write_text("value = 1\n", encoding="utf-8")

        result = assess_changes(self.worktree, self.base_sha, ["src/*.py"])

        self.assertEqual(result.scope_violations, ("src/nested/app.py",))

    def test_unsafe_scope_patterns_are_rejected(self):
        for pattern in (
            "../app.py",
            "/app.py",
            "C:\\app.py",
            "src/[ab].py",
            "src//app.py",
            "src/app.py/",
            "src/foo**bar.py",
        ):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ConfigurationError):
                    assess_changes(self.worktree, self.base_sha, [pattern])


class GateChecksExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.worktree = Path(self.temp_dir.name) / "target"
        self.worktree.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=main"],
            cwd=self.worktree,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Factory Test"],
            cwd=self.worktree,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "factory-test@example.invalid"],
            cwd=self.worktree,
            check=True,
        )
        factory_dir = self.worktree / ".factory"
        factory_dir.mkdir()
        self.checks_script = factory_dir / "checks.py"
        self.checks_script.write_text(
            "import json\n"
            "print(json.dumps({'schema_version': 1, 'checks': ["
            "{'name': 'unit', 'status': 'PASS', 'exit_code': 0}]}))\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", ".factory/checks.py"],
            cwd=self.worktree,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "baseline"],
            cwd=self.worktree,
            check=True,
        )
        self.base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.worktree,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.config = FactoryConfig.model_validate(
            {"gate": {"timeout_seconds": 3}}
        )

    def _commit_checks_script(self, source):
        self.checks_script.write_text(source, encoding="utf-8")
        subprocess.run(
            ["git", "add", ".factory/checks.py"],
            cwd=self.worktree,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "update checks script"],
            cwd=self.worktree,
            check=True,
        )
        self.base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.worktree,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_valid_passing_checks_report_returns_pass(self):
        result = run_checks(self.worktree, self.base_sha, "fast", self.config)

        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(
            result.checks,
            ({"name": "unit", "status": "PASS", "exit_code": 0},),
        )

    def test_validated_checks_report_preserves_extra_fields(self):
        self._commit_checks_script(
            "import json\n"
            "print(json.dumps({'schema_version': 1, 'checks': ["
            "{'name': 'unit', 'status': 'PASS', 'exit_code': 0}], "
            "'diagnostics': {'tool': 'pytest'}}))\n"
        )

        result = run_checks(self.worktree, self.base_sha, "fast", self.config)

        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.report["diagnostics"], {"tool": "pytest"})

    def test_gate_timeout_defaults_to_1800_and_must_be_positive(self):
        self.assertEqual(FactoryConfig.model_validate({}).gate.timeout_seconds, 1800)
        for timeout in (0, True):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValidationError):
                    FactoryConfig.model_validate(
                        {"gate": {"timeout_seconds": timeout}}
                    )

    def test_invalid_mode_is_a_configuration_error(self):
        with self.assertRaises(ConfigurationError):
            run_checks(self.worktree, self.base_sha, [], self.config)

    def test_run_checks_requires_git_repository_root(self):
        with self.assertRaisesRegex(
            ConfigurationError, "worktree must be a Git repository root"
        ):
            run_checks(
                self.worktree / ".factory",
                self.base_sha,
                "fast",
                self.config,
            )

    def test_invalid_secret_environment_name_is_rejected(self):
        config = FactoryConfig.model_validate(
            {
                "gate": {"timeout_seconds": 3},
                "runner": {"secret_env_vars": ["BAD NAME"]},
            }
        )

        with self.assertRaises(ConfigurationError):
            run_checks(self.worktree, self.base_sha, "fast", config)

    def test_missing_checks_script_fails_gate(self):
        self.checks_script.unlink()

        result = run_checks(self.worktree, self.base_sha, "fast", self.config)

        self.assertEqual(result.verdict, "FAIL")
        self.assertIn(".factory/checks.py", result.error)

    def test_nonzero_process_exit_cannot_pass_a_passing_report(self):
        self._commit_checks_script(
            "import json, sys\n"
            "print(json.dumps({'schema_version': 1, 'checks': ["
            "{'name': 'unit', 'status': 'PASS', 'exit_code': 0}]}))\n"
            "sys.exit(2)\n"
        )

        result = run_checks(self.worktree, self.base_sha, "fast", self.config)

        self.assertEqual(result.verdict, "FAIL")
        self.assertIn("exited with status 2", result.error)

    def test_reported_check_failure_cannot_pass(self):
        self._commit_checks_script(
            "import json\n"
            "print(json.dumps({'schema_version': 1, 'checks': ["
            "{'name': 'unit', 'status': 'FAIL', 'exit_code': 1}]}))\n"
        )

        result = run_checks(self.worktree, self.base_sha, "fast", self.config)

        self.assertEqual(result.verdict, "FAIL")
        self.assertIsNone(result.error)

    def test_check_status_and_exit_code_must_agree(self):
        for status, exit_code in (("PASS", 1), ("FAIL", 0)):
            with self.subTest(status=status, exit_code=exit_code):
                self._commit_checks_script(
                    "import json\n"
                    "print(json.dumps({'schema_version': 1, 'checks': ["
                    f"{{'name': 'unit', 'status': {status!r}, "
                    f"'exit_code': {exit_code}}}]}}))\n"
                )

                result = run_checks(
                    self.worktree, self.base_sha, "fast", self.config
                )

                self.assertEqual(result.verdict, "FAIL")

    def test_malformed_checks_reports_fail_closed(self):
        reports = (
            "not json",
            "",
            '{"schema_version": 1, "checks": []}',
            '{"schema_version": 1, "schema_version": 1, "checks": [{"name": "unit", "status": "PASS", "exit_code": 0}]}',
            '{"schema_version": 1, "checks": [{"name": "unit", "status": "PASS", "exit_code": 0}], "extra": NaN}',
            '{"schema_version": true, "checks": [{"name": "unit", "status": "PASS", "exit_code": 0}]}',
            '{"schema_version": 1, "checks": [{"name": "unit", "status": [], "exit_code": 0}]}',
            '{"schema_version": 1, "checks": [{"name": "unit", "status": "PASS", "exit_code": true}]}',
            '{"schema_version": 1, "checks": [{"name": "unit", "status": "PASS", "exit_code": 0}, {"name": "unit", "status": "PASS", "exit_code": 0}]}',
        )
        for report in reports:
            with self.subTest(report=report):
                self._commit_checks_script(
                    f"print({report!r})\n" if report else "pass\n"
                )

                result = run_checks(
                    self.worktree, self.base_sha, "fast", self.config
                )

                self.assertEqual(result.verdict, "FAIL")

    def test_changed_checks_script_is_not_executed(self):
        marker = self.worktree / "script-ran.txt"
        self.checks_script.write_text(
            f"from pathlib import Path; Path({str(marker)!r}).touch()\n",
            encoding="utf-8",
        )

        result = run_checks(self.worktree, self.base_sha, "fast", self.config)

        self.assertEqual(result.verdict, "FAIL")
        self.assertIn("differs from base_sha", result.error)
        self.assertFalse(marker.exists())

    def test_configured_secret_environment_variable_is_removed(self):
        self._commit_checks_script(
            "import json, os, sys\n"
            "print(json.dumps({'schema_version': 1, 'checks': ["
            "{'name': 'unit', 'status': 'PASS', 'exit_code': 0}]}))\n"
            "print('present' if 'FACTORY_GATE_SECRET' in os.environ else 'absent', file=sys.stderr)\n"
        )
        config = FactoryConfig.model_validate(
            {
                "gate": {"timeout_seconds": 3},
                "runner": {"secret_env_vars": ["FACTORY_GATE_SECRET"]},
            }
        )

        with patch.dict(os.environ, {"FACTORY_GATE_SECRET": "not-for-checks"}):
            result = run_checks(self.worktree, self.base_sha, "fast", config)

        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.stderr.splitlines(), [b"absent"])

    def test_checks_script_receives_requested_mode(self):
        self._commit_checks_script(
            "import json, sys\n"
            "print(json.dumps({'schema_version': 1, 'checks': ["
            "{'name': 'unit', 'status': 'PASS', 'exit_code': 0}]}))\n"
            "print(sys.argv[1], file=sys.stderr)\n"
        )

        result = run_checks(self.worktree, self.base_sha, "full", self.config)

        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.stderr.splitlines(), [b"--full"])

    def test_checks_launch_failure_returns_fail(self):
        with patch(
            "factory.gate._spawn_process", side_effect=OSError("launch failed")
        ):
            result = run_checks(
                self.worktree, self.base_sha, "fast", self.config
            )

        self.assertEqual(result.verdict, "FAIL")
        self.assertIn("launch failed", result.error)

    @unittest.skipUnless(os.name == "nt", "Windows process-tree verification")
    def test_timeout_terminates_checks_child_and_preserves_output(self):
        marker = self.worktree / "child-survived.txt"
        child_code = (
            "import time; from pathlib import Path; "
            f"time.sleep(4); Path({str(marker)!r}).touch()"
        )
        self._commit_checks_script(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
            "print('checks-started', flush=True)\n"
            "time.sleep(30)\n"
        )
        config = FactoryConfig.model_validate({"gate": {"timeout_seconds": 2}})

        result = run_checks(self.worktree, self.base_sha, "fast", config)
        time.sleep(4.5)

        self.assertEqual(result.verdict, "FAIL")
        self.assertIn("timed out", result.error)
        self.assertIn(b"checks-started", result.stdout)
        self.assertFalse(marker.exists(), "checks child survived the timeout")


if __name__ == "__main__":
    unittest.main()
