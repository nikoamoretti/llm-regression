"""Construct the provider for a (model, track, effort) series."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from runner import CLAUDE_CODE_TRACK, CONTROL_TRACK, CURSOR_TRACK, PRIMARY_TRACK, ULTRA_TRACK
from runner.capability import verify_capability
from runner.config import ModelConfig
from runner.errors import InvalidConfigurationError
from runner.models import resolve_model
from runner.images import host_execution_allowed, recorded_image
from runner.providers.claude_code_cli import ClaudeCodeCLIProvider, ContainerRuntime, find_claude_executable
from runner.providers.cursor_cli import CursorCLIProvider
from runner.providers.codex_cli import CodexCLIProvider, find_codex_executable
from runner.providers.fake import FakeResponsesProvider
from runner.providers.openai_astra import AstraProvider
from runner.providers.openai_responses import OpenAIResponsesProvider
from runner.providers.xai_grok import GrokProvider


def claude_code_runtime() -> ContainerRuntime | None:
    """Container runtime from the recorded images, with the egress proxy started."""
    return agent_runtime("claude_code", "claude_code_cli")


def agent_runtime(image_kind: str, provider: str) -> ContainerRuntime | None:
    """Runtime for a recorded agent image, egress limited to ``provider``'s allowlist (its own proxy)."""
    agent_image = recorded_image(image_kind)
    toolchain_image = recorded_image("toolchain")
    if not agent_image or not toolchain_image:
        return None
    from runner.egress import allowlist, ensure_egress, proxy_name

    extra_ca = os.environ.get("LLMREG_EXTRA_CA") or None
    if extra_ca and not Path(extra_ca).is_file():
        raise InvalidConfigurationError(f"LLMREG_EXTRA_CA={extra_ca} is not a file")
    egress = ensure_egress(toolchain_image, allowlist(provider), name=proxy_name(provider))
    return ContainerRuntime(
        image=agent_image,
        network=egress.network,
        proxy_url=egress.proxy_url,
        egress_allow=egress.allow,
        extra_ca=extra_ca,
    )


def make_provider(
    config: ModelConfig,
    *,
    source: str = "model",
    capability_env: dict[str, str] | None = None,
    fake_scenario: str = "ok",
    fake_patch: str = "",
) -> tuple[Any, Any]:
    if source == "fake":
        return (
            FakeResponsesProvider(
                model=config.model,
                effort=config.reasoning_effort,
                provider=config.provider,
                scenario=fake_scenario,
                patch_text=fake_patch,
            ),
            None,
        )
    if config.track in {PRIMARY_TRACK, ULTRA_TRACK}:
        executable = find_codex_executable()
        if executable is None:
            raise SystemExit("Codex executable not found. Install Codex CLI or set CODEX_BIN.")
        report = verify_capability(
            model=config.model,
            effort=config.reasoning_effort,
            codex_bin=executable,
            extra_env=capability_env,
            allow_live_probe=False,
        )
        return (
            CodexCLIProvider(
                codex_bin=executable,
                model=config.model,
                effort=config.reasoning_effort,
                effort_flag=report.effort_flag,
                extra_env=capability_env,
                auth_surface=config.auth_surface,
                effort_syntax=report.effort_syntax,
            ),
            report,
        )
    if config.track == CLAUDE_CODE_TRACK:
        # Identity is verified per attempt from the stream; run `doctor --live` first.
        runtime = claude_code_runtime()
        if runtime is None:
            if not host_execution_allowed():
                raise SystemExit(
                    "Claude Code agent image not built. Run `python -m runner images build`, "
                    "or set LLMREG_ALLOW_HOST=1 to run Claude Code on this host."
                )
            executable = find_claude_executable()
            if executable is None:
                raise SystemExit("Claude Code executable not found. Install Claude Code or set CLAUDE_BIN.")
        else:
            executable = Path("claude")
        return (
            ClaudeCodeCLIProvider(
                claude_bin=executable,
                model=config.model,
                effort=config.reasoning_effort,
                extra_env=capability_env,
                auth_surface=config.auth_surface,
                runtime=runtime,
            ),
            None,
        )
    if config.track == CURSOR_TRACK:
        # No host mode: the Cursor CLI only runs in its pinned image.
        runtime = agent_runtime("cursor", "cursor_cli")
        if runtime is None:
            raise SystemExit("Cursor agent image not built. Run `python -m runner images build`.")
        return (
            CursorCLIProvider(
                model=config.model,
                effort=config.reasoning_effort,
                runtime=runtime,
                extra_env=capability_env,
                auth_surface=config.auth_surface,
            ),
            None,
        )
    if config.track == CONTROL_TRACK:
        return OpenAIResponsesProvider(model=config.model, effort=config.reasoning_effort), None
    spec = resolve_model(config.model)
    if spec.provider == "xai":
        return GrokProvider(model=config.model, effort=config.reasoning_effort), None
    if spec.provider == "openai":
        return AstraProvider(model=config.model, effort=config.reasoning_effort), None
    raise InvalidConfigurationError(f"no provider for model {config.model} track {config.track}")
