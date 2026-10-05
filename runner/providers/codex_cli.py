"""Primary Codex CLI provider (`codex exec`)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from runner.errors import HarnessError
from runner.providers.base import ProviderResult
from runner.providers.jsonl import classify_provider_error, parse_jsonl, summarize_jsonl


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_codex_executable(explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None
    located = shutil.which("codex")
    return Path(located) if located else None


def detect_codex_version(codex_bin: Path) -> str:
    proc = subprocess.run(
        [str(codex_bin), "--version"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    text = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0 or not text:
        raise HarnessError(f"codex --version failed: {text or proc.returncode}")
    return text.splitlines()[0].strip()


def effort_cli_flag(effort: str, syntax: str = "config_override") -> list[str]:
    if syntax == "config_override":
        return ["-c", f"model_reasoning_effort={effort}"]
    if syntax == "long_flag":
        return ["--reasoning-effort", effort]
    raise HarnessError(f"unknown effort syntax {syntax}")


def build_codex_exec_command(
    *,
    codex_bin: Path,
    model: str,
    prompt: str,
    effort_flag: list[str],
    extra_config: list[str] | None = None,
    sandbox: str = "workspace-write",
) -> list[str]:
    return [
        str(codex_bin),
        "exec",
        "-m",
        model,
        "--ephemeral",
        "--sandbox",
        sandbox,
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        *effort_flag,
        *(extra_config or []),
        prompt,
    ]


class CodexCLIProvider:
    name = "codex_cli"
    track = "codex_product"

    def __init__(
        self,
        *,
        codex_bin: Path,
        model: str,
        effort: str,
        extra_config: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        effort_flag: list[str] | None = None,
        auth_surface: str = "chatgpt",
        effort_syntax: str = "config_override",
    ) -> None:
        self.codex_bin = Path(codex_bin)
        self.model = model
        self.effort = effort
        self.extra_config = extra_config or []
        self.extra_env = extra_env or {}
        self.effort_flag = effort_flag or effort_cli_flag(effort, effort_syntax)
        self.auth_surface = auth_surface

    def run_attempt(
        self,
        *,
        prompt: str,
        workspace: Path,
        model: str,
        effort: str,
        timeout_seconds: int,
    ) -> ProviderResult:
        command = build_codex_exec_command(
            codex_bin=self.codex_bin,
            model=model,
            prompt=prompt,
            effort_flag=self.effort_flag if effort == self.effort else effort_cli_flag(effort),
            extra_config=self.extra_config,
        )
        env = os.environ.copy()
        env.update(self.extra_env)
        env.setdefault("CODEX_QUIET_MODE", "1")
        # Never leak grader locations into the Codex process.
        for leaked in ("GRADER", "GOLD_PATCH", "PRIVATE_GRADERS", "HARNESS_ROOT"):
            env.pop(leaked, None)
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                command,
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            events, _ = parse_jsonl(stdout)
            summary = summarize_jsonl(events)
            wall_ms = (time.perf_counter() - started) * 1000
            return ProviderResult(
                requested_model=model,
                verified_model=summary.get("verified_model"),
                requested_effort=effort,
                verified_effort=summary.get("verified_effort"),
                auth_surface=self.auth_surface,
                thread_id=summary.get("thread_id"),
                final_text=summary.get("final_text") or "",
                usage=summary.get("usage") or {},
                events=events,
                raw_jsonl=stdout,
                stdout=stdout,
                stderr=stderr,
                exit_code=124,
                latency_ms=wall_ms,
                wall_ms=wall_ms,
                command=command,
                error_code="timeout",
                error={"message": "wall-clock budget exhausted"},
                metadata={"summary": summary},
            )
        except OSError as exc:
            raise HarnessError(f"failed to launch Codex: {exc}") from exc

        wall_ms = (time.perf_counter() - started) * 1000
        events, malformed = parse_jsonl(proc.stdout or "")
        summary = summarize_jsonl(events)
        infrastructure, error_code = classify_provider_error(events, proc.stderr or "", proc.returncode)
        verified_model = summary.get("verified_model")
        verified_effort = summary.get("verified_effort")
        invalid = False
        if verified_model and verified_model != model:
            invalid = True
            error_code = error_code or "model_mismatch"
        if verified_effort and verified_effort != effort:
            invalid = True
            error_code = error_code or "effort_mismatch"
        error = None
        if proc.returncode != 0 or infrastructure or invalid:
            error = {
                "message": (proc.stderr or "").strip() or summary.get("final_text") or f"codex exited {proc.returncode}",
                "malformed_jsonl": malformed,
                "unknown_event_types": summary.get("unknown_event_types"),
            }
        return ProviderResult(
            requested_model=model,
            verified_model=verified_model,
            requested_effort=effort,
            verified_effort=verified_effort,
            auth_surface=self.auth_surface,
            thread_id=summary.get("thread_id"),
            final_text=summary.get("final_text") or "",
            usage=summary.get("usage") or {},
            events=events,
            raw_jsonl=proc.stdout or "",
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            exit_code=proc.returncode,
            latency_ms=wall_ms,
            wall_ms=wall_ms,
            command=command,
            invalid_configuration=invalid,
            infrastructure=infrastructure,
            error_code=error_code,
            error=error,
            metadata={"summary": summary, "malformed_jsonl": malformed},
        )

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "track": self.track,
            "codex_bin": str(self.codex_bin),
            "model": self.model,
            "effort": self.effort,
            "effort_flag": self.effort_flag,
            "auth_surface": self.auth_surface,
        }
