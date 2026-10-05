"""Canonical model catalog for Grok 4.6 and GPT-6 Astra."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from runner.errors import InvalidConfigurationError

ROOT = Path(__file__).resolve().parents[1]
MODELS_PATH = ROOT / "configs" / "models.yaml"

COMPARISON_EFFORTS = ("low", "medium", "high", "xhigh")
AUXILIARY_ASTRA_EFFORTS = ("max",)
CANONICAL_MODELS = ("grok-4.6", "gpt-6-astra")
CANONICAL_TRACKS = ("model_only", "agentic")
OPTIONAL_CODEX_MODEL = "gpt-5.6-sol"
OPTIONAL_CODEX_TRACK = "codex_product"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    display_name: str
    provider: str
    api_model: str
    comparison_efforts: tuple[str, ...]
    auxiliary_efforts: tuple[str, ...]
    tracks: tuple[str, ...]
    scientific: bool
    api_base_env: str = ""
    default_api_base: str = ""
    responses_path: str = "/responses"
    api_key_env: str = ""
    api_key_env_aliases: tuple[str, ...] = ()
    send_sampling_params: bool = False
    temperature: float | None = None
    top_p: float | None = None
    store: bool = False
    note: str = ""
    optional: bool = False

    @property
    def allowed_efforts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*self.comparison_efforts, *self.auxiliary_efforts]))

    def is_comparison_effort(self, effort: str) -> bool:
        return effort in self.comparison_efforts

    def is_auxiliary_effort(self, effort: str) -> bool:
        return effort in self.auxiliary_efforts


@dataclass(frozen=True)
class ModelCatalog:
    models: dict[str, ModelSpec] = field(default_factory=dict)

    def get(self, name: str) -> ModelSpec:
        if name not in self.models:
            known = ", ".join(sorted(self.models))
            raise InvalidConfigurationError(f"unknown model {name!r}; known models: {known}")
        return self.models[name]

    def canonical(self) -> list[ModelSpec]:
        return [self.models[name] for name in CANONICAL_MODELS if name in self.models]


def _spec_from_mapping(name: str, raw: dict[str, Any], *, optional: bool = False) -> ModelSpec:
    return ModelSpec(
        name=name,
        display_name=str(raw.get("display_name") or name),
        provider=str(raw["provider"]),
        api_model=str(raw.get("api_model") or name),
        comparison_efforts=tuple(raw.get("comparison_efforts") or COMPARISON_EFFORTS),
        auxiliary_efforts=tuple(raw.get("auxiliary_efforts") or ()),
        tracks=tuple(raw.get("tracks") or CANONICAL_TRACKS),
        scientific=bool(raw.get("scientific", True)),
        api_base_env=str(raw.get("api_base_env") or ""),
        default_api_base=str(raw.get("default_api_base") or ""),
        responses_path=str(raw.get("responses_path") or "/responses"),
        api_key_env=str(raw.get("api_key_env") or ""),
        api_key_env_aliases=tuple(raw.get("api_key_env_aliases") or ()),
        send_sampling_params=bool(raw.get("send_sampling_params", False)),
        temperature=raw.get("temperature"),
        top_p=raw.get("top_p"),
        store=bool(raw.get("store", False)),
        note=str(raw.get("note") or ""),
        optional=optional,
    )


@lru_cache(maxsize=1)
def load_model_catalog(path: Path | None = None) -> ModelCatalog:
    target = path or MODELS_PATH
    raw = yaml.safe_load(target.read_text()) or {}
    models: dict[str, ModelSpec] = {}
    for name, spec in (raw.get("models") or {}).items():
        models[name] = _spec_from_mapping(name, spec, optional=False)
    for name, spec in (raw.get("optional_series") or {}).items():
        models[name] = _spec_from_mapping(name, spec, optional=True)
    return ModelCatalog(models=models)


def resolve_model(name: str) -> ModelSpec:
    return load_model_catalog().get(name)


def validate_model_effort_track(model: str, effort: str, track: str) -> ModelSpec:
    spec = resolve_model(model)
    if track not in spec.tracks:
        raise InvalidConfigurationError(
            f"track {track!r} is not allowed for {model}; allowed tracks: {', '.join(spec.tracks)}"
        )
    if effort not in spec.allowed_efforts:
        raise InvalidConfigurationError(
            f"effort {effort!r} is not allowed for {model}; allowed efforts: {', '.join(spec.allowed_efforts)}"
        )
    if track in CANONICAL_TRACKS and effort not in (*COMPARISON_EFFORTS, *AUXILIARY_ASTRA_EFFORTS):
        raise InvalidConfigurationError(f"unsupported effort {effort!r} for track {track}")
    if effort == "max" and "max" not in spec.allowed_efforts:
        raise InvalidConfigurationError(f"max is not an allowed effort for {model}")
    if effort == "max" and model == "gpt-6-astra" and track in CANONICAL_TRACKS:
        # Allowed as auxiliary only; callers must not blend into four-level comparison.
        pass
    return spec


def four_level_efforts() -> tuple[str, ...]:
    return COMPARISON_EFFORTS
