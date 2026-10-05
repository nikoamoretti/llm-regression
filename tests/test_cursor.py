from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from runner.behavior import analyze
from runner.errors import InvalidConfigurationError
from runner.evaluate import _config_for
from runner.judge import final_message
from runner.models import validate_model_effort_track
from runner.providers.claude_code_cli import ContainerRuntime
from runner.providers.cursor_cli import (
    CLI_CONFIG,
    CursorCLIProvider,
    build_cursor_command,
    classify,
    cursor_model_arg,
    model_matches,
    redact,
    summarize_stream,
)

MODEL = "grok-4.7"
KEY = "key_live_0123456789abcdef"
RUNTIME = ContainerRuntime(image="sha256:cursor", network="n", proxy_url="http://llmreg-egress-cursor-cli:3128")


def _call(subtype: str, kind: str, args: dict, result: dict | None = None, call_id: str = "c1") -> dict:
    body = {"args": args, **({"result": result} if result is not None else {})}
    return {"type": "tool_call", "subtype": subtype, "call_id": call_id, "tool_call": {kind: body}}


def _text(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def stream(*middle: dict, model: str = "Grok 4.7 High", result_text: str = "done") -> str:
    events = [
        {"type": "system", "subtype": "init", "apiKeySource": "env", "model": model, "session_id": "s1",
         "permissionMode": "default"},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "fix it"}]}},
        *middle,
        {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 1200, "result": result_text,
         "session_id": "s1", "usage": {"inputTokens": 900, "outputTokens": 120, "cacheReadTokens": 50,
                                       "cacheWriteTokens": 0}},
    ]
    return "\n".join(json.dumps(event) for event in events) + "\n"


CAREFUL = (
    _text("Let me look."),
    _call("started", "readToolCall", {"path": "calc.py"}),
    _call("completed", "readToolCall", {"path": "calc.py"}, {"success": {"content": "..."}}),
    _call("started", "editToolCall", {"path": "calc.py"}, call_id="c2"),
    _call("completed", "editToolCall", {"path": "calc.py"}, {"success": {}}, call_id="c2"),
    _call("started", "shellToolCall", {"command": "python3 -m pytest -q"}, call_id="c3"),
    _call("completed", "shellToolCall", {"command": "python3 -m pytest -q"},
          {"success": {"exitCode": 0, "stdout": "3 passed"}}, call_id="c3"),
    _text("I fixed the rounding bug in calc.py; the 3 tests pass."),
)


def _provider_run(tmp_path, monkeypatch, stdout: str, *, key: str | None = KEY, exit_code: int = 0):
    import runner.providers.cursor_cli as cli

    seen: list[list[str]] = []

    def fake_run(command, **kwargs):
        seen.append(list(command))
        if list(command[:2]) == ["docker", "run"]:
            seen.append(sorted(kwargs.get("env") or {}))
            return subprocess.CompletedProcess(command, exit_code, stdout, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    if key:
        monkeypatch.setenv("CURSOR_API_KEY", key)
    else:
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    provider = CursorCLIProvider(model=MODEL, effort="high", runtime=RUNTIME)
    result = provider.run_attempt(prompt="fix it", workspace=tmp_path, model=MODEL, effort="high", timeout_seconds=5)
    return result, seen


def test_command_is_headless_and_pins_the_model() -> None:
    command = build_cursor_command(model_arg=cursor_model_arg(MODEL, "high"), prompt="fix it")
    assert command[:4] == ["agent", "-p", "--output-format", "stream-json"]
    assert command[command.index("--model") + 1] == "grok-4.7-high"
    assert command[command.index("--sandbox") + 1] == "disabled"
    assert command[-1] == "fix it"
    assert "WebSearch(*)" in CLI_CONFIG["permissions"]["deny"] and CLI_CONFIG["autoAcceptWebSearch"] is False
    # --force approves web search whatever the deny rules say; the allow list covers shell and edits instead.
    assert "--force" not in command and "--yolo" not in command
    assert {"Shell(*)", "Write(**)"} <= set(CLI_CONFIG["permissions"]["allow"])


def test_a_verified_attempt_runs_in_the_container_with_the_key_passed_by_name(tmp_path, monkeypatch) -> None:
    result, seen = _provider_run(tmp_path, monkeypatch, stream(*CAREFUL))
    docker = seen[0]
    assert not (result.invalid_configuration or result.infrastructure or result.error_code)
    assert result.verified_model == MODEL and result.verified_effort == "high"
    assert docker[docker.index("-e", docker.index("CURSOR_API_KEY") - 1) + 1] == "CURSOR_API_KEY"
    assert KEY not in " ".join(docker) and "sha256:cursor" in docker
    assert docker[docker.index("sha256:cursor") + 1 : docker.index("sha256:cursor") + 3] == ["sh", "-c"]
    assert "CURSOR_API_KEY" in seen[1] and "LLMREG_CURSOR_CONFIG" in seen[1]
    assert result.final_text == "I fixed the rounding bug in calc.py; the 3 tests pass."
    assert result.usage["output_tokens"] == 120 and result.usage["input_tokens"] == 900
    assert "<prompt>" in result.command and "fix it" not in result.command


@pytest.mark.parametrize(
    ("display", "code"),
    [("Auto", "model_mismatch"), ("Grok 4.71", "model_mismatch"), ("Claude Opus 5.5", "model_mismatch"),
     ("Grok 4.7 Low", "effort_mismatch")],
)
def test_a_different_served_model_or_level_is_invalid(tmp_path, monkeypatch, display, code) -> None:
    result, _ = _provider_run(tmp_path, monkeypatch, stream(*CAREFUL, model=display))
    assert result.invalid_configuration and result.error_code == code


def test_display_names_match_the_model_by_words() -> None:
    assert model_matches(MODEL, "Grok 4.7") and model_matches(MODEL, "grok-4.7-high")
    assert not model_matches(MODEL, "Grok 4") and not model_matches(MODEL, None)


def test_web_access_that_was_not_rejected_invalidates_the_attempt(tmp_path, monkeypatch) -> None:
    rejected = (_call("started", "webSearchToolCall", {"searchTerm": "forecastlab fix"}),
                _call("completed", "webSearchToolCall", {}, {"rejected": {"reason": "User Rejected"}}))
    result, _ = _provider_run(tmp_path, monkeypatch, stream(*rejected, *CAREFUL))
    assert not result.invalid_configuration
    assert result.metadata["summary"]["rejected_tools"] == ["WebSearch"]
    searched = (_call("started", "webFetchToolCall", {"url": "https://github.com/x"}),
                _call("completed", "webFetchToolCall", {}, {"success": {"content": "patch"}}))
    result, _ = _provider_run(tmp_path, monkeypatch, stream(*searched, *CAREFUL))
    assert result.invalid_configuration and result.error_code == "web_access"


def test_without_the_key_or_image_the_attempt_is_refused(tmp_path, monkeypatch) -> None:
    result, seen = _provider_run(tmp_path, monkeypatch, stream(*CAREFUL), key=None)
    assert result.invalid_configuration and result.error_code == "isolation_unavailable" and not seen
    monkeypatch.setenv("CURSOR_API_KEY", KEY)
    provider = CursorCLIProvider(model=MODEL, effort="high", runtime=None)
    result = provider.run_attempt(prompt="p", workspace=tmp_path, model=MODEL, effort="high", timeout_seconds=5)
    assert result.invalid_configuration and "images build" in result.error["message"]


def test_auth_failure_without_a_result_is_infrastructure(tmp_path, monkeypatch) -> None:
    verdict = classify(summarize_stream([]), model=MODEL, effort="high", exit_code=1,
                       stderr="Warning: The provided API key is invalid.")
    assert verdict["infrastructure"] and verdict["error_code"] == "auth"
    verdict = classify(summarize_stream([]), model=MODEL, effort="high", exit_code=1,
                       stderr="Cannot use this model: grok-4.7-high")
    assert verdict["invalid"] and verdict["error_code"] == "model_unavailable"


def test_stored_transcripts_drop_env_snapshots_and_the_key() -> None:
    raw = json.dumps({"type": "tool_call", "subtype": "completed", "env": f"CURSOR_API_KEY={KEY}",
                      "tool_call": {"shellToolCall": {"args": {"command": f"echo {KEY}"}}}})
    cleaned = redact(raw + "\n", [KEY])
    assert KEY not in cleaned and '"env"' not in cleaned and "<redacted>" in cleaned


def test_behaviour_reads_the_cursor_stream_like_claude_code() -> None:
    careful = analyze(stream(*CAREFUL), "diff --git a/calc.py b/calc.py\n+x = 1\n", strict_pass=True)
    assert not any(careful["flags"].values())
    assert careful["metrics"]["tools"] == {"Bash": 1, "Edit": 1, "Read": 1}
    assert careful["metrics"]["test_runs"] == 1 and careful["metrics"]["output_tokens"] == 120
    lazy = analyze(stream(_call("started", "editToolCall", {"path": "calc.py"}),
                          _call("completed", "shellToolCall", {}, {"failure": {"exitCode": 1}}),
                          _text("I fixed it. All tests pass.")), "", strict_pass=False)
    assert lazy["flags"]["no_verification"] and lazy["flags"]["false_success_claim"]
    assert lazy["flags"]["unverified_test_claim"] and lazy["metrics"]["tool_errors"] == 1


def test_the_judge_reads_the_closing_message_not_the_whole_narration() -> None:
    assert final_message(stream(*CAREFUL, result_text="Let me look.I fixed…")) == (
        "I fixed the rounding bug in calc.py; the 3 tests pass."
    )


def test_series_is_its_own_product_track() -> None:
    config = _config_for(track="cursor_product", model=MODEL, effort="high", client_mode="latest")
    assert config.provider == "cursor_cli" and config.auth_surface == "cursor_subscription"
    assert config.toolset_version == "cursor-cli-v2"
    with pytest.raises(InvalidConfigurationError):
        validate_model_effort_track(MODEL, "high", "claude_code_product")
    with pytest.raises(InvalidConfigurationError):
        validate_model_effort_track("claude-opus-5-5", "high", "cursor_product")


def test_the_cursor_environment_is_layered_on_the_cursor_image(monkeypatch, tmp_path) -> None:
    from runner import images

    monkeypatch.setattr(images, "docker_image_available", lambda ref: True)
    monkeypatch.setattr(images, "_sha256", lambda path: "d" * 64)
    record = {"cursor": {"id": "sha256:cursor"}, "claude_code": {"id": "sha256:cc"},
              "environments": {"r" * 64: {"cursor_agent": {"id": "sha256:env-cursor", "base_id": "sha256:cursor",
                                                           "dockerfile_sha256": "d" * 64}}}}
    path = tmp_path / "images.json"
    path.write_text(json.dumps(record))
    monkeypatch.setenv("LLMREG_IMAGES_FILE", str(path))
    assert images.resolve_environment("r" * 64, "cursor_agent") == "sha256:env-cursor"
    assert images.resolve_environment("r" * 64, "agent") is None
    assert CursorCLIProvider.environment_kind == "cursor_agent"


def test_judge_and_behaviour_can_cover_every_run_since_a_time(tmp_path) -> None:
    from runner.judge import runs_since
    from runner.storage import Store

    store = Store(f"sqlite:///{tmp_path / 'r.db'}")
    assert runs_since(store, "2026-10-03T00:00:00") == []
    raw = sqlite3.connect(tmp_path / "r.db")  # foreign keys off: bare runs are enough here
    for run_id, started in (("old", "2026-10-02 23:59:00"), ("today", "2026-10-03 09:00:00")):
        raw.execute("INSERT INTO eval_runs (run_id, suite_id, config_id, trigger, status, runner_git_sha, "
                    "scientific_data, started_at) VALUES (?, 's', 'c', 'schedule', 'completed', 'x', 1, ?)",
                    (run_id, started))
    raw.commit()
    assert runs_since(store, "2026-10-03T08:52:00") == ["today"]


def test_package_hash_is_pinned_for_the_default_build() -> None:
    from runner.images import CURSOR_SHA256, DEFAULT_CURSOR_VERSION

    assert len(CURSOR_SHA256[DEFAULT_CURSOR_VERSION]) == 64
    dockerfile = (Path(__file__).resolve().parents[1] / "docker" / "Dockerfile.toolchain").read_text()
    assert 'sha256sum -c -' in dockerfile and "AS cursor" in dockerfile
