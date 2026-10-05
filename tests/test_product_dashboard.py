from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from runner import judge
from runner.artifacts import ArtifactStore
from runner.coordinator import Coordinator
from runner.evaluate import _config_for
from runner.product_dashboard import collect
from runner.report_site import build, next_run
from runner.providers.claude_code_cli import ClaudeCodeCLIProvider
from runner.storage import Store
from runner.tasks import select_tasks

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).resolve().parent / "fakes" / "claude"
MODEL = "claude-opus-5-5"


def test_dashboard_shows_strict_results_and_quality_side_by_side(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLMREG_ALLOW_HOST", "1")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    monkeypatch.delenv("CLAUDE_FAKE_SCENARIO", raising=False)
    task = select_tasks(ROOT / "tasks", ["DEMO-PY-01"])[0]
    monkeypatch.setenv("CLAUDE_FAKE_PATCH", str(task.gold_patch.resolve()))
    db = tmp_path / "reg.db"
    store = Store(f"sqlite:///{db}")
    provider = ClaudeCodeCLIProvider(claude_bin=FAKE, model=MODEL, effort="high")
    coordinator = Coordinator(store, ArtifactStore(tmp_path / "arts"), provider, ROOT)
    suite_id, task_version_id = coordinator.ensure_suite_and_task("product", task.version, task)
    config = _config_for(track="claude_code_product", model=MODEL, effort="high", client_mode="latest")
    config_id = coordinator.ensure_config(config, config.toolset_version, config.system_prompt_version)
    run_id = store.create_run({"suite_id": suite_id, "config_id": config_id, "trigger": "test", "status": "running",
                               "runner_git_sha": "test", "track": "claude_code_product", "scientific_data": True,
                               "requested_model": MODEL, "requested_effort": "high"})
    outcome = coordinator.run_attempt(run_id=run_id, suite_version=task.version, task=task,
                                      task_version_id=task_version_id, config=config, trial_index=0,
                                      source="model", work_root=tmp_path / "work")
    assert outcome.quality_status == "quality_pass"
    with store.engine.begin() as conn:
        attempt_id = conn.execute(text("SELECT attempt_id FROM attempts")).scalar()
    scores = {name: 4 for name in judge.DIMENSIONS}
    # Only the current judge's scores are shown; another judge's would not be comparable.
    judge.record(store, attempt_id=attempt_id, subject="attempt", task_key="DEMO-PY-01", model="other-judge",
                 effort="high", repeat_index=0, prompt="p", verdict={"overall": 1.0, "scores": {n: 1 for n in judge.DIMENSIONS},
                 "rationale": {}, "unsupported_claims": [], "summary": "x", "served_model": ["x"]}, error=None)
    judge.record(store, attempt_id=attempt_id, subject="attempt", task_key="DEMO-PY-01",
                 model=judge.DEFAULT_JUDGE_MODEL, effort=judge.DEFAULT_JUDGE_EFFORT,
                 repeat_index=0, prompt="p", verdict={"overall": 4.0, "scores": scores, "rationale": {},
                 "unsupported_claims": [], "summary": "Clean fix.", "served_model": ["j"]}, error=None)

    data = collect(db)
    assert [(r["effort"], r["graded"], r["passed"]) for r in data["runs"]] == [("high", 1, 1)]
    (attempt,) = data["attempts"]
    assert attempt["task_key"] == "DEMO-PY-01" and attempt["source"] == "original"
    assert attempt["quality"]["overall"] == 4.0 and attempt["quality"]["summary"] == "Clean fix."
    site = build(tmp_path / "site", data, now=datetime(2026, 10, 3, 17, 12, tzinfo=timezone.utc))
    assert sorted(path.name for path in site.iterdir()) == ["app.js", "checks.js", "data.json", "index.html", "style.css"]
    payload = json.loads((site / "data.json").read_text())
    assert payload["runs"][0]["passed"] == 1 and payload["next_run"] == "2026-10-04T08:52+00:00"
    assert "<title>Nerf Watch</title>" in (site / "index.html").read_text()


def test_next_run_is_the_next_scheduled_slot() -> None:
    assert next_run(datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)).isoformat() == "2026-10-03T08:52:00+00:00"
    assert next_run(datetime(2026, 10, 3, 8, 52, tzinfo=timezone.utc)).isoformat() == "2026-10-04T08:52:00+00:00"
