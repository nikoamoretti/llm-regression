"""Benchmark identity and incompatible-comparison guards.

A point on a chart is never merely “Grok” or “Astra.” It is
model + effort + track + suite + protocol. Mixing those fields is a
methodology error unless the caller explicitly forces a non-canonical
comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runner import (
    CANONICAL_EFFORTS,
    CANONICAL_TRACKS,
    CONTROL_TRACK,
    PRIMARY_TRACK,
    SINGLE_AGENT_EFFORTS,
    ULTRA_TRACK,
)


@dataclass(frozen=True)
class SeriesIdentity:
    track: str
    model: str
    effort: str
    client_mode: str
    suite_id: str
    suite_version: str
    suite_hash: str
    grading_protocol: str = "hidden_final_state"
    auth_surface: str = "chatgpt"
    provider: str = ""
    protocol_version: str = "1.0.0"
    tool_protocol_version: str = "2.0.0"
    scientific_data: bool = True

    def key(self) -> tuple[str, ...]:
        return (
            self.provider or "",
            self.track,
            self.model,
            self.effort,
            self.client_mode,
            self.suite_id,
            self.suite_version,
            self.suite_hash,
            self.grading_protocol,
            self.auth_surface,
            self.protocol_version,
            self.tool_protocol_version,
        )

    def series_key(self) -> str:
        return "|".join(self.key())


INCOMPATIBLE_REASONS = {
    "track": "different track (model_only vs agentic vs Codex product vs Responses control vs Ultra)",
    "model": "different model slug",
    "effort": "different reasoning effort",
    "client_mode": "pinned-client and latest-client histories must stay separate",
    "suite_id": "different suite identity",
    "suite_version": "different suite version",
    "suite_hash": "suite content hash differs (task mutation or different members)",
    "grading_protocol": "different grading protocol",
    "auth_surface": "ChatGPT-authenticated Codex vs API-key track",
    "ultra": "Ultra must never be aggregated with single-agent efforts",
    "task_version": "different task version",
    "provider": "different provider (xAI vs OpenAI vs Codex)",
    "protocol_version": "different prompt/pairing protocol version",
    "tool_protocol_version": "different tool protocol version",
    "scientific_data": "non-scientific (gold/fake/synthetic) rows cannot mix with live series",
    "astra_max": "Astra max is auxiliary and must never blend into the four-level comparison",
    "codex_vs_api": "Codex product series must never mix with Grok/Astra API series",
}


def assert_single_agent_effort(effort: str) -> None:
    if effort == "ultra" or effort not in SINGLE_AGENT_EFFORTS:
        raise ValueError(
            f"effort {effort!r} is not a single-agent series; "
            "Ultra belongs on the independent codex_ultra track"
        )


def _is_api_track(track: str) -> bool:
    return track in CANONICAL_TRACKS


def _is_codex_track(track: str) -> bool:
    return track in {PRIMARY_TRACK, CONTROL_TRACK, ULTRA_TRACK}


def compatibility_problems(left: SeriesIdentity, right: SeriesIdentity) -> list[str]:
    problems: list[str] = []
    for field_name in (
        "track",
        "model",
        "effort",
        "client_mode",
        "suite_id",
        "suite_version",
        "suite_hash",
        "grading_protocol",
        "auth_surface",
        "protocol_version",
        "tool_protocol_version",
    ):
        if getattr(left, field_name) != getattr(right, field_name):
            problems.append(INCOMPATIBLE_REASONS[field_name])
    if left.provider and right.provider and left.provider != right.provider:
        problems.append(INCOMPATIBLE_REASONS["provider"])
    if left.scientific_data != right.scientific_data:
        problems.append(INCOMPATIBLE_REASONS["scientific_data"])
    if {left.track, right.track} == {PRIMARY_TRACK, CONTROL_TRACK}:
        if INCOMPATIBLE_REASONS["track"] not in problems:
            problems.append(INCOMPATIBLE_REASONS["track"])
    if ULTRA_TRACK in {left.track, right.track} and left.track != right.track:
        problems.append(INCOMPATIBLE_REASONS["ultra"])
    if left.effort == "ultra" or right.effort == "ultra":
        if left.effort != right.effort:
            problems.append(INCOMPATIBLE_REASONS["ultra"])
    if _is_api_track(left.track) != _is_api_track(right.track) or _is_codex_track(
        left.track
    ) != _is_codex_track(right.track):
        if left.track != right.track:
            problems.append(INCOMPATIBLE_REASONS["codex_vs_api"])
    if {left.effort, right.effort} == {"max", left.effort} or {left.effort, right.effort} == {
        "max",
        right.effort,
    }:
        if (
            left.effort != right.effort
            and "max" in {left.effort, right.effort}
            and (
                left.effort in CANONICAL_EFFORTS
                or right.effort in CANONICAL_EFFORTS
            )
        ):
            problems.append(INCOMPATIBLE_REASONS["astra_max"])
    return problems


def require_compatible(
    left: SeriesIdentity,
    right: SeriesIdentity,
    *,
    force: bool = False,
) -> dict[str, Any]:
    problems = compatibility_problems(left, right)
    if problems and not force:
        raise ValueError("incompatible benchmark identities: " + "; ".join(problems))
    return {
        "canonical": not problems,
        "non_canonical": bool(problems),
        "problems": problems,
        "force": force,
    }


def identity_from_mapping(data: dict[str, Any]) -> SeriesIdentity:
    return SeriesIdentity(
        track=str(data["track"]),
        model=str(data["model"]),
        effort=str(data["effort"]),
        client_mode=str(data.get("client_mode", "latest")),
        suite_id=str(data["suite_id"]),
        suite_version=str(data.get("suite_version", "")),
        suite_hash=str(data.get("suite_hash", "")),
        grading_protocol=str(data.get("grading_protocol", "hidden_final_state")),
        auth_surface=str(data.get("auth_surface", "chatgpt")),
        provider=str(data.get("provider") or ""),
        protocol_version=str(data.get("protocol_version") or "1.0.0"),
        tool_protocol_version=str(data.get("tool_protocol_version") or "2.0.0"),
        scientific_data=_coerce_scientific(data.get("scientific_data", True)),
    )


def _coerce_scientific(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.lower() in {"0", "false", "no"}:
        return False
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)
