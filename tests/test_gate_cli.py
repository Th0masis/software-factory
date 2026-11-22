import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from factory import job as job_service


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class GateCliTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        from test_job_create import JobCreateCliTests

        helper = JobCreateCliTests()
        (
            self.factory_root,
            self.target_repo,
            self.env,
            self.job_id,
            self.branch,
            _,
        ) = helper._prepare_approved_worktree_job(Path(self.temp_dir.name))
        self._add_gate_project_files()
        job_service.attach_worktree(
            self.factory_root, self.job_id, self.branch
        )
        self.job = job_service.read_job(self.factory_root, self.job_id)["job"]
        self.worktree = Path(self.job["worktree_path"])
        self.job_dir = self.factory_root / "jobs" / self.job_id
        (self.job_dir / "scope.json").write_text(
            json.dumps(
                {"schema_version": 1, "allowed_paths": ["feature.txt"]}
            ),
            encoding="utf-8",
        )

    def _git(self, *arguments, cwd=None):
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd or self.target_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _add_gate_project_files(self):
        self._git("checkout", "--quiet", "main")
        factory_dir = self.target_repo / ".factory"
        (factory_dir / "protected.txt").write_text(
            "tests/**\n", encoding="utf-8"
        )
        (factory_dir / "checks.py").write_text(
            "import json\n"
            "print(json.dumps({'schema_version': 1, 'checks': ["
            "{'name': 'unit', 'status': 'PASS', 'exit_code': 0}]}))\n",
            encoding="utf-8",
        )
        self._git("add", ".factory/protected.txt", ".factory/checks.py")
        self._git("commit", "--quiet", "-m", "add project checks")
        self._git("push", "--quiet", "origin", "main")
        self._git("checkout", "--quiet", self.branch)
        self._git("merge", "--quiet", "--no-edit", "main")
        self._git("checkout", "--quiet", "main")

    def _run_cli(self, *arguments):
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(REPOSITORY_ROOT) + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        return subprocess.run(
            [sys.executable, "-m", "factory", *arguments],
            cwd=self.factory_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _run_gate(self, *, base_sha=None, mode="fast"):
        return self._run_cli(
            "job",
            "gate",
            self.job_id,
            "--base",
            base_sha or self.job["base_sha"],
            "--mode",
            mode,
        )

    def test_gate_cli_writes_report_and_preserves_job_files(self):
        job_snapshot = (self.job_dir / "job.json").read_bytes()
        event_log = (self.job_dir / "events.jsonl").read_bytes()

        result = self._run_gate()

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        report_path = Path(output["report_path"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(output["verdict"], "PASS")
        self.assertEqual(report["job_id"], self.job_id)
        self.assertEqual(report["mode"], "fast")
        self.assertEqual(report["checks"][0]["status"], "PASS")
        self.assertIsNone(report["checks_error"])
        self.assertIsNone(report["checks_skipped"])
        self.assertEqual(report["scope"]["changed_paths"], ["feature.txt"])
        self.assertEqual(report["risk"], {"status": "DEFERRED"})
        self.assertTrue((report_path.parent / "checks.stdout.txt").is_file())
        self.assertTrue((report_path.parent / "checks.stderr.txt").is_file())
        self.assertEqual((self.job_dir / "job.json").read_bytes(), job_snapshot)
        self.assertEqual((self.job_dir / "events.jsonl").read_bytes(), event_log)

    def test_scope_violation_fails_and_skips_checks(self):
        outside_scope = self.worktree / "unapproved.txt"
        outside_scope.write_text("not approved\n", encoding="utf-8")
        job_snapshot = (self.job_dir / "job.json").read_bytes()
        event_log = (self.job_dir / "events.jsonl").read_bytes()

        result = self._run_gate()

        output = json.loads(result.stdout)
        report_path = Path(output["report_path"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("unapproved.txt", report["scope"]["violations"])
        self.assertEqual(report["checks"], [])
        self.assertIn("scope violations", report["checks_skipped"])
        self.assertEqual(
            (report_path.parent / "checks.stdout.txt").read_bytes(), b""
        )
        self.assertEqual((self.job_dir / "job.json").read_bytes(), job_snapshot)
        self.assertEqual((self.job_dir / "events.jsonl").read_bytes(), event_log)

    def test_protected_path_violation_is_reported_and_skips_checks(self):
        protected_file = self.worktree / "tests" / "test_app.py"
        protected_file.parent.mkdir()
        protected_file.write_text("assert True\n", encoding="utf-8")

        result = self._run_gate()

        output = json.loads(result.stdout)
        report = json.loads(
            Path(output["report_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("tests/test_app.py", report["protected_paths"]["violations"])
        self.assertIn("protected-path violations", report["checks_skipped"])
        self.assertEqual(report["checks"], [])

    def test_failed_checks_return_nonzero_and_preserve_report(self):
        failing_script = (
            "import json\n"
            "print(json.dumps({'schema_version': 1, 'checks': ["
            "{'name': 'unit', 'status': 'FAIL', 'exit_code': 1}]}))\n"
        )
        (self.worktree / ".factory" / "checks.py").write_text(
            failing_script, encoding="utf-8"
        )
        subprocess.run(["git", "add", ".factory/checks.py"], cwd=self.worktree, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "record failing checks"],
            cwd=self.worktree,
            check=True,
        )
        base_sha = self._git("rev-parse", "HEAD", cwd=self.worktree)
        job_snapshot = (self.job_dir / "job.json").read_bytes()
        event_log = (self.job_dir / "events.jsonl").read_bytes()

        result = self._run_gate(base_sha=base_sha)

        output = json.loads(result.stdout)
        report_path = Path(output["report_path"])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["checks"][0]["status"], "FAIL")
        self.assertEqual(report["checks_report"]["schema_version"], 1)
        self.assertEqual((self.job_dir / "job.json").read_bytes(), job_snapshot)
        self.assertEqual((self.job_dir / "events.jsonl").read_bytes(), event_log)

    def test_each_gate_attempt_gets_a_distinct_report_directory(self):
        first = self._run_gate()
        first_report_path = Path(json.loads(first.stdout)["report_path"])
        first_report = first_report_path.read_bytes()

        second = self._run_gate()
        second_report_path = Path(json.loads(second.stdout)["report_path"])

        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertNotEqual(first_report_path.parent, second_report_path.parent)
        self.assertEqual(first_report_path.read_bytes(), first_report)

    def test_invalid_scope_manifest_is_a_usage_error_without_attempt(self):
        (self.job_dir / "scope.json").write_text(
            json.dumps(
                {"schema_version": 1, "allowed_paths": ["../outside.py"]}
            ),
            encoding="utf-8",
        )
        job_snapshot = (self.job_dir / "job.json").read_bytes()
        event_log = (self.job_dir / "events.jsonl").read_bytes()

        result = self._run_gate()

        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe path pattern", result.stderr)
        self.assertFalse((self.job_dir / "gate").exists())
        self.assertEqual((self.job_dir / "job.json").read_bytes(), job_snapshot)
        self.assertEqual((self.job_dir / "events.jsonl").read_bytes(), event_log)

    def test_missing_job_is_a_usage_error(self):
        result = self._run_cli(
            "job",
            "gate",
            "00000000-0000-0000-0000-000000000000",
            "--base",
            self.job["base_sha"],
            "--mode",
            "fast",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("job not found", result.stderr)

    def test_scope_manifest_must_be_a_regular_file(self):
        scope_path = self.job_dir / "scope.json"
        scope_path.unlink()
        scope_path.mkdir()

        result = self._run_gate()

        self.assertEqual(result.returncode, 2)
        self.assertIn("scope.json must be a regular file", result.stderr)
        self.assertFalse((self.job_dir / "gate").exists())


if __name__ == "__main__":
    unittest.main()
