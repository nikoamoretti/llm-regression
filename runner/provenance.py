"""Full environment manifest for every evaluation run."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runner.hash_tree import hash_file
from runner.providers.codex_cli import find_codex_executable, sha256_file
from runner.sandbox import docker_available


def _cmd(args: list[str]) -> str | None:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr) else None


def runner_git_state(root: Path) -> dict[str, Any]:
    sha = _cmd(["git", "-C", str(root), "rev-parse", "HEAD"]) or "unknown"
    dirty_proc = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = bool((dirty_proc.stdout or "").strip()) if dirty_proc.returncode == 0 else True
    return {
        "harness_git_commit": sha,
        "harness_git_dirty": dirty,
        "harness_git_status": (dirty_proc.stdout or "")[:4000],
    }


def lockfile_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for relative in (
        "pyproject.toml",
        "docker/requirements.lock",
        "package.json",
        "configs/suites.yaml",
        "configs/alert_policy.yaml",
        "configs/models.yaml",
        "configs/protocols.yaml",
        "configs/budgets.yaml",
    ):
        path = root / relative
        if path.exists():
            hashes[relative] = hash_file(path)
    return hashes


def collect_environment_manifest(
    *,
    root: Path,
    track: str,
    provider: str,
    requested_model: str,
    verified_model: str | None,
    requested_effort: str,
    verified_effort: str | None,
    auth_surface: str,
    client_mode: str,
    suite_id: str,
    suite_version: str,
    suite_hash: str,
    schedule_seed: int,
    network_policy: str = "disabled",
    sandbox_configuration: str = "workspace-write",
    ignore_user_config: bool = True,
    ignore_rules: bool = True,
    mcp_plugin_skill_policy: str = "disabled_unless_frozen",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    local_tz = datetime.now().astimezone().tzinfo
    local_name = getattr(local_tz, "key", None) or str(local_tz)
    codex = find_codex_executable(os.environ.get("CODEX_BIN"))
    codex_version = None
    codex_sha = None
    if codex is not None:
        try:
            from runner.providers.codex_cli import detect_codex_version

            codex_version = detect_codex_version(codex)
            codex_sha = sha256_file(codex)
        except Exception as exc:  # noqa: BLE001
            codex_version = f"unreadable: {exc}"
    claude_info: dict[str, Any] = {}
    if track == "claude_code_product":
        from runner.providers.claude_code_cli import (
            TOOLS,
            detect_claude_version,
            find_claude_executable,
            isolation_mode,
            provider_env,
        )

        from runner.egress import allowlist
        from runner.images import load_images, recorded_image

        images = load_images()
        container = bool(recorded_image("claude_code") and recorded_image("toolchain"))
        claude = None if container else find_claude_executable()
        claude_info = {
            "claude_code_runtime": "container" if container else "host",
            "claude_code_executable": "claude (in image)" if container else (str(claude) if claude else None),
            "claude_code_tools": list(TOOLS),
            "claude_code_isolation": isolation_mode(provider_env(), container=container),
            "claude_code_egress_allow": list(allowlist("claude_code_cli")) if container else None,
        }
        if container:
            claude_info["claude_code_cli_version"] = (images.get("claude_code") or {}).get("cli_version")
            claude_info["claude_code_image"] = images.get("claude_code")
        if claude is not None:
            try:
                claude_info["claude_code_cli_version"] = detect_claude_version(claude)
                claude_info["claude_code_binary_sha256"] = sha256_file(claude.resolve())
            except Exception as exc:  # noqa: BLE001
                claude_info["claude_code_cli_version"] = f"unreadable: {exc}"
    if track == "cursor_product":
        from runner.egress import allowlist
        from runner.images import load_images
        from runner.providers.cursor_cli import CLI_CONFIG, KEY_ENV, cursor_model_arg

        record = load_images().get("cursor") or {}
        claude_info = {
            "cursor_runtime": "container",
            "cursor_cli_version": record.get("cli_version"),
            "cursor_image": record or None,
            "cursor_model_arg": cursor_model_arg(requested_model, requested_effort),
            "cursor_cli_config": CLI_CONFIG,
            "cursor_api_key_set": bool(os.environ.get(KEY_ENV)),
            "cursor_egress_allow": list(allowlist("cursor_cli")),
        }
    from runner.images import load_images as _load_images

    toolchain_record = _load_images().get("toolchain")
    git = runner_git_state(root)
    manifest = {
        "utc_start": now.isoformat(),
        "local_timezone": local_name,
        "hostname": socket.gethostname(),
        "suite_id": suite_id,
        "suite_version": suite_version,
        "suite_hash": suite_hash,
        **git,
        "provider": provider,
        "track": track,
        "client_mode": client_mode,
        "requested_model": requested_model,
        "verified_model": verified_model,
        "requested_effort": requested_effort,
        "verified_effort": verified_effort,
        "auth_surface": auth_surface,
        "codex_cli_version": codex_version,
        "codex_binary_sha256": codex_sha,
        "codex_executable": str(codex) if codex else None,
        "operating_system": platform.system(),
        "os_release": platform.release(),
        "kernel": platform.version(),
        "architecture": platform.machine(),
        "container_runtime_version": _cmd(["docker", "--version"]) if docker_available() else None,
        "python_version": platform.python_version(),
        "node_version": _cmd(["node", "--version"]),
        "go_version": _cmd(["go", "version"]),
        "rust_toolchain": _cmd(["rustc", "--version"]),
        "network_policy": network_policy,
        "sandbox_configuration": sandbox_configuration,
        "ignore_user_config": ignore_user_config,
        "ignore_rules": ignore_rules,
        "mcp_plugin_skill_policy": mcp_plugin_skill_policy,
        "schedule_randomization_seed": schedule_seed,
        "lockfile_hashes": lockfile_hashes(root),
        "which_codex": shutil.which("codex"),
        "which_docker": shutil.which("docker"),
        "toolchain_image": toolchain_record,
        **claude_info,
    }
    if extra:
        manifest.update(extra)
    return manifest
