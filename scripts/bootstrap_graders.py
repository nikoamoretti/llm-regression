#!/usr/bin/env python3
"""Copy shared grader entrypoints into each task and generate gold patches."""

from __future__ import annotations

import shutil
from pathlib import Path

from runner.make_gold import write_patch

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "tasks" / "_shared"

PYTHON_TASKS = ["BUG-PY-01", "REF-PY-01", "DATA-PY-01", "BUILD-01", "LONG-01"]
NODE_TASKS = ["BUG-TS-01", "FEAT-TS-01", "SPEC-TS-01"]
GO_TASKS = ["STATE-GO-01"]
RUST_TASKS = ["NAV-RUST-01"]


def copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    dest.chmod(0o755)


def main() -> None:
    for task in PYTHON_TASKS:
        copy(SHARED / "grade.py", ROOT / "tasks" / task / "grader" / "grade.py")
        copy(SHARED / "run.sh", ROOT / "tasks" / task / "grader" / "run.sh")
        hidden = ROOT / "tasks" / task / "grader" / "hidden_tests"
        if hidden.exists():
            (hidden / "__init__.py").touch()
            for sub in hidden.iterdir():
                if sub.is_dir():
                    (sub / "__init__.py").touch()
    for task in NODE_TASKS:
        copy(SHARED / "grade.mjs", ROOT / "tasks" / task / "grader" / "grade.mjs")
        copy(SHARED / "run_node.sh", ROOT / "tasks" / task / "grader" / "run.sh")
    for task in GO_TASKS:
        copy(SHARED / "grade_go.py", ROOT / "tasks" / task / "grader" / "grade.py")
        copy(SHARED / "run.sh", ROOT / "tasks" / task / "grader" / "run.sh")
    for task in RUST_TASKS:
        copy(SHARED / "grade_rust.py", ROOT / "tasks" / task / "grader" / "grade.py")
        copy(SHARED / "run.sh", ROOT / "tasks" / task / "grader" / "run.sh")
    for task_dir in sorted((ROOT / "tasks").iterdir()):
        if task_dir.name.startswith("_"):
            continue
        if (task_dir / "gold" / "overlay").exists():
            path = write_patch(task_dir)
            print("gold", path)


if __name__ == "__main__":
    main()
