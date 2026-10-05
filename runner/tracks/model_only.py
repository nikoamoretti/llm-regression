"""Model-only track: frozen snapshot, no tools, patch-only output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from runner.hash_tree import hash_text
from runner.patch import PatchError, extract_structured_patch, git_apply
from runner.providers.base import ProviderResult
from runner.protocols import prompt_protocol_version


def repository_snapshot(workspace: Path) -> str:
    files = sorted(
        path
        for path in workspace.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )
    parts = ["<REPOSITORY>"]
    for path in files:
        rel = path.relative_to(workspace).as_posix()
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = "<binary omitted>"
        parts.append(f"FILE: {rel}\nSHA256: {digest}\n<<<\n{text}\n>>>")
    parts.append("</REPOSITORY>")
    return "\n\n".join(parts)


def model_only_prompt(task_prompt: str, snapshot: str) -> str:
    return (
        "<TASK>\n"
        f"{task_prompt.rstrip()}\n"
        "</TASK>\n\n"
        f"{snapshot}\n\n"
        "No tools are available. Return a JSON object only:\n"
        '{"patch": "<unified diff>", "summary": "<short>", "files_expected_to_change": ["..."]}\n'
        "The summary is informational. Only the applied patch is graded.\n"
    )


def run_model_only(
    *,
    provider: Any,
    prompt: str,
    workspace: Path,
    model: str,
    effort: str,
    timeout_seconds: int,
) -> ProviderResult:
    snapshot = repository_snapshot(workspace)
    user_input = model_only_prompt(prompt, snapshot)
    response = provider.create_response(
        input_messages=user_input,
        model=model,
        effort=effort,
        timeout_seconds=timeout_seconds,
        tools=None,
        instructions="Return only the required JSON object. Do not call tools.",
    )
    result = ProviderResult(
        requested_model=model,
        verified_model=response.returned_model,
        requested_effort=effort,
        verified_effort=response.verified_effort,
        auth_surface="api_key",
        thread_id=response.response_id,
        final_text=response.output_text,
        usage=response.usage,
        events=[{"type": "responses.completed", "response": response.raw_response}],
        raw_jsonl=response.raw_text or json.dumps(response.raw_response, default=str),
        stdout=response.output_text,
        retry_count=response.retry_count,
        latency_ms=response.latency_ms,
        command=[getattr(provider, "name", "responses"), model, effort, "model_only"],
        invalid_configuration=response.invalid_configuration,
        infrastructure=response.infrastructure,
        error_code=response.error_code,
        error=response.error,
        metadata={
            "track": "model_only",
            "prompt_protocol_version": prompt_protocol_version(),
            "snapshot_sha256": hash_text(snapshot),
            "request_hash": getattr(response, "request_hash", ""),
            "raw_headers": getattr(response, "raw_headers", {}),
        },
    )
    if result.infrastructure or result.invalid_configuration or result.error_code:
        return result
    try:
        diff = extract_structured_patch(response.output_text)
        if diff.strip():
            git_apply(workspace, diff)
            result.metadata["applied_patch"] = True
        else:
            result.metadata["applied_patch"] = False
    except PatchError as exc:
        result.error_code = "invalid_patch"
        result.error = {"message": str(exc)}
        result.metadata["applied_patch"] = False
    return result
