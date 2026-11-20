import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class JobCreateCliTests(unittest.TestCase):
    def _prepare_repositories(self, temp_root):
        factory_root = temp_root / "factory-root"
        target_repo = temp_root / "target"
        remote_repo = temp_root / "remote.git"
        factory_root.mkdir()
        (factory_root / "config.json").write_text(
            json.dumps({"jobs_dir": "jobs", "worktrees_dir": "worktrees"}),
            encoding="utf-8",
        )
        target_repo.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=main"],
            cwd=target_repo,
            check=True,
        )
        subprocess.run(
            ["git", "init", "--quiet", "--bare", "--initial-branch=main", str(remote_repo)],
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Factory Test"],
            cwd=target_repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "factory-test@example.invalid"],
            cwd=target_repo,
            check=True,
        )
        (target_repo / "README.md").write_text("target\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=target_repo, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "initial"],
            cwd=target_repo,
            check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote_repo)],
            cwd=target_repo,
            check=True,
        )
        subprocess.run(
            ["git", "push", "--quiet", "-u", "origin", "main"],
            cwd=target_repo,
            check=True,
        )
        project_config = target_repo / ".factory"
        project_config.mkdir()
        (project_config / "project.json").write_text(
            json.dumps(
                {
                    "project": "test-project",
                    "remote": "origin",
                    "base_branch": "main",
                }
            ),
            encoding="utf-8",
        )

        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(REPOSITORY_ROOT) + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        return factory_root, target_repo, env

    def _run_cli(self, factory_root, env, *args):
        return subprocess.run(
            [sys.executable, "-m", "factory", *args],
            cwd=factory_root,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_new_creates_metadata_without_branch_or_worktree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, target_repo, env = self._prepare_repositories(Path(temp_dir))
            result = self._run_cli(
                factory_root,
                env,
                "job",
                "new",
                "--project",
                str(target_repo),
                "--title",
                "Repair widget",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            job_id = output["job_id"]
            job_dir = factory_root / "jobs" / job_id
            job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
            events = [
                json.loads(line)
                for line in (job_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(job["state"], "DRAFT_SPEC")
            self.assertEqual(job["title"], "Repair widget")
            self.assertIsNone(job["work_branch"])
            self.assertEqual(job["worktree_status"], "not_attached")
            self.assertEqual(events[0]["type"], "JOB_CREATED")
            self.assertFalse((factory_root / "worktrees").exists())

    def test_status_returns_job_and_last_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, target_repo, env = self._prepare_repositories(Path(temp_dir))
            created = self._run_cli(
                factory_root,
                env,
                "job",
                "new",
                "--project",
                str(target_repo),
                "--title",
                "Repair widget",
            )
            job_id = json.loads(created.stdout)["job_id"]

            result = self._run_cli(factory_root, env, "job", "status", job_id)

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["job"]["job_id"], job_id)
            self.assertEqual(output["job"]["state"], "DRAFT_SPEC")
            self.assertEqual(output["last_event"]["type"], "JOB_CREATED")

    def test_list_returns_jobs_in_stable_id_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, target_repo, env = self._prepare_repositories(Path(temp_dir))
            created_ids = []
            for title in ("First task", "Second task"):
                created = self._run_cli(
                    factory_root,
                    env,
                    "job",
                    "new",
                    "--project",
                    str(target_repo),
                    "--title",
                    title,
                )
                self.assertEqual(created.returncode, 0, created.stderr)
                created_ids.append(json.loads(created.stdout)["job_id"])

            result = self._run_cli(factory_root, env, "job", "list")

            self.assertEqual(result.returncode, 0, result.stderr)
            jobs = json.loads(result.stdout)
            self.assertEqual([job["job_id"] for job in jobs], sorted(created_ids))

    def test_new_rejects_blank_title_without_creating_a_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, target_repo, env = self._prepare_repositories(Path(temp_dir))

            result = self._run_cli(
                factory_root,
                env,
                "job",
                "new",
                "--project",
                str(target_repo),
                "--title",
                "  ",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("title must not be empty", result.stderr)
            self.assertFalse((factory_root / "jobs").exists())

    def test_new_rejects_storage_path_outside_factory_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            factory_root, target_repo, env = self._prepare_repositories(temp_root)
            (factory_root / "config.json").write_text(
                json.dumps(
                    {"jobs_dir": "../escaped-jobs", "worktrees_dir": "worktrees"}
                ),
                encoding="utf-8",
            )

            result = self._run_cli(
                factory_root,
                env,
                "job",
                "new",
                "--project",
                str(target_repo),
                "--title",
                "Repair widget",
            )

            self.assertEqual(result.returncode, 2)
            self.assertFalse((temp_root / "escaped-jobs").exists())

    def test_new_rejects_invalid_base_branch_without_creating_a_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, target_repo, env = self._prepare_repositories(Path(temp_dir))
            project_config_path = target_repo / ".factory" / "project.json"
            project_config = json.loads(project_config_path.read_text(encoding="utf-8"))
            project_config["base_branch"] = "../main"
            project_config_path.write_text(json.dumps(project_config), encoding="utf-8")

            result = self._run_cli(
                factory_root,
                env,
                "job",
                "new",
                "--project",
                str(target_repo),
                "--title",
                "Repair widget",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid base branch", result.stderr)
            self.assertFalse((factory_root / "jobs").exists())

    def test_new_reports_missing_project_path_as_usage_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, target_repo, env = self._prepare_repositories(Path(temp_dir))
            missing_project = target_repo / "does-not-exist"

            result = self._run_cli(
                factory_root,
                env,
                "job",
                "new",
                "--project",
                str(missing_project),
                "--title",
                "Repair widget",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid project path", result.stderr)
            self.assertFalse((factory_root / "jobs").exists())

    def test_new_reports_invalid_project_config_as_usage_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, target_repo, env = self._prepare_repositories(Path(temp_dir))
            project_config_path = target_repo / ".factory" / "project.json"
            project_config = json.loads(project_config_path.read_text(encoding="utf-8"))
            project_config["project"] = 42
            project_config_path.write_text(json.dumps(project_config), encoding="utf-8")

            result = self._run_cli(
                factory_root,
                env,
                "job",
                "new",
                "--project",
                str(target_repo),
                "--title",
                "Repair widget",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid configuration", result.stderr)
            self.assertFalse((factory_root / "jobs").exists())


if __name__ == "__main__":
    unittest.main()
