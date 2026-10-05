"""Cursor CLI product provider (`agent -p --output-format stream-json`).

Runs the Cursor agent headless inside the task workspace on the operator's
Cursor plan, so the series measures a model *as Cursor serves it* (Grok 4.7
by default), the same way the Claude Code series measures Opus as Claude Code
serves it. Every attempt runs in a fresh container from the recorded
``cursor`` image (pinned CLI build, verified by sha256). Only the workspace is
mounted, HOME is an empty tmpfs, the API key arrives as ``CURSOR_API_KEY``
(passed by name, never in argv) and the only route out is the egress allowlist
proxy. There is no host mode: without the image or the key the attempt is
``invalid_configuration``.

The CLI starts from a fixed config (``CLI_CONFIG``) written into the empty
HOME: no sandbox (the container is the sandbox), shell commands, reads and
writes pre-approved, and no web. Claude Code runs without web tools, and a web
search could find a mined task's upstream fix. ``--force`` is deliberately not
used: it approves web search and web fetch whatever the deny rules say (a live
run proved it). Without it, headless runs reject every tool the allow list
does not cover, which includes web search, web fetch and the delete tool
(``rm`` through the shell still works, as in Claude Code). An attempt whose
stream still shows web access that was not rejected is
``invalid_configuration`` (``web_access``).

Identity: the stream's ``system/init`` event names the served model by display
name (e.g. "Grok 4.7 High"). An attempt whose display name is not the
requested model, or names another reasoning level, is ``invalid_configuration``.
Cursor reports token counts but no cost.

Transcripts are stored, so ``env`` snapshots (only emitted with the CLI's
capture-env option, which is never set) are dropped from events and the key's
value is redacted from everything kept.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from runner.errors import HarnessError
from runner.providers.base import ProviderResult
from runner.providers.claude_code_cli import ContainerRuntime, build_container_command
from runner.providers.jsonl import parse_jsonl

KEY_ENV = "CURSOR_API_KEY"
CONFIG_ENV = "LLMREG_CURSOR_CONFIG"
MODEL_ENV = "LLMREG_CURSOR_MODEL"
EFFORTS = ("low", "medium", "high", "xhigh")
# Frozen CLI config for the product series. Changing it is a new series
# (bump toolset_version, now "cursor-cli-v2").
CLI_CONFIG: dict[str, Any] = {
    "version": 1,
    "permissions": {"allow": ["Shell(*)", "Read(**)", "Write(**)"],
                    "deny": ["WebSearch(*)", "WebFetch(*)", "Mcp(*:*)"]},
    "autoAcceptWebSearch": False,
    "sandbox": {"mode": "disabled"},
}
KNOWN_EVENT_TYPES = {"system", "user", "assistant", "thinking", "tool_call", "result", "interaction_query"}
WEB_TOOLS = {"webSearchToolCall": "WebSearch", "webFetchToolCall": "WebFetch"}
# Cursor tool-call kinds under the names the Claude Code series uses, so behaviour
# metrics read the same for both products.
TOOL_NAMES = {
    "shellToolCall": "Bash",
    "editToolCall": "Edit",
    "writeToolCall": "Write",
    "deleteToolCall": "Delete",
    "readToolCall": "Read",
    "grepToolCall": "Grep",
    "globToolCall": "Glob",
    "lsToolCall": "LS",
    "semSearchToolCall": "SemanticSearch",
    "taskToolCall": "Task",
    "updateTodosToolCall": "TodoWrite",
    "readTodosToolCall": "TodoRead",
    "readLintsToolCall": "ReadLints",
    "createPlanToolCall": "Plan",
    "mcpToolCall": "Mcp",
    **WEB_TOOLS,
}
FAILED_RESULTS = {"failure", "error", "rejected", "timeout", "spawnError", "permissionDenied"}

# Launch script: the config goes into the empty HOME, then the CLI replaces the shell.
LAUNCH = 'mkdir -p "$HOME/.cursor" && printf %s "$LLMREG_CURSOR_CONFIG" > "$HOME/.cursor/cli-config.json" && exec "$@"'


def cursor_model_arg(model: str, effort: str) -> str:
    """The CLI's model id for a series: ``grok-4.7`` at ``high`` is ``grok-4.7-high``."""
    return os.environ.get(MODEL_ENV) or f"{model}-{effort}"


def build_cursor_command(*, model_arg: str, prompt: str) -> list[str]:
    return [
        "agent",
        "-p",
        "--output-format",
        "stream-json",
        "--model",
        model_arg,
        "--trust",
        "--sandbox",
        "disabled",
        prompt,
    ]


def tool_call_parts(tool_call: Any) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """(kind, args, result) of a stream ``tool_call`` payload, e.g. ``{"shellToolCall": {"args": …}}``."""
    if not isinstance(tool_call, dict) or not tool_call:
        return "?", {}, {}
    kind, body = next(iter(tool_call.items()))
    body = body if isinstance(body, dict) else {}
    args = body.get("args") if isinstance(body.get("args"), dict) else {}
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    return str(kind), args, result


def tool_name(kind: str) -> str:
    return TOOL_NAMES.get(kind, kind.removesuffix("ToolCall") or "?")


def _norm_words(text: str) -> list[str]:
    return [word for word in re.split(r"[^a-z0-9]+", text.lower()) if word]


def model_matches(requested: str, display: str | None) -> bool:
    """``grok-4.7`` matches "Grok 4.7", "Grok 4.7 High", "grok-4.7-high"; not "Grok 4.71" or "Auto"."""
    if not display:
        return False
    want, got = _norm_words(requested), _norm_words(display)
    return got[: len(want)] == want


def displayed_effort(requested: str, display: str | None) -> str | None:
    """Reasoning level named in the display name after the model, if any."""
    rest = _norm_words(display or "")[len(_norm_words(requested)):]
    found = [word for word in rest if word in EFFORTS]
    return found[-1] if found else None


def summarize_stream(events: list[dict[str, Any]]) -> dict[str, Any]:
    init: dict[str, Any] = {}
    result: dict[str, Any] = {}
    final_parts: list[str] = []
    tool_uses: list[str] = []
    web_access: list[str] = []
    rejected: list[str] = []
    unknown: list[str] = []
    for event in events:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init" and not init:
            init = event
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    final_parts.append(str(block["text"]))
        elif kind == "tool_call":
            name, _, outcome = tool_call_parts(event.get("tool_call"))
            if event.get("subtype") == "started":
                tool_uses.append(tool_name(name))
                final_parts = []  # the final message is the text after the last tool call
            elif event.get("subtype") == "completed":
                failed = sorted(FAILED_RESULTS & set(outcome))
                if name in WEB_TOOLS and "rejected" not in outcome and "permissionDenied" not in outcome:
                    web_access.append(WEB_TOOLS[name])
                if "rejected" in failed or "permissionDenied" in failed:
                    rejected.append(tool_name(name))
        elif kind == "result":
            result = event
        elif kind not in KNOWN_EVENT_TYPES:
            unknown.append(str(kind))
    final_text = "".join(final_parts).strip() or (str(result.get("result") or "") if not tool_uses else "")
    return {
        "session_id": init.get("session_id") or result.get("session_id"),
        "init_model": init.get("model"),
        "api_key_source": init.get("apiKeySource"),
        "permission_mode": init.get("permissionMode"),
        "result_subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "duration_ms": result.get("duration_ms"),
        "final_text": final_text,
        "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
        "request_id": result.get("request_id"),
        "tool_uses": tool_uses,
        "web_access": web_access,
        "rejected_tools": rejected,
        "has_result": bool(result),
        "unknown_event_types": sorted(set(unknown)),
    }


def classify(summary: dict[str, Any], *, model: str, effort: str, stderr: str, exit_code: int) -> dict[str, Any]:
    invalid = infrastructure = False
    error_code: str | None = None
    notes: list[str] = []
    display = summary.get("init_model")
    verified_model = verified_effort = None
    if display:
        if model_matches(model, display):
            verified_model = model
            shown = displayed_effort(model, display)
            if shown and shown != effort:
                invalid, error_code = True, "effort_mismatch"
            verified_effort = shown
        else:
            invalid, error_code, verified_model = True, "model_mismatch", str(display)
    if not verified_effort:
        notes.append("reasoning level is chosen by the model id; the display name does not echo it")
    if summary.get("web_access"):
        invalid, error_code = True, error_code or "web_access"
        notes.append("web access during the attempt: " + ", ".join(summary["web_access"]))
    blob = (stderr or "").lower() + " " + str(summary.get("final_text") or "").lower()
    if not summary.get("has_result"):
        infrastructure, error_code = True, error_code or (
            "auth" if any(t in blob for t in ("authentication required", "api key is invalid", "unauthorized"))
            else "usage_limit" if any(t in blob for t in ("rate limit", "usage limit", "quota", "429"))
            else "no_result_event"
        )
        if any(t in blob for t in ("cannot use this model", "model not found", "invalid model", "unknown model")):
            invalid, infrastructure, error_code = True, False, "model_unavailable"
    elif summary.get("is_error"):
        infrastructure, error_code = True, error_code or str(summary.get("result_subtype") or "cli_error")
    elif exit_code != 0:
        infrastructure, error_code = True, error_code or f"exit_{exit_code}"
    return {
        "verified_model": verified_model,
        "verified_effort": verified_effort,
        "invalid": invalid,
        "infrastructure": infrastructure,
        "error_code": error_code,
        "notes": notes,
    }


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items() if key != "env"}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def redact(stdout: str, secrets: list[str]) -> str:
    """Drop ``env`` snapshots from stream events and blank any secret value."""
    lines = []
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            lines.append(line)
            continue
        lines.append(json.dumps(_scrub(payload), separators=(",", ":")) if isinstance(payload, dict) else line)
    text = "\n".join(lines) + ("\n" if stdout.endswith("\n") else "")
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "<redacted>")
    return text


def usage_from(summary: dict[str, Any]) -> dict[str, Any]:
    raw = summary.get("usage") or {}
    if not raw:
        return {}
    usage = {
        "input_tokens": raw.get("inputTokens"),
        "output_tokens": raw.get("outputTokens"),
        "cache_read_input_tokens": raw.get("cacheReadTokens"),
        "cache_creation_input_tokens": raw.get("cacheWriteTokens"),
    }
    usage["total_tokens"] = sum(int(v or 0) for v in usage.values())
    return usage


class CursorCLIProvider:
    name = "cursor_cli"
    track = "cursor_product"
    environment_kind = "cursor_agent"

    def __init__(
        self,
        *,
        model: str,
        effort: str,
        runtime: ContainerRuntime | None,
        extra_env: dict[str, str] | None = None,
        auth_surface: str = "cursor_subscription",
    ) -> None:
        self.model = model
        self.effort = effort
        self.runtime = runtime
        self.extra_env = extra_env or {}
        self.auth_surface = auth_surface

    def run_attempt(
        self,
        *,
        prompt: str,
        workspace: Path,
        model: str,
        effort: str,
        timeout_seconds: int,
        image: str | None = None,
    ) -> ProviderResult:
        runtime = replace(self.runtime, image=image) if self.runtime and image else self.runtime
        model_arg = cursor_model_arg(model, effort)
        inner = build_cursor_command(model_arg=model_arg, prompt=prompt)
        env = os.environ.copy()
        env.update(self.extra_env)
        for key in ("GRADER", "GOLD_PATCH", "PRIVATE_GRADERS", "HARNESS_ROOT"):
            env.pop(key, None)
        if runtime is None or not env.get(KEY_ENV):
            return self._refused(inner, prompt, model, effort, image_missing=runtime is None)
        env[CONFIG_ENV] = json.dumps(CLI_CONFIG, sort_keys=True)
        name = f"llmreg-cursor-{uuid.uuid4().hex[:12]}"
        command = build_container_command(
            runtime,
            workspace=Path(workspace).resolve(),
            name=name,
            inner=["sh", "-c", LAUNCH, "sh", *inner],
            env_names=sorted({KEY_ENV, CONFIG_ENV, *self.extra_env}),
        )
        started, started_epoch = time.perf_counter(), time.time()
        timed_out = False
        try:
            proc = subprocess.run(
                command, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=timeout_seconds, check=False,
            )
            stdout, stderr, exit_code = proc.stdout or "", proc.stderr or "", proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            exit_code = 124
        except OSError as exc:
            raise HarnessError(f"failed to launch the Cursor CLI: {exc}") from exc
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        wall_ms = (time.perf_counter() - started) * 1000
        secrets = [env.get(KEY_ENV, "")]
        stdout, stderr = redact(stdout, secrets), redact(stderr, secrets)
        events, malformed = parse_jsonl(stdout)
        summary = summarize_stream(events)
        summary["model_arg"] = model_arg
        summary["runtime"] = runtime.describe()
        summary["egress_denied"] = _denied_since(runtime, started_epoch)
        verdict = classify(summary, model=model, effort=effort, stderr=stderr, exit_code=exit_code)
        error_code, infrastructure = verdict["error_code"], verdict["infrastructure"]
        if timed_out:
            error_code, infrastructure = "timeout", False
        error = None
        if timed_out or verdict["invalid"] or infrastructure or exit_code != 0:
            error = {
                "message": "wall-clock budget exhausted" if timed_out
                else (stderr.strip() or str(summary.get("final_text") or f"agent exited {exit_code}"))[:4000],
                "malformed_jsonl": malformed,
                "unknown_event_types": summary.get("unknown_event_types"),
                "notes": verdict["notes"],
            }
        return ProviderResult(
            requested_model=model,
            verified_model=verdict["verified_model"],
            requested_effort=effort,
            verified_effort=verdict["verified_effort"],
            auth_surface=self.auth_surface,
            thread_id=summary.get("session_id"),
            final_text=str(summary.get("final_text") or ""),
            usage=usage_from(summary),
            events=events,
            raw_jsonl=stdout,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            latency_ms=wall_ms,
            wall_ms=wall_ms,
            command=["<prompt>" if part == prompt else part for part in command],
            invalid_configuration=verdict["invalid"],
            infrastructure=infrastructure,
            error_code=error_code,
            error=error,
            metadata={"summary": summary, "malformed_jsonl": malformed, "notes": verdict["notes"]},
        )

    def _refused(self, inner: list[str], prompt: str, model: str, effort: str, *, image_missing: bool) -> ProviderResult:
        message = (
            "Cursor agent image not built: run `python -m runner images build`" if image_missing
            else f"set {KEY_ENV} (Cursor dashboard → Integrations → User API Keys) to run the Cursor series"
        )
        return ProviderResult(
            requested_model=model,
            verified_model=None,
            requested_effort=effort,
            verified_effort=None,
            auth_surface=self.auth_surface,
            command=["<prompt>" if part == prompt else part for part in inner],
            invalid_configuration=True,
            error_code="isolation_unavailable",
            error={"message": message},
            metadata={"notes": [message]},
        )

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "track": self.track,
            "model": self.model,
            "effort": self.effort,
            "model_arg": cursor_model_arg(self.model, self.effort),
            "cli_config": CLI_CONFIG,
            "auth_surface": self.auth_surface,
            "runtime": self.runtime.describe() if self.runtime else None,
        }


def _denied_since(runtime: ContainerRuntime, since_epoch: float) -> list[str]:
    """Hosts the egress proxy refused since the attempt started (diagnostic; shared by parallel attempts)."""
    from runner.egress import decisions

    name = runtime.proxy_url.removeprefix("http://").split(":", 1)[0]
    try:
        return sorted({str(d.get("target")) for d in decisions(since_epoch, name=name) if d.get("decision") != "allow"})
    except (OSError, subprocess.SubprocessError):
        return []


def list_models(runtime: ContainerRuntime, *, timeout_seconds: int = 120) -> list[str]:
    """`agent --list-models` in the agent image (no model call); lines are ``<id> - <name>``."""
    env = os.environ.copy()
    if not env.get(KEY_ENV):
        raise HarnessError(f"{KEY_ENV} is not set")
    name = f"llmreg-cursor-models-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="llmreg-cursor-") as empty:
        command = build_container_command(
            runtime, workspace=Path(empty), name=name, inner=["agent", "--list-models"], env_names=[KEY_ENV]
        )
        try:
            proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout_seconds,
                                  check=False)
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    output = redact(proc.stdout or "", [env[KEY_ENV]])
    if proc.returncode != 0:
        raise HarnessError(redact(proc.stderr or output, [env[KEY_ENV]]).strip()[:500] or "agent --list-models failed")
    return [re.sub(r"\x1b\[[0-9;]*m", "", line).strip() for line in output.splitlines() if " - " in line]
