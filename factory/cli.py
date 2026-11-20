import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .job import ConfigurationError, FactoryError, create_job, list_jobs, read_job


def _parser():
    parser = argparse.ArgumentParser(prog="factory")
    commands = parser.add_subparsers(dest="command", required=True)
    job = commands.add_parser("job")
    job_commands = job.add_subparsers(dest="job_command", required=True)
    new = job_commands.add_parser("new")
    new.add_argument("--project", required=True, type=Path)
    new.add_argument("--title", required=True)
    status = job_commands.add_parser("status")
    status.add_argument("job_id")
    job_commands.add_parser("list")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.job_command == "new":
            result = create_job(Path.cwd(), args.project, args.title)
        elif args.job_command == "status":
            result = read_job(Path.cwd(), args.job_id)
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
