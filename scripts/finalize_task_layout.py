#!/usr/bin/env python3
"""Reorganize tasks and add immutable v1 metadata plus negative patches."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.reorganize_tasks import main as reorganize

FAMILY = {
    "BUG-PY-01": "bugfix",
    "BUG-TS-01": "bugfix",
    "REF-PY-01": "refactor",
    "FEAT-TS-01": "feature",
    "DATA-PY-01": "data",
    "SPEC-TS-01": "spec",
    "BUILD-01": "build",
    "STATE-GO-01": "state",
    "NAV-RUST-01": "navigation",
    "LONG-01": "long-context",
}


def first_source(fixture: Path) -> Path | None:
    for pattern in ("**/*.py", "**/*.ts", "**/*.go", "**/*.rs"):
        matches = [path for path in fixture.glob(pattern) if "tests" not in path.parts and path.name != "generate_corpus.py"]
        if matches:
            return sorted(matches)[0]
    return None


def comment_for(path: Path) -> str:
    if path.suffix in {".py", ".ts"}:
        return "# NEGATIVE_NOOP: intentionally does not fix the task\n"
    if path.suffix == ".go":
        return "// NEGATIVE_NOOP: intentionally does not fix the task\n"
    if path.suffix == ".rs":
        return "// NEGATIVE_NOOP: intentionally does not fix the task\n"
    return "# NEGATIVE_NOOP\n"


def write_patch(workspace: Path, relative: Path) -> str:
    proc = subprocess.run(
        ["git", "diff", "--", str(relative)],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def make_negatives(task_id: str, fixture: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    import shutil
    import tempfile

    from runner.patch import init_workspace_git

    tmp = Path(tempfile.mkdtemp(prefix="neg-"))
    workspace = tmp / "workspace"
    shutil.copytree(fixture, workspace)
    init_workspace_git(workspace)
    source = first_source(workspace)
    if source is not None:
        rel = source.relative_to(workspace)
        original = source.read_text(encoding="utf-8")
        source.write_text(comment_for(source) + original, encoding="utf-8")
        (dest / "noop.patch").write_text(write_patch(workspace, rel), encoding="utf-8")
        source.write_text(original, encoding="utf-8")
    else:
        (dest / "noop.patch").write_text("", encoding="utf-8")

    test_files = list(workspace.glob("**/test_*.py")) + list(workspace.glob("**/*.test.ts"))
    if test_files:
        target = test_files[0]
        rel = target.relative_to(workspace)
        target.write_text("// deleted by negative patch\n", encoding="utf-8")
        (dest / "delete_tests.patch").write_text(write_patch(workspace, rel), encoding="utf-8")
        # restore for next patches
        shutil.rmtree(workspace)
        shutil.copytree(fixture, workspace)
        init_workspace_git(workspace)

    # Disable a local test command if present.
    pkg = workspace / "package.json"
    if pkg.exists():
        text = pkg.read_text(encoding="utf-8")
        pkg.write_text(text.replace('"test"', '"test_disabled_by_negative"'), encoding="utf-8")
        (dest / "disable_test_command.patch").write_text(write_patch(workspace, Path("package.json")), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)


def update_manifest(path: Path, task_id: str, grader_sha: str, fixture_sha: str, prompt_sha: str) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["version"] = "v1"
    data["family"] = FAMILY.get(task_id, data.get("category", "unknown"))
    data["languages"] = [data.get("language") or "unknown"]
    data["task_review_status"] = "pending_human_review"
    data["task_reviewed_at"] = None
    data["task_review_notes"] = (
        "Automated structural review only. A human must review whether more than "
        "one reasonable implementation should pass before CORE promotion claims."
    )
    data["grading_protocol"] = "hidden_final_state"
    data["public_test_policy"] = "fixture_visible_only"
    data["network_policy"] = "disabled"
    data["prohibited_modifications"] = list(
        data.get("permissions", {}).get("forbidden_paths") or ["grader/", "task.yaml"]
    )
    data.setdefault("fixture", {})["sha256"] = fixture_sha
    data.setdefault("grader", {})["sha256"] = grader_sha
    data["prompt_sha256"] = prompt_sha
    data["private_grader_path"] = f"private_graders/{task_id}/v1"
    env = data.setdefault("environment", {})
    # Historical one-time rewrite from the original targeting error.
    # See docs/TARGETING_CORRECTION.md. Not a production runtime path.
    if isinstance(env.get("image"), str) and "grok-regression" in env["image"]:
        env["image"] = env["image"].replace("grok-regression", "sol-regression")
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main() -> None:
    reorganize()
    from runner.hash_tree import hash_file, hash_tree

    tasks_root = ROOT / "tasks"
    for task_id, family in FAMILY.items():
        public = tasks_root / task_id / "v1"
        priv = ROOT / "private_graders" / task_id / "v1"
        if not (public / "task.yaml").exists():
            raise SystemExit(f"missing reorganized task {public}")
        make_negatives(task_id, public / "fixture", priv / "negatives")
        fixture_sha = hash_tree(public / "fixture")
        grader_sha = hash_tree(priv)
        prompt_sha = hash_file(public / "prompt.md")
        update_manifest(public / "task.yaml", task_id, grader_sha, fixture_sha, prompt_sha)
        print(f"finalized {task_id} family={family}")


if __name__ == "__main__":
    main()
