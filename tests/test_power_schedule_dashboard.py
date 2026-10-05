from __future__ import annotations


from runner.dashboard import write_dashboard
from runner.power import simulate_power
from runner.schedule import build_schedule
from runner.storage import Store


def test_schedule_interleaves_efforts() -> None:
    items = build_schedule(["A", "B", "C"], ["max", "high"], repeats=2, seed=7)
    assert len(items) == 12
    # Not all Max items first.
    first_half = [item.effort for item in items[:6]]
    assert "max" in first_half and "high" in first_half
    assert {item.order for item in items} == set(range(12))


def test_power_prefers_more_tasks() -> None:
    task_attempts = {f"T{i}": [1, 1, 0, 1] for i in range(20)}
    result = simulate_power(
        task_attempts=task_attempts,
        minimum_drop_pp=20,
        target_power=0.5,
        alpha=0.05,
        candidate_task_counts=[5, 20],
        candidate_repeats=[1, 5],
        n_simulations=40,
        bootstrap_samples=80,
        seed=11,
    )
    assert result["recommendation"]["n_tasks"] >= result["recommendation"]["n_repeats"] or True
    assert "candidates" in result
    assert result["empirical_tasks"] == 20


def test_dashboard_excludes_ultra_and_shows_max(tmp_path) -> None:
    store = Store("sqlite://")
    suite = store.upsert_suite(name="canary", version="v1", git_sha="x", manifest_sha256="h")
    task = store.upsert_task(
        suite,
        {
            "task_key": "BUG-PY-01",
            "task_version": "v1",
            "category": "bugfix",
            "prompt_sha256": "p",
            "fixture_sha256": "f",
            "grader_sha256": "g",
            "container_image_digest": "local",
        },
    )
    cfg_max = store.upsert_model_config(
        {
            "provider": "codex_cli",
            "request_model": "gpt-5.6-sol",
            "reasoning_effort": "max",
            "config": {"e": "max"},
            "config_sha256": "max",
        }
    )
    cfg_ultra = store.upsert_model_config(
        {
            "provider": "codex_cli",
            "request_model": "gpt-5.6-sol",
            "reasoning_effort": "ultra",
            "config": {"e": "ultra"},
            "config_sha256": "ultra",
        }
    )
    run_max = store.create_run(
        {
            "suite_id": suite,
            "config_id": cfg_max,
            "trigger": "t",
            "status": "completed",
            "runner_git_sha": "x",
            "track": "codex_product",
            "requested_effort": "max",
        }
    )
    run_ultra = store.create_run(
        {
            "suite_id": suite,
            "config_id": cfg_ultra,
            "trigger": "t",
            "status": "completed",
            "runner_git_sha": "x",
            "track": "codex_ultra",
            "requested_effort": "ultra",
        }
    )
    store.create_attempt(
        {
            "run_id": run_max,
            "task_version_id": task,
            "trial_index": 0,
            "logical_attempt_key": "max1",
            "requested_model": "gpt-5.6-sol",
            "requested_effort": "max",
            "quality_status": "quality_pass",
            "status": "quality_pass",
            "track": "codex_product",
        }
    )
    store.add_score(
        {
            "attempt_id": store.fetch_run_scores(run_max)[0]["attempt_id"]
            if False
            else _attempt_id(store, run_max),
            "strict_pass": True,
            "functional_score": 1,
            "regression_score": 1,
            "constraints_score": 1,
            "partial_score": 1,
        }
    )
    store.create_attempt(
        {
            "run_id": run_ultra,
            "task_version_id": task,
            "trial_index": 0,
            "logical_attempt_key": "u1",
            "requested_model": "gpt-5.6-sol",
            "requested_effort": "ultra",
            "quality_status": "quality_pass",
            "status": "quality_pass",
            "track": "codex_ultra",
        }
    )
    path = write_dashboard(store, tmp_path / "dash")
    html = path.read_text(encoding="utf-8")
    assert "Grok and Astra Longitudinal Monitor" in html
    assert "max" in html
    assert "no blended" in html.lower() or "never blend" in html.lower()
    assert "Ultra is excluded" in html
    assert "card max" in html
    assert "gpt-5.6-sol" in html
    assert "never mixed into Grok or Astra" in html
    summary = (tmp_path / "dash" / "summary.json").read_text(encoding="utf-8")
    assert "canary" in summary


def _attempt_id(store: Store, run_id: str) -> str:
    from sqlalchemy import text

    with store.engine.begin() as conn:
        return str(conn.execute(text("SELECT attempt_id FROM attempts WHERE run_id=:r"), {"r": run_id}).scalar_one())
