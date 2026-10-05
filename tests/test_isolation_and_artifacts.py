from __future__ import annotations

from pathlib import Path

import pytest

from runner.artifacts import ArtifactStore
from runner.coordinator import Coordinator
from runner.hash_tree import hash_text
from runner.isolation import probe_from_workspace, workspace_leak_report
from runner.providers.codex_cli import CodexCLIProvider
from runner.storage import Store
from runner.tasks import select_tasks
from runner.test_graders import gold_config


FAKE = Path(__file__).resolve().parent / "fakes" / "codex"


def test_fixture_cannot_see_private_graders() -> None:
    task = select_tasks(Path("tasks"), ["DEMO-PY-01"])[0]
    leaks = workspace_leak_report(task.fixture_path, task.grader_path)
    assert leaks == []
    visible = probe_from_workspace(
        task.fixture_path,
        [Path("gold.patch"), Path("hidden_tests"), Path("grader"), Path("private_graders")],
    )["visible"]
    assert visible == []
    assert "private_graders" not in task.fixture_path.as_posix()


def test_agent_workspace_does_not_mount_grader(tmp_path) -> None:
    store = Store("sqlite://")
    artifacts = ArtifactStore(tmp_path / "arts")
    provider = CodexCLIProvider(codex_bin=FAKE, model="gpt-5.6-sol", effort="max")
    coordinator = Coordinator(store, artifacts, provider, Path.cwd())
    task = select_tasks(Path("tasks"), ["DEMO-PY-01"])[0]
    suite_id, task_id = coordinator.ensure_suite_and_task("iso", task.version, task)
    config = gold_config()
    config_id = coordinator.ensure_config(config, "t", "s")
    run = store.create_run({"suite_id": suite_id, "config_id": config_id, "trigger": "t", "status": "running", "runner_git_sha": "x"})
    outcome = coordinator.run_attempt(
        run_id=run,
        suite_version="v1",
        task=task,
        task_version_id=task_id,
        config=config,
        trial_index=0,
        source="none",
        work_root=tmp_path / "work",
    )
    assert outcome.grade is not None
    assert outcome.quality_status == "quality_fail"


def test_artifact_hashes_are_stored(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    written = store.write_text("a/raw_codex.jsonl", '{"type":"thread.started"}\n')
    assert written["sha256"] == hash_text('{"type":"thread.started"}\n')
    db = Store("sqlite://")
    suite = db.upsert_suite(name="s", version="1", git_sha="x", manifest_sha256="m")
    task = db.upsert_task(
        suite,
        {
            "task_key": "T",
            "task_version": "v1",
            "category": "bugfix",
            "prompt_sha256": "p",
            "fixture_sha256": "f",
            "grader_sha256": "g",
            "container_image_digest": "local",
        },
    )
    cfg = db.upsert_model_config(
        {
            "provider": "codex_cli",
            "request_model": "gpt-5.6-sol",
            "reasoning_effort": "max",
            "config": {},
            "config_sha256": "c2",
        }
    )
    run = db.create_run({"suite_id": suite, "config_id": cfg, "trigger": "t", "status": "running", "runner_git_sha": "x"})
    attempt = db.create_attempt(
        {
            "run_id": run,
            "task_version_id": task,
            "trial_index": 0,
            "logical_attempt_key": "k",
            "requested_model": "gpt-5.6-sol",
        }
    )
    db.add_artifact(attempt, "raw_codex.jsonl", written["uri"], written["sha256"])
    rows = db.list_artifacts(attempt)
    assert rows[0]["sha256"] == written["sha256"]
    assert db.verify_attempt_artifacts(attempt)[0]["ok"] is True
    Path(written["uri"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        db.verify_attempt_artifacts(attempt)
    with pytest.raises(ValueError, match="hash mismatch"):
        store.verify("a/raw_codex.jsonl", written["sha256"])


def test_fixture_hash_mismatch_is_invalid(tmp_path) -> None:
    store = Store("sqlite://")
    artifacts = ArtifactStore(tmp_path / "arts")
    coordinator = Coordinator(store, artifacts, None, Path.cwd())
    task = select_tasks(Path("tasks"), ["DEMO-PY-01"])[0]
    suite_id, task_id = coordinator.ensure_suite_and_task("iso", task.version, task)
    config = gold_config()
    config_id = coordinator.ensure_config(config, "t", "s")
    run = store.create_run({"suite_id": suite_id, "config_id": config_id, "trigger": "t", "status": "running", "runner_git_sha": "x"})
    outcome = coordinator.run_attempt(
        run_id=run,
        suite_version="v1",
        task=task,
        task_version_id=task_id,
        config=config,
        trial_index=0,
        source="none",
        expected_hashes={"fixture_sha256": "0" * 64},
    )
    assert outcome.quality_status == "invalid_configuration"


def test_search_graders_scenario_sees_nothing_in_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_FAKE_SCENARIO", "search_graders")
    provider = CodexCLIProvider(codex_bin=FAKE, model="gpt-5.6-sol", effort="max")
    result = provider.run_attempt(
        prompt="search",
        workspace=tmp_path,
        model="gpt-5.6-sol",
        effort="max",
        timeout_seconds=5,
    )
    assert "[]" in result.final_text or '"visible": []' in result.final_text or result.final_text == "done"


def test_unresolvable_image_uses_local_backend(tmp_path) -> None:
    from runner.sandbox import TaskSandbox

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "hello.txt").write_text("hi\n", encoding="utf-8")
    sandbox = TaskSandbox(fixture, work_root=tmp_path / "work", image="placeholder@sha256:local")
    try:
        sandbox.materialize()
        result = sandbox.run(["cat", "hello.txt"])
    finally:
        sandbox.cleanup()
    assert result.backend == "local"
    assert result.returncode == 0 and result.stdout == "hi\n"
