import hashlib
import io
import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from factory import job as job_service
from factory.cli import main as cli_main
from factory.runner import RunResult


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

    def _prepare_approved_worktree_job(self, temp_root, branch="feature/widget"):
        factory_root, target_repo, env = self._prepare_repositories(temp_root)
        subprocess.run(
            ["git", "add", ".factory/project.json"], cwd=target_repo, check=True
        )
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "add project configuration"],
            cwd=target_repo,
            check=True,
        )
        subprocess.run(
            ["git", "push", "--quiet", "origin", "main"],
            cwd=target_repo,
            check=True,
        )
        subprocess.run(["git", "branch", branch], cwd=target_repo, check=True)
        subprocess.run(["git", "checkout", "--quiet", branch], cwd=target_repo, check=True)
        (target_repo / "feature.txt").write_text("feature\n", encoding="utf-8")
        subprocess.run(["git", "add", "feature.txt"], cwd=target_repo, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "prepare feature branch"],
            cwd=target_repo,
            check=True,
        )
        subprocess.run(["git", "checkout", "--quiet", "main"], cwd=target_repo, check=True)
        branch_sha = subprocess.run(
            ["git", "rev-parse", f"refs/heads/{branch}"],
            cwd=target_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
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
        spec_path = temp_root / "approved-spec.md"
        spec_path.write_bytes(b"# Approved specification\n")
        approved = self._run_cli(
            factory_root,
            env,
            "job",
            "prepare",
            job_id,
            "--stage",
            "spec",
            "--path",
            str(spec_path),
            "--approve",
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        return factory_root, target_repo, env, job_id, branch, branch_sha

    def _prepare_baseline_red_job(self, temp_root, test_names=("test_acceptance",)):
        (
            factory_root,
            target_repo,
            env,
            job_id,
            branch,
            _,
        ) = self._prepare_approved_worktree_job(temp_root)
        subprocess.run(
            ["git", "checkout", "--quiet", branch],
            cwd=target_repo,
            check=True,
        )
        (target_repo / "tests").mkdir()
        test_source = "\n".join(
            f"def {test_name}():\n    assert False"
            for test_name in test_names
        ) + "\n"
        (target_repo / "tests" / "test_widget.py").write_text(
            test_source,
            encoding="utf-8",
        )
        (target_repo / ".factory" / "protected.txt").write_text(
            "tests/**\n", encoding="utf-8"
        )
        (target_repo / ".factory" / "checks.py").write_text(
            "import argparse\n"
            "import hashlib\n"
            "import json\n"
            "import os\n"
            "from pathlib import Path\n"
            "parser = argparse.ArgumentParser()\n"
            "mode = parser.add_mutually_exclusive_group(required=True)\n"
            "mode.add_argument('--red', action='store_true')\n"
            "mode.add_argument('--fast', action='store_true')\n"
            "parser.add_argument('--manifest', required=True)\n"
            "parser.add_argument('--baseline-sha')\n"
            "args = parser.parse_args()\n"
            "manifest = Path(args.manifest).read_bytes()\n"
            "tests = json.loads(manifest)['tests']\n"
            "if os.environ.get('FACTORY_TEST_MUTATE_INPUT'):\n"
            "    Path(tests[0]['path']).write_text('modified\\n', encoding='utf-8')\n"
            "if args.red:\n"
            "    manifest_sha256 = hashlib.sha256(manifest).hexdigest()\n"
            "    if os.environ.get('FACTORY_TEST_BAD_MANIFEST_SHA'):\n"
            "        manifest_sha256 = '0' * 64\n"
            "    outcome = os.environ.get('FACTORY_TEST_RED_OUTCOME', "
            "'ASSERTION_FAILURE')\n"
            "    report = {"
            "'schema_version': 1, "
            "'work_branch_start_sha': args.baseline_sha, "
            "'manifest_sha256': manifest_sha256, "
            "'tests': [{'id': test['id'], 'outcome': outcome} "
            "for test in tests]}\n"
            "else:\n"
            "    outcome = os.environ.get('FACTORY_TEST_FAST_OUTCOME', 'PASS')\n"
            "    report = {"
            "'schema_version': 1, "
            "'checks': [{'name': 'selected-tests', "
            "'status': 'PASS' if outcome == 'PASS' else 'FAIL', "
            "'exit_code': 0 if outcome == 'PASS' else 1}], "
            "'selected_ids': [test['id'] for test in tests]}\n"
            "print(json.dumps(report))\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "git",
                "add",
                "tests/test_widget.py",
                ".factory/checks.py",
                ".factory/protected.txt",
            ],
            cwd=target_repo,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "add TDD test and checks"],
            cwd=target_repo,
            check=True,
        )
        subprocess.run(
            ["git", "checkout", "--quiet", "main"],
            cwd=target_repo,
            check=True,
        )
        attached = self._run_cli(
            factory_root,
            env,
            "job",
            "worktree",
            "attach",
            job_id,
            "--branch",
            branch,
        )
        self.assertEqual(attached.returncode, 0, attached.stderr)
        manifest_path = temp_root / "approved-tests.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "tests": [
                        {
                            "id": f"tests.test_widget.{test_name}",
                            "path": "tests/test_widget.py",
                        }
                        for test_name in test_names
                    ],
                }
            ),
            encoding="utf-8",
        )
        return factory_root, target_repo, env, job_id, manifest_path

    def _prepare_initializer_job(self, temp_root, test_names=("test_acceptance",)):
        factory_root, target_repo, env, job_id, manifest_path = (
            self._prepare_baseline_red_job(temp_root, test_names)
        )
        registered = self._run_cli(
            factory_root,
            env,
            "job",
            "prepare",
            job_id,
            "--stage",
            "tests",
            "--path",
            str(manifest_path),
            "--approve",
        )
        self.assertEqual(registered.returncode, 0, registered.stderr)
        config_path = factory_root / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["profiles"] = {
            "initializer-test": {
                "agent": "builder",
                "model": "test-model",
                "effort": "low",
                "context": "default",
                "max_ai_credits": 10,
            }
        }
        config_path.write_text(json.dumps(config), encoding="utf-8")
        job_dir = factory_root / "jobs" / job_id
        (job_dir / "scope.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "allowed_paths": [".factory/init.py", "tests/**", "src/**"],
                }
            ),
            encoding="utf-8",
        )
        plan = {
            "schema_version": 1,
            "features": [
                {
                    "id": "widget-acceptance",
                    "title": "Widget acceptance",
                    "slices": [
                        {
                            "id": (
                                "widget-acceptance-test"
                                if test_name == "test_acceptance"
                                else f"widget-{test_name}"
                            ),
                            "goal": f"Satisfy the {test_name} test",
                            "paths": [f"src/{test_name}.py"],
                            "acceptance": [f"{test_name} passes"],
                            "test_ids": [f"tests.test_widget.{test_name}"],
                        }
                        for test_name in test_names
                    ],
                }
            ],
        }
        init_script = "from pathlib import Path\nprint(Path.cwd())\n"
        run_dir = job_dir / "runs" / "1"
        run_dir.mkdir(parents=True)
        stdout_path = run_dir / "stdout.jsonl"
        stdout_path.write_text(
            json.dumps(
                {
                    "type": "assistant.message",
                    "content": json.dumps(
                        {"plan": plan, "init_script": init_script}
                    ),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        session_path = run_dir / "session.md"
        session_path.write_text("Initializer session\n", encoding="utf-8")
        runner_result = RunResult(
            outcome="OK",
            exit_code=0,
            usage=None,
            session_id="fake-session",
            paths={
                "run_dir": run_dir,
                "stdout": stdout_path,
                "session": session_path,
            },
            error=None,
        )
        return factory_root, target_repo, env, job_id, plan, init_script, runner_result

    def _run_initializer(self, factory_root, job_id, runner_result, side_effect=None):
        output = io.StringIO()
        if side_effect is None:
            def return_result(role, profile, task_file, cwd, session_name):
                self.assertEqual(role, "builder")
                return runner_result

            side_effect = return_result
        runner_patch = (
            patch("factory.runner.run_agent", side_effect=side_effect)
        )
        with patch("factory.cli.Path.cwd", return_value=factory_root):
            with runner_patch:
                with redirect_stdout(output):
                    exit_code = cli_main(
                        ["job", "initialize", job_id, "--profile", "initializer-test"]
                    )
        return exit_code, output.getvalue()

    def test_initialize_persists_pending_plan_and_script_from_runner_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _target_repo,
                _env,
                job_id,
                plan,
                init_script,
                runner_result,
            ) = self._prepare_initializer_job(Path(temp_dir))
            job_dir = factory_root / "jobs" / job_id
            exit_code, output = self._run_initializer(
                factory_root, job_id, runner_result
            )

            self.assertEqual(exit_code, 0)
            result = json.loads(output)
            self.assertEqual(result["job"]["state"], "TESTS_RED_OK")
            self.assertEqual(
                json.loads(
                    (job_dir / result["last_event"]["plan_path"]).read_text(
                        encoding="utf-8"
                    )
                ),
                plan,
            )
            self.assertEqual(
                (job_dir / result["last_event"]["init_script_path"]).read_text(
                    encoding="utf-8"
                ),
                init_script,
            )
            self.assertEqual(result["last_event"]["type"], "INITIALIZER_RUN_COMPLETED")
            self.assertEqual(result["last_event"]["outcome"], "OK")
            worktree = Path(
                job_service.read_job(factory_root, job_id)["job"]["worktree_path"]
            )
            self.assertFalse((worktree / ".factory" / "task.md").exists())
            script_path = job_dir / result["last_event"]["init_script_path"]
            script_path.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(
                job_service.ConfigurationError, "initializer artifact hash mismatch"
            ):
                job_service.read_job(factory_root, job_id)

    def test_initializer_malformed_json_escalates_to_human(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, _, _, job_id, _, _, runner_result = (
                self._prepare_initializer_job(Path(temp_dir))
            )
            runner_result.paths["stdout"].write_text(
                '{"type":"assistant.message","content":"not JSON"}\n',
                encoding="utf-8",
            )

            exit_code, _ = self._run_initializer(
                factory_root, job_id, runner_result
            )

            self.assertEqual(exit_code, 1)
            status = job_service.read_job(factory_root, job_id)
            self.assertEqual(status["job"]["state"], "NEEDS_HUMAN")
            self.assertEqual(
                status["last_event"]["failure_outcome"], "ERROR"
            )
            self.assertIn("one JSON object", status["last_event"]["failure_reason"])

    def test_initializer_worktree_changes_are_preserved_and_escalated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, _, _, job_id, _, _, runner_result = (
                self._prepare_initializer_job(Path(temp_dir))
            )
            worktree = Path(
                job_service.read_job(factory_root, job_id)["job"]["worktree_path"]
            )

            def change_target(role, profile, task_file, cwd, session_name):
                (cwd / "initializer-change.txt").write_text(
                    "preserve\n", encoding="utf-8"
                )
                return runner_result

            exit_code, _ = self._run_initializer(
                factory_root, job_id, runner_result, side_effect=change_target
            )

            self.assertEqual(exit_code, 1)
            self.assertEqual(
                (worktree / "initializer-change.txt").read_text(encoding="utf-8"),
                "preserve\n",
            )
            status = job_service.read_job(factory_root, job_id)
            self.assertEqual(status["job"]["state"], "NEEDS_HUMAN")
            self.assertIn("changed the target worktree", status["last_event"]["failure_reason"])

    def test_initializer_timeout_escalates_with_durable_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, _, _, job_id, _, _, runner_result = (
                self._prepare_initializer_job(Path(temp_dir))
            )
            failed_result = replace(
                runner_result,
                outcome="TIMEOUT",
                exit_code=None,
                error="Copilot session timed out",
            )

            exit_code, output = self._run_initializer(
                factory_root, job_id, failed_result
            )

            self.assertEqual(exit_code, 1)
            status = job_service.read_job(factory_root, job_id)
            self.assertEqual(status["job"]["state"], "NEEDS_HUMAN")
            self.assertEqual(
                status["last_event"]["failure_stage"], "initializer"
            )
            self.assertEqual(status["last_event"]["failure_outcome"], "TIMEOUT")
            self.assertIn("runs/1", status["last_event"]["run_dir"])

    def test_initializer_invalid_success_run_directory_escalates_to_human(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, _, _, job_id, _, _, runner_result = (
                self._prepare_initializer_job(Path(temp_dir))
            )
            runner_result.paths["run_dir"] = Path(temp_dir) / "outside-run"

            exit_code, _ = self._run_initializer(
                factory_root, job_id, runner_result
            )

            self.assertEqual(exit_code, 1)
            status = job_service.read_job(factory_root, job_id)
            self.assertEqual(status["job"]["state"], "NEEDS_HUMAN")
            self.assertEqual(status["last_event"]["failure_stage"], "initializer")
            self.assertIn(
                "initializer run artifacts are outside the job",
                status["last_event"]["failure_reason"],
            )

    def test_initializer_retry_requires_resume_and_uses_a_new_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, _, env, job_id, _, _, runner_result = (
                self._prepare_initializer_job(Path(temp_dir))
            )
            failed_result = replace(
                runner_result,
                outcome="TIMEOUT",
                exit_code=None,
                error="Copilot session timed out",
            )
            failed, _ = self._run_initializer(factory_root, job_id, failed_result)
            self.assertEqual(failed, 1)
            failed_status = job_service.read_job(factory_root, job_id)
            self.assertEqual(failed_status["job"]["state"], "NEEDS_HUMAN")
            failed_attempt_id = failed_status["last_event"]["attempt_id"]
            without_resume, _ = self._run_initializer(
                factory_root, job_id, runner_result
            )
            self.assertEqual(without_resume, 2)
            resumed = self._run_cli(
                factory_root,
                env,
                "job",
                "resume",
                job_id,
                "--reason",
                "Human approved another initializer attempt",
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)

            second_run = factory_root / "jobs" / job_id / "runs" / "2"
            second_run.mkdir()
            second_stdout = second_run / "stdout.jsonl"
            first_stdout = runner_result.paths["stdout"].read_text(encoding="utf-8")
            second_stdout.write_text(first_stdout, encoding="utf-8")
            second_session = second_run / "session.md"
            second_session.write_text("Initializer retry\n", encoding="utf-8")
            retry_result = replace(
                runner_result,
                paths={
                    "run_dir": second_run,
                    "stdout": second_stdout,
                    "session": second_session,
                },
            )
            retried, output = self._run_initializer(
                factory_root, job_id, retry_result
            )

            self.assertEqual(retried, 0, output)
            retried_event = json.loads(output)["last_event"]
            self.assertNotEqual(retried_event["attempt_id"], failed_attempt_id)
            self.assertEqual(
                job_service.read_job(factory_root, job_id)["job"]["state"],
                "TESTS_RED_OK",
            )

    def test_prepare_plan_rejects_unmapped_registered_test(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, _, env, job_id, _, _, runner_result = (
                self._prepare_initializer_job(Path(temp_dir))
            )
            initialized, output = self._run_initializer(
                factory_root, job_id, runner_result
            )
            self.assertEqual(initialized, 0, output)
            init_event = json.loads(output)["last_event"]
            job_dir = factory_root / "jobs" / job_id
            plan = json.loads(
                (job_dir / init_event["plan_path"]).read_text(encoding="utf-8")
            )
            plan["features"][0]["slices"][0]["test_ids"] = ["unknown.test.id"]
            plan_path = Path(temp_dir) / "incomplete-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            prepared = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "plan",
                "--path",
                str(plan_path),
                "--init-script",
                str(job_dir / init_event["init_script_path"]),
                "--approve",
            )

            self.assertEqual(prepared.returncode, 2)
            self.assertIn("every registered test ID must appear exactly once", prepared.stderr)
            self.assertEqual(job_service.read_job(factory_root, job_id)["job"]["state"], "TESTS_RED_OK")
            worktree = Path(
                job_service.read_job(factory_root, job_id)["job"]["worktree_path"]
            )
            self.assertFalse((worktree / ".factory" / "init.py").exists())

    def test_prepare_plan_rejects_paths_outside_approved_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, _, env, job_id, _, _, runner_result = (
                self._prepare_initializer_job(Path(temp_dir))
            )
            initialized, output = self._run_initializer(
                factory_root, job_id, runner_result
            )
            self.assertEqual(initialized, 0, output)
            job_dir = factory_root / "jobs" / job_id
            init_event = json.loads(output)["last_event"]
            plan = json.loads(
                (job_dir / init_event["plan_path"]).read_text(encoding="utf-8")
            )
            plan["features"][0]["slices"][0]["paths"] = ["outside/module.py"]
            plan_path = Path(temp_dir) / "out-of-scope-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            prepared = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "plan",
                "--path",
                str(plan_path),
                "--init-script",
                str(job_dir / init_event["init_script_path"]),
                "--approve",
            )

            self.assertEqual(prepared.returncode, 2)
            self.assertIn("outside approved scope", prepared.stderr)
            self.assertEqual(
                job_service.read_job(factory_root, job_id)["job"]["state"],
                "TESTS_RED_OK",
            )

    def test_prepare_plan_requires_initializer_script_in_approved_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            factory_root, _, env, job_id, _, _, runner_result = (
                self._prepare_initializer_job(Path(temp_dir))
            )
            (factory_root / "jobs" / job_id / "scope.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "allowed_paths": ["tests/**", "src/**"],
                    }
                ),
                encoding="utf-8",
            )
            initialized, output = self._run_initializer(
                factory_root, job_id, runner_result
            )
            self.assertEqual(initialized, 0, output)
            init_event = json.loads(output)["last_event"]
            job_dir = factory_root / "jobs" / job_id

            prepared = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "plan",
                "--path",
                str(job_dir / init_event["plan_path"]),
                "--init-script",
                str(job_dir / init_event["init_script_path"]),
                "--approve",
            )

            self.assertEqual(prepared.returncode, 2)
            self.assertIn("initializer script path is outside approved scope", prepared.stderr)
            self.assertEqual(
                job_service.read_job(factory_root, job_id)["job"]["state"],
                "TESTS_RED_OK",
            )

    def test_prepare_approved_plan_commits_only_the_initializer_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _target_repo,
                env,
                job_id,
                _plan,
                init_script,
                runner_result,
            ) = self._prepare_initializer_job(Path(temp_dir))
            initialized, output = self._run_initializer(
                factory_root, job_id, runner_result
            )
            self.assertEqual(initialized, 0, output)
            init_event = json.loads(output)["last_event"]
            job_dir = factory_root / "jobs" / job_id
            plan_args = [
                "job",
                "prepare",
                job_id,
                "--stage",
                "plan",
                "--path",
                str(job_dir / init_event["plan_path"]),
                "--init-script",
                str(job_dir / init_event["init_script_path"]),
            ]
            unapproved = self._run_cli(factory_root, env, *plan_args)
            self.assertEqual(unapproved.returncode, 2)
            self.assertEqual(
                job_service.read_job(factory_root, job_id)["job"]["state"],
                "TESTS_RED_OK",
            )
            self.assertFalse(
                (Path(job_service.read_job(factory_root, job_id)["job"]["worktree_path"])
                 / ".factory" / "init.py").exists()
            )
            prepared = self._run_cli(factory_root, env, *plan_args, "--approve")

            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            result = json.loads(prepared.stdout)
            self.assertEqual(result["job"]["state"], "BUILDING")
            worktree = Path(result["job"]["worktree_path"])
            self.assertEqual(
                job_service.read_job(factory_root, job_id)["job"]["state"],
                "BUILDING",
            )
            self.assertEqual(
                result["last_event"]["init_script_target"], ".factory/init.py"
            )
            self.assertEqual(
                result["last_event"]["plan_sha256"],
                hashlib.sha256((job_dir / "slice-plan.json").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                result["last_event"]["init_script_sha256"],
                hashlib.sha256(init_script.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                (job_dir / "init.py").read_text(encoding="utf-8"), init_script
            )
            changed_paths = subprocess.run(
                [
                    "git",
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                ],
                cwd=worktree,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(changed_paths, [".factory/init.py"])
            self.assertEqual(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=worktree,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout,
                "",
            )

    def test_run_stops_after_slice_gate_failure_without_pass_or_next_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _target_repo,
                env,
                job_id,
                _plan,
                _init_script,
                initializer_result,
            ) = self._prepare_initializer_job(
                Path(temp_dir), ("test_first", "test_second")
            )
            initialized, output = self._run_initializer(
                factory_root, job_id, initializer_result
            )
            self.assertEqual(initialized, 0, output)
            init_event = json.loads(output)["last_event"]
            job_dir = factory_root / "jobs" / job_id
            approved = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "plan",
                "--path",
                str(job_dir / init_event["plan_path"]),
                "--init-script",
                str(job_dir / init_event["init_script_path"]),
                "--approve",
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)
            worktree = Path(json.loads(approved.stdout)["job"]["worktree_path"])
            runner_calls = []

            def fail_first_slice(role, profile, task_file, cwd, session_name):
                runner_calls.append(task_file.read_text(encoding="utf-8"))
                source_path = cwd / "src" / "test_first.py"
                source_path.parent.mkdir(exist_ok=True)
                source_path.write_text("value = 1\n", encoding="utf-8")
                subprocess.run(
                    ["git", "add", "--", "src/test_first.py"],
                    cwd=cwd,
                    check=True,
                )
                subprocess.run(
                    [
                        "git",
                        "commit",
                        "--quiet",
                        "-m",
                        "Implement first TDD slice",
                        "--",
                        "src/test_first.py",
                    ],
                    cwd=cwd,
                    check=True,
                )
                run_dir = job_dir / "runs" / "2"
                run_dir.mkdir()
                stdout_path = run_dir / "stdout.jsonl"
                stdout_path.write_text(
                    json.dumps(
                        {
                            "type": "assistant.message",
                            "content": "Implemented first slice; next is test_second.",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                session_path = run_dir / "session.md"
                session_path.write_text("Builder session\n", encoding="utf-8")
                return RunResult(
                    outcome="OK",
                    exit_code=0,
                    usage=None,
                    session_id="fake-builder-session",
                    paths={
                        "run_dir": run_dir,
                        "stdout": stdout_path,
                        "session": session_path,
                    },
                    error=None,
                )

            output = io.StringIO()
            with patch("factory.cli.Path.cwd", return_value=factory_root):
                with patch("factory.runner.run_agent", side_effect=fail_first_slice):
                    with patch.dict(
                        os.environ, {"FACTORY_TEST_FAST_OUTCOME": "FAIL"}
                    ):
                        with redirect_stdout(output):
                            try:
                                exit_code = cli_main(
                                    [
                                        "job",
                                        "run",
                                        job_id,
                                        "--profile",
                                        "initializer-test",
                                    ]
                                )
                            except SystemExit as error:
                                exit_code = error.code

            self.assertEqual(exit_code, 1)
            self.assertEqual(len(runner_calls), 1)
            self.assertIn("widget-test_first", runner_calls[0])
            self.assertNotIn("widget-test_second", runner_calls[0])
            status = job_service.read_job(factory_root, job_id)
            self.assertEqual(status["job"]["state"], "NEEDS_HUMAN")
            events = [
                json.loads(line)
                for line in (job_dir / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertIn("SLICE_GATE_COMPLETED", [event["type"] for event in events])
            self.assertNotIn("SLICE_PASSED", [event["type"] for event in events])
            features = json.loads(
                (job_dir / "features.json").read_text(encoding="utf-8")
            )
            self.assertFalse(
                any(
                    item["passes"]
                    for feature in features["features"]
                    for item in feature["slices"]
                )
            )
            gate_event = next(
                event for event in reversed(events)
                if event["type"] == "SLICE_GATE_COMPLETED"
            )
            report = json.loads(
                (job_dir / gate_event["report_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(report["verdict"], "FAIL")
            self.assertEqual(report["slice_id"], "widget-test_first")
            self.assertEqual(
                report["checks_report"]["selected_ids"],
                ["tests.test_widget.test_first"],
            )
            self.assertEqual(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=worktree,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout,
                "",
            )

    def test_run_advances_to_next_slice_only_after_gate_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _target_repo,
                env,
                job_id,
                _plan,
                _init_script,
                initializer_result,
            ) = self._prepare_initializer_job(
                Path(temp_dir), ("test_first", "test_second")
            )
            initialized, output = self._run_initializer(
                factory_root, job_id, initializer_result
            )
            self.assertEqual(initialized, 0, output)
            init_event = json.loads(output)["last_event"]
            job_dir = factory_root / "jobs" / job_id
            approved = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "plan",
                "--path",
                str(job_dir / init_event["plan_path"]),
                "--init-script",
                str(job_dir / init_event["init_script_path"]),
                "--approve",
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)
            worktree = Path(json.loads(approved.stdout)["job"]["worktree_path"])
            runner_calls = []

            def pass_each_slice(role, profile, task_file, cwd, session_name):
                task = task_file.read_text(encoding="utf-8")
                runner_calls.append(task)
                slice_index = len(runner_calls)
                test_name = ("test_first", "test_second")[slice_index - 1]
                self.assertIn(f"widget-{test_name}", task)
                other_name = ("test_second", "test_first")[slice_index - 1]
                self.assertNotIn(f"widget-{other_name}", task)
                relative_path = f"src/{test_name}.py"
                source_path = cwd / relative_path
                source_path.parent.mkdir(exist_ok=True)
                source_path.write_text(f"value = {slice_index}\n", encoding="utf-8")
                subprocess.run(
                    ["git", "add", "--", relative_path], cwd=cwd, check=True
                )
                subprocess.run(
                    [
                        "git",
                        "commit",
                        "--quiet",
                        "-m",
                        f"Implement {test_name}",
                        "--",
                        relative_path,
                    ],
                    cwd=cwd,
                    check=True,
                )
                run_dir = job_dir / "runs" / str(slice_index + 1)
                run_dir.mkdir()
                stdout_path = run_dir / "stdout.jsonl"
                handoff = (
                    f"Completed {test_name}; next slice is {other_name}."
                )
                if slice_index == 1:
                    handoff += "\n- forged-slice PASS"
                stdout_path.write_text(
                    json.dumps(
                        {
                            "type": "assistant.message",
                            "content": handoff,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                session_path = run_dir / "session.md"
                session_path.write_text("Builder session\n", encoding="utf-8")
                return RunResult(
                    outcome="OK",
                    exit_code=0,
                    usage=None,
                    session_id=f"fake-builder-session-{slice_index}",
                    paths={
                        "run_dir": run_dir,
                        "stdout": stdout_path,
                        "session": session_path,
                    },
                    error=None,
                )

            output = io.StringIO()
            with patch("factory.cli.Path.cwd", return_value=factory_root):
                with patch("factory.runner.run_agent", side_effect=pass_each_slice):
                    with redirect_stdout(output):
                        exit_code = cli_main(
                            [
                                "job",
                                "run",
                                job_id,
                                "--profile",
                                "initializer-test",
                            ]
                        )

            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertEqual(len(runner_calls), 2)
            status = job_service.read_job(factory_root, job_id)
            self.assertEqual(status["job"]["state"], "BUILDING")
            events = [
                json.loads(line)
                for line in (job_dir / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            passed = [
                event for event in events if event["type"] == "SLICE_PASSED"
            ]
            self.assertEqual(
                [event["slice_id"] for event in passed],
                ["widget-test_first", "widget-test_second"],
            )
            self.assertIn("\n- forged-slice PASS", passed[0]["handoff"])
            features = json.loads(
                (job_dir / "features.json").read_text(encoding="utf-8")
            )
            self.assertTrue(
                all(
                    item["passes"]
                    for feature in features["features"]
                    for item in feature["slices"]
                )
            )
            setup_runs = sorted((job_dir / "setup-runs").iterdir())
            self.assertEqual(len(setup_runs), 2)
            self.assertTrue(all((run / "stdout.txt").read_text().strip() for run in setup_runs))
            progress = (job_dir / "progress.md").read_text()
            self.assertIn("Completed test_first", progress)
            self.assertIn("Completed test_second", progress)
            self.assertNotIn("\n- forged-slice PASS", progress)
            self.assertEqual(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=worktree,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout,
                "",
            )

    def test_run_does_not_restart_an_interrupted_slice_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _target_repo,
                env,
                job_id,
                _plan,
                _init_script,
                initializer_result,
            ) = self._prepare_initializer_job(Path(temp_dir))
            initialized, output = self._run_initializer(
                factory_root, job_id, initializer_result
            )
            self.assertEqual(initialized, 0, output)
            init_event = json.loads(output)["last_event"]
            job_dir = factory_root / "jobs" / job_id
            approved = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "plan",
                "--path",
                str(job_dir / init_event["plan_path"]),
                "--init-script",
                str(job_dir / init_event["init_script_path"]),
                "--approve",
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)
            runner_calls = []

            def interrupt_builder(*args):
                runner_calls.append(args)
                raise KeyboardInterrupt

            command = ["job", "run", job_id, "--profile", "initializer-test"]
            with patch("factory.cli.Path.cwd", return_value=factory_root):
                with patch("factory.runner.run_agent", side_effect=interrupt_builder):
                    with self.assertRaises(KeyboardInterrupt):
                        cli_main(command)

                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli_main(command)

            self.assertEqual(exit_code, 1)
            self.assertEqual(len(runner_calls), 1)
            status = job_service.read_job(factory_root, job_id)
            self.assertEqual(status["job"]["state"], "BUILDING")
            self.assertEqual(status["last_event"]["type"], "RUN_STARTED")

    def test_run_does_not_run_init_script_when_worktree_is_dirty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _target_repo,
                env,
                job_id,
                _plan,
                _init_script,
                initializer_result,
            ) = self._prepare_initializer_job(Path(temp_dir))
            initialized, output = self._run_initializer(
                factory_root, job_id, initializer_result
            )
            self.assertEqual(initialized, 0, output)
            init_event = json.loads(output)["last_event"]
            job_dir = factory_root / "jobs" / job_id
            approved = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "plan",
                "--path",
                str(job_dir / init_event["plan_path"]),
                "--init-script",
                str(job_dir / init_event["init_script_path"]),
                "--approve",
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)
            worktree = Path(json.loads(approved.stdout)["job"]["worktree_path"])
            user_change = worktree / "src" / "user-change.py"
            user_change.parent.mkdir()
            user_change.write_text("preserve = True\n", encoding="utf-8")
            output = io.StringIO()

            with patch("factory.cli.Path.cwd", return_value=factory_root):
                with patch(
                    "factory.runner.run_agent",
                    side_effect=AssertionError("Runner must not start"),
                ):
                    with redirect_stdout(output):
                        exit_code = cli_main(
                            [
                                "job",
                                "run",
                                job_id,
                                "--profile",
                                "initializer-test",
                            ]
                        )

            self.assertEqual(exit_code, 1)
            self.assertFalse((job_dir / "setup-runs").exists())
            self.assertEqual(user_change.read_text(encoding="utf-8"), "preserve = True\n")
            status = job_service.read_job(factory_root, job_id)
            self.assertEqual(status["job"]["state"], "NEEDS_HUMAN")
            self.assertEqual(
                status["last_event"]["failure_stage"], "initializer_script"
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

    def test_prepare_approved_spec_records_hash_and_advances_state(self):
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
            spec_content = "# Repair widget\n\nApproved acceptance criteria.\n"
            spec_path = Path(temp_dir) / "approved-spec.md"
            spec_path.write_bytes(spec_content.encode("utf-8"))

            prepared = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "spec",
                "--path",
                str(spec_path),
                "--approve",
            )

            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            output = json.loads(prepared.stdout)
            self.assertEqual(output["job"]["state"], "SPEC_APPROVED")
            self.assertEqual(
                output["last_event"]["input_sha256"],
                hashlib.sha256(spec_content.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(output["last_event"]["input_path"], "spec.md")
            self.assertEqual(
                (factory_root / "jobs" / job_id / "spec.md").read_text(
                    encoding="utf-8"
                ),
                spec_content,
            )

    def test_prepare_spec_without_human_approval_preserves_job(self):
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
            spec_path = Path(temp_dir) / "proposed-spec.md"
            spec_path.write_bytes(b"# Proposed specification\n")

            prepared = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "spec",
                "--path",
                str(spec_path),
            )

            self.assertEqual(prepared.returncode, 2)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            output = json.loads(status.stdout)
            self.assertEqual(output["job"]["state"], "DRAFT_SPEC")
            self.assertEqual(output["last_event"]["seq"], 1)
            self.assertFalse((factory_root / "jobs" / job_id / "spec.md").exists())

    def test_prepare_spec_after_approval_preserves_locked_input(self):
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
            spec_path = Path(temp_dir) / "approved-spec.md"
            spec_path.write_bytes(b"# Approved specification\n")
            prepare_args = (
                "job",
                "prepare",
                job_id,
                "--stage",
                "spec",
                "--path",
                str(spec_path),
                "--approve",
            )
            approved = self._run_cli(factory_root, env, *prepare_args)
            self.assertEqual(approved.returncode, 0, approved.stderr)
            events_path = factory_root / "jobs" / job_id / "events.jsonl"
            original_events = events_path.read_bytes()

            repeated = self._run_cli(factory_root, env, *prepare_args)

            self.assertEqual(repeated.returncode, 2)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            output = json.loads(status.stdout)
            self.assertEqual(output["job"]["state"], "SPEC_APPROVED")
            self.assertEqual(output["last_event"]["seq"], 2)
            self.assertEqual(events_path.read_bytes(), original_events)
            self.assertEqual(
                (factory_root / "jobs" / job_id / "spec.md").read_bytes(),
                spec_path.read_bytes(),
            )

    def test_prepare_approved_tests_records_manifest_after_baseline_red(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _,
                env,
                job_id,
                manifest_path,
            ) = self._prepare_baseline_red_job(Path(temp_dir))

            registered = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "tests",
                "--path",
                str(manifest_path),
                "--approve",
            )

            self.assertEqual(registered.returncode, 0, registered.stderr)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(status.returncode, 0, status.stderr)
            output = json.loads(status.stdout)
            stored_manifest = (
                factory_root / "jobs" / job_id / "tests.json"
            ).read_bytes()
            self.assertEqual(output["job"]["state"], "TESTS_RED_OK")
            self.assertEqual(output["last_event"]["to"], "TESTS_RED_OK")
            report_path = (
                factory_root / "jobs" / job_id / output["last_event"]["red_report_path"]
            )
            self.assertTrue(report_path.is_file())
            self.assertEqual(
                output["last_event"]["red_report_sha256"],
                hashlib.sha256(report_path.read_bytes()).hexdigest(),
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["work_branch_start_sha"],
                output["job"]["work_branch_start_sha"],
            )
            self.assertNotEqual(
                report["work_branch_start_sha"],
                output["job"]["base_sha"],
            )
            events = [
                json.loads(line)
                for line in (
                    factory_root / "jobs" / job_id / "events.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            tests_written = next(
                event for event in events if event.get("to") == "TESTS_WRITTEN"
            )
            self.assertEqual(
                tests_written["input_sha256"],
                hashlib.sha256(stored_manifest).hexdigest(),
            )
            self.assertEqual(
                json.loads(stored_manifest)["tests"][0]["sha256"],
                hashlib.sha256(
                    (
                        Path(output["job"]["worktree_path"])
                        / "tests"
                        / "test_widget.py"
                    ).read_bytes()
                ).hexdigest(),
            )

    def test_prepare_tests_retries_failed_baseline_red_without_unlocking_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _,
                env,
                job_id,
                manifest_path,
            ) = self._prepare_baseline_red_job(Path(temp_dir))
            env["FACTORY_TEST_RED_OUTCOME"] = "PASS"
            arguments = (
                "job",
                "prepare",
                job_id,
                "--stage",
                "tests",
                "--path",
                str(manifest_path),
                "--approve",
            )

            rejected = self._run_cli(factory_root, env, *arguments)

            self.assertEqual(rejected.returncode, 1)
            self.assertIn("assertion failures for every test", rejected.stderr)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["job"]["state"], "TESTS_WRITTEN")
            self.assertEqual(
                len(list((factory_root / "jobs" / job_id / "tests-red").iterdir())),
                1,
            )

            env.pop("FACTORY_TEST_RED_OUTCOME")
            retried = self._run_cli(factory_root, env, *arguments)

            self.assertEqual(retried.returncode, 0, retried.stderr)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["job"]["state"], "TESTS_RED_OK")
            self.assertEqual(
                len(list((factory_root / "jobs" / job_id / "tests-red").iterdir())),
                2,
            )

    def test_prepare_tests_requires_human_approval_without_changing_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _,
                env,
                job_id,
                manifest_path,
            ) = self._prepare_baseline_red_job(Path(temp_dir))
            events_path = factory_root / "jobs" / job_id / "events.jsonl"
            original_events = events_path.read_bytes()

            rejected = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "tests",
                "--path",
                str(manifest_path),
            )

            self.assertEqual(rejected.returncode, 2)
            self.assertIn("requires explicit --approve", rejected.stderr)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(json.loads(status.stdout)["job"]["state"], "SPEC_APPROVED")
            self.assertEqual(events_path.read_bytes(), original_events)
            self.assertFalse((factory_root / "jobs" / job_id / "tests.json").exists())

    def test_prepare_tests_rejects_mismatched_baseline_red_manifest_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _,
                env,
                job_id,
                manifest_path,
            ) = self._prepare_baseline_red_job(Path(temp_dir))
            env["FACTORY_TEST_BAD_MANIFEST_SHA"] = "1"

            rejected = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "tests",
                "--path",
                str(manifest_path),
                "--approve",
            )

            self.assertEqual(rejected.returncode, 1)
            self.assertIn("metadata does not match registered inputs", rejected.stderr)
            events = [
                json.loads(line)
                for line in (
                    factory_root / "jobs" / job_id / "events.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[-1]["to"], "TESTS_WRITTEN")

    def test_prepare_tests_rejects_path_outside_worktree_without_registration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _,
                env,
                job_id,
                manifest_path,
            ) = self._prepare_baseline_red_job(Path(temp_dir))
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "tests": [
                            {
                                "id": "tests.test_widget.test_acceptance",
                                "path": "../README.md",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            rejected = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "tests",
                "--path",
                str(manifest_path),
                "--approve",
            )

            self.assertEqual(rejected.returncode, 2)
            self.assertIn("safe repository-relative POSIX path", rejected.stderr)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(json.loads(status.stdout)["job"]["state"], "SPEC_APPROVED")
            self.assertFalse((factory_root / "jobs" / job_id / "tests.json").exists())
            self.assertFalse((factory_root / "jobs" / job_id / "tests-red").exists())

    def test_prepare_tests_rechecks_locked_files_after_baseline_red(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _,
                env,
                job_id,
                manifest_path,
            ) = self._prepare_baseline_red_job(Path(temp_dir))
            env["FACTORY_TEST_MUTATE_INPUT"] = "1"

            rejected = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "tests",
                "--path",
                str(manifest_path),
                "--approve",
            )

            self.assertEqual(rejected.returncode, 1)
            self.assertIn("registered tests changed during baseline-red check", rejected.stderr)
            events = [
                json.loads(line)
                for line in (
                    factory_root / "jobs" / job_id / "events.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[-1]["to"], "TESTS_WRITTEN")
            self.assertTrue(
                (factory_root / "jobs" / job_id / "tests-red").is_dir()
            )

    def test_status_rejects_modified_registered_test(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _,
                env,
                job_id,
                manifest_path,
            ) = self._prepare_baseline_red_job(Path(temp_dir))
            registered = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "tests",
                "--path",
                str(manifest_path),
                "--approve",
            )
            self.assertEqual(registered.returncode, 0, registered.stderr)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(status.returncode, 0, status.stderr)
            test_path = (
                Path(json.loads(status.stdout)["job"]["worktree_path"])
                / "tests"
                / "test_widget.py"
            )
            test_path.write_text("def test_acceptance():\n    assert True\n", encoding="utf-8")
            events_path = factory_root / "jobs" / job_id / "events.jsonl"
            original_events = events_path.read_bytes()

            rejected = self._run_cli(factory_root, env, "job", "status", job_id)

            self.assertEqual(rejected.returncode, 2)
            self.assertIn("registered test file hash mismatch", rejected.stderr)
            self.assertEqual(events_path.read_bytes(), original_events)

    def test_status_rejects_modified_approved_spec(self):
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
            spec_path = Path(temp_dir) / "approved-spec.md"
            spec_path.write_bytes(b"# Approved spec\n")
            prepared = self._run_cli(
                factory_root,
                env,
                "job",
                "prepare",
                job_id,
                "--stage",
                "spec",
                "--path",
                str(spec_path),
                "--approve",
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)

            events_path = factory_root / "jobs" / job_id / "events.jsonl"
            original_events = events_path.read_bytes()
            (factory_root / "jobs" / job_id / "spec.md").write_bytes(
                b"# Changed spec\n"
            )

            status = self._run_cli(factory_root, env, "job", "status", job_id)

            self.assertEqual(status.returncode, 2)
            self.assertIn("registered spec hash mismatch", status.stderr)
            self.assertEqual(events_path.read_bytes(), original_events)

    def test_worktree_attach_uses_existing_approved_local_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                target_repo,
                env,
                job_id,
                branch,
                branch_sha,
            ) = self._prepare_approved_worktree_job(Path(temp_dir))

            result = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                branch,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["job"]["state"], "SPEC_APPROVED")
            self.assertEqual(output["job"]["work_branch"], branch)
            self.assertEqual(output["job"]["work_branch_start_sha"], branch_sha)
            base_sha = subprocess.run(
                ["git", "rev-parse", "refs/remotes/origin/main"],
                cwd=target_repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(output["job"]["base_sha"], base_sha)
            self.assertNotEqual(base_sha, branch_sha)
            self.assertEqual(output["job"]["worktree_status"], "attached")
            worktree_path = Path(output["job"]["worktree_path"])
            self.assertEqual(
                worktree_path,
                factory_root / "worktrees" / "test-project" / job_id,
            )
            checked_out_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=worktree_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(checked_out_branch, branch)
            current_branch_sha = subprocess.run(
                ["git", "rev-parse", f"refs/heads/{branch}"],
                cwd=target_repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(current_branch_sha, branch_sha)

    def test_worktree_attach_requires_approved_spec(self):
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

            result = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                "feature/widget",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("requires SPEC_APPROVED", result.stderr)
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(json.loads(status.stdout)["job"]["state"], "DRAFT_SPEC")
            self.assertFalse((factory_root / "worktrees").exists())

    def test_worktree_attach_rejects_invalid_branch_and_occupied_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _,
                env,
                job_id,
                branch,
                _,
            ) = self._prepare_approved_worktree_job(Path(temp_dir))

            for requested_branch, expected_code, diagnostic in (
                ("../main", 2, "invalid local branch"),
                ("feature/missing", 1, "local branch does not exist"),
                ("main", 1, "already checked out"),
            ):
                with self.subTest(branch=requested_branch):
                    result = self._run_cli(
                        factory_root,
                        env,
                        "job",
                        "worktree",
                        "attach",
                        job_id,
                        "--branch",
                        requested_branch,
                    )
                    self.assertEqual(result.returncode, expected_code)
                    self.assertIn(diagnostic, result.stderr)

            worktree_path = factory_root / "worktrees" / "test-project" / job_id
            worktree_path.mkdir(parents=True)
            sentinel = worktree_path / "do-not-remove.txt"
            sentinel.write_text("preserve\n", encoding="utf-8")
            result = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                branch,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("worktree path already exists", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(
                json.loads(status.stdout)["job"]["worktree_status"], "not_attached"
            )

    def test_worktree_repair_retries_base_fetch_interrupted_during_attach(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                target_repo,
                env,
                job_id,
                branch,
                _,
            ) = self._prepare_approved_worktree_job(Path(temp_dir))
            remote_repo = target_repo.parent / "remote.git"
            cached_base_sha = subprocess.run(
                ["git", "rev-parse", "refs/heads/main"],
                cwd=target_repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/main", cached_base_sha],
                cwd=target_repo,
                check=True,
            )
            (target_repo / "remote-update.txt").write_text(
                "new base\n", encoding="utf-8"
            )
            subprocess.run(["git", "add", "remote-update.txt"], cwd=target_repo, check=True)
            subprocess.run(
                ["git", "commit", "--quiet", "-m", "advance remote base"],
                cwd=target_repo,
                check=True,
            )
            subprocess.run(
                ["git", "push", "--quiet", "origin", "main"],
                cwd=target_repo,
                check=True,
            )
            expected_base_sha = subprocess.run(
                ["git", "rev-parse", "refs/heads/main"],
                cwd=target_repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/main", cached_base_sha],
                cwd=target_repo,
                check=True,
            )
            subprocess.run(
                ["git", "remote", "set-url", "origin", str(remote_repo) + "-offline"],
                cwd=target_repo,
                check=True,
            )

            attached = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                branch,
            )

            self.assertEqual(attached.returncode, 1)
            interrupted = self._run_cli(factory_root, env, "job", "status", job_id)
            interrupted_job = json.loads(interrupted.stdout)["job"]
            self.assertEqual(interrupted_job["worktree_status"], "attaching")
            self.assertIsNone(interrupted_job["base_sha"])

            subprocess.run(
                ["git", "remote", "set-url", "origin", str(remote_repo)],
                cwd=target_repo,
                check=True,
            )
            repaired = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "repair",
                job_id,
            )

            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            job = json.loads(repaired.stdout)["job"]
            self.assertEqual(job["worktree_status"], "attached")
            self.assertEqual(job["base_sha"], expected_base_sha)
            self.assertNotEqual(job["base_sha"], cached_base_sha)
            self.assertTrue(Path(job["worktree_path"]).exists())

    def test_worktree_repair_reattaches_missing_worktree_at_current_branch_head(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                target_repo,
                env,
                job_id,
                branch,
                branch_sha,
            ) = self._prepare_approved_worktree_job(Path(temp_dir))
            attached = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                branch,
            )
            self.assertEqual(attached.returncode, 0, attached.stderr)
            worktree_path = Path(json.loads(attached.stdout)["job"]["worktree_path"])
            (worktree_path / "recovery.txt").write_text("committed\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "recovery.txt"], cwd=worktree_path, check=True
            )
            subprocess.run(
                ["git", "commit", "--quiet", "-m", "preserve branch work"],
                cwd=worktree_path,
                check=True,
            )
            expected_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=worktree_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "worktree", "remove", str(worktree_path)],
                cwd=target_repo,
                check=True,
            )
            cached_base_sha = subprocess.run(
                ["git", "rev-parse", "refs/remotes/origin/main"],
                cwd=target_repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (target_repo / "remote-update.txt").write_text("new base\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "remote-update.txt"], cwd=target_repo, check=True
            )
            subprocess.run(
                ["git", "commit", "--quiet", "-m", "advance remote base"],
                cwd=target_repo,
                check=True,
            )
            subprocess.run(
                ["git", "push", "--quiet", "origin", "main"],
                cwd=target_repo,
                check=True,
            )
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/main", cached_base_sha],
                cwd=target_repo,
                check=True,
            )

            repaired = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "repair",
                job_id,
            )

            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            job = json.loads(repaired.stdout)["job"]
            self.assertEqual(job["worktree_status"], "attached")
            self.assertEqual(job["work_branch_start_sha"], branch_sha)
            self.assertEqual(job["base_sha"], cached_base_sha)
            self.assertEqual(
                subprocess.run(
                    ["git", "rev-parse", "refs/remotes/origin/main"],
                    cwd=target_repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                cached_base_sha,
            )
            repaired_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=worktree_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(repaired_head, expected_head)
            second_repair = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "repair",
                job_id,
            )
            self.assertEqual(second_repair.returncode, 0, second_repair.stderr)
            self.assertEqual(json.loads(second_repair.stdout)["last_event"]["seq"], 6)

    def test_worktree_release_refuses_dirty_worktree_without_changing_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                target_repo,
                env,
                job_id,
                branch,
                _,
            ) = self._prepare_approved_worktree_job(Path(temp_dir))
            attached = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                branch,
            )
            worktree_path = Path(json.loads(attached.stdout)["job"]["worktree_path"])
            dirty_file = worktree_path / "uncommitted.txt"
            dirty_file.write_text("preserve\n", encoding="utf-8")

            released = self._run_cli(
                factory_root, env, "job", "worktree", "release", job_id
            )

            self.assertEqual(released.returncode, 1)
            self.assertIn("uncommitted changes", released.stderr)
            self.assertEqual(dirty_file.read_text(encoding="utf-8"), "preserve\n")
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(
                json.loads(status.stdout)["job"]["worktree_status"], "attached"
            )
            self.assertTrue(worktree_path.exists())

    def test_worktree_repair_completes_interrupted_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                target_repo,
                env,
                job_id,
                branch,
                branch_sha,
            ) = self._prepare_approved_worktree_job(Path(temp_dir))
            attached = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                branch,
            )
            worktree_path = Path(json.loads(attached.stdout)["job"]["worktree_path"])
            current = job_service.read_job(factory_root, job_id)
            job_service._record_worktree_event(
                factory_root / "jobs" / job_id,
                current,
                "WORKTREE_RELEASE_STARTED",
                "Simulate interrupted release before Git removal",
                {"worktree_status": "releasing"},
            )

            repaired = self._run_cli(
                factory_root, env, "job", "worktree", "repair", job_id
            )

            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            output = json.loads(repaired.stdout)
            self.assertEqual(output["job"]["worktree_status"], "released")
            self.assertEqual(output["last_event"]["type"], "WORKTREE_RELEASED")
            self.assertFalse(worktree_path.exists())
            self.assertEqual(
                subprocess.run(
                    ["git", "rev-parse", f"refs/heads/{branch}"],
                    cwd=target_repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                branch_sha,
            )

    def test_worktree_repair_finalizes_release_after_git_removal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                target_repo,
                env,
                job_id,
                branch,
                branch_sha,
            ) = self._prepare_approved_worktree_job(Path(temp_dir))
            attached = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                branch,
            )
            worktree_path = Path(json.loads(attached.stdout)["job"]["worktree_path"])
            current = job_service.read_job(factory_root, job_id)
            job_service._record_worktree_event(
                factory_root / "jobs" / job_id,
                current,
                "WORKTREE_RELEASE_STARTED",
                "Simulate interrupted release after Git removal",
                {"worktree_status": "releasing"},
            )
            subprocess.run(
                ["git", "worktree", "remove", str(worktree_path)],
                cwd=target_repo,
                check=True,
            )

            repaired = self._run_cli(
                factory_root, env, "job", "worktree", "repair", job_id
            )

            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            output = json.loads(repaired.stdout)
            self.assertEqual(output["job"]["worktree_status"], "released")
            self.assertEqual(output["last_event"]["type"], "WORKTREE_RELEASED")
            self.assertFalse(worktree_path.exists())
            self.assertEqual(
                subprocess.run(
                    ["git", "rev-parse", f"refs/heads/{branch}"],
                    cwd=target_repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                branch_sha,
            )

    def test_worktree_repair_rejects_missing_but_registered_release_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                _,
                env,
                job_id,
                branch,
                _,
            ) = self._prepare_approved_worktree_job(Path(temp_dir))
            attached = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                branch,
            )
            worktree_path = Path(json.loads(attached.stdout)["job"]["worktree_path"])
            current = job_service.read_job(factory_root, job_id)
            job_service._record_worktree_event(
                factory_root / "jobs" / job_id,
                current,
                "WORKTREE_RELEASE_STARTED",
                "Simulate interrupted release",
                {"worktree_status": "releasing"},
            )
            preserved_path = worktree_path.with_name("moved-worktree")
            worktree_path.rename(preserved_path)

            repaired = self._run_cli(
                factory_root, env, "job", "worktree", "repair", job_id
            )

            self.assertEqual(repaired.returncode, 1)
            self.assertIn("remains registered", repaired.stderr)
            self.assertEqual(
                (preserved_path / "feature.txt").read_text(encoding="utf-8"),
                "feature\n",
            )
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(
                json.loads(status.stdout)["job"]["worktree_status"], "releasing"
            )

    def test_worktree_repair_preserves_unregistered_path_collision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                target_repo,
                env,
                job_id,
                branch,
                _,
            ) = self._prepare_approved_worktree_job(Path(temp_dir))
            attached = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                branch,
            )
            worktree_path = Path(json.loads(attached.stdout)["job"]["worktree_path"])
            subprocess.run(
                ["git", "worktree", "remove", str(worktree_path)],
                cwd=target_repo,
                check=True,
            )
            worktree_path.mkdir()
            sentinel = worktree_path / "preserve.txt"
            sentinel.write_text("not factory-owned\n", encoding="utf-8")

            repaired = self._run_cli(
                factory_root, env, "job", "worktree", "repair", job_id
            )

            self.assertEqual(repaired.returncode, 1)
            self.assertIn("worktree ownership mismatch", repaired.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "not factory-owned\n")
            status = self._run_cli(factory_root, env, "job", "status", job_id)
            self.assertEqual(
                json.loads(status.stdout)["job"]["worktree_status"], "attached"
            )

    def test_worktree_release_removes_clean_worktree_and_keeps_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (
                factory_root,
                target_repo,
                env,
                job_id,
                branch,
                branch_sha,
            ) = self._prepare_approved_worktree_job(Path(temp_dir))
            attached = self._run_cli(
                factory_root,
                env,
                "job",
                "worktree",
                "attach",
                job_id,
                "--branch",
                branch,
            )
            worktree_path = Path(json.loads(attached.stdout)["job"]["worktree_path"])

            released = self._run_cli(
                factory_root, env, "job", "worktree", "release", job_id
            )

            self.assertEqual(released.returncode, 0, released.stderr)
            output = json.loads(released.stdout)
            self.assertEqual(output["job"]["worktree_status"], "released")
            self.assertEqual(output["last_event"]["type"], "WORKTREE_RELEASED")
            self.assertFalse(worktree_path.exists())
            self.assertEqual(
                subprocess.run(
                    ["git", "rev-parse", f"refs/heads/{branch}"],
                    cwd=target_repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                branch_sha,
            )

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
