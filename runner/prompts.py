"""Frozen system prompts for model-only and agentic tracks."""

from __future__ import annotations

MODEL_ONLY_SYSTEM = """You are modifying the repository shown below.

Solve the user task completely.

Rules:
- Do not modify files outside the allowed paths.
- Preserve unrelated behavior.
- Do not invent external dependencies.
- Return only a unified diff.
- Do not wrap the diff in Markdown.
"""

AGENTIC_SYSTEM = """You are a coding agent working in a frozen local workspace.

Use only the provided tools to inspect and edit the repository.
Do not access the network. Do not modify grader files or hidden tests.
Solve the task completely, then stop calling tools.
"""


def model_only_prompt(task_prompt: str, repository_snapshot: str) -> str:
    return (
        f"{MODEL_ONLY_SYSTEM}\n"
        f"TASK:\n{task_prompt.strip()}\n\n"
        f"REPOSITORY:\n{repository_snapshot}"
    )


def snapshot_repository(workspace, max_file_bytes: int = 200_000) -> str:
    parts: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace).as_posix()
        if rel.startswith(".git/") or "/.git/" in rel:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            parts.append(f"--- {rel} (binary, skipped) ---")
            continue
        if len(text.encode("utf-8")) > max_file_bytes:
            text = text[:max_file_bytes] + "\n... [truncated] ...\n"
        parts.append(f"--- {rel} ---\n{text}")
    return "\n\n".join(parts)
