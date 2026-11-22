import subprocess
import tempfile
import unittest
from pathlib import Path

from factory.gate import assess_changes
from factory.job import ConfigurationError


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


if __name__ == "__main__":
    unittest.main()
