"""Generate private gold.patch from gold_overlay files."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from runner.patch import init_workspace_git
from runner.tasks import discover_tasks


def write_patch(task) -> Path:
    overlay = task.grader_path / "gold_overlay"
    if not overlay.exists():
        raise FileNotFoundError(f"{task.id} has no gold_overlay")
    fixture = task.fixture_path
    tmp = Path(tempfile.mkdtemp(prefix="gold-"))
    try:
        workspace = tmp / "workspace"
        shutil.copytree(fixture, workspace)
        init_workspace_git(workspace)
        for source in overlay.rglob("*"):
            if source.is_file():
                dest = workspace / source.relative_to(overlay)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, capture_output=True)
        proc = subprocess.run(
            ["git", "diff", "--cached", "--binary"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        patch_path = task.gold_patch
        patch_path.write_text(proc.stdout, encoding="utf-8")
        return patch_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="tasks")
    parser.add_argument("--task")
    args = parser.parse_args(argv)
    tasks = discover_tasks(Path(args.tasks))
    if args.task:
        tasks = [task for task in tasks if task.id == args.task]
    for task in tasks:
        path = write_patch(task)
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
