"""Frozen model-configuration objects and suite manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from runner import (
    CANONICAL_EFFORTS,
    CANONICAL_MODELS,
    CANONICAL_TRACKS,
    DEFAULT_EFFORT,
    PRIMARY_TRACK,
    REASONING_EFFORTS,
    REQUEST_MODEL,
    ULTRA_TRACK,
)
from runner.hash_tree import canonical_hash, hash_file, hash_text


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    reasoning_effort: str
    temperature: float | None
    top_p: float | None
    max_output_tokens: int
    parallel_tool_calls: bool
    store: bool
    system_prompt_version: str
    toolset_version: str
    track: str
    extra: dict[str, Any]
    client_mode: str = "latest"
    auth_surface: str = "chatgpt"
    send_sampling_params: bool = False
    protocol_version: str = "1.0.0"
    tool_protocol_version: str = "2.0.0"

    def to_canonical(self) -> dict[str, Any]:
        payload = {
            "provider": self.provider,
            "model": self.model,
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
            "parallel_tool_calls": self.parallel_tool_calls,
            "store": self.store,
            "system_prompt_version": self.system_prompt_version,
            "toolset_version": self.toolset_version,
            "track": self.track,
            "client_mode": self.client_mode,
            "auth_surface": self.auth_surface,
            "send_sampling_params": self.send_sampling_params,
            "protocol_version": self.protocol_version,
            "tool_protocol_version": self.tool_protocol_version,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.top_p is not None:
            payload["top_p"] = self.top_p
        if self.extra:
            payload["extra"] = self.extra
        return payload

    @property
    def config_sha256(self) -> str:
        return canonical_hash(self.to_canonical())

    @property
    def series_key(self) -> str:
        return (
            f"{self.provider} / {self.model} / {self.reasoning_effort} / "
            f"{self.track} / {self.client_mode}"
        )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _default_temperature(data: dict[str, Any], track: str) -> float | None:
    if "temperature" in data:
        return None if data["temperature"] is None else float(data["temperature"])
    if track in {PRIMARY_TRACK, ULTRA_TRACK, "responses_control"}:
        return 0.0
    return None


def _default_top_p(data: dict[str, Any], track: str) -> float | None:
    if "top_p" in data:
        return None if data["top_p"] is None else float(data["top_p"])
    if track in {PRIMARY_TRACK, ULTRA_TRACK, "responses_control"}:
        return 1.0
    return None


def model_config_from_json(data: dict[str, Any]) -> ModelConfig:
    effort = data.get("reasoning", {}).get("effort", DEFAULT_EFFORT)
    allowed = set(REASONING_EFFORTS) | {"ultra"}
    if effort not in allowed:
        raise ValueError(f"Unknown reasoning effort: {effort}")
    extra = {
        key: value
        for key, value in data.items()
        if key
        not in {
            "provider",
            "model",
            "reasoning",
            "temperature",
            "top_p",
            "max_output_tokens",
            "parallel_tool_calls",
            "store",
            "system_prompt_version",
            "toolset_version",
            "track",
            "client_mode",
            "auth_surface",
            "send_sampling_params",
            "protocol_version",
            "tool_protocol_version",
        }
    }
    track = data.get("track", PRIMARY_TRACK)
    if effort == "ultra":
        track = ULTRA_TRACK
    if track in CANONICAL_TRACKS:
        auth = data.get("auth_surface", "api_key")
    elif track == "responses_control":
        auth = data.get("auth_surface", "api_key")
    else:
        auth = data.get("auth_surface", "chatgpt")
    send_sampling = bool(data.get("send_sampling_params", track not in CANONICAL_TRACKS))
    return ModelConfig(
        provider=data.get("provider", "codex_cli"),
        model=data.get("model", REQUEST_MODEL),
        reasoning_effort=effort,
        temperature=_default_temperature(data, track),
        top_p=_default_top_p(data, track),
        max_output_tokens=int(data.get("max_output_tokens", 16384)),
        parallel_tool_calls=bool(data.get("parallel_tool_calls", False)),
        store=bool(data.get("store", False)),
        system_prompt_version=data.get("system_prompt_version", "codex-frozen-v1"),
        toolset_version=data.get("toolset_version", "codex-cli-v1"),
        track=track,
        extra=extra,
        client_mode=data.get("client_mode", "latest"),
        auth_surface=auth,
        send_sampling_params=send_sampling,
        protocol_version=str(data.get("protocol_version") or "1.0.0"),
        tool_protocol_version=str(data.get("tool_protocol_version") or "2.0.0"),
    )


def load_model_config(path: Path) -> ModelConfig:
    return model_config_from_json(load_json(path))


def default_configs_dir(root: Path | None = None) -> Path:
    return (root or Path.cwd()) / "configs"


def iter_effort_configs(root: Path | None = None) -> dict[str, ModelConfig]:
    configs = {}
    for effort in REASONING_EFFORTS:
        for prefix in ("sol", "codex"):
            path = default_configs_dir(root) / f"{prefix}-{effort}.json"
            if path.exists():
                configs[effort] = load_model_config(path)
                break
    return configs


@dataclass(frozen=True)
class SuiteSpec:
    name: str
    version: str
    tasks: list[str]
    trials: int
    efforts: list[str]
    track: str
    notes: str = ""
    suite_class: str = "canary"
    models: list[str] | None = None
    tracks: list[str] | None = None


def load_suites(path: Path) -> dict[str, SuiteSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    suites = {}
    for name, item in raw.items():
        suites[name] = SuiteSpec(
            name=name,
            version=str(item["version"]),
            tasks=list(item.get("tasks") or []),
            trials=int(item.get("trials", 1)),
            efforts=list(item.get("efforts", list(CANONICAL_EFFORTS))),
            track=item.get("track", PRIMARY_TRACK),
            notes=item.get("notes", ""),
            suite_class=item.get("class", name),
            models=list(item["models"]) if item.get("models") else None,
            tracks=list(item["tracks"]) if item.get("tracks") else None,
        )
    return suites


def system_prompt_hash(text: str) -> str:
    return hash_text(text)


def prompt_file_hash(path: Path) -> str:
    return hash_file(path)


def default_experiment_models() -> list[str]:
    return list(CANONICAL_MODELS)


def default_experiment_tracks() -> list[str]:
    return list(CANONICAL_TRACKS)
