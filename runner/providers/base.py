"""Provider contract for Codex-product and Responses-control tracks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ProviderResult:
    requested_model: str
    verified_model: str | None
    requested_effort: str
    verified_effort: str | None
    auth_surface: str
    thread_id: str | None = None
    final_text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    raw_jsonl: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    retry_count: int = 0
    latency_ms: float | None = None
    wall_ms: float | None = None
    command: list[str] = field(default_factory=list)
    invalid_configuration: bool = False
    infrastructure: bool = False
    error_code: str | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    name: str
    track: str

    def run_attempt(
        self,
        *,
        prompt: str,
        workspace: Path,
        model: str,
        effort: str,
        timeout_seconds: int,
    ) -> ProviderResult: ...
