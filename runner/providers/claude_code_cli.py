"""Claude Code CLI product provider (`claude -p --output-format stream-json`).

Runs Claude Code headless inside the task workspace on the operator's Claude
subscription login. The run is isolated from the operator's own Claude Code
configuration: ``--restricted`` ignores user/project/local settings files and
confines file tools to the workspace, ``--strict-mcp-config`` loads no MCP
servers, ``--disable-slash-commands`` disables skills, and ``--tools`` freezes
the built-in tool set. ``--bare`` is deliberately not used: it never reads
OAuth credentials, so it cannot run on a subscription.

By default every attempt runs in a fresh container from the recorded
``claude-code`` image (pinned CLI version): only the task workspace is mounted,
HOME and the Claude config dir are an empty tmpfs, the subscription token from
``claude setup-token`` arrives as ``CLAUDE_CODE_OAUTH_TOKEN`` (passed by name,
never in argv), and the only network route is the egress allowlist proxy
(``runner.egress``). Without the image or the token the attempt is
``invalid_configuration``.

Host execution is an explicit opt-in (``LLMREG_ALLOW_HOST=1``) recorded as the
attempt's isolation: ``hermetic`` (empty ``CLAUDE_CONFIG_DIR`` + token) or
``user_config`` (``CLAUDE_ALLOW_USER_CONFIG=1``: the operator's own config,
including ``~/.claude/CLAUDE.md`` and memory, can reach the model).

The applied effort is set by ``--effort`` and is not echoed in the stream, so
``verified_effort`` stays empty unless a future CLI reports it.

Fail closed: an attempt whose stream reports a model other than the requested
one is ``invalid_configuration``. API-key auth is refused because it would
measure the API, not the subscription product.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from runner.errors import HarnessError
from runner.providers.base import ProviderResult
from runner.providers.jsonl import parse_jsonl
from runner.sandbox import container_user_args

# Frozen built-in tool set for the product series. Changing it is a new series
# (bump toolset_version "claude-code-restricted-v1").
TOOLS = ("Bash", "Read", "Edit", "Write", "Glob", "Grep")
PERMISSION_MODE = "dontAsk"

KNOWN_EVENT_TYPES = {"system", "assistant", "user", "result", "stream_event", "rate_limit_event"}

# Env vars that would switch Claude Code off the subscription or leak harness paths.
_STRIPPED_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "CLAUDE_CONFIG_DIR",
    "GRADER",
    "GOLD_PATCH",
    "PRIVATE_GRADERS",
    "HARNESS_ROOT",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_claude_executable(explicit: str | None = None) -> Path | None:
    explicit = explicit or os.environ.get("CLAUDE_BIN")
    if explicit:
        path = Path(explicit).expanduser().absolute()
        return path if path.exists() else None
    located = shutil.which("claude")
    return Path(located).absolute() if located else None


def detect_claude_version(claude_bin: Path) -> str:
    proc = subprocess.run(
        [str(claude_bin), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    text = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0 or not text:
        raise HarnessError(f"claude --version failed: {text or proc.returncode}")
    return text.splitlines()[0].strip()


def build_claude_command(*, claude_bin: Path, model: str, effort: str, prompt: str) -> list[str]:
    return [
        str(claude_bin),
        "-p",
        prompt,
        "--model",
        model,
        "--effort",
        effort,
        "--output-format",
        "stream-json",
        "--verbose",
        "--restricted",
        "--tools",
        ",".join(TOOLS),
        "--allowedTools",
        ",".join(TOOLS),
        "--permission-mode",
        PERMISSION_MODE,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]


EXTRA_CA_MOUNT = "/etc/llmreg/extra-ca.pem"


@dataclass(frozen=True)
class ContainerRuntime:
    """Where container-mode attempts run: the agent image and its egress route."""

    image: str
    network: str
    proxy_url: str
    egress_allow: tuple[str, ...] = ()
    # Operator CA bundle for a TLS-inspecting network (LLMREG_EXTRA_CA). It adds
    # trust; certificate verification itself is never relaxed.
    extra_ca: str | None = None

    def describe(self) -> dict[str, Any]:
        described = {
            "image": self.image,
            "network": self.network,
            "proxy_url": self.proxy_url,
            "egress_allow": list(self.egress_allow),
        }
        if self.extra_ca:
            described["extra_ca_sha256"] = hashlib.sha256(Path(self.extra_ca).read_bytes()).hexdigest()
        return described


def build_container_command(
    runtime: ContainerRuntime,
    *,
    workspace: Path,
    name: str,
    inner: list[str],
    env_names: list[str],
) -> list[str]:
    proxy = runtime.proxy_url
    proxy_env = {
        "HTTPS_PROXY": proxy,
        "https_proxy": proxy,
        "HTTP_PROXY": proxy,
        "http_proxy": proxy,
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
        "CLAUDE_CONFIG_DIR": "/tmp/claude-config",
    }
    ca_args: list[str] = []
    if runtime.extra_ca:
        proxy_env["NODE_EXTRA_CA_CERTS"] = EXTRA_CA_MOUNT
        ca_args = ["-v", f"{Path(runtime.extra_ca).resolve()}:{EXTRA_CA_MOUNT}:ro"]
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        runtime.network,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "4g",
        "--cpus",
        "2",
        "--pids-limit",
        "512",
        *container_user_args(),
        "--tmpfs",
        "/tmp:rw,exec,size=2g",
        *[arg for key, value in proxy_env.items() for arg in ("-e", f"{key}={value}")],
        # Secrets are forwarded by name: docker reads the values from its own env.
        *[arg for key in env_names for arg in ("-e", key)],
        "-v",
        f"{workspace}:/workspace:rw",
        *ca_args,
        "-w",
        "/workspace",
        runtime.image,
        *inner,
    ]


def provider_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(extra_env or {})
    for key in _STRIPPED_ENV:
        env.pop(key, None)
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def isolation_mode(env: dict[str, str], *, container: bool = False) -> str | None:
    """``container``/``hermetic`` with a subscription token, ``user_config`` if opted in, else None."""
    if container:
        return "container" if env.get("CLAUDE_CODE_OAUTH_TOKEN") else None
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "hermetic"
    if env.get("CLAUDE_ALLOW_USER_CONFIG") == "1":
        return "user_config"
    return None


def summarize_stream(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract identity and outcome from Claude Code stream-json events."""
    init: dict[str, Any] = {}
    result: dict[str, Any] = {}
    assistant_models: list[str] = []
    texts: list[str] = []
    tool_uses: list[str] = []
    unknown: list[str] = []
    for event in events:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init" and not init:
            init = event
        elif kind == "assistant":
            message = event.get("message") or {}
            if message.get("model"):
                assistant_models.append(str(message["model"]))
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    texts.append(str(block["text"]))
                elif block.get("type") == "tool_use":
                    tool_uses.append(str(block.get("name")))
        elif kind == "result":
            result = event
        elif kind not in KNOWN_EVENT_TYPES:
            unknown.append(str(kind))
    model_usage = result.get("modelUsage") if isinstance(result.get("modelUsage"), dict) else {}
    served_models = sorted({*assistant_models, *model_usage.keys()})
    return {
        "session_id": init.get("session_id") or result.get("session_id"),
        "init_model": init.get("model"),
        "served_models": served_models,
        "api_key_source": init.get("apiKeySource"),
        "permission_mode": init.get("permissionMode"),
        "tools": init.get("tools"),
        "mcp_servers": init.get("mcp_servers"),
        "cli_version": init.get("claude_code_version"),
        "plugins": [item.get("source") or item.get("name") for item in init.get("plugins") or [] if isinstance(item, dict)],
        "skills": init.get("skills"),
        "slash_commands": init.get("slash_commands"),
        "terminal_reason": result.get("terminal_reason"),
        "api_error_status": result.get("api_error_status"),
        "reported_effort": init.get("effort") or result.get("effort"),
        "result_subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "final_text": result.get("result") if isinstance(result.get("result"), str) else (texts[-1] if texts else ""),
        "num_turns": result.get("num_turns"),
        "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
        "model_usage": model_usage,
        "total_cost_usd": result.get("total_cost_usd"),
        "permission_denials": result.get("permission_denials") or [],
        "tool_uses": tool_uses,
        "has_result": bool(result),
        "unknown_event_types": sorted(set(unknown)),
    }


def _model_matches(requested: str, reported: str | None) -> bool:
    # Claude Code may report a dated or suffixed snapshot id ("claude-opus-5-5[1m]").
    if not reported:
        return False
    base = reported.split("[", 1)[0]
    return base == requested or base.startswith(requested + "-")


def classify(summary: dict[str, Any], *, model: str, effort: str, stderr: str, exit_code: int) -> dict[str, Any]:
    """Return verified identity plus invalid/infrastructure flags for one attempt."""
    invalid = False
    infrastructure = False
    error_code: str | None = None
    notes: list[str] = []
    verified_model = None
    init_model = summary.get("init_model")
    served = [item for item in summary.get("served_models") or [] if item != "<synthetic>"]
    if init_model:
        if _model_matches(model, init_model):
            verified_model = model
        else:
            invalid, error_code = True, "model_mismatch"
            verified_model = init_model
    mismatched = [item for item in served if not _model_matches(model, item)]
    if mismatched and not invalid:
        # A served fallback model means this attempt did not measure the requested model.
        invalid, error_code = True, "model_mismatch"
        verified_model = mismatched[0]
    if summary.get("api_key_source") not in {None, "none"}:
        invalid, error_code = True, error_code or "auth_not_subscription"
        notes.append(f"apiKeySource={summary.get('api_key_source')}; subscription auth required")
    reported_effort = summary.get("reported_effort")
    if reported_effort and reported_effort != effort:
        invalid, error_code = True, error_code or "effort_mismatch"
    verified_effort = reported_effort or None
    if not reported_effort:
        notes.append("effort is applied by --effort; the stream does not echo it")

    blob = (stderr or "").lower() + " " + str(summary.get("final_text") or "").lower()
    if not summary.get("has_result"):
        infrastructure, error_code = True, error_code or "no_result_event"
    elif summary.get("is_error"):
        subtype = str(summary.get("result_subtype") or "")
        try:
            status = int(summary.get("api_error_status") or 0)
        except (TypeError, ValueError):
            status = 0
        if status == 429:
            infrastructure, error_code = True, error_code or "usage_limit"
        elif status >= 500:
            infrastructure, error_code = True, error_code or "http_5xx"
        elif status in {401, 403}:
            infrastructure, error_code = True, error_code or "auth"
        elif any(token in blob for token in ("rate limit", "usage limit", "limit reached", "429")):
            infrastructure, error_code = True, error_code or "usage_limit"
        elif any(token in blob for token in ("overloaded", "529", "500", "502", "503")):
            infrastructure, error_code = True, error_code or "http_5xx"
        elif any(token in blob for token in ("not logged in", "please run /login", "authentication", "oauth")):
            infrastructure, error_code = True, error_code or "auth"
        elif subtype == "error_max_turns":
            error_code = error_code or "max_turns"
        else:
            reason = summary.get("terminal_reason")
            infrastructure, error_code = True, error_code or str(reason or subtype or "cli_error")
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


class ClaudeCodeCLIProvider:
    name = "claude_code_cli"
    track = "claude_code_product"

    def __init__(
        self,
        *,
        claude_bin: Path,
        model: str,
        effort: str,
        extra_env: dict[str, str] | None = None,
        auth_surface: str = "claude_subscription",
        runtime: ContainerRuntime | None = None,
    ) -> None:
        self.claude_bin = Path(claude_bin)
        self.model = model
        self.effort = effort
        self.extra_env = extra_env or {}
        self.auth_surface = auth_surface
        self.runtime = runtime

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
        """``image`` overrides the runtime's agent image (a task's dependency environment)."""
        runtime = replace(self.runtime, image=image) if self.runtime and image else self.runtime
        inner = build_claude_command(
            claude_bin=Path("claude") if runtime else self.claude_bin, model=model, effort=effort, prompt=prompt
        )
        env = provider_env(self.extra_env)
        isolation = isolation_mode(env, container=runtime is not None)
        if isolation is None:
            return self._refused(inner, model, effort, container=runtime is not None)
        container_name = f"llmreg-agent-{uuid.uuid4().hex[:12]}" if runtime else None
        if runtime:
            env_names = sorted(key for key in {"CLAUDE_CODE_OAUTH_TOKEN", *self.extra_env} if env.get(key))
            command = build_container_command(
                runtime, workspace=Path(workspace).resolve(), name=str(container_name), inner=inner, env_names=env_names
            )
        else:
            command = inner
        started = time.perf_counter()
        timed_out = False
        with tempfile.TemporaryDirectory(prefix="claude-config-") as config_dir:
            if isolation == "hermetic":
                env["CLAUDE_CONFIG_DIR"] = config_dir
            try:
                proc = subprocess.run(
                    command,
                    cwd=workspace,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                stdout, stderr, exit_code = proc.stdout or "", proc.stderr or "", proc.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                exit_code = 124
            except OSError as exc:
                raise HarnessError(f"failed to launch Claude Code: {exc}") from exc
            finally:
                if container_name:
                    # Killing the docker CLI does not stop the container.
                    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
        wall_ms = (time.perf_counter() - started) * 1000
        events, malformed = parse_jsonl(stdout)
        summary = summarize_stream(events)
        summary["isolation"] = isolation
        if runtime:
            summary["runtime"] = runtime.describe()
        verdict = classify(summary, model=model, effort=effort, stderr=stderr, exit_code=exit_code)
        error_code = verdict["error_code"]
        infrastructure = verdict["infrastructure"]
        if timed_out:
            error_code, infrastructure = "timeout", False
        error = None
        if timed_out or verdict["invalid"] or infrastructure or exit_code != 0:
            error = {
                "message": "wall-clock budget exhausted"
                if timed_out
                else (stderr.strip() or str(summary.get("final_text") or f"claude exited {exit_code}"))[:4000],
                "malformed_jsonl": malformed,
                "unknown_event_types": summary.get("unknown_event_types"),
                "notes": verdict["notes"],
            }
        usage = dict(summary.get("usage") or {})
        if summary.get("total_cost_usd") is not None:
            usage["equivalent_cost_usd"] = summary["total_cost_usd"]
        return ProviderResult(
            requested_model=model,
            verified_model=verdict["verified_model"],
            requested_effort=effort,
            verified_effort=verdict["verified_effort"],
            auth_surface=self.auth_surface,
            thread_id=summary.get("session_id"),
            final_text=str(summary.get("final_text") or ""),
            usage=usage,
            events=events,
            raw_jsonl=stdout,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            latency_ms=wall_ms,
            wall_ms=wall_ms,
            command=_redact_prompt(command, prompt),
            invalid_configuration=verdict["invalid"],
            infrastructure=infrastructure,
            error_code=error_code,
            error=error,
            metadata={"summary": summary, "malformed_jsonl": malformed, "notes": verdict["notes"]},
        )

    def _refused(self, command: list[str], model: str, effort: str, *, container: bool) -> ProviderResult:
        if container:
            message = "container runs need CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token` on the subscription)"
        else:
            message = (
                "Claude Code isolation unavailable: set CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) "
                "for hermetic runs, or CLAUDE_ALLOW_USER_CONFIG=1 to accept the operator's own config"
            )
        return ProviderResult(
            requested_model=model,
            verified_model=None,
            requested_effort=effort,
            verified_effort=None,
            auth_surface=self.auth_surface,
            exit_code=0,
            command=[*command[:2], "<prompt>", *command[3:]],
            invalid_configuration=True,
            error_code="isolation_unavailable",
            error={"message": message},
            metadata={"notes": [message]},
        )

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "track": self.track,
            "claude_bin": str(self.claude_bin),
            "model": self.model,
            "effort": self.effort,
            "tools": list(TOOLS),
            "permission_mode": PERMISSION_MODE,
            "auth_surface": self.auth_surface,
            "runtime": self.runtime.describe() if self.runtime else "host",
        }


def _redact_prompt(command: list[str], prompt: str) -> list[str]:
    return ["<prompt>" if part == prompt else part for part in command]


def auth_status(claude_bin: Path) -> dict[str, Any]:
    """`claude auth status --json`; never makes a model call."""
    proc = subprocess.run(
        [str(claude_bin), "auth", "status", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        env=provider_env(),
        check=False,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"raw": (proc.stdout or "")[:2000]}
    return {"returncode": proc.returncode, "payload": payload, "stderr": (proc.stderr or "")[:2000]}
