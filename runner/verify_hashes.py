"""Verify or rewrite frozen fixture/grader hashes in task.yaml files."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from runner.tasks import GRADERS_HINT, discover_tasks


def verify_or_write(tasks_root: Path, write: bool = False, require_graders: bool = True) -> list[str]:
    errors = []
    for task in discover_tasks(tasks_root):
        if not task.has_graders:
            if require_graders:
                errors.append(f"{task.id}: no graders at {task.grader_path}; {GRADERS_HINT}")
            else:
                print(f"skip {task.id}: graders are hidden")
            continue
        computed = task.computed_hashes()
        manifest_path = task.root / "task.yaml"
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        data.setdefault("fixture", {})
        data.setdefault("grader", {})
        data["prompt_sha256"] = computed["prompt_sha256"]
        changed = False
        if write:
            data["fixture"]["sha256"] = computed["fixture_sha256"]
            data["grader"]["sha256"] = computed["grader_sha256"]
            manifest_path.write_text(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            print(f"wrote hashes for {task.id}")
            changed = True
        expected_f = data.get("fixture", {}).get("sha256")
        expected_g = data.get("grader", {}).get("sha256")
        if expected_f not in {None, "REPLACE_AFTER_FREEZE"} and expected_f != computed["fixture_sha256"]:
            errors.append(f"{task.id} fixture {computed['fixture_sha256']} != {expected_f}")
        if expected_g not in {None, "REPLACE_AFTER_FREEZE"} and expected_g != computed["grader_sha256"]:
            errors.append(f"{task.id} grader {computed['grader_sha256']} != {expected_g}")
        if not changed and not errors:
            print(f"ok {task.id} fixture={computed['fixture_sha256'][:12]} grader={computed['grader_sha256'][:12]}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="tasks")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--allow-missing-graders", action="store_true",
                        help="public CI: skip tasks whose hidden graders are not checked out")
    args = parser.parse_args(argv)
    errors = verify_or_write(Path(args.tasks), write=args.write, require_graders=not args.allow_missing_graders)
    if errors:
        for error in errors:
            print(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
