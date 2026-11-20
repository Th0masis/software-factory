import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from factory import job as job_service


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

    def test_transition_to_needs_human_persists_resume_state_and_event(self):
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

            transition = self._run_cli(
                factory_root,
                env,
                "job",
                "transition",
                job_id,
                "NEEDS_HUMAN",
                "--reason",
                "Spec needs clarification",
            )

            self.assertEqual(transition.returncode, 0, transition.stderr)
            json.loads(transition.stdout)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(status.returncode, 0, status.stderr)
            output = json.loads(status.stdout)
            self.assertEqual(output["job"]["state"], "NEEDS_HUMAN")
            self.assertEqual(output["job"]["resume_state"], "DRAFT_SPEC")
            self.assertEqual(output["last_event"]["seq"], 2)
            self.assertEqual(output["last_event"]["from"], "DRAFT_SPEC")
            self.assertEqual(output["last_event"]["to"], "NEEDS_HUMAN")
            self.assertEqual(
                output["last_event"]["reason"], "Spec needs clarification"
            )

    def test_resume_returns_job_to_saved_state_and_appends_event(self):
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
            transition = self._run_cli(
                factory_root,
                env,
                "job",
                "transition",
                job_id,
                "NEEDS_HUMAN",
                "--reason",
                "Spec needs clarification",
            )
            self.assertEqual(transition.returncode, 0, transition.stderr)

            resume = self._run_cli(
                factory_root,
                env,
                "job",
                "resume",
                job_id,
                "--reason",
                "Clarification received",
            )

            self.assertEqual(resume.returncode, 0, resume.stderr)
            output = json.loads(resume.stdout)
            self.assertEqual(output["job"]["state"], "DRAFT_SPEC")
            self.assertIsNone(output["job"]["resume_state"])
            self.assertEqual(output["last_event"]["seq"], 3)
            self.assertEqual(output["last_event"]["from"], "NEEDS_HUMAN")
            self.assertEqual(output["last_event"]["to"], "DRAFT_SPEC")
            self.assertEqual(output["last_event"]["reason"], "Clarification received")

    def test_state_transition_graph_accepts_only_defined_edges(self):
        allowed = (
            ("DRAFT_SPEC", "SPEC_APPROVED"),
            ("DRAFT_SPEC", "NEEDS_HUMAN"),
            ("DRAFT_SPEC", "FAILED"),
            ("SPEC_APPROVED", "TESTS_WRITTEN"),
            ("SPEC_APPROVED", "NEEDS_HUMAN"),
            ("SPEC_APPROVED", "FAILED"),
            ("TESTS_WRITTEN", "TESTS_RED_OK"),
            ("TESTS_WRITTEN", "NEEDS_HUMAN"),
            ("TESTS_WRITTEN", "FAILED"),
            ("TESTS_RED_OK", "BUILDING"),
            ("TESTS_RED_OK", "NEEDS_HUMAN"),
            ("TESTS_RED_OK", "FAILED"),
            ("BUILDING", "GATE"),
            ("BUILDING", "NEEDS_HUMAN"),
            ("BUILDING", "FAILED"),
            ("GATE", "REVIEW"),
            ("GATE", "BUILDING"),
            ("GATE", "NEEDS_HUMAN"),
            ("GATE", "FAILED"),
            ("REVIEW", "READY_FOR_HUMAN"),
            ("REVIEW", "BUILDING"),
            ("REVIEW", "NEEDS_HUMAN"),
            ("REVIEW", "FAILED"),
            ("READY_FOR_HUMAN", "PR_OPEN"),
            ("READY_FOR_HUMAN", "NEEDS_HUMAN"),
            ("READY_FOR_HUMAN", "FAILED"),
            ("PR_OPEN", "MERGED"),
            ("PR_OPEN", "NEEDS_HUMAN"),
            ("PR_OPEN", "FAILED"),
            ("NEEDS_HUMAN", "FAILED"),
        )
        rejected = (
            ("DRAFT_SPEC", "BUILDING"),
            ("NEEDS_HUMAN", "DRAFT_SPEC"),
            ("MERGED", "NEEDS_HUMAN"),
            ("FAILED", "NEEDS_HUMAN"),
            ("UNKNOWN", "DRAFT_SPEC"),
            ("DRAFT_SPEC", "UNKNOWN"),
        )

        for source, target in allowed:
            with self.subTest(source=source, target=target):
                job_service.validate_state_transition(source, target)
        for source, target in rejected:
            with self.subTest(source=source, target=target):
                with self.assertRaises(job_service.FactoryError):
                    job_service.validate_state_transition(source, target)

    def test_status_rebuilds_stale_snapshot_from_event_log(self):
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
            transition = self._run_cli(
                factory_root,
                env,
                "job",
                "transition",
                job_id,
                "NEEDS_HUMAN",
                "--reason",
                "Spec needs clarification",
            )
            self.assertEqual(transition.returncode, 0, transition.stderr)
            job_path = factory_root / "jobs" / job_id / "job.json"
            stale_job = json.loads(job_path.read_text(encoding="utf-8"))
            stale_job["state"] = "DRAFT_SPEC"
            stale_job["resume_state"] = None
            job_path.write_text(json.dumps(stale_job), encoding="utf-8")

            status = self._run_cli(factory_root, env, "job", "status", job_id)

            self.assertEqual(status.returncode, 0, status.stderr)
            output = json.loads(status.stdout)
            self.assertEqual(output["job"]["state"], "NEEDS_HUMAN")
            self.assertEqual(output["job"]["resume_state"], "DRAFT_SPEC")
            self.assertEqual(
                json.loads(job_path.read_text(encoding="utf-8"))["state"],
                "NEEDS_HUMAN",
            )

    def test_status_rebuilds_invalid_snapshot_from_event_log(self):
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
            snapshot_path = factory_root / "jobs" / job_id / "job.json"
            snapshot_path.write_text("{", encoding="utf-8")

            status = self._run_cli(factory_root, env, "job", "status", job_id)

            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["job"]["state"], "DRAFT_SPEC")
            self.assertEqual(
                json.loads(snapshot_path.read_text(encoding="utf-8"))["state"],
                "DRAFT_SPEC",
            )

    def test_state_change_waits_for_cross_process_job_lock(self):
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
            job_dir = factory_root / "jobs" / job_id
            child = None
            try:
                with job_service._job_lock(job_dir):
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "factory",
                            "job",
                            "transition",
                            job_id,
                            "NEEDS_HUMAN",
                            "--reason",
                            "Spec needs clarification",
                        ],
                        cwd=factory_root,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    with self.assertRaises(subprocess.TimeoutExpired):
                        child.wait(timeout=0.2)
            finally:
                if child is not None and child.poll() is None:
                    child.wait(timeout=5)

            self.assertIsNotNone(child)
            output, error = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, error)
            self.assertTrue(json.loads(output))
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(json.loads(status.stdout)["last_event"]["seq"], 2)

    def test_status_rejects_incomplete_event_log_without_rewriting_it(self):
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
            transition = self._run_cli(
                factory_root,
                env,
                "job",
                "transition",
                job_id,
                "NEEDS_HUMAN",
                "--reason",
                "Spec needs clarification",
            )
            self.assertEqual(transition.returncode, 0, transition.stderr)
            job_dir = factory_root / "jobs" / job_id
            events_path = job_dir / "events.jsonl"
            original_events = events_path.read_text(encoding="utf-8")
            incomplete_events = original_events + '{"seq":3'
            events_path.write_text(incomplete_events, encoding="utf-8")
            snapshot_path = job_dir / "job.json"
            original_snapshot = snapshot_path.read_text(encoding="utf-8")

            status = self._run_cli(factory_root, env, "job", "status", job_id)

            self.assertEqual(status.returncode, 2)
            self.assertIn("incomplete event log", status.stderr)
            self.assertEqual(
                events_path.read_text(encoding="utf-8"), incomplete_events
            )
            self.assertEqual(
                snapshot_path.read_text(encoding="utf-8"), original_snapshot
            )

    def test_status_rejects_event_with_invalid_timestamp(self):
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
            events_path = factory_root / "jobs" / job_id / "events.jsonl"
            event = json.loads(events_path.read_text(encoding="utf-8"))
            event["ts"] = "not-a-timestamp"
            events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

            status = self._run_cli(factory_root, env, "job", "status", job_id)

            self.assertEqual(status.returncode, 2)
            self.assertIn("invalid event timestamp", status.stderr)

    def test_status_recovers_after_event_commit_but_snapshot_write_fails(self):
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

            with patch.object(
                job_service, "_write_snapshot", side_effect=OSError("disk unavailable")
            ):
                with self.assertRaisesRegex(OSError, "disk unavailable"):
                    job_service.transition_job(
                        factory_root,
                        job_id,
                        "NEEDS_HUMAN",
                        "Spec needs clarification",
                    )

            recovered = job_service.read_job(factory_root, job_id)

            self.assertEqual(recovered["job"]["state"], "NEEDS_HUMAN")
            self.assertEqual(recovered["last_event"]["seq"], 2)

    def test_invalid_transition_and_resume_requests_leave_job_unchanged(self):
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

            normal_transition = self._run_cli(
                factory_root,
                env,
                "job",
                "transition",
                job_id,
                "SPEC_APPROVED",
                "--reason",
                "Spec approved",
            )
            blank_reason = self._run_cli(
                factory_root,
                env,
                "job",
                "transition",
                job_id,
                "NEEDS_HUMAN",
                "--reason",
                "  ",
            )
            invalid_resume = self._run_cli(
                factory_root,
                env,
                "job",
                "resume",
                job_id,
                "--reason",
                "Resume",
            )

            self.assertEqual(normal_transition.returncode, 2)
            self.assertEqual(blank_reason.returncode, 2)
            self.assertEqual(invalid_resume.returncode, 1)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            output = json.loads(status.stdout)
            self.assertEqual(output["job"]["state"], "DRAFT_SPEC")
            self.assertEqual(output["last_event"]["seq"], 1)


if __name__ == "__main__":
    unittest.main()
