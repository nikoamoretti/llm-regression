"""Agentic track: six local tools, sequential execution, versioned protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runner.agent import run_agent
from runner.providers.base import ProviderResult
from runner.protocols import TOOL_PROTOCOL_VERSION, tool_protocol_version


def run_agentic(
    *,
    provider: Any,
    prompt: str,
    workspace: Path,
    model: str,
    effort: str,
    timeout_seconds: int,
    max_tool_calls: int = 80,
    writable_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
) -> ProviderResult:
    result = run_agent(
        provider=provider,
        prompt=prompt,
        workspace=workspace,
        model=model,
        effort=effort,
        timeout_seconds=timeout_seconds,
        max_tool_calls=max_tool_calls,
        writable_paths=writable_paths,
        forbidden_paths=forbidden_paths,
    )
    result.metadata["track"] = "agentic"
    result.metadata["tool_protocol_version"] = tool_protocol_version() or TOOL_PROTOCOL_VERSION
    return result
