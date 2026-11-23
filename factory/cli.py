import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .gate import evaluate_gate
from .job import (
    ConfigurationError,
    FactoryError,
    attach_worktree,
    create_job,
    list_jobs,
    register_spec,
    register_tests,
    read_job,
    release_worktree,
    repair_worktree,
    resume_job,
    transition_job,
)


def _parser():
    parser = argparse.ArgumentParser(prog="factory")
    commands = parser.add_subparsers(dest="command", required=True)
    job = commands.add_parser("job")
    job_commands = job.add_subparsers(dest="job_command", required=True)
    new = job_commands.add_parser("new")
    new.add_argument("--project", required=True, type=Path)
    new.add_argument("--title", required=True)
    prepare = job_commands.add_parser("prepare")
    prepare.add_argument("job_id")
    prepare.add_argument("--stage", choices=("spec", "tests"), required=True)
    prepare.add_argument("--path", required=True, type=Path)
    prepare.add_argument("--approve", action="store_true")
    status = job_commands.add_parser("status")
    status.add_argument("job_id")
    transition = job_commands.add_parser("transition")
    transition.add_argument("job_id")
    transition.add_argument("target", choices=("NEEDS_HUMAN", "FAILED"))
    transition.add_argument("--reason", required=True)
    resume = job_commands.add_parser("resume")
    resume.add_argument("job_id")
    resume.add_argument("--reason", required=True)
    gate = job_commands.add_parser("gate")
    gate.add_argument("job_id")
    gate.add_argument("--base", required=True)
    gate.add_argument("--mode", choices=("fast", "full"), required=True)
    worktree = job_commands.add_parser("worktree")
    worktree_commands = worktree.add_subparsers(
        dest="worktree_command", required=True
    )
    attach = worktree_commands.add_parser("attach")
    attach.add_argument("job_id")
    attach.add_argument("--branch", required=True)
    repair = worktree_commands.add_parser("repair")
    repair.add_argument("job_id")
    release = worktree_commands.add_parser("release")
    release.add_argument("job_id")
    job_commands.add_parser("list")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.job_command == "new":
            result = create_job(Path.cwd(), args.project, args.title)
        elif args.job_command == "prepare":
            if args.stage == "spec":
                result = register_spec(
                    Path.cwd(), args.job_id, args.path, args.approve
                )
            else:
                result = register_tests(
                    Path.cwd(), args.job_id, args.path, args.approve
                )
        elif args.job_command == "status":
            result = read_job(Path.cwd(), args.job_id)
        elif args.job_command == "transition":
            result = transition_job(Path.cwd(), args.job_id, args.target, args.reason)
        elif args.job_command == "resume":
            result = resume_job(Path.cwd(), args.job_id, args.reason)
        elif args.job_command == "gate":
            result = evaluate_gate(
                Path.cwd(), args.job_id, args.base, args.mode
            )
            print(
                json.dumps(
                    {
                        "verdict": result.verdict,
                        "report_path": str(result.report_path),
                        "report": result.report,
                    },
                    sort_keys=True,
                )
            )
            return 0 if result.verdict == "PASS" else 1
        elif args.job_command == "worktree":
            if args.worktree_command == "attach":
                result = attach_worktree(Path.cwd(), args.job_id, args.branch)
            elif args.worktree_command == "repair":
                result = repair_worktree(Path.cwd(), args.job_id)
            else:
                result = release_worktree(Path.cwd(), args.job_id)
        else:
            result = list_jobs(Path.cwd())
    except (ConfigurationError, ValidationError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except FactoryError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError as error:
        print(str(error), file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0
