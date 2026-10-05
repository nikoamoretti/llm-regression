"""Unified-diff extraction and application for the model-only track."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

DIFF_FENCE = re.compile(r"```(?:diff|patch|udiff)?\n(.*?)```", re.DOTALL)
UNIFIED_HEADER = re.compile(r"(?m)^(?:--- |diff --git |Index: )")


class PatchError(Exception):
    pass


JSON_FENCE = re.compile(r"```(?:json)?\n(.*?)```", re.DOTALL)


def extract_structured_patch(text: str) -> str:
    """Accept JSON {patch, summary, ...} or a raw/fenced unified diff."""
    if not text or not text.strip():
        raise PatchError("Model returned empty output; expected a patch")
    stripped = text.strip()
    candidates = [stripped, *[block.strip() for block in JSON_FENCE.findall(stripped)]]
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "patch" in payload:
            patch = payload.get("patch") or ""
            if not str(patch).strip():
                return ""
            return extract_unified_diff(str(patch))
    return extract_unified_diff(stripped)


def extract_unified_diff(text: str) -> str:
    if not text or not text.strip():
        raise PatchError("Model returned empty output; expected a unified diff")

    stripped = text.strip()
    fenced = DIFF_FENCE.findall(stripped)
    candidates = [block.strip() for block in fenced] + [stripped]

    for candidate in candidates:
        if UNIFIED_HEADER.search(candidate):
            return _normalize_diff(candidate)

    raise PatchError("Could not find a unified diff in the model output")


def _normalize_diff(diff: str) -> str:
    lines = diff.replace("\r\n", "\n").split("\n")
    while lines and not lines[0].startswith(("--- ", "diff --git ", "Index: ")):
        lines.pop(0)
    if not lines:
        raise PatchError("Diff header missing after normalization")
    if not lines[-1].endswith("\n") and lines[-1] != "":
        return "\n".join(lines) + "\n"
    return "\n".join(lines) if lines[-1] == "" else "\n".join(lines) + "\n"


def git_apply(workspace: Path, diff_text: str, check_only: bool = False) -> None:
    workspace = workspace.resolve()
    args = ["git", "apply", "--whitespace=nowarn"]
    if check_only:
        args.append("--check")
    proc = subprocess.run(
        args,
        cwd=workspace,
        input=diff_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        # Three-way apply can recover some offset hunks without changing semantics.
        retry = subprocess.run(
            ["git", "apply", "--3way", "--whitespace=nowarn"] + (["--check"] if check_only else []),
            cwd=workspace,
            input=diff_text,
            text=True,
            capture_output=True,
            check=False,
        )
        if retry.returncode != 0:
            raise PatchError(
                "git apply failed\n"
                f"stdout={proc.stdout}\nstderr={proc.stderr}\n"
                f"retry_stderr={retry.stderr}"
            )


def init_workspace_git(workspace: Path) -> None:
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=regression@local",
            "-c",
            "user.name=regression",
            "commit",
            "-m",
            "fixture",
            "--allow-empty",
        ],
        cwd=workspace,
        check=True,
        capture_output=True,
    )


def workspace_diff(workspace: Path) -> str:
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, capture_output=True)
    proc = subprocess.run(
        ["git", "diff", "--cached"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout
