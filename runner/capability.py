"""Fail-closed Codex model/effort capability detection.

Never silently downgrade Max to xhigh/high/default. Accepting a config file is
not proof that Max was applied. Verification requires a machine-readable
capability surface or an explicit probe result that echoes the requested values.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from runner import SINGLE_AGENT_EFFORTS
from runner.errors import InvalidConfigurationError
from runner.providers.codex_cli import (
    detect_codex_version,
    effort_cli_flag,
    find_codex_executable,
    sha256_file,
)
from runner.providers.jsonl import extract_verified_effort, extract_verified_model, parse_jsonl


@dataclass
class CapabilityReport:
    executable: str
    version: str
    executable_sha256: str
    requested_model: str
    requested_effort: str
    verified_model: str | None
    verified_effort: str | None
    effort_flag: list[str]
    effort_syntax: str
    method: str
    accepted: bool
    notes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_codex_capabilities(codex_bin: Path) -> dict[str, Any]:
    """Use the strongest available machine-readable Codex surface."""
    raw: dict[str, Any] = {"probes": []}
    for args in (
        ["features", "--json"],
        ["features"],
        ["features", "list"],
        ["debug", "config"],
        ["exec", "--help"],
        ["--help"],
    ):
        try:
            proc = subprocess.run(
                [str(codex_bin), *args],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raw["probes"].append({"args": args, "error": str(exc)})
            continue
        raw["probes"].append(
            {
                "args": args,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[:8000],
                "stderr": (proc.stderr or "")[:4000],
            }
        )
        if proc.returncode == 0 and (proc.stdout or "").strip().startswith("{"):
            try:
                raw["features_json"] = json.loads(proc.stdout)
            except json.JSONDecodeError:
                pass
    return raw


def detect_effort_syntax(inspection: dict[str, Any]) -> str:
    blob = json.dumps(inspection).lower()
    if "--reasoning-effort" in blob:
        return "long_flag"
    return "config_override"


def parse_supported_models_efforts(inspection: dict[str, Any]) -> tuple[set[str], set[str]]:
    models: set[str] = set()
    efforts: set[str] = set()
    features = inspection.get("features_json")
    if isinstance(features, dict):
        for item in features.get("models") or []:
            models.add(str(item))
        for item in features.get("reasoning_efforts") or features.get("efforts") or []:
            efforts.add(str(item))
        if features.get("model"):
            models.add(str(features["model"]))
    blob = json.dumps(inspection).lower()
    for effort in (*SINGLE_AGENT_EFFORTS, "ultra"):
        token = f"model_reasoning_effort={effort}"
        if token in blob or f'"{effort}"' in blob:
            efforts.add(effort)
    if "gpt-5.6-sol" in blob:
        models.add("gpt-5.6-sol")
    return models, efforts


def probe_effort(
    *,
    codex_bin: Path,
    model: str,
    effort: str,
    effort_flag: list[str],
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Tiny ephemeral invocation used only when inspection is inconclusive."""
    env = os.environ.copy()
    env.update(extra_env or {})
    env.setdefault("CODEX_QUIET_MODE", "1")
    command = [
        str(codex_bin),
        "exec",
        "-m",
        model,
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        *effort_flag,
        "Reply with the single word PONG and do not modify files.",
    ]
    with tempfile.TemporaryDirectory(prefix="codex-probe-") as tmp:
        proc = subprocess.run(
            command,
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    return {
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def extract_verified_from_probe(stdout: str, stderr: str) -> tuple[str | None, str | None, list[str]]:
    notes: list[str] = []
    events, _ = parse_jsonl(stdout)
    verified_model = extract_verified_model(events)
    verified_effort = extract_verified_effort(events)
    blob = (stdout + "\n" + stderr).lower()
    if "unknown" in blob and "effort" in blob:
        notes.append("Codex rejected the requested effort value")
    if "unknown" in blob and "model" in blob:
        notes.append("Codex rejected the requested model value")
    return verified_model, verified_effort, notes


def verify_capability(
    *,
    model: str,
    effort: str,
    codex_bin: Path | None = None,
    allow_live_probe: bool = False,
    extra_env: dict[str, str] | None = None,
    effort_syntax: str | None = None,
    require_accepted: bool = True,
) -> CapabilityReport:
    notes: list[str] = []
    extra_env = extra_env or {}
    executable = find_codex_executable(str(codex_bin) if codex_bin else extra_env.get("CODEX_BIN"))
    if executable is None:
        raise InvalidConfigurationError("Codex executable not found")
    version = detect_codex_version(executable)
    digest = sha256_file(executable)
    inspection = inspect_codex_capabilities(executable)
    syntax = effort_syntax or detect_effort_syntax(inspection)
    effort_flag = effort_cli_flag(effort, syntax)
    hinted_models, hinted_efforts = parse_supported_models_efforts(inspection)
    verified_model = None
    verified_effort = None
    method = "inspection"
    accepted = False

    if effort in hinted_efforts and model in hinted_models:
        notes.append(
            "machine-readable inspection listed both model and effort; "
            "this is not treated as applied until an event or capability JSON confirms it"
        )

    if extra_env.get("CODEX_FORCE_VERIFIED_EFFORT"):
        verified_effort = extra_env["CODEX_FORCE_VERIFIED_EFFORT"]
        verified_model = extra_env.get("CODEX_FORCE_VERIFIED_MODEL", model)
        method = "forced_env"
        accepted = verified_effort == effort and verified_model == model
    elif extra_env.get("CODEX_CAPABILITY_JSON"):
        method = "capability_json"
        payload = json.loads(Path(extra_env["CODEX_CAPABILITY_JSON"]).read_text(encoding="utf-8"))
        verified_model = payload.get("verified_model")
        verified_effort = payload.get("verified_effort")
        accepted = verified_model == model and verified_effort == effort
        inspection["capability_json"] = payload
    elif isinstance(inspection.get("features_json"), dict) and extra_env.get("CODEX_TRUST_FEATURES_JSON") == "1":
        method = "features_json"
        features = inspection["features_json"]
        models = {str(item) for item in (features.get("models") or [])}
        efforts = {str(item) for item in (features.get("reasoning_efforts") or features.get("efforts") or [])}
        if model in models and effort in efforts:
            verified_model = model
            verified_effort = effort
            accepted = True
            notes.append("trusted structured features JSON from the Codex executable")
        else:
            notes.append("structured features JSON did not list the requested combination")
    elif allow_live_probe:
        method = "ephemeral_probe"
        notes.append("LIVE CAPABILITY PROBE EXECUTED")
        probe = probe_effort(
            codex_bin=executable,
            model=model,
            effort=effort,
            effort_flag=effort_flag,
            extra_env=extra_env,
        )
        inspection["live_probe"] = {
            "command": probe["command"],
            "returncode": probe["returncode"],
            "stdout_head": probe["stdout"][:4000],
            "stderr_head": probe["stderr"][:2000],
        }
        verified_model, verified_effort, extra_notes = extract_verified_from_probe(
            probe.get("stdout", ""), probe.get("stderr", "")
        )
        notes.extend(extra_notes)
        accepted = verified_model == model and verified_effort == effort
    else:
        notes.append(
            "capability unproven: inspection alone is insufficient; "
            "provide CODEX_CAPABILITY_JSON, CODEX_TRUST_FEATURES_JSON=1 with structured features, "
            "or pass --allow-live-probe"
        )
        accepted = False

    if effort not in SINGLE_AGENT_EFFORTS and effort != "ultra":
        raise InvalidConfigurationError(f"unknown effort {effort}")

    report = CapabilityReport(
        executable=str(executable),
        version=version,
        executable_sha256=digest,
        requested_model=model,
        requested_effort=effort,
        verified_model=verified_model,
        verified_effort=verified_effort,
        effort_flag=effort_flag,
        effort_syntax=syntax,
        method=method,
        accepted=accepted,
        notes=notes,
        raw=inspection,
    )
    if require_accepted and not accepted:
        raise InvalidConfigurationError(
            "requested model/effort cannot be established; refusing to silently downgrade. "
            + json.dumps({"requested": [model, effort], "verified": [verified_model, verified_effort]}),
            requested={"model": model, "effort": effort},
            verified={"model": verified_model, "effort": verified_effort},
        )
    if require_accepted and (verified_effort != effort or verified_model != model):
        raise InvalidConfigurationError(
            f"capability mismatch requested={model}/{effort} verified={verified_model}/{verified_effort}",
            requested={"model": model, "effort": effort},
            verified={"model": verified_model, "effort": verified_effort},
        )
    return report


def mismatch_is_invalid(requested_model: str, requested_effort: str, verified_model: str | None, verified_effort: str | None) -> bool:
    if verified_model and verified_model != requested_model:
        return True
    if verified_effort and verified_effort != requested_effort:
        return True
    return False
