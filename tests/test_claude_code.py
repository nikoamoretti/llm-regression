from __future__ import annotations

import stat
from pathlib import Path

import pytest

from runner.artifacts import ArtifactStore
from runner.coordinator import Coordinator
from runner.errors import InvalidConfigurationError
from runner.evaluate import _config_for
from runner.models import validate_model_effort_track
from runner.providers.claude_code_cli import (
    ClaudeCodeCLIProvider,
    ContainerRuntime,
    build_claude_command,
    build_container_command,
    classify,
    isolation_mode,
    provider_env,
    summarize_stream,
)
from runner.providers.jsonl import parse_jsonl
from runner.storage import Store
from runner.tasks import select_tasks

FAKE = Path(__file__).resolve().parent / "fakes" / "claude"
ROOT = Path(__file__).resolve().parents[1]
MODEL = "claude-opus-5-5"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    monkeypatch.delenv("CLAUDE_ALLOW_USER_CONFIG", raising=False)
    monkeypatch.delenv("CLAUDE_FAKE_SCENARIO", raising=False)
    monkeypatch.delenv("CLAUDE_FAKE_PATCH", raising=False)


def _run(tmp_path: Path, effort: str = "xhigh", timeout: int = 30):
    provider = ClaudeCodeCLIProvider(claude_bin=FAKE, model=MODEL, effort=effort)
    return provider.run_attempt(prompt="fix it", workspace=tmp_path, model=MODEL, effort=effort, timeout_seconds=timeout)


def test_fake_claude_is_executable() -> None:
    assert FAKE.stat().st_mode & stat.S_IXUSR


def test_command_is_isolated_and_never_bare() -> None:
    command = build_claude_command(claude_bin=FAKE, model=MODEL, effort="max", prompt="p")
    assert command[1:3] == ["-p", "p"]
    for flag in (
        "--restricted",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--verbose",
    ):
        assert flag in command
    assert command[command.index("--model") + 1] == MODEL
    assert command[command.index("--effort") + 1] == "max"
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert "WebFetch" not in command[command.index("--tools") + 1]
    # --bare never reads OAuth, so it cannot run on a subscription.
    assert "--bare" not in command and "bypassPermissions" not in command


def test_env_strips_api_keys_and_effort_override(monkeypatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_EFFORT_LEVEL", "GRADER"):
        monkeypatch.setenv(key, "leak")
    env = provider_env()
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_EFFORT_LEVEL", "GRADER"):
        assert key not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "test-token"


def test_success_verifies_model_and_records_unverified_effort(tmp_path) -> None:
    result = _run(tmp_path)
    assert result.exit_code == 0
    assert not result.invalid_configuration and not result.infrastructure
    assert result.error_code is None
    assert result.verified_model == MODEL
    assert result.verified_effort is None
    assert result.thread_id == "sess_fake_1"
    assert result.usage["equivalent_cost_usd"] == pytest.approx(0.0123)
    assert result.metadata["summary"]["isolation"] == "hermetic"
    assert result.metadata["summary"]["unknown_event_types"] == ["future_event_type"]
    assert "<prompt>" in result.command and "fix it" not in result.command


@pytest.mark.parametrize(
    "scenario,invalid,infra,code",
    [
        ("model_mismatch", True, False, "model_mismatch"),
        ("fallback", True, False, "model_mismatch"),
        ("api_key", True, False, "auth_not_subscription"),
        ("usage_limit", False, True, "usage_limit"),
        ("crash", False, True, "no_result_event"),
        ("max_turns", False, False, "max_turns"),
    ],
)
def test_scenarios_fail_closed(tmp_path, monkeypatch, scenario, invalid, infra, code) -> None:
    monkeypatch.setenv("CLAUDE_FAKE_SCENARIO", scenario)
    result = _run(tmp_path)
    assert result.invalid_configuration is invalid
    assert result.infrastructure is infra
    assert result.error_code == code


def test_timeout_is_not_infrastructure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_FAKE_SCENARIO", "sleep")
    result = _run(tmp_path, timeout=1)
    assert result.error_code == "timeout"
    assert result.exit_code == 124 and not result.infrastructure


def test_without_token_or_opt_in_attempt_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    assert isolation_mode(provider_env()) is None
    result = _run(tmp_path)
    assert result.invalid_configuration and result.error_code == "isolation_unavailable"
    monkeypatch.setenv("CLAUDE_ALLOW_USER_CONFIG", "1")
    assert _run(tmp_path).metadata["summary"]["isolation"] == "user_config"


def test_series_accepts_comparison_and_max_but_not_api_tracks() -> None:
    for effort in ("low", "medium", "high", "xhigh", "max"):
        validate_model_effort_track(MODEL, effort, "claude_code_product")
    with pytest.raises(InvalidConfigurationError):
        validate_model_effort_track(MODEL, "xhigh", "model_only")
    with pytest.raises(InvalidConfigurationError):
        validate_model_effort_track("grok-4.6", "max", "model_only")
    config = _config_for(track="claude_code_product", model=MODEL, effort="xhigh", client_mode="latest")
    assert config.provider == "claude_code_cli"
    assert config.auth_surface == "claude_subscription"
    assert config.temperature is None and config.top_p is None


def _attempt(tmp_path: Path, task_id: str = "BUG-PY-01", provider=None):
    task = select_tasks(ROOT / "tasks", [task_id])[0]
    store = Store(f"sqlite:///{tmp_path / 'reg.db'}")
    provider = provider or ClaudeCodeCLIProvider(claude_bin=FAKE, model=MODEL, effort="xhigh")
    coordinator = Coordinator(store, ArtifactStore(tmp_path / "arts"), provider, ROOT)
    suite_id, task_version_id = coordinator.ensure_suite_and_task("canary", task.version, task)
    config = _config_for(track="claude_code_product", model=MODEL, effort="xhigh", client_mode="latest")
    config_id = coordinator.ensure_config(config, config.toolset_version, config.system_prompt_version)
    run_id = store.create_run(
        {
            "suite_id": suite_id,
            "config_id": config_id,
            "trigger": "test",
            "status": "running",
            "runner_git_sha": "test",
        }
    )
    return coordinator.run_attempt(
        run_id=run_id,
        suite_version=task.version,
        task=task,
        task_version_id=task_version_id,
        config=config,
        trial_index=0,
        source="model",
        work_root=tmp_path / "work",
    ), task


def test_end_to_end_gold_edits_pass_and_no_edits_fail(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLMREG_ALLOW_HOST", "1")
    task = select_tasks(ROOT / "tasks", ["BUG-PY-01"])[0]
    monkeypatch.setenv("CLAUDE_FAKE_PATCH", str(task.gold_patch.resolve()))
    passed, _ = _attempt(tmp_path / "pass")
    assert passed.quality_status == "quality_pass"
    monkeypatch.delenv("CLAUDE_FAKE_PATCH")
    failed, _ = _attempt(tmp_path / "fail")
    assert failed.quality_status == "quality_fail"

    from runner.dashboard import write_dashboard

    html = write_dashboard(Store(f"sqlite:///{tmp_path / 'pass' / 'reg.db'}"), tmp_path / "dash").read_text()
    assert "Optional Claude Code product series" in html
    assert "claude-opus-5-5 · xhigh · claude_code_product" in html


def test_end_to_end_model_mismatch_is_not_graded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLMREG_ALLOW_HOST", "1")
    monkeypatch.setenv("CLAUDE_FAKE_SCENARIO", "model_mismatch")
    outcome, _ = _attempt(tmp_path)
    assert outcome.quality_status == "invalid_configuration"
    assert outcome.error_code == "model_mismatch"
    assert outcome.grade is None


class _RecordingProvider:
    name = "claude_code_cli"
    track = "claude_code_product"

    def __init__(self) -> None:
        self.calls = 0

    def run_attempt(self, **_: object):
        self.calls += 1
        raise AssertionError("the agent must not run without a grader container")


def test_scientific_attempt_without_containers_fails_before_the_agent_runs(tmp_path) -> None:
    provider = _RecordingProvider()
    outcome, _ = _attempt(tmp_path, provider=provider)
    assert outcome.quality_status == "invalid_configuration"
    assert "images build" in str((outcome.error or {}).get("message"))
    assert provider.calls == 0


def test_container_command_forwards_secrets_by_name_only(tmp_path, monkeypatch) -> None:
    runtime = ContainerRuntime(
        image="sha256:abc", network="llmreg-agent-net", proxy_url="http://llmreg-egress:3128",
        egress_allow=("api.anthropic.com:443",),
    )
    inner = build_claude_command(claude_bin=Path("claude"), model=MODEL, effort="high", prompt="p")
    command = build_container_command(
        runtime, workspace=tmp_path, name="llmreg-agent-x", inner=inner, env_names=["CLAUDE_CODE_OAUTH_TOKEN"]
    )
    joined = " ".join(command)
    assert command[:4] == ["docker", "run", "--rm", "--name"]
    assert command[command.index("--network") + 1] == "llmreg-agent-net"
    assert "--cap-drop ALL" in joined and "no-new-privileges" in joined
    assert f"{tmp_path}:/workspace:rw" in command
    assert "HTTPS_PROXY=http://llmreg-egress:3128" in command
    assert "CLAUDE_CONFIG_DIR=/tmp/claude-config" in command
    token_flag = command.index("CLAUDE_CODE_OAUTH_TOKEN")
    assert command[token_flag - 1] == "-e"
    assert "test-token" not in joined
    assert command[command.index("sha256:abc") + 1 :] == inner
    # Only the workspace is mounted: no home directory, no grader, no docker socket.
    mounts = [command[i + 1] for i, part in enumerate(command) if part == "-v"]
    assert mounts == [f"{tmp_path}:/workspace:rw"]


def test_container_mode_requires_the_token(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    monkeypatch.setenv("CLAUDE_ALLOW_USER_CONFIG", "1")
    assert isolation_mode(provider_env(), container=True) is None
    runtime = ContainerRuntime(image="sha256:abc", network="n", proxy_url="http://p:3128")
    provider = ClaudeCodeCLIProvider(claude_bin=Path("claude"), model=MODEL, effort="high", runtime=runtime)
    result = provider.run_attempt(prompt="p", workspace=tmp_path, model=MODEL, effort="high", timeout_seconds=5)
    assert result.invalid_configuration and result.error_code == "isolation_unavailable"


def test_real_cli_not_logged_in_stream_is_infrastructure() -> None:
    # Captured from Claude Code 2.1.286 in the agent image with the provider's exact flags.
    stream = (Path(__file__).parent / "data" / "claude_code_2.1.286_not_logged_in.jsonl").read_text()
    summary = summarize_stream(parse_jsonl(stream)[0])
    assert summary["init_model"] == MODEL
    assert summary["tools"] == ["Bash", "Edit", "Glob", "Grep", "Read", "Write"]
    assert summary["mcp_servers"] == [] and summary["skills"] == [] and summary["slash_commands"] == []
    assert summary["permission_mode"] == "dontAsk"
    assert summary["terminal_reason"] == "api_error"
    verdict = classify(summary, model=MODEL, effort="xhigh", stderr="", exit_code=1)
    assert verdict["infrastructure"] and verdict["error_code"] == "auth"
    assert verdict["verified_model"] == MODEL and not verdict["invalid"]


@pytest.mark.parametrize("status,code", [(429, "usage_limit"), (503, "http_5xx"), (401, "auth")])
def test_api_error_status_is_classified(status, code) -> None:
    summary = {
        "init_model": MODEL,
        "served_models": [],
        "api_key_source": "none",
        "has_result": True,
        "is_error": True,
        "result_subtype": "success",
        "terminal_reason": "api_error",
        "api_error_status": status,
        "final_text": "API Error",
    }
    verdict = classify(summary, model=MODEL, effort="high", stderr="", exit_code=1)
    assert verdict["infrastructure"] and verdict["error_code"] == code


def test_factory_refuses_without_images_or_host_opt_in(monkeypatch) -> None:
    from runner.providers.factory import make_provider

    config = _config_for(track="claude_code_product", model=MODEL, effort="xhigh", client_mode="latest")
    with pytest.raises(SystemExit, match="images build"):
        make_provider(config)
    monkeypatch.setenv("LLMREG_ALLOW_HOST", "1")
    monkeypatch.setenv("CLAUDE_BIN", str(FAKE))
    provider, _ = make_provider(config)
    assert provider.runtime is None


def test_evaluate_defaults_to_opus_on_the_claude_code_track(tmp_path, monkeypatch) -> None:
    from runner.evaluate import evaluate

    monkeypatch.setenv("CLAUDE_BIN", str(FAKE))
    run_ids = evaluate(
        ROOT,
        "canary",
        efforts=["xhigh"],
        source="none",
        track="claude_code_product",
        repeats=1,
        artifact_dir=tmp_path / "arts",
        database_url=f"sqlite:///{tmp_path / 'reg.db'}",
    )
    assert list(run_ids) == ["claude-opus-5-5/xhigh/claude_code_product"]


def test_operator_ca_adds_trust_without_relaxing_verification(tmp_path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    runtime = ContainerRuntime(image="sha256:abc", network="n", proxy_url="http://p:3128", extra_ca=str(ca))
    inner = build_claude_command(claude_bin=Path("claude"), model=MODEL, effort="high", prompt="p")
    command = build_container_command(runtime, workspace=tmp_path, name="x", inner=inner, env_names=[])
    mounts = [command[i + 1] for i, part in enumerate(command) if part == "-v"]
    assert mounts == [f"{tmp_path}:/workspace:rw", f"{ca}:/etc/llmreg/extra-ca.pem:ro"]
    assert "NODE_EXTRA_CA_CERTS=/etc/llmreg/extra-ca.pem" in command
    joined = " ".join(command)
    assert "NODE_TLS_REJECT_UNAUTHORIZED" not in joined and "--insecure" not in joined
    assert len(runtime.describe()["extra_ca_sha256"]) == 64
