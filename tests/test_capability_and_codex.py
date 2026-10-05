from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from runner.capability import verify_capability
from runner.errors import InvalidConfigurationError
from runner.providers.codex_cli import CodexCLIProvider, build_codex_exec_command, effort_cli_flag
from runner.status import classify_attempt, reliability_metrics, task_balanced_scores


FAKE = Path(__file__).resolve().parent / "fakes" / "codex"


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(extra)
    cap = tmp_path / "capability.json"
    cap.write_text(json.dumps({"verified_model": "gpt-5.6-sol", "verified_effort": "max"}), encoding="utf-8")
    env.setdefault("CODEX_CAPABILITY_JSON", str(cap))
    env["CODEX_BIN"] = str(FAKE)
    return env


def test_fake_codex_is_executable() -> None:
    assert FAKE.exists()
    assert FAKE.stat().st_mode & stat.S_IXUSR


def test_capability_accepts_max_from_capability_json(tmp_path, monkeypatch) -> None:
    env = _env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    report = verify_capability(model="gpt-5.6-sol", effort="max", extra_env=env)
    assert report.accepted
    assert report.verified_effort == "max"
    assert report.effort_flag == ["-c", "model_reasoning_effort=max"]


def test_capability_fails_closed_on_mismatch(tmp_path, monkeypatch) -> None:
    cap = tmp_path / "capability.json"
    cap.write_text(json.dumps({"verified_model": "gpt-5.6-sol", "verified_effort": "high"}), encoding="utf-8")
    env = {"CODEX_BIN": str(FAKE), "CODEX_CAPABILITY_JSON": str(cap)}
    with pytest.raises(InvalidConfigurationError):
        verify_capability(model="gpt-5.6-sol", effort="max", extra_env=env)


def test_codex_command_for_max() -> None:
    command = build_codex_exec_command(
        codex_bin=FAKE,
        model="gpt-5.6-sol",
        prompt="frozen prompt",
        effort_flag=effort_cli_flag("max"),
    )
    assert command[:2] == [str(FAKE), "exec"]
    assert "-m" in command and "gpt-5.6-sol" in command
    assert "--ephemeral" in command
    assert "--sandbox" in command and "workspace-write" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--json" in command
    assert "-c" in command and "model_reasoning_effort=max" in command
    assert "xhigh" not in command
    assert "high" not in "".join(command[2:])


@pytest.mark.parametrize(
    "scenario,expect",
    [
        ("success", {"exit": 0, "infra": False, "invalid": False}),
        ("quality_fail", {"exit": 0, "infra": False, "invalid": False}),
        ("http_429", {"exit": 1, "infra": True, "code": "http_429"}),
        ("http_500", {"exit": 1, "infra": True, "code": "http_5xx"}),
        ("stream_interrupt", {"exit": 1}),
        ("malformed_jsonl", {"exit": 0}),
        ("unknown_event", {"exit": 0, "unknown": True}),
        ("effort_mismatch", {"invalid": True, "code": "effort_mismatch"}),
        ("model_mismatch", {"invalid": True, "code": "model_mismatch"}),
        ("auth_mismatch", {"exit": 1}),
        ("crash", {"infra": True, "code": "empty_crash"}),
    ],
)
def test_fake_codex_scenarios(tmp_path, monkeypatch, scenario, expect) -> None:
    monkeypatch.setenv("CODEX_FAKE_SCENARIO", scenario)
    provider = CodexCLIProvider(codex_bin=FAKE, model="gpt-5.6-sol", effort="max")
    result = provider.run_attempt(
        prompt="PONG",
        workspace=tmp_path,
        model="gpt-5.6-sol",
        effort="max",
        timeout_seconds=5,
    )
    if "exit" in expect:
        assert result.exit_code == expect["exit"]
    if expect.get("infra"):
        assert result.infrastructure
        assert result.error_code == expect["code"]
    if expect.get("invalid"):
        assert result.invalid_configuration
        assert result.error_code == expect["code"]
    if expect.get("unknown"):
        assert "future.event.v9" in (result.metadata.get("summary") or {}).get("unknown_event_types", [])
    if scenario == "malformed_jsonl":
        assert result.metadata.get("malformed_jsonl")


def test_timeout_is_quality_fail(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_FAKE_SCENARIO", "timeout")
    monkeypatch.setenv("CODEX_FAKE_SLEEP", "2")
    provider = CodexCLIProvider(codex_bin=FAKE, model="gpt-5.6-sol", effort="max")
    result = provider.run_attempt(
        prompt="PONG",
        workspace=tmp_path,
        model="gpt-5.6-sol",
        effort="max",
        timeout_seconds=1,
    )
    assert result.error_code == "timeout"
    assert classify_attempt(timed_out=True, error_code="timeout") == "quality_fail"


def test_reliability_metrics_exclude_infra_from_quality() -> None:
    metrics = reliability_metrics(
        ["quality_pass", "quality_fail", "infra_fail", "quality_pass", "harness_fail"]
    )
    assert metrics["quality_given_valid"] == pytest.approx(2 / 3)
    assert metrics["operational_availability"] == pytest.approx(3 / 5)
    assert metrics["end_to_end_success"] == pytest.approx(2 / 5)


def test_repeated_attempts_do_not_reweight_tasks() -> None:
    scores = task_balanced_scores(
        {
            "A": ["quality_pass", "quality_pass", "quality_pass"],
            "B": ["quality_fail"],
        }
    )
    assert scores["suite_score"] == pytest.approx(0.5)
    assert scores["n_tasks"] == 2
