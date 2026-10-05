"""Keep private graders, gold patches, and prior results out of the agent namespace."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SECRET_NAMES = {
    "gold.patch",
    "gold_overlay",
    "hidden_tests",
    "negatives",
    "private_graders",
    "baseline_traces",
    "expected_outputs",
}


def workspace_leak_report(workspace: Path, grader_root: Path | None = None) -> list[str]:
    leaks: list[str] = []
    workspace = workspace.resolve()
    for path in workspace.rglob("*"):
        if not path.exists():
            continue
        parts = {part.lower() for part in path.relative_to(workspace).parts}
        if parts & SECRET_NAMES:
            leaks.append(f"secret path materialized in workspace: {path.relative_to(workspace)}")
        if path.name in {"gold.patch", "solution.patch"}:
            leaks.append(f"gold patch present in workspace: {path}")
    if grader_root is not None:
        grader_root = grader_root.resolve()
        if grader_root == workspace or workspace in grader_root.parents:
            leaks.append("grader root is inside the agent workspace")
        if grader_root in workspace.parents:
            # Walking up from /tmp will not hit the repo; this only fires if
            # the workspace was created under the harness tree.
            leaks.append("agent workspace is a descendant of the private grader tree")
    return leaks


def env_leak_report(env: dict[str, str] | None = None) -> list[str]:
    env = env or dict(os.environ)
    leaks = []
    for key in ("GRADER", "GOLD_PATCH", "PRIVATE_GRADERS", "HARNESS_ROOT"):
        if key in env:
            leaks.append(f"environment leaks {key}={env[key]}")
    return leaks


def assert_agent_namespace_clean(workspace: Path, grader_root: Path | None = None) -> None:
    leaks = workspace_leak_report(workspace, grader_root) + env_leak_report()
    if leaks:
        raise RuntimeError("agent namespace is not isolated: " + "; ".join(leaks))


def probe_from_workspace(workspace: Path, candidates: list[Path]) -> dict[str, Any]:
    """Enumerate candidate secret paths the way a confused agent might."""
    visible = []
    for candidate in candidates:
        try:
            resolved = candidate if candidate.is_absolute() else (workspace / candidate)
            if resolved.exists():
                visible.append(str(resolved))
        except OSError:
            continue
    return {"visible": visible, "cwd": str(workspace)}
