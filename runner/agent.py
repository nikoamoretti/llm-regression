"""Frozen local coding tools for the agentic evaluation track."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from runner.patch import PatchError, git_apply

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "list_files",
        "description": "List files under a repository directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
            },
        },
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a UTF-8 text file inside the repository.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1, "default": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        },
    },
    {
        "type": "function",
        "name": "search_text",
        "description": "Search for a literal string in repository text files.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string", "default": "."},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "write_file",
        "description": "Replace a UTF-8 repository file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "type": "function",
        "name": "apply_patch",
        "description": "Apply a unified diff to the repository.",
        "parameters": {
            "type": "object",
            "properties": {
                "diff": {"type": "string"},
            },
            "required": ["diff"],
        },
    },
    {
        "type": "function",
        "name": "run_command",
        "description": "Run an allowed repository command. No network access is available.",
        "parameters": {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                }
            },
            "required": ["argv"],
        },
    },
]

ALLOWED_COMMANDS = {
    "pytest",
    "python",
    "python3",
    "npm",
    "npx",
    "node",
    "pnpm",
    "go",
    "cargo",
    "make",
    "git",
}

MAX_TOOL_OUTPUT_CHARS = 50_000


class ToolWorkspace:
    def __init__(
        self,
        workspace: Path,
        writable_paths: list[str] | None = None,
        forbidden_paths: list[str] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.writable_paths = writable_paths or ["."]
        self.forbidden_paths = forbidden_paths or ["grader/", "task.yaml"]

    def safe_path(self, relative: str) -> Path:
        if relative in {"", "."}:
            return self.workspace
        candidate = (self.workspace / relative).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ValueError("Path escapes workspace")
        rel = candidate.relative_to(self.workspace).as_posix()
        for forbidden in self.forbidden_paths:
            prefix = forbidden.rstrip("/")
            if rel == prefix or rel.startswith(prefix + "/"):
                raise ValueError(f"Path is forbidden: {relative}")
        return candidate

    def _writable(self, path: Path) -> None:
        rel = path.relative_to(self.workspace).as_posix()
        for allowed in self.writable_paths:
            prefix = allowed.rstrip("/") or "."
            if prefix == "." or rel == prefix or rel.startswith(prefix + "/"):
                return
        raise ValueError(f"Path is not writable: {rel}")

    def list_files(self, path: str = ".") -> dict[str, Any]:
        root = self.safe_path(path)
        files = []
        if root.is_file():
            files.append(root.relative_to(self.workspace).as_posix())
        else:
            for item in sorted(root.rglob("*")):
                if item.is_file() and ".git" not in item.parts:
                    files.append(item.relative_to(self.workspace).as_posix())
        return {"path": path, "files": files}

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
        target = self.safe_path(path)
        lines = target.read_text(encoding="utf-8").splitlines()
        start = max(0, start_line - 1)
        stop = len(lines) if end_line is None else end_line
        return {"path": path, "content": "\n".join(lines[start:stop])}

    def search_text(self, query: str, path: str = ".") -> dict[str, Any]:
        root = self.safe_path(path)
        hits = []
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for file_path in files:
            if ".git" in file_path.parts:
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for idx, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    hits.append(
                        {
                            "path": file_path.relative_to(self.workspace).as_posix(),
                            "line": idx,
                            "text": line[:240],
                        }
                    )
                    if len(hits) >= 50:
                        return {"query": query, "hits": hits, "truncated": True}
        return {"query": query, "hits": hits, "truncated": False}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        target = self.safe_path(path)
        self._writable(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path}

    def apply_patch(self, diff: str | None = None, patch: str | None = None) -> dict[str, Any]:
        text = diff if diff is not None else patch
        if not text:
            return {"ok": False, "error": "missing diff"}
        try:
            git_apply(self.workspace, text)
        except PatchError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def run_command(self, argv: list[str]) -> dict[str, Any]:
        if not argv:
            raise ValueError("empty argv")
        if argv[0] not in ALLOWED_COMMANDS:
            raise ValueError(f"Command not allowed: {argv[0]}")
        result = subprocess.run(
            argv,
            cwd=self.workspace,
            text=True,
            capture_output=True,
            timeout=120,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/tmp/model-home",
                "TZ": "UTC",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONHASHSEED": "410728",
            },
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-MAX_TOOL_OUTPUT_CHARS:],
            "stderr": result.stderr[-MAX_TOOL_OUTPUT_CHARS:],
        }


DISPATCH: dict[str, str] = {
    "list_files": "list_files",
    "read_file": "read_file",
    "search_text": "search_text",
    "write_file": "write_file",
    "apply_patch": "apply_patch",
    "run_command": "run_command",
}


def execute_tool(workspace: ToolWorkspace, name: str, arguments_json: str) -> dict[str, Any]:
    if name not in DISPATCH:
        return {"error": f"Unknown tool: {name}"}
    try:
        arguments = json.loads(arguments_json) if arguments_json else {}
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        method: Callable[..., dict[str, Any]] = getattr(workspace, DISPATCH[name])
        return method(**arguments)
    except Exception as exc:  # noqa: BLE001 - tool errors are returned to the model
        return {"error": type(exc).__name__, "message": str(exc)}


AGENTIC_INSTRUCTIONS = (
    "You are solving a frozen coding task. Use only the provided tools: "
    "list_files, read_file, search_text, write_file, apply_patch, run_command. "
    "run_command takes an argv array, not a shell string. "
    "Do not request network access. Sequential tool use only."
)


def run_agent(
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
):
    from runner.providers.base import ProviderResult
    from runner.providers.responses_common import extract_function_calls
    from runner.protocols import TOOL_PROTOCOL_VERSION

    tools_ws = ToolWorkspace(workspace, writable_paths=writable_paths, forbidden_paths=forbidden_paths)
    transcript: list[dict[str, Any]] = []
    tool_count = 0
    response = provider.create_response(
        input_messages=prompt,
        model=model,
        effort=effort,
        timeout_seconds=timeout_seconds,
        tools=TOOLS,
        instructions=AGENTIC_INSTRUCTIONS,
    )
    last = response
    while True:
        transcript.append(getattr(last, "raw_response", {}) or {})
        if getattr(last, "infrastructure", False) or getattr(last, "invalid_configuration", False):
            break
        calls = extract_function_calls(getattr(last, "raw_response", {}) or {})
        if not calls:
            break
        results: list[tuple[str, Any]] = []
        for call in calls:
            tool_count += 1
            if tool_count > max_tool_calls:
                return ProviderResult(
                    requested_model=model,
                    verified_model=getattr(last, "returned_model", None),
                    requested_effort=effort,
                    verified_effort=getattr(last, "verified_effort", None),
                    auth_surface="api_key",
                    thread_id=getattr(last, "response_id", None),
                    final_text=getattr(last, "output_text", "") or "",
                    usage=getattr(last, "usage", {}) or {},
                    events=[{"type": "tool_budget_exceeded", "tool_calls": tool_count}],
                    raw_jsonl=json.dumps(transcript, default=str),
                    error_code="tool_budget_exceeded",
                    error={"message": f"exceeded max_tool_calls={max_tool_calls}"},
                    metadata={"tool_calls": tool_count, "tool_protocol_version": TOOL_PROTOCOL_VERSION},
                )
            executed = execute_tool(tools_ws, call.name, json.dumps(call.arguments))
            transcript.append(
                {
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "result": executed,
                }
            )
            results.append((call.call_id, executed))
        if not hasattr(provider, "continue_with_tool_results"):
            break
        last = provider.continue_with_tool_results(
            previous_response_id=getattr(last, "response_id", "") or "",
            tool_results=results,
            model=model,
            effort=effort,
            timeout_seconds=timeout_seconds,
            tools=TOOLS,
        )
    return ProviderResult(
        requested_model=model,
        verified_model=getattr(last, "returned_model", None),
        requested_effort=effort,
        verified_effort=getattr(last, "verified_effort", None),
        auth_surface="api_key",
        thread_id=getattr(last, "response_id", None),
        final_text=getattr(last, "output_text", "") or "",
        usage=getattr(last, "usage", {}) or {},
        events=[{"type": "agentic.completed", "transcript_events": len(transcript)}],
        raw_jsonl=json.dumps(transcript, default=str),
        stdout=getattr(last, "output_text", "") or "",
        retry_count=getattr(last, "retry_count", 0),
        latency_ms=getattr(last, "latency_ms", None),
        command=[getattr(provider, "name", "responses"), model, effort, "agentic"],
        invalid_configuration=bool(getattr(last, "invalid_configuration", False)),
        infrastructure=bool(getattr(last, "infrastructure", False)),
        error_code=getattr(last, "error_code", None),
        error=getattr(last, "error", None),
        metadata={
            "tool_calls": tool_count,
            "tool_protocol_version": TOOL_PROTOCOL_VERSION,
            "request_hash": getattr(last, "request_hash", ""),
        },
    )
