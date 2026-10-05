from __future__ import annotations

import json
from pathlib import Path

import pytest

from runner.calibrate import calibrate_rows, load_rows, main as calibrate_main, series_run_ids, wilson
from runner.coordinator import cost_usd_ticks
from runner.plan import attempts_needed, main as plan_main, min_detectable_drop, plan
from runner.storage import Store


def _rows(task: str, passes: int, fails: int, infra: int = 0, invalid: int = 0) -> list[dict]:
    rows = [{"task_key": task, "quality_status": "quality_pass", "partial_score": 1.0} for _ in range(passes)]
    rows += [{"task_key": task, "quality_status": "quality_fail", "partial_score": 0.5} for _ in range(fails)]
    rows += [{"task_key": task, "quality_status": "infra_fail", "partial_score": None} for _ in range(infra)]
    rows += [{"task_key": task, "quality_status": "invalid_configuration", "partial_score": None} for _ in range(invalid)]
    return rows


def test_wilson_interval_matches_reference_values() -> None:
    assert wilson(0, 10) == pytest.approx((0.0, 0.2775), abs=1e-3)
    assert wilson(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-3)
    assert wilson(0, 0) == (0.0, 1.0)


def test_calibration_keeps_only_mid_band_tasks() -> None:
    rows = (
        _rows("MID", 2, 3)
        + _rows("EASY", 5, 0)
        + _rows("HARD", 0, 5, infra=2)
        + _rows("FEW", 1, 1, invalid=1)
    )
    report = calibrate_rows(rows, min_attempts=3)
    verdicts = {item["task"]: item["verdict"] for item in report["tasks"]}
    assert verdicts == {"EASY": "too_easy", "FEW": "insufficient", "HARD": "too_hard", "MID": "keep"}
    assert report["kept"] == ["MID"]
    by_task = {item["task"]: item for item in report["tasks"]}
    # Infrastructure and invalid attempts are counted but never graded.
    assert by_task["HARD"]["graded"] == 5 and by_task["HARD"]["infra"] == 2
    assert by_task["FEW"]["graded"] == 2 and by_task["FEW"]["invalid"] == 1
    assert by_task["MID"]["mean_partial"] == pytest.approx((2 * 1.0 + 3 * 0.5) / 5)
    # Mean of per-task means, not a pooled attempt rate.
    assert report["series_pass_rate"] == pytest.approx((0.4 + 1.0 + 0.0 + 0.5) / 4)


def test_calibration_reads_runs_and_excludes_non_scientific_by_default(tmp_path) -> None:
    from runner.evaluate import evaluate

    db = f"sqlite:///{tmp_path / 'reg.db'}"
    evaluate(
        Path.cwd(),
        "demo",
        efforts=["xhigh"],
        source="gold",
        tracks=["model_only"],
        models=["grok-4.6"],
        repeats=1,
        artifact_dir=tmp_path / "arts",
        database_url=db,
    )
    store = Store(db)
    run_ids = series_run_ids(store, model="grok-4.6", effort="xhigh", track="model_only")
    assert len(run_ids) == 1
    assert load_rows(store, run_ids) == []
    report = calibrate_rows(load_rows(store, run_ids, scientific_only=False), min_attempts=1)
    assert len(report["tasks"]) == 2
    assert all(item["verdict"] == "too_easy" for item in report["tasks"])

    out = tmp_path / "cal.json"
    assert calibrate_main(
        ["--series", "grok-4.6/xhigh/model_only", "--any-source", "--min-attempts", "1", "--database-url", db,
         "--out", str(out)]
    ) == 0
    assert json.loads(out.read_text())["kept"] == []


def test_detectable_drop_and_required_attempts() -> None:
    assert min_detectable_drop(100) == pytest.approx(0.198, abs=1e-3)
    assert min_detectable_drop(10) == pytest.approx(0.626, abs=1e-3)
    assert attempts_needed(0.15, pass_rate=0.55) == 169
    assert attempts_needed(0.10, pass_rate=0.55) == 385
    report = plan(attempts_per_week=60, tasks=10)
    assert report["attempts_per_window"] == 60 and report["repeats_per_task"] == 6
    assert report["min_detectable_drop"] == pytest.approx(0.256, abs=1e-3)
    assert [item["drop"] for item in report["targets"]] == [0.10, 0.15, 0.20, 0.30]


def test_plan_converts_a_budget_with_a_calibration_file(tmp_path, capsys) -> None:
    calibration = tmp_path / "cal.json"
    calibration.write_text(json.dumps({"kept": ["A", "B", "C", "D"], "kept_pass_rate": 0.5}))
    assert plan_main(
        ["--budget-usd-per-week", "100", "--cost-per-attempt", "2.5", "--calibration", str(calibration), "--json"]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["attempts_per_week"] == 40 and report["tasks"] == 4 and report["repeats_per_task"] == 10


def test_attempt_cost_is_persisted_in_ticks() -> None:
    assert cost_usd_ticks({"cost_usd_ticks": 123}) == 123
    assert cost_usd_ticks({"cost_in_usd_ticks": 7}) == 7
    assert cost_usd_ticks({"equivalent_cost_usd": 0.0123}) == 123_000_000
    assert cost_usd_ticks({}) is None
