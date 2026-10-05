"""Validate every task manifest before a live run."""

from __future__ import annotations

import argparse
from pathlib import Path

from runner.tasks import discover_tasks, validate_task


def validate_all(tasks_root: Path, check_hashes: bool = True, require_graders: bool = True) -> list[str]:
    errors: list[str] = []
    tasks = discover_tasks(tasks_root)
    if not tasks:
        errors.append(f"No tasks found in {tasks_root}")
    ids = [task.id for task in tasks]
    if len(ids) != len(set(ids)):
        errors.append(f"Duplicate task ids: {ids}")
    for task in tasks:
        errors.extend(validate_task(task, check_hashes=check_hashes, require_graders=require_graders))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="tasks")
    parser.add_argument("--skip-hashes", action="store_true")
    parser.add_argument("--allow-missing-graders", action="store_true",
                        help="public CI: validate tasks whose hidden graders are not checked out")
    args = parser.parse_args(argv)
    errors = validate_all(Path(args.tasks), check_hashes=not args.skip_hashes,
                          require_graders=not args.allow_missing_graders)
    if args.allow_missing_graders:
        hidden = sorted(task.id for task in discover_tasks(Path(args.tasks)) if not task.has_graders)
        if hidden:
            print(f"Not checked here, graders are hidden: {', '.join(hidden)}")
    if errors:
        print("Task validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("All task manifests are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
