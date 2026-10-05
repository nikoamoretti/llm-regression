"""Safe preflight for Grok/Astra API series and optional Codex / Claude Code / Cursor."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from runner import (
    CANONICAL_EFFORTS,
    CANONICAL_MODELS,
    CANONICAL_TRACKS,
    CLAUDE_CODE_MODEL,
    CLAUDE_CODE_TRACK,
    CURSOR_MODEL,
    CURSOR_TRACK,
    PRIMARY_TRACK,
)
from runner.capability import verify_capability
from runner.config import load_suites
from runner.isolation import probe_from_workspace
from runner.models import resolve_model, validate_model_effort_track
from runner.providers.codex_cli import find_codex_executable, sha256_file
from runner.providers.openai_astra import astra_api_key, astra_key_source
from runner.providers.xai_grok import grok_api_key
from runner.provenance import runner_git_state
from runner.sandbox import docker_available
from runner.storage import Store
from runner.tasks import discover_tasks, validate_task


def _ok(label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"check": label, "ok": ok, "detail": detail}


def sqlite_version_tuple(version: str | None = None) -> tuple[int, int, int]:
    raw = version or sqlite3.sqlite_version
    parts = [int(item) for item in raw.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def sqlite_wal_known_safe(version: str | None = None) -> bool:
    return sqlite_version_tuple(version) >= (3, 51, 3)


def detect_auth_surface(codex_home: Path | None = None) -> dict[str, Any]:
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    auth = home / "auth.json"
    exists = auth.exists()
    kind = None
    if exists:
        try:
            payload = json.loads(auth.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if "tokens" in payload or "chatgpt" in json.dumps(payload).lower():
            kind = "chatgpt"
        elif "OPENAI_API_KEY" in os.environ or payload.get("api_key"):
            kind = "api_key"
        else:
            kind = "present_unclassified"
    if os.environ.get("OPENAI_API_KEY") and kind is None:
        kind = "api_key_env_only"
    return {
        "codex_home": str(home),
        "auth_file_present": exists,
        "auth_surface": kind,
        "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
        "astra_key_present": bool(astra_api_key()),
        "xai_key_present": bool(grok_api_key()),
    }


def _offline_checks(repo_root: Path, database_url: str, *, require_graders: bool = True) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    sqlite_ok = True
    sqlite_detail = database_url
    try:
        store = Store(database_url)
        with store.engine.begin() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        sqlite_ok = False
        sqlite_detail = str(exc)
    checks.append(_ok("sqlite_writable", sqlite_ok, sqlite_detail))
    wal_safe = sqlite_wal_known_safe()
    checks.append(
        _ok(
            "sqlite_wal_version",
            True if wal_safe else True,
            (
                f"SQLite {sqlite3.sqlite_version}; WAL-safe (>=3.51.3)={wal_safe}. "
                "Known-vulnerable WAL builds should not run concurrent writers."
            ),
        )
    )
    if not wal_safe:
        checks[-1]["ok"] = True
        checks[-1]["detail"] += " WARNING: concurrent WAL writers are unsafe on this SQLite."

    isolation_ok = True
    isolation_detail = "private grader paths are outside task fixtures"
    try:
        tasks = discover_tasks(repo_root / "tasks")
        visible = []
        for task in tasks[:1]:
            report = probe_from_workspace(
                task.fixture_path, [Path("gold.patch"), Path("hidden_tests"), Path("grader")]
            )
            visible.extend(report["visible"])
        if visible:
            isolation_ok = False
            isolation_detail = f"fixture leaked {visible}"
    except Exception as exc:  # noqa: BLE001
        isolation_ok = False
        isolation_detail = str(exc)
    checks.append(_ok("grader_isolation", isolation_ok, isolation_detail))

    usage = shutil.disk_usage(repo_root)
    free_gb = usage.free / (1024**3)
    checks.append(_ok("disk_space", free_gb >= 2, f"{free_gb:.1f} GiB free"))

    git = runner_git_state(repo_root)
    checks.append(
        _ok(
            "harness_git",
            not git["harness_git_dirty"] or os.environ.get("SOL_ALLOW_DIRTY") == "1",
            f"commit={git['harness_git_commit']} dirty={git['harness_git_dirty']}",
        )
    )

    hash_ok = True
    hash_detail = []
    hidden = []
    for task in discover_tasks(repo_root / "tasks"):
        errors = validate_task(task, check_hashes=True, require_graders=require_graders)
        if errors:
            hash_ok = False
            hash_detail.extend(errors)
        if not task.has_graders:
            hidden.append(task.id)
    if not hash_detail:
        hash_detail.append("all task hashes valid")
        if hidden:
            hash_detail.append(f"graders hidden, not checked here: {', '.join(sorted(hidden))}")
    checks.append(_ok("suite_hashes", hash_ok, "; ".join(hash_detail)))

    suites = load_suites(repo_root / "configs" / "suites.yaml")
    checks.append(_ok("suites_loaded", bool(suites), ", ".join(sorted(suites))))
    checks.append(
        _ok(
            "canonical_efforts",
            True,
            ",".join(CANONICAL_EFFORTS),
        )
    )
    docker_detail = (
        shutil.which("docker")
        if docker_available()
        else "docker not installed; local isolated fallback is supported and is not a hard failure"
    )
    checks.append(
        {
            **_ok("docker", True, docker_detail),
            "available": docker_available(),
            "blocking": False,
        }
    )
    return checks


def _codex_checks(
    *,
    model: str,
    effort: str,
    allow_live_probe: bool,
) -> tuple[list[dict[str, Any]], Any, bool, dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    executable = find_codex_executable(os.environ.get("CODEX_BIN"))
    checks.append(
        _ok(
            "codex_executable",
            executable is not None,
            str(executable) if executable else "codex not found on PATH or CODEX_BIN",
        )
    )
    if executable is not None:
        from runner.providers.codex_cli import detect_codex_version

        try:
            version = detect_codex_version(executable)
            digest = sha256_file(executable)
            checks.append(_ok("codex_version", True, version))
            checks.append(_ok("codex_sha256", True, digest))
        except Exception as exc:  # noqa: BLE001
            checks.append(_ok("codex_version", False, str(exc)))
    auth = detect_auth_surface()
    authenticated = auth["auth_file_present"] or os.environ.get("CODEX_FAKE_AUTH") == "1"
    checks.append(
        _ok(
            "chatgpt_auth",
            authenticated,
            f"surface={auth['auth_surface']} file={auth['auth_file_present']}",
        )
    )
    capability = None
    live_probe = False
    if executable is not None:
        try:
            capability = verify_capability(
                model=model,
                effort=effort,
                codex_bin=executable,
                allow_live_probe=allow_live_probe,
                extra_env=dict(os.environ),
                require_accepted=True,
            )
            live_probe = capability.method == "ephemeral_probe"
            checks.append(
                _ok(
                    "model_effort_accepted",
                    capability.accepted,
                    f"requested={model}/{effort} verified={capability.verified_model}/{capability.verified_effort} method={capability.method}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(_ok("model_effort_accepted", False, str(exc)))
    return checks, capability, live_probe, auth


def _claude_code_checks(*, model: str, effort: str, live: bool) -> tuple[list[dict[str, Any]], bool]:
    """Images, runtime, auth; with --live one tiny probe through the real provider."""
    import subprocess
    import tempfile
    import time

    from runner.images import host_execution_allowed, load_images, recorded_image
    from runner.providers.claude_code_cli import (
        ClaudeCodeCLIProvider,
        auth_status,
        detect_claude_version,
        find_claude_executable,
        isolation_mode,
        provider_env,
        sha256_file as claude_sha256,
    )

    checks: list[dict[str, Any]] = []
    try:
        validate_model_effort_track(model, effort, CLAUDE_CODE_TRACK)
        checks.append(_ok("claude_code_series", True, f"{model}/{effort}/{CLAUDE_CODE_TRACK}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_ok("claude_code_series", False, str(exc)))

    daemon = docker_available() and subprocess.run(
        ["docker", "info"], capture_output=True, check=False
    ).returncode == 0
    images = load_images()
    toolchain = recorded_image("toolchain") if daemon else None
    agent = recorded_image("claude_code") if daemon else None
    host = host_execution_allowed()
    build_hint = "run `python -m runner images build`"
    checks.append(_ok("docker_daemon", daemon or host, "reachable" if daemon else "not reachable"))
    checks.append(
        _ok(
            "toolchain_image",
            bool(toolchain) or host,
            f"{toolchain} {' / '.join((images.get('toolchain') or {}).get('versions') or [])}".strip()
            if toolchain
            else f"missing: {build_hint}" + (" (LLMREG_ALLOW_HOST=1: grading on host)" if host else ""),
        )
    )
    container = bool(agent and toolchain)
    checks.append(
        _ok(
            "claude_code_image",
            container or host,
            f"{agent} cli={(images.get('claude_code') or {}).get('cli_version')}"
            if agent
            else f"missing: {build_hint}" + (" (LLMREG_ALLOW_HOST=1: agent on host)" if host else ""),
        )
    )
    env = provider_env()
    if container:
        from runner.egress import allowlist

        checks.append(_ok("egress_allowlist", True, ", ".join(allowlist("claude_code_cli"))))
        checks.append(
            _ok(
                "claude_token",
                isolation_mode(env, container=True) is not None,
                "CLAUDE_CODE_OAUTH_TOKEN set"
                if env.get("CLAUDE_CODE_OAUTH_TOKEN")
                else "set CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token` (your Max login)",
            )
        )
        executable = Path("claude")
    elif host:
        executable = find_claude_executable()
        checks.append(
            _ok(
                "claude_executable",
                executable is not None,
                str(executable) if executable else "claude not found on PATH or CLAUDE_BIN",
            )
        )
        if executable is None:
            return checks, False
        try:
            checks.append(_ok("claude_version", True, detect_claude_version(executable)))
            checks.append(_ok("claude_sha256", True, claude_sha256(executable.resolve())))
        except Exception as exc:  # noqa: BLE001
            checks.append(_ok("claude_version", False, str(exc)))
        mode = isolation_mode(env)
        checks.append(
            _ok(
                "claude_isolation",
                mode is not None,
                {
                    "hermetic": "hermetic (host): empty CLAUDE_CONFIG_DIR per attempt + CLAUDE_CODE_OAUTH_TOKEN",
                    "user_config": "WARNING user_config: ~/.claude/CLAUDE.md and auto-memory can reach the model",
                    None: "set CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token` (or CLAUDE_ALLOW_USER_CONFIG=1)",
                }[mode],
            )
        )
        if mode != "hermetic":
            status = auth_status(executable)
            payload = status["payload"] if isinstance(status["payload"], dict) else {}
            logged_in = status["returncode"] == 0 and payload.get("loggedIn", True) is not False
            checks.append(
                _ok(
                    "claude_auth",
                    logged_in,
                    f"authMethod={payload.get('authMethod')} subscription={payload.get('subscriptionType')}",
                )
            )
    else:
        return checks, False
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if os.environ.get(key):
            checks.append(_ok(f"{key}_ignored", True, "stripped from Claude Code runs; subscription auth is used"))
    if not live:
        return checks, False

    from runner.providers.factory import claude_code_runtime

    runtime = claude_code_runtime() if container else None
    provider = ClaudeCodeCLIProvider(claude_bin=executable, model=model, effort=effort, runtime=runtime)
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="claude-probe-") as tmp:
        result = provider.run_attempt(
            prompt="Reply with the single word PONG. Do not use any tools or modify files.",
            workspace=Path(tmp),
            model=model,
            effort=effort,
            timeout_seconds=300,
        )
    probe_ok = not (result.invalid_configuration or result.infrastructure or result.error_code)
    checks.append(
        _ok(
            "claude_live_probe",
            probe_ok and result.verified_model == model,
            f"verified_model={result.verified_model} error={result.error_code} "
            f"effort={effort} (applied via --effort; not echoed)"
            + ("" if probe_ok else f" message={str((result.error or {}).get('message'))[:200]}"),
        )
    )
    if runtime:
        from runner.egress import decisions

        seen = sorted({f"{item['decision']}:{item['target']}" for item in decisions(since_epoch=started)})
        denied = [item for item in seen if not item.startswith("allow:")]
        checks.append(_ok("egress_during_probe", not denied, ", ".join(seen) or "no connections"))
    return checks, True


def _cursor_checks(*, model: str, effort: str, live: bool) -> tuple[list[dict[str, Any]], bool]:
    """Image, key and egress; with --live the account's model list and one tiny probe."""
    import subprocess
    import tempfile
    import time

    from runner.egress import allowlist, decisions, proxy_name
    from runner.images import load_images, recorded_image
    from runner.providers.cursor_cli import KEY_ENV, CursorCLIProvider, cursor_model_arg, list_models
    from runner.providers.factory import agent_runtime

    checks: list[dict[str, Any]] = []
    try:
        validate_model_effort_track(model, effort, CURSOR_TRACK)
        checks.append(_ok("cursor_series", True, f"{model}/{effort}/{CURSOR_TRACK} as {cursor_model_arg(model, effort)}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_ok("cursor_series", False, str(exc)))
    daemon = docker_available() and subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0
    image = recorded_image("cursor") if daemon else None
    checks.append(_ok("cursor_image", bool(image),
                      f"{image} cli={(load_images().get('cursor') or {}).get('cli_version')}" if image
                      else "missing: run `python -m runner images build`"))
    checks.append(_ok("cursor_api_key", bool(os.environ.get(KEY_ENV)),
                      f"{KEY_ENV} set" if os.environ.get(KEY_ENV)
                      else f"set {KEY_ENV} (Cursor dashboard → Integrations → User API Keys)"))
    checks.append(_ok("cursor_egress_allowlist", True, ", ".join(allowlist("cursor_cli"))))
    if not (live and image and os.environ.get(KEY_ENV)):
        return checks, False
    runtime = agent_runtime("cursor", "cursor_cli")
    assert runtime is not None
    started = time.time()
    try:
        listed = list_models(runtime)
        wanted = cursor_model_arg(model, effort)
        ids = [line.split(" - ", 1)[0].strip() for line in listed]
        related = [line for line in listed if model.split("-")[0] in line.lower()]
        checks.append(_ok("cursor_model_listed", wanted in ids,
                          f"{wanted} " + ("listed" if wanted in ids else "not listed; similar: " + "; ".join(related[:12]))))
    except Exception as exc:  # noqa: BLE001
        checks.append(_ok("cursor_model_listed", False, str(exc)[:300]))
    provider = CursorCLIProvider(model=model, effort=effort, runtime=runtime)
    with tempfile.TemporaryDirectory(prefix="cursor-probe-") as tmp:
        result = provider.run_attempt(
            prompt="Reply with the single word PONG. Do not use any tools or modify files.",
            workspace=Path(tmp), model=model, effort=effort, timeout_seconds=300,
        )
    probe_ok = not (result.invalid_configuration or result.infrastructure or result.error_code)
    display = (result.metadata.get("summary") or {}).get("init_model")
    checks.append(_ok("cursor_live_probe", probe_ok and result.verified_model == model,
                      f"served={display!r} verified_model={result.verified_model} error={result.error_code}"
                      + ("" if probe_ok else f" message={str((result.error or {}).get('message'))[:200]}")))
    seen = sorted({f"{item['decision']}:{item['target']}"
                   for item in decisions(since_epoch=started, name=proxy_name("cursor_cli"))})
    denied = [item for item in seen if not item.startswith("allow:")]
    checks.append(_ok("cursor_egress_during_probe", not denied, ", ".join(seen) or "no connections"))
    return checks, True


def _api_live_checks(models: list[str], tracks: list[str], effort: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for model in models:
        spec = resolve_model(model)
        for track in tracks:
            if track not in spec.tracks:
                continue
            try:
                validate_model_effort_track(model, effort if effort in spec.allowed_efforts else spec.comparison_efforts[0], track)
                checks.append(_ok(f"config:{model}/{track}", True, f"efforts={','.join(spec.comparison_efforts)}"))
            except Exception as exc:  # noqa: BLE001
                checks.append(_ok(f"config:{model}/{track}", False, str(exc)))
        if spec.provider == "xai":
            checks.append(_ok("XAI_API_KEY", bool(grok_api_key()), "required for grok-4.6 live runs"))
        if spec.provider == "openai":
            checks.append(
                _ok(
                    "OPENAI_API_KEY_or_ASTRA_API_KEY",
                    bool(astra_api_key()),
                    f"source={astra_key_source() or 'missing'}",
                )
            )
        checks.append(_ok(f"requested_model:{model}", spec.api_model == model, spec.api_model))
    return checks


def run_doctor(
    *,
    repo_root: Path,
    track: str,
    model: str,
    effort: str,
    allow_live_probe: bool = False,
    database_url: str = "sqlite:///./artifacts/regression.db",
    live: bool = False,
    models: list[str] | None = None,
    tracks: list[str] | None = None,
) -> dict[str, Any]:
    # A live run grades attempts, so it needs every hidden grader; an offline check (public CI) does not.
    checks = _offline_checks(repo_root, database_url, require_graders=live)
    capability = None
    live_probe = False
    auth = detect_auth_surface()
    chosen_models = models or ([model] if model else list(CANONICAL_MODELS))
    chosen_tracks = tracks or ([track] if track and track in CANONICAL_TRACKS else list(CANONICAL_TRACKS))

    if track == CLAUDE_CODE_TRACK:
        extra, live_probe = _claude_code_checks(model=model or CLAUDE_CODE_MODEL, effort=effort, live=live)
        checks.extend(extra)
    elif track == CURSOR_TRACK:
        extra, live_probe = _cursor_checks(model=model or CURSOR_MODEL, effort=effort, live=live)
        checks.extend(extra)
    elif track in {PRIMARY_TRACK, "codex_ultra", "responses_control"}:
        extra, capability, live_probe, auth = _codex_checks(
            model=model or "gpt-5.6-sol",
            effort=effort,
            allow_live_probe=allow_live_probe and live,
        )
        checks.extend(extra)
        if track == "responses_control" and live:
            checks.append(_ok("openai_api_key", bool(os.environ.get("OPENAI_API_KEY")), "required for responses_control"))
    elif live:
        checks.extend(_api_live_checks(chosen_models, chosen_tracks, effort))
        if not sqlite_wal_known_safe():
            checks.append(
                _ok(
                    "sqlite_wal_safe_for_live",
                    False,
                    f"SQLite {sqlite3.sqlite_version} is below 3.51.3; refuse concurrent WAL writers",
                )
            )
    else:
        checks.append(
            _ok(
                "offline_mode",
                True,
                "API keys are not required offline; live probes are not benchmark data",
            )
        )

    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "mode": "live" if live else "offline",
        "track": track,
        "model": model,
        "models": chosen_models,
        "tracks": chosen_tracks,
        "effort": effort,
        "live_probe_executed": live_probe,
        "auth": auth,
        "capability": capability.to_dict() if capability else None,
        "checks": checks,
        "note": "A tiny capability probe is not benchmark data.",
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "Grok / Astra longitudinal doctor",
        "────────────────────────────────────────────────",
        f"Mode                                  {report['mode']}",
        f"Track                                 {report['track']}",
        f"Requested                             {report['model']} / {report['effort']}",
        f"Live probe executed                   {report['live_probe_executed']}",
        "",
    ]
    for item in report["checks"]:
        mark = "PASS" if item["ok"] else "FAIL"
        lines.append(f"[{mark}] {item['check']}: {item['detail']}")
    lines.append("────────────────────────────────────────────────")
    if report["live_probe_executed"]:
        lines.append("NOTE: a tiny ephemeral probe was used. It is not benchmark data.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prove a benchmark configuration is valid")
    parser.add_argument("--track", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--models", default="")
    parser.add_argument("--tracks", default="")
    parser.add_argument("--effort", default="xhigh")
    parser.add_argument("--allow-live-probe", action="store_true")
    parser.add_argument("--database-url", default="sqlite:///./artifacts/regression.db")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    live = bool(args.live) and not args.offline
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    tracks = [item.strip() for item in args.tracks.split(",") if item.strip()]
    report = run_doctor(
        repo_root=Path.cwd(),
        track=args.track or (PRIMARY_TRACK if live and args.model == "gpt-5.6-sol" else "model_only"),
        model=args.model,
        effort=args.effort,
        allow_live_probe=args.allow_live_probe,
        database_url=args.database_url,
        live=live,
        models=models or None,
        tracks=tracks or None,
    )
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
