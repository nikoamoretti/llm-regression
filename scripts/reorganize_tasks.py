#!/usr/bin/env python3
"""Move canary tasks into versioned public/private trees."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tasks"
PRIVATE = ROOT / "private_graders"
IDS = [
    "BUG-PY-01",
    "BUG-TS-01",
    "REF-PY-01",
    "FEAT-TS-01",
    "DATA-PY-01",
    "SPEC-TS-01",
    "BUILD-01",
    "STATE-GO-01",
    "NAV-RUST-01",
    "LONG-01",
]


def move_task(task_id: str) -> None:
    src = TASKS / task_id
    if not (src / "task.yaml").exists():
        print(f"skip {task_id}: already reorganized or missing")
        return
    dest = TASKS / task_id / "v1"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("task.yaml", "prompt.md", "fixture"):
        item = src / name
        if item.exists():
            shutil.move(str(item), dest / name)
    priv = PRIVATE / task_id / "v1"
    priv.mkdir(parents=True, exist_ok=True)
    if (src / "grader").exists():
        # Flatten into v1/ rather than creating v1/grader/.
        grader_src = src / "grader"
        for item in grader_src.iterdir():
            shutil.move(str(item), priv / item.name)
        grader_src.rmdir()
    gold = src / "gold" / "solution.patch"
    if gold.exists():
        shutil.move(str(gold), priv / "gold.patch")
    overlay = src / "gold" / "overlay"
    if overlay.exists():
        shutil.move(str(overlay), priv / "gold_overlay")
    gold_dir = src / "gold"
    if gold_dir.exists():
        shutil.rmtree(gold_dir)
    negatives = priv / "negatives"
    negatives.mkdir(exist_ok=True)
    print(f"moved {task_id}")


def main() -> None:
    PRIVATE.mkdir(exist_ok=True)
    for task_id in IDS:
        move_task(task_id)


if __name__ == "__main__":
    main()
